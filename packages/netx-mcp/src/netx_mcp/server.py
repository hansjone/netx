"""netx stdio MCP server (HTTP client to netx REST API).

Environment:
- ``NETX_API_URL``: netx REST base URL (default ``http://127.0.0.1:8890``)
- ``NETX_API_TOKEN``: optional Bearer token
- ``NETX_LANG``: ``zh`` or ``en`` for localized API responses
"""

from __future__ import annotations

import json
import sys
from typing import Any

from netx_mcp.http_client import http_json
from netx_mcp.http_tools import TOOL_REQUIRED_SCOPE, call_http_tool, tools_for_scopes


def _ensure_utf8_stdio() -> None:
    """MCP stdio must be UTF-8; Windows defaults to GBK and breaks the host reader."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _ok(rid: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _err(rid: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, ensure_ascii=False)
        + "\n"
    )
    sys.stdout.flush()


_UNSET = object()


def _fetch_scopes() -> list[str] | None:
    """Return granted scopes from /v1/auth/me, or None if the call fails (show all tools)."""
    try:
        envelope = http_json("GET", "/v1/auth/me")
        if not isinstance(envelope, dict) or not envelope.get("ok"):
            return None
        data = envelope.get("data")
        if not isinstance(data, dict):
            return None
        scopes = data.get("scopes")
        if isinstance(scopes, list):
            return [str(s) for s in scopes]
        user = data.get("user")
        if isinstance(user, dict) and isinstance(user.get("scopes"), list):
            return [str(s) for s in user["scopes"]]
    except Exception:
        return None
    return None


def run_stdio_loop() -> None:
    cached_scopes: list[str] | None | object = _UNSET

    def scopes() -> list[str] | None:
        nonlocal cached_scopes
        if cached_scopes is _UNSET:
            cached_scopes = _fetch_scopes()
        return cached_scopes  # type: ignore[return-value]

    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception:
            continue
        rid = req.get("id")
        method = str(req.get("method") or "")
        params = req.get("params") if isinstance(req.get("params"), dict) else {}

        try:
            if method == "initialize":
                _ok(
                    rid,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "netx-mcp", "version": "0.2.1", "mode": "http"},
                    },
                )
                continue
            if method == "notifications/initialized":
                continue
            if method == "tools/list":
                _ok(rid, {"tools": tools_for_scopes(scopes())})
                continue
            if method == "tools/call":
                name = str(params.get("name") or "")
                need = TOOL_REQUIRED_SCOPE.get(name)
                granted = scopes()
                if need and granted is not None and need not in {str(s).lower() for s in granted}:
                    _err(
                        rid,
                        -32001,
                        (
                            f"insufficient_scope:{need}. "
                            "Ask a netx admin to grant this scope on the API token; "
                            "for sql:query prefer aggregateUmeAlarms/queryUmeAlarmsRaw/ume_alarm_xlsx_report instead."
                        ),
                    )
                    continue
                args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                _ok(rid, call_http_tool(name, args))
                continue
            _err(rid, -32601, f"method not found: {method}")
        except Exception as exc:
            _err(rid, -32000, str(exc))


def main() -> None:
    _ensure_utf8_stdio()
    run_stdio_loop()


if __name__ == "__main__":
    main()
