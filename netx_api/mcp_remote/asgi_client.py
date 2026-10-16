"""In-process ASGI client used by MCP tool handlers (no TCP loopback)."""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote

import httpx

from .context import get_app, get_request_context

_PROTOCOL_KEY_ZH_TO_EN: dict[str, str] = {
    "其他": "Other",
    "时钟": "Clock",
    "OTN/光": "OTN/Optical",
    "电源": "Power",
}

_SECRET_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "api_token",
        "api_key",
        "authorization",
        "access_token",
        "refresh_token",
        "private_key",
        "client_secret",
        "ssh_password",
        "enable_password",
        "hop_password",
        "hop_password_enc",
        "password_enc",
        "password_ciphertext",
        "hop_password_ciphertext",
        "credential_secret",
        "secret_key",
        "credentials",
    }
)


def quote_ne_id(ne_id: str) -> str:
    return quote(str(ne_id or "").strip(), safe="")


def api_token() -> str:
    from pathlib import Path

    ctx = get_request_context()
    if ctx and ctx.token and ctx.token != "disabled":
        return ctx.token
    token = (os.getenv("NETX_API_TOKEN") or os.getenv("OCLAW_NETX_API_TOKEN") or "").strip()
    if token:
        return token
    for candidate in (os.getenv("NETX_MCP_TOKEN_FILE", "").strip(), "data/auth/mcp_token"):
        if not candidate:
            continue
        try:
            token = Path(candidate).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError, ValueError):
            continue
        if token:
            return token
    return ""


def lang_query_params() -> dict[str, str]:
    ctx = get_request_context()
    if ctx is not None:
        lang = str(ctx.lang or "zh").strip().lower()
    else:
        lang = str(os.getenv("NETX_LANG") or "zh").strip().lower()
    if lang.startswith("en"):
        return {"lang": "en"}
    return {}


def localize_payload(data: dict[str, Any]) -> dict[str, Any]:
    ctx = get_request_context()
    if ctx is not None:
        lang = str(ctx.lang or "zh").strip().lower()
    else:
        lang = str(os.getenv("NETX_LANG") or "zh").strip().lower()
    if not lang.startswith("en"):
        return data
    proto = data.get("protocol_summary")
    if isinstance(proto, list):
        for row in proto:
            if isinstance(row, dict):
                k = str(row.get("key") or "")
                if k in _PROTOCOL_KEY_ZH_TO_EN:
                    row["key"] = _PROTOCOL_KEY_ZH_TO_EN[k]
    return data


def redact(value: Any, *, tokens: tuple[str, ...] | None = None) -> Any:
    if tokens is None:
        tokens = (api_token(),)
    if isinstance(value, dict):
        return {
            k: redact(v, tokens=tokens)
            for k, v in value.items()
            if str(k).lower() not in _SECRET_KEYS
        }
    if isinstance(value, list):
        return [redact(v, tokens=tokens) for v in value]
    if isinstance(value, str):
        for token in tokens:
            if token:
                value = value.replace(token, "[REDACTED]")
        value = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        value = re.sub(
            r"(?im)(\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*)([^\s,;]+)",
            r"\1[REDACTED]",
            value,
        )
    return value


def _headers() -> dict[str, str]:
    h = {"accept": "application/json"}
    tok = api_token()
    if tok:
        h["authorization"] = f"Bearer {tok}"
    return h


def _default_timeout() -> float:
    try:
        return float(os.getenv("NETX_HTTP_TIMEOUT") or 120.0)
    except (TypeError, ValueError):
        return 120.0


_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def bind_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Remember the API loop; tool handlers run in worker threads and submit ASGI calls to it."""
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def _request(app: Any, method: str, path: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
    if app is None:
        base = (
            os.getenv("NETX_API_URL")
            or os.getenv("OCLAW_NETX_BASE_URL")
            or "http://127.0.0.1:8890"
        ).rstrip("/")
        with httpx.Client(base_url=base, timeout=timeout, trust_env=False) as client:
            return client.request(method, path, **kwargs)

    # httpx.ASGITransport is async-only: run the in-process call as a coroutine.
    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mcp.internal",
            timeout=timeout,
            trust_env=False,
        ) as client:
            return await client.request(method, path, **kwargs)

    loop = _MAIN_LOOP
    if loop is not None and loop.is_running():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run_coroutine_threadsafe(_go(), loop).result(timeout + 5)
        raise RuntimeError("http_json must not be called on the event loop thread")
    return asyncio.run(_go())


def http_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    # When not attached to the FastAPI app, require an explicit outbound token
    # (remote MCP itself authenticates at /mcp; this covers unit tests / fallback).
    if get_app() is None and not api_token():
        return {
            "ok": False,
            "error": "netx_token_missing",
            "hint": "设置 NETX_API_TOKEN、NETX_MCP_TOKEN_FILE，或工作目录 data/auth/mcp_token。",
        }
    merged: dict[str, Any] = dict(lang_query_params())
    if params:
        merged.update(params)
    to = float(timeout) if timeout is not None else _default_timeout()
    tok = api_token()
    headers = {"accept": "application/json"}
    if tok:
        headers["authorization"] = f"Bearer {tok}"
    try:
        resp = _request(
            get_app(),
            method,
            path,
            timeout=to,
            params=merged or None,
            json=body,
            headers=headers,
        )
        text = resp.text
        if not resp.is_success:
            return {
                "ok": False,
                "error": f"netx_http_{resp.status_code}",
                "detail": text[:800],
                "hint": "检查 API 地址、令牌 scope 和资源 ID。",
            }
        data = resp.json() if text else {}
        if isinstance(data, dict):
            data = localize_payload(data)
            data = redact(data, tokens=(tok,))
        else:
            data = {"raw": data}
        return {"ok": True, "data": data}
    except Exception as exc:
        return {
            "ok": False,
            "error": "netx_request_failed",
            "detail": str(exc)[:800],
            "hint": "检查 API 连通性及 JSON 响应。",
        }


def http_post_json(path: str, body: dict[str, Any], *, timeout: float = 180.0) -> dict[str, Any]:
    return http_json("POST", path, body=body, timeout=timeout)


def http_json_many(
    requests: list[dict[str, Any]],
    *,
    max_workers: int = 12,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    if not requests:
        return []
    if len(requests) == 1:
        r0 = requests[0]
        out = http_json(
            str(r0.get("method") or "GET"),
            str(r0.get("path") or ""),
            params=r0.get("params") if isinstance(r0.get("params"), dict) else None,
            body=r0.get("body") if isinstance(r0.get("body"), dict) else None,
            timeout=timeout,
        )
        if "key" in r0:
            out = {**out, "key": r0.get("key")}
        return [out]

    to = float(timeout) if timeout is not None else _default_timeout()
    workers = max(1, min(int(max_workers), len(requests)))
    results: list[dict[str, Any] | None] = [None] * len(requests)

    def _one(idx: int, req: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        out = http_json(
            str(req.get("method") or "GET"),
            str(req.get("path") or ""),
            params=req.get("params") if isinstance(req.get("params"), dict) else None,
            body=req.get("body") if isinstance(req.get("body"), dict) else None,
            timeout=to,
        )
        if "key" in req:
            out = {**out, "key": req.get("key")}
        return idx, out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Copy the caller's context so each request keeps the caller's token (RBAC).
        futs = [pool.submit(contextvars.copy_context().run, _one, i, r) for i, r in enumerate(requests)]
        for fut in as_completed(futs):
            idx, out = fut.result()
            results[idx] = out
    return [r if isinstance(r, dict) else {"ok": False, "error": "parallel_slot_empty"} for r in results]


def mcp_text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
    }
    if is_error:
        out["isError"] = True
    return out


def mcp_from_handler_result(result: dict[str, Any]) -> dict[str, Any]:
    result = redact(result) if isinstance(result, dict) else result
    data = result.get("data") if isinstance(result, dict) else None
    if (
        not isinstance(result, dict)
        or not result.get("ok")
        or (isinstance(data, dict) and data.get("ok") is False)
    ):
        return mcp_text_result(result, is_error=True)
    return mcp_text_result(result)
