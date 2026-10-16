"""Low-level MCP Server exposing ops/topology/bizMonitor tools over Streamable HTTP."""

from __future__ import annotations

import json
from importlib.metadata import version as pkg_version
from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
)

from .context import get_request_context
from .registry import TOOL_REQUIRED_SCOPE, call_tool, to_mcp_tool, tools_for_request


def netx_version() -> str:
    try:
        return pkg_version("netx-ops")
    except Exception:
        pass
    try:
        from pathlib import Path

        import tomllib

        root = Path(__file__).resolve().parents[2]
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data.get("project", {}).get("version") or "0.0.0")
    except Exception:
        return "0.0.0"


def _scopes_and_groups() -> tuple[frozenset[str] | None, frozenset[str]]:
    ctx = get_request_context()
    if ctx is None:
        return None, frozenset()
    return ctx.scopes, ctx.groups


async def _on_list_tools(
    ctx: ServerRequestContext,
    params: PaginatedRequestParams | None,
) -> ListToolsResult:
    scopes, groups = _scopes_and_groups()
    tools = [to_mcp_tool(t) for t in tools_for_request(scopes, groups)]
    return ListToolsResult(tools=tools)


async def _on_call_tool(
    ctx: ServerRequestContext,
    params: CallToolRequestParams,
) -> CallToolResult:
    name = str(params.name or "")
    scopes, groups = _scopes_and_groups()
    advertised = {str(t["name"]) for t in tools_for_request(scopes, groups)}
    if name not in advertised:
        need = TOOL_REQUIRED_SCOPE.get(name)
        msg = (
            f"insufficient_scope:{need}. "
            "Ask a netx admin to grant this scope on the API token; "
            "for sql:query prefer aggregateNmsAlarms/queryNmsAlarmsRaw instead."
            if need
            else f"unknown or filtered tool: {name}"
        )
        return CallToolResult(
            content=[TextContent(type="text", text=msg)],
            is_error=True,
        )

    args = params.arguments if isinstance(params.arguments, dict) else {}
    try:
        # Handlers are sync and may nest ASGI calls; run off the event loop thread
        # so BaseHTTPMiddleware on the outer MCP request cannot deadlock.
        import asyncio

        import anyio

        from .asgi_client import bind_main_loop

        bind_main_loop(asyncio.get_running_loop())
        result = await anyio.to_thread.run_sync(call_tool, name, dict(args))
    except Exception as exc:
        result = {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
        }

    content_raw = result.get("content") if isinstance(result, dict) else None
    texts: list[TextContent] = []
    if isinstance(content_raw, list):
        for item in content_raw:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(TextContent(type="text", text=str(item.get("text") or "")))
    if not texts:
        texts = [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]
    is_error = bool(isinstance(result, dict) and result.get("isError"))
    return CallToolResult(content=texts, is_error=is_error)


def build_mcp_server() -> Server[Any]:
    return Server(
        "netx",
        version=netx_version(),
        instructions=(
            "netx remote MCP: ops / topology / bizMonitor tools. "
            "Filter with ?groups=ops,topology,bizMonitor. "
            "Authorize with Bearer MCP token or user JWT."
        ),
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
    )


# Singleton used by mount; streamable_http_app() must be called once so session_manager exists.
mcp_server = build_mcp_server()
