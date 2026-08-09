"""HTTP client for netx REST API (topology MCP)."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx


def api_base_url() -> str:
    raw = (
        os.getenv("NETX_API_URL")
        or os.getenv("OCLAW_NETX_BASE_URL")
        or "http://127.0.0.1:8890"
    )
    return str(raw or "").strip().rstrip("/")


def api_headers() -> dict[str, str]:
    h = {"accept": "application/json"}
    tok = (os.getenv("NETX_API_TOKEN") or os.getenv("OCLAW_NETX_API_TOKEN") or "").strip()
    if not tok:
        candidates = [
            os.getenv("NETX_MCP_TOKEN_FILE", "").strip(),
            "data/auth/mcp_token",
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "auth", "mcp_token"),
        ]
        for raw in candidates:
            if not raw:
                continue
            path = os.path.abspath(raw)
            try:
                if os.path.isfile(path):
                    with open(path, encoding="utf-8") as fh:
                        tok = fh.read().strip()
                    if tok:
                        break
            except Exception:
                continue
    if tok:
        h["authorization"] = f"Bearer {tok}"
    return h


def lang_query_params() -> dict[str, str]:
    lang = str(os.getenv("NETX_LANG") or "zh").strip().lower()
    if lang.startswith("en"):
        return {"lang": "en"}
    return {}


def _default_timeout() -> float:
    try:
        return float(os.getenv("NETX_HTTP_TIMEOUT") or 120.0)
    except (TypeError, ValueError):
        return 120.0


def http_json(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    url = f"{api_base_url()}{path}"
    merged: dict[str, Any] = dict(lang_query_params())
    if params:
        merged.update(params)
    to = float(timeout) if timeout is not None else _default_timeout()
    try:
        with httpx.Client(timeout=to, trust_env=False) as client:
            resp = client.request(
                method,
                url,
                params=merged or None,
                json=body,
                headers=api_headers(),
            )
            text = resp.text
            if not resp.is_success:
                return {"ok": False, "error": f"netx_http_{resp.status_code}", "detail": text[:800]}
            data = resp.json() if text else {}
            return {"ok": True, "data": data if isinstance(data, dict) else {"raw": data}}
    except Exception as exc:
        return {"ok": False, "error": "netx_request_failed", "detail": str(exc)[:800]}


def http_json_many(
    requests: list[dict[str, Any]],
    *,
    max_workers: int = 12,
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """Run many HTTP calls in parallel; preserve input order in results.

    Each request: ``{method, path, params?, body?, key?}``.
    """
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
        futs = [pool.submit(_one, i, r) for i, r in enumerate(requests)]
        for fut in as_completed(futs):
            idx, out = fut.result()
            results[idx] = out
    return [r if isinstance(r, dict) else {"ok": False, "error": "parallel_slot_empty"} for r in results]


def mcp_text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    if is_error:
        out["isError"] = True
    return out


def mcp_from_handler_result(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return mcp_text_result(result, is_error=True)
    return mcp_text_result(result)
