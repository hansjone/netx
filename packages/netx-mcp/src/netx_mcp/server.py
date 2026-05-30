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

from netx_mcp.http_tools import HTTP_MCP_TOOLS, call_http_tool


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


def run_stdio_loop() -> None:
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
                        "serverInfo": {"name": "netx-mcp", "version": "0.2.0", "mode": "http"},
                    },
                )
                continue
            if method == "notifications/initialized":
                continue
            if method == "tools/list":
                _ok(rid, {"tools": HTTP_MCP_TOOLS})
                continue
            if method == "tools/call":
                name = str(params.get("name") or "")
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
