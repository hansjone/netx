"""Unified MCP tool registry: schemas, scopes, groups, annotations."""

from __future__ import annotations

from typing import Any, Callable

from mcp.types import Tool, ToolAnnotations

from .bizmonitor.http_tools import (
    HTTP_MCP_TOOLS as BIZ_TOOLS,
    TOOL_REQUIRED_SCOPE as BIZ_SCOPES,
    call_http_tool as call_biz_tool,
)
from .ops.http_tools import (
    HTTP_MCP_TOOLS as OPS_TOOLS,
    TOOL_REQUIRED_SCOPE as OPS_SCOPES,
    call_http_tool as call_ops_tool,
)
from .topology.http_tools import (
    HTTP_MCP_TOOLS as TOPO_TOOLS,
    TOOL_REQUIRED_SCOPE as TOPO_SCOPES,
    call_http_tool as call_topo_tool,
)

GROUP_OPS = "ops"
GROUP_TOPOLOGY = "topology"
GROUP_BIZ = "bizMonitor"
VALID_GROUPS = frozenset({GROUP_OPS, GROUP_TOPOLOGY, GROUP_BIZ})

# Write / destructive hints (task contract).
_OPS_WRITE = frozenset({"execManagedNe"})
_TOPO_WRITE = frozenset(
    {
        "createTopologyFolder",
        "addTopologyViewNodes",
        "removeTopologyViewNodes",
        "sinkTopologyDualUnits",
        "copyTopologyViewNodes",
        "updateTopologyViewPositions",
        "projectTopologyNeighbors",
        "classifyTopologyFabricNodes",
        "layoutTopologyView",
    }
)
_TOPO_DESTRUCTIVE = frozenset({"removeTopologyViewNodes"})


def _enrich(tool: dict[str, Any], *, group: str) -> dict[str, Any]:
    name = str(tool.get("name") or "")
    out = dict(tool)
    if group == GROUP_OPS:
        read_only = name not in _OPS_WRITE
        destructive = False
    elif group == GROUP_TOPOLOGY:
        read_only = name not in _TOPO_WRITE
        destructive = name in _TOPO_DESTRUCTIVE
    else:
        read_only = True
        destructive = False
    annotations = dict(out.get("annotations") or {})
    annotations.setdefault("readOnlyHint", read_only)
    annotations.setdefault("destructiveHint", destructive)
    if read_only:
        annotations.setdefault("idempotentHint", True)
    out["annotations"] = annotations
    meta = dict(out.get("_meta") or out.get("meta") or {})
    netx = dict(meta.get("netx") or {})
    netx["group"] = group
    meta["netx"] = netx
    out["_meta"] = meta
    out["_group"] = group
    return out


ALL_TOOLS: list[dict[str, Any]] = (
    [_enrich(t, group=GROUP_OPS) for t in OPS_TOOLS]
    + [_enrich(t, group=GROUP_TOPOLOGY) for t in TOPO_TOOLS]
    + [_enrich(t, group=GROUP_BIZ) for t in BIZ_TOOLS]
)

TOOL_REQUIRED_SCOPE: dict[str, str] = {}
TOOL_REQUIRED_SCOPE.update(OPS_SCOPES)
TOOL_REQUIRED_SCOPE.update(TOPO_SCOPES)
TOOL_REQUIRED_SCOPE.update(BIZ_SCOPES)

TOOL_GROUP: dict[str, str] = {str(t["name"]): str(t["_group"]) for t in ALL_TOOLS}

_HANDLERS: dict[str, Callable[[str, dict[str, Any]], dict[str, Any]]] = {
    **{str(t["name"]): call_ops_tool for t in OPS_TOOLS},
    **{str(t["name"]): call_topo_tool for t in TOPO_TOOLS},
    **{str(t["name"]): call_biz_tool for t in BIZ_TOOLS},
}


def parse_groups_param(raw: str | None) -> frozenset[str]:
    if raw is None or not str(raw).strip():
        return frozenset()
    parts = {p.strip() for p in str(raw).split(",") if p.strip()}
    return frozenset(p for p in parts if p in VALID_GROUPS)


def tools_for_request(
    scopes: frozenset[str] | list[str] | None,
    groups: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Filter by scopes and optional groups. scopes=None advertises all (offline)."""
    wanted_groups = groups or frozenset()
    if scopes is None:
        scoped = list(ALL_TOOLS)
    else:
        granted = {str(s).strip().lower() for s in scopes if str(s).strip()}
        if not granted:
            return []
        scoped = []
        for tool in ALL_TOOLS:
            name = str(tool.get("name") or "")
            need = TOOL_REQUIRED_SCOPE.get(name)
            if need is None or need in granted:
                scoped.append(tool)
    if not wanted_groups:
        return scoped
    return [t for t in scoped if t.get("_group") in wanted_groups]


def to_mcp_tool(tool: dict[str, Any]) -> Tool:
    ann = tool.get("annotations") or {}
    return Tool(
        name=str(tool["name"]),
        description=str(tool.get("description") or ""),
        input_schema=tool.get("inputSchema") or {"type": "object", "properties": {}},
        annotations=ToolAnnotations(
            read_only_hint=bool(ann.get("readOnlyHint", True)),
            destructive_hint=bool(ann.get("destructiveHint", False))
            if not ann.get("readOnlyHint", True)
            else None,
            idempotent_hint=ann.get("idempotentHint"),
        ),
        meta=tool.get("_meta") or tool.get("meta"),
    )


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = _HANDLERS.get(str(name or "").strip())
    if handler is None:
        raise ValueError(f"unknown tool: {name}")
    return handler(name, args)
