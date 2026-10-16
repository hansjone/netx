"""Newline-delimited UTF-8 JSON-RPC MCP on stdio; stdout is protocol-only."""

from __future__ import annotations

import json
import sys
from typing import Any

from . import __version__
from .http_client import http_json, mcp_from_handler_result
from .http_tools import TOOL_REQUIRED_SCOPE, call_http_tool, tools_for_scopes


def _fetch_scopes() -> list[str] | None:
    result = http_json("GET", "/v1/auth/me")
    if result.get("ok") is not True or not isinstance(result.get("data"), dict):
        return None
    data = result["data"]
    scopes = data.get("scopes")
    if not isinstance(scopes, list) and isinstance(data.get("user"), dict):
        scopes = data["user"].get("scopes")
    return [str(scope) for scope in scopes] if isinstance(scopes, list) else None


def _result(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle_request(request: Any) -> dict[str, Any] | None:
    if (not isinstance(request, dict) or request.get("jsonrpc") != "2.0"
            or not isinstance(request.get("method"), str)
            or ("id" in request and (isinstance(request["id"], bool)
                or not isinstance(request["id"], (str, int, type(None)))))):
        return _error(None, -32600, "Invalid Request")
    # Includes initialize/ping/unknown-method notifications: never reply or execute tools.
    if "id" not in request:
        return None
    rid, method = request["id"], request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return _error(rid, -32602, "params must be an object")
    if method == "initialize":
        return _result(rid, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                             "serverInfo": {"name": "netx-bizmonitor-mcp", "version": __version__}})
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": tools_for_scopes(_fetch_scopes())})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str) or name not in TOOL_REQUIRED_SCOPE:
            return _error(rid, -32602, "Unknown tool name")
        if not isinstance(args, dict):
            return _error(rid, -32602, "arguments must be an object")
        # Refresh per call so revocation is not hidden by a cached permission list.
        granted = _fetch_scopes()
        required = TOOL_REQUIRED_SCOPE[name]
        if granted is not None and required not in granted:
            return _result(rid, mcp_from_handler_result({"ok": False, "error": "insufficient_scope",
                            "required_scope": required, "hint": "请管理员授予 API 令牌 biz-monitor:read。"}))
        return _result(rid, call_http_tool(name, args))
    return _error(rid, -32601, "Method not found")


def run_stdio_loop() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
        except (ValueError, RecursionError):
            response = _error(None, -32700, "Parse error")
        else:
            try:
                response = handle_request(request)
            except Exception:
                # Never expose traceback, connection URL, headers or exception text.
                response = None if isinstance(request, dict) and "id" not in request else _error(
                    request.get("id") if isinstance(request, dict) else None, -32603, "Internal error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    run_stdio_loop()


if __name__ == "__main__":
    main()
