"""HTTP transport. Never echo credentials, upstream error bodies, or URLs."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

_SECRET_KEYS = {
    "password", "passwd", "pwd", "secret", "token", "api_token", "api_key",
    "authorization", "access_token", "refresh_token", "private_key", "client_secret",
    "ssh_password", "enable_password", "hop_password", "credentials",
}


def api_token() -> str:
    """Use the shared MCP credential convention; never log paths or file contents."""
    token = os.getenv("NETX_API_TOKEN", "").strip()
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


def redact(value: Any, *, tokens: tuple[str, ...] | None = None) -> Any:
    """Remove credential fields while retaining device raw A/B and CLI evidence."""
    if tokens is None:
        tokens = (api_token(),)
    if isinstance(value, dict):
        return {k: redact(v, tokens=tokens) for k, v in value.items() if str(k).lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [redact(v, tokens=tokens) for v in value]
    if isinstance(value, str):
        for token in tokens:
            if token:
                value = value.replace(token, "[REDACTED]")
        value = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        value = re.sub(
            r"(?im)(\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*)([^\s,;]+)",
            r"\1[REDACTED]", value,
        )
    return value


def http_json(method: str, path: str, *, params: dict[str, Any] | None = None,
              timeout: float = 45.0) -> dict[str, Any]:
    if method != "GET":
        return {"ok": False, "error": "read_only_transport"}
    base = os.getenv("NETX_API_URL", "http://127.0.0.1:8890").strip().rstrip("/")
    try:
        parsed = urlsplit(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            return {"ok": False, "error": "netx_api_url_invalid"}
    except ValueError:
        return {"ok": False, "error": "netx_api_url_invalid"}
    token = api_token()
    if not token:
        return {"ok": False, "error": "netx_token_missing",
                "hint": "设置 NETX_API_TOKEN、NETX_MCP_TOKEN_FILE，或工作目录 data/auth/mcp_token。"}
    query: dict[str, Any] = {}
    if os.getenv("NETX_LANG", "zh").strip().lower().startswith("en"):
        query["lang"] = "en"
    query.update(params or {})
    try:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            response = client.request("GET", base + path, params=query or None, headers={
                "accept": "application/json", "authorization": f"Bearer {token}",
            })
            if not response.is_success:
                return {"ok": False, "error": f"netx_http_{response.status_code}",
                        "hint": "检查 API 地址、令牌 scope 和资源 ID。"}
            data = response.json() if response.content else {}
            # Use the actual request token even if the credential file rotates mid-request.
            return {"ok": True, "data": redact(data if isinstance(data, dict) else {"raw": data}, tokens=(token,))}
    except (ValueError, httpx.HTTPError):
        return {"ok": False, "error": "netx_request_failed",
                "hint": "检查 API 连通性及 JSON 响应。"}


def mcp_from_handler_result(result: dict[str, Any]) -> dict[str, Any]:
    result = redact(result)
    out: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
    if result.get("ok") is False or (isinstance(result.get("data"), dict)
                                     and result["data"].get("ok") is False):
        out["isError"] = True
    return out
