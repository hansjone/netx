"""Low-level MCP Server exposing ops/topology/bizMonitor tools over Streamable HTTP."""

from __future__ import annotations

import json
from importlib.metadata import version as pkg_version
from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    GetPromptRequestParams,
    GetPromptResult,
    INVALID_PARAMS,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    Prompt,
    PromptMessage,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
)

from .context import get_request_context
from .registry import TOOL_REQUIRED_SCOPE, call_tool, to_mcp_tool, tools_for_request
from .skills import (
    build_instructions,
    list_visible_skill_files,
    list_visible_skills,
    prompt_skill_md_text,
    read_skill_text,
    resolve_uri,
    resource_dict,
)


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


async def _on_list_resources(
    ctx: ServerRequestContext,
    params: PaginatedRequestParams | None,
) -> ListResourcesResult:
    scopes, groups = _scopes_and_groups()
    resources: list[Resource] = []
    for sf in list_visible_skill_files(scopes, groups):
        d = resource_dict(sf, scopes=scopes)
        resources.append(
            Resource(
                uri=d["uri"],
                name=d["name"],
                mimeType=d.get("mimeType"),
                description=d.get("description"),
                meta=d.get("_meta"),
            )
        )
    return ListResourcesResult(resources=resources)


async def _on_read_resource(
    ctx: ServerRequestContext,
    params: ReadResourceRequestParams,
) -> ReadResourceResult:
    scopes, groups = _scopes_and_groups()
    uri = str(params.uri or "")
    sf = resolve_uri(uri, scopes, groups)
    if sf is None:
        raise MCPError(INVALID_PARAMS, "resource not found or not authorized")
    try:
        text = read_skill_text(sf)
    except OSError:
        raise MCPError(INVALID_PARAMS, "resource not found or not authorized") from None
    return ReadResourceResult(
        contents=[
            TextResourceContents(
                uri=sf.uri,
                mimeType="text/markdown",
                text=text,
            )
        ]
    )


async def _on_list_prompts(
    ctx: ServerRequestContext,
    params: PaginatedRequestParams | None,
) -> ListPromptsResult:
    scopes, groups = _scopes_and_groups()
    prompts = [
        Prompt(name=name, description=desc or None)
        for name, _group, desc in list_visible_skills(scopes, groups)
    ]
    return ListPromptsResult(prompts=prompts)


async def _on_get_prompt(
    ctx: ServerRequestContext,
    params: GetPromptRequestParams,
) -> GetPromptResult:
    scopes, groups = _scopes_and_groups()
    name = str(params.name or "").strip()
    text = prompt_skill_md_text(name, scopes, groups)
    if text is None:
        raise MCPError(INVALID_PARAMS, "prompt not found or not authorized")
    skills = {n: d for n, _g, d in list_visible_skills(scopes, groups)}
    return GetPromptResult(
        description=skills.get(name) or None,
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )
        ],
    )


def build_mcp_server() -> Server[Any]:
    server: Server[Any] = Server(
        "netx",
        version=netx_version(),
        instructions=_BASE_INSTRUCTIONS_STATIC,
        on_list_tools=_on_list_tools,
        on_call_tool=_on_call_tool,
        on_list_resources=_on_list_resources,
        on_read_resource=_on_read_resource,
        on_list_prompts=_on_list_prompts,
        on_get_prompt=_on_get_prompt,
    )

    # Server.instructions is fixed at construct time; override init options so
    # each initialize sees a catalog filtered by the current token / ?groups=.
    _orig_create = server.create_initialization_options

    def _create_initialization_options(*args: Any, **kwargs: Any) -> Any:
        opts = _orig_create(*args, **kwargs)
        scopes, groups = _scopes_and_groups()
        return opts.model_copy(update={"instructions": build_instructions(scopes, groups)})

    server.create_initialization_options = _create_initialization_options  # type: ignore[method-assign]
    return server


_BASE_INSTRUCTIONS_STATIC = (
    "netx remote MCP: ops / topology / bizMonitor tools. "
    "Filter with ?groups=ops,topology,bizMonitor. "
    "Authorize with Bearer MCP token or user JWT."
)


# Singleton used by mount; streamable_http_app() must be called once so session_manager exists.
mcp_server = build_mcp_server()
