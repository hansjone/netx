"""HTTP client for netx REST API (used by stdio MCP server)."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

import httpx

_PROTOCOL_KEY_ZH_TO_EN: dict[str, str] = {
    "其他": "Other",
    "时钟": "Clock",
    "OTN/光": "OTN/Optical",
    "电源": "Power",
}


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
        # Lab default written by netx API bootstrap: data/auth/mcp_token
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


def localize_payload(data: dict[str, Any]) -> dict[str, Any]:
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


def http_json(method: str, path: str, *, params: dict[str, Any] | None = None, timeout: float = 45.0) -> dict[str, Any]:
    url = f"{api_base_url()}{path}"
    merged: dict[str, Any] = dict(lang_query_params())
    if params:
        merged.update(params)
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.request(method, url, params=merged or None, headers=api_headers())
            text = resp.text
            if not resp.is_success:
                return {"ok": False, "error": f"netx_http_{resp.status_code}", "detail": text[:800]}
            data = resp.json() if text else {}
            if isinstance(data, dict):
                data = localize_payload(data)
            return {"ok": True, "data": data if isinstance(data, dict) else {"raw": data}}
    except Exception as exc:
        return {"ok": False, "error": "netx_request_failed", "detail": str(exc)[:800]}


def http_post_json(path: str, body: dict[str, Any], *, timeout: float = 180.0) -> dict[str, Any]:
    url = f"{api_base_url()}{path}"
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(url, json=body, headers=api_headers())
            text = resp.text
            if not resp.is_success:
                return {"ok": False, "error": f"netx_http_{resp.status_code}", "detail": text[:800]}
            data = resp.json() if text else {}
            if isinstance(data, dict):
                data = localize_payload(data)
            return {"ok": True, "data": data if isinstance(data, dict) else {"raw": data}}
    except Exception as exc:
        return {"ok": False, "error": "netx_request_failed", "detail": str(exc)[:800]}


def mcp_text_result(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    if is_error:
        out["isError"] = True
    return out


def mcp_from_handler_result(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        return mcp_text_result(result, is_error=True)
    return mcp_text_result(result)


def quote_ne_id(ne_id: str) -> str:
    return quote(str(ne_id or "").strip(), safe="")
