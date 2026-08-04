"""MCP tool schemas and HTTP handlers for netx topology canvas / fabric."""

from __future__ import annotations

from typing import Any, Callable

from netx_topology_mcp.http_client import http_json, mcp_from_handler_result


def _data(out: dict[str, Any]) -> dict[str, Any]:
    """Return API payload dict from http_json envelope (or error as-is)."""
    if not isinstance(out, dict):
        return {"ok": False, "error": "invalid_response"}
    if not out.get("ok"):
        return out
    data = out.get("data")
    if isinstance(data, dict):
        merged = dict(data)
        merged["ok"] = True
        return merged
    return {"ok": True, "data": data}


def _get_topology_tree(_args: dict[str, Any]) -> dict[str, Any]:
    return _data(http_json("GET", "/v1/topology/tree"))


def _list_topology_views(_args: dict[str, Any]) -> dict[str, Any]:
    return _data(http_json("GET", "/v1/topology/views"))


def _get_topology_view(args: dict[str, Any]) -> dict[str, Any]:
    view_id = str(args.get("view_id") or "").strip()
    if not view_id:
        return {"ok": False, "error": "view_id_required"}
    return _data(http_json("GET", f"/v1/topology/views/{view_id}"))


def _create_topology_view(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    folder_id = str(args.get("folder_id") or "").strip()
    if not name:
        return {"ok": False, "error": "name_required"}
    if not folder_id:
        return {"ok": False, "error": "folder_id_required"}
    body: dict[str, Any] = {
        "name": name,
        "folder_id": folder_id,
        "remark": str(args.get("remark") or ""),
        "kind": str(args.get("kind") or "custom").strip() or "custom",
        "role": str(args.get("role") or "core").strip() or "core",
        "sort_order": int(args.get("sort_order") or 0),
    }
    filt = args.get("filter")
    if isinstance(filt, dict):
        body["filter"] = filt
    return _data(http_json("POST", "/v1/topology/views", body=body))


def _create_topology_folder(args: dict[str, Any]) -> dict[str, Any]:
    """Create a region folder under the topology root (API kind=region only)."""
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name_required"}
    body: dict[str, Any] = {
        "name": name,
        "kind": "region",
        "sort_order": int(args.get("sort_order") or 0),
    }
    parent_id = str(args.get("parent_id") or "").strip()
    if parent_id:
        body["parent_id"] = parent_id
    return _data(http_json("POST", "/v1/topology/folders", body=body))


_CHUNK = 500


def _filter_fields(args: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("keyword", "role", "vendor", "link_status"):
        val = str(args.get(key) or "").strip()
        if val:
            out[key] = val
    return out


def _has_filter(args: dict[str, Any]) -> bool:
    return bool(_filter_fields(args))


def _merge_mutation_summaries(parts: list[dict[str, Any]]) -> dict[str, Any]:
    if not parts:
        return {"ok": False, "error": "empty_batch"}
    if len(parts) == 1:
        return parts[0]
    base = dict(parts[-1])
    for key in (
        "matched",
        "added",
        "updated",
        "removed",
        "skipped_existing",
        "skipped_missing",
        "skipped_locked",
    ):
        base[key] = sum(int(p.get(key) or 0) for p in parts)
    base["truncated"] = any(bool(p.get("truncated")) for p in parts)
    # Keep last next_offset / view_node_count / max_nodes from final chunk.
    base["ok"] = all(bool(p.get("ok", True)) for p in parts) and not any(
        str(p.get("error") or "") for p in parts
    )
    return base


def _add_topology_view_nodes(args: dict[str, Any]) -> dict[str, Any]:
    """Place existing fabric nodes on a view — prefer server-side filters over id lists."""
    view_id = str(args.get("view_id") or "").strip()
    if not view_id:
        return {"ok": False, "error": "view_id_required"}
    if args.get("managed_ne_ids") or args.get("ume_ne_ids"):
        return {
            "ok": False,
            "error": "fabric_nodes_only",
            "detail": "Use keyword/role/vendor/link_status or fabric_node_ids; never managed/UME ids.",
        }
    filters = _filter_fields(args)
    fabric_ids = [str(x) for x in (args.get("fabric_node_ids") or []) if str(x).strip()]
    if not filters and not fabric_ids:
        return {
            "ok": False,
            "error": "filter_or_fabric_node_ids_required",
            "detail": "Pass keyword/role/vendor/link_status (preferred) or fabric_node_ids.",
        }

    layout = str(args.get("layout") or "grid").strip() or "grid"
    if filters:
        body: dict[str, Any] = {
            "managed_ne_ids": [],
            "ume_ne_ids": [],
            "fabric_node_ids": [],
            "layout": layout,
            "limit": min(2000, max(1, int(args.get("limit") or 500))),
            "offset": max(0, int(args.get("offset") or 0)),
            "return_graph": False,
            **filters,
        }
        return _data(http_json("POST", f"/v1/topology/views/{view_id}/nodes", body=body))

    # Explicit ids: chunk to avoid huge payloads.
    parts: list[dict[str, Any]] = []
    for i in range(0, len(fabric_ids), _CHUNK):
        chunk = fabric_ids[i : i + _CHUNK]
        body = {
            "managed_ne_ids": [],
            "ume_ne_ids": [],
            "fabric_node_ids": chunk,
            "layout": layout,
            "return_graph": False,
        }
        parts.append(_data(http_json("POST", f"/v1/topology/views/{view_id}/nodes", body=body)))
    return _merge_mutation_summaries(parts)


def _remove_topology_view_nodes(args: dict[str, Any]) -> dict[str, Any]:
    view_id = str(args.get("view_id") or "").strip()
    if not view_id:
        return {"ok": False, "error": "view_id_required"}
    filters = _filter_fields(args)
    ids = [str(x) for x in (args.get("fabric_node_ids") or []) if str(x).strip()]
    if not filters and not ids:
        return {"ok": False, "error": "filter_or_fabric_node_ids_required"}
    if filters:
        body: dict[str, Any] = {"fabric_node_ids": ids, "return_graph": False, **filters}
        return _data(http_json("POST", f"/v1/topology/views/{view_id}/nodes/remove", body=body))
    parts: list[dict[str, Any]] = []
    for i in range(0, len(ids), _CHUNK):
        chunk = ids[i : i + _CHUNK]
        parts.append(
            _data(
                http_json(
                    "POST",
                    f"/v1/topology/views/{view_id}/nodes/remove",
                    body={"fabric_node_ids": chunk, "return_graph": False},
                )
            )
        )
    return _merge_mutation_summaries(parts)


def _update_topology_view_positions(args: dict[str, Any]) -> dict[str, Any]:
    view_id = str(args.get("view_id") or "").strip()
    if not view_id:
        return {"ok": False, "error": "view_id_required"}
    layout = str(args.get("layout") or "").strip().lower()
    filters = _filter_fields(args)
    fabric_ids = [str(x) for x in (args.get("fabric_node_ids") or []) if str(x).strip()]
    positions = args.get("positions")

    if layout in {"grid", "offset", "stack"}:
        body: dict[str, Any] = {
            "layout": layout,
            "origin_x": float(args.get("origin_x") if args.get("origin_x") is not None else 40),
            "origin_y": float(args.get("origin_y") if args.get("origin_y") is not None else 40),
            "gap_x": float(args.get("gap_x") if args.get("gap_x") is not None else 180),
            "gap_y": float(args.get("gap_y") if args.get("gap_y") is not None else 120),
            "cols": int(args.get("cols") or 0),
            "dx": float(args.get("dx") or 0),
            "dy": float(args.get("dy") or 0),
            "fabric_node_ids": fabric_ids,
            "return_graph": False,
            **filters,
        }
        return _data(http_json("PATCH", f"/v1/topology/views/{view_id}/positions", body=body))

    if not isinstance(positions, list) or not positions:
        return {
            "ok": False,
            "error": "layout_or_positions_required",
            "detail": "Pass layout=grid|offset|stack with optional filters, or positions[].",
        }
    cleaned: list[dict[str, Any]] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        fid = str(p.get("fabric_node_id") or "").strip()
        if not fid:
            continue
        cleaned.append(
            {
                "fabric_node_id": fid,
                "x": float(p.get("x") or 0),
                "y": float(p.get("y") or 0),
                "label": str(p.get("label") or ""),
                "locked": bool(p.get("locked") or False),
            }
        )
    if not cleaned:
        return {"ok": False, "error": "positions_required"}
    parts: list[dict[str, Any]] = []
    for i in range(0, len(cleaned), _CHUNK):
        chunk = cleaned[i : i + _CHUNK]
        parts.append(
            _data(
                http_json(
                    "PATCH",
                    f"/v1/topology/views/{view_id}/positions",
                    body={"positions": chunk, "return_graph": False},
                )
            )
        )
    return _merge_mutation_summaries(parts)


def _project_topology_neighbors(args: dict[str, Any]) -> dict[str, Any]:
    view_id = str(args.get("view_id") or "").strip()
    if not view_id:
        return {"ok": False, "error": "view_id_required"}
    return _data(http_json("POST", f"/v1/topology/views/{view_id}/project-neighbors", body={}))


def _get_topology_fabric_summary(_args: dict[str, Any]) -> dict[str, Any]:
    return _data(http_json("GET", "/v1/topology/fabric/summary"))


def _list_topology_fabric_nodes(args: dict[str, Any]) -> dict[str, Any]:
    page = max(1, int(args.get("page") or 1))
    page_size = min(500, max(1, int(args.get("page_size") or 50)))
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if str(args.get("keyword") or "").strip():
        params["keyword"] = str(args.get("keyword")).strip()
    if str(args.get("role") or "").strip():
        params["role"] = str(args.get("role")).strip()
    if str(args.get("link_status") or "").strip():
        params["link_status"] = str(args.get("link_status")).strip()
    return _data(http_json("GET", "/v1/topology/fabric/nodes", params=params))


def _search_topology_fabric_nodes(args: dict[str, Any]) -> dict[str, Any]:
    q = str(args.get("q") or args.get("keyword") or "").strip()
    if not q:
        return {"ok": False, "error": "q_required"}
    params: dict[str, Any] = {
        "q": q,
        "page": max(1, int(args.get("page") or 1)),
        "page_size": min(200, max(1, int(args.get("page_size") or args.get("limit") or 50))),
    }
    return _data(http_json("GET", "/v1/topology/fabric/nodes/search", params=params))


def _query_topology_neighborhood(args: dict[str, Any]) -> dict[str, Any]:
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        return {"ok": False, "error": "node_id_required"}
    params: dict[str, Any] = {
        "node_id": node_id,
        "depth": min(3, max(1, int(args.get("depth") or 1))),
        "layer": str(args.get("layer") or "physical").strip() or "physical",
    }
    return _data(http_json("GET", "/v1/topology/fabric/neighborhood", params=params))


def _query_topology_edges(args: dict[str, Any]) -> dict[str, Any]:
    """List fabric edges; with node_id, also summarize unique peer NEs."""
    page = max(1, int(args.get("page") or 1))
    page_size = min(500, max(1, int(args.get("page_size") or 100)))
    node_id = str(args.get("node_id") or "").strip()
    params: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "layer": str(args.get("layer") or "physical").strip() or "physical",
    }
    if node_id:
        params["node_id"] = node_id
    if str(args.get("status") or "").strip():
        params["status"] = str(args.get("status")).strip()
    if str(args.get("source") or "").strip():
        src = str(args.get("source")).strip().lower()
        if src == "stale":
            src = "lldp"
        params["source"] = src
    if str(args.get("keyword") or "").strip():
        params["keyword"] = str(args.get("keyword")).strip()

    out = http_json("GET", "/v1/topology/fabric/edges", params=params)
    if not isinstance(out, dict) or not out.get("ok"):
        return out if isinstance(out, dict) else {"ok": False, "error": "invalid_response"}
    data = out.get("data") if isinstance(out.get("data"), dict) else {}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    result: dict[str, Any] = {"ok": True, **data}
    if node_id and items:
        peers: set[str] = set()
        peer_labels: list[dict[str, str]] = []
        seen_label: set[str] = set()
        for e in items:
            if not isinstance(e, dict):
                continue
            a_id = str(e.get("a_node_id") or "")
            b_id = str(e.get("b_node_id") or "")
            if a_id == node_id:
                peer_id, pname, pip = b_id, str(e.get("b_name") or ""), str(e.get("b_ip") or "")
            elif b_id == node_id:
                peer_id, pname, pip = a_id, str(e.get("a_name") or ""), str(e.get("a_ip") or "")
            else:
                continue
            if not peer_id or peer_id in peers:
                continue
            peers.add(peer_id)
            if peer_id not in seen_label:
                seen_label.add(peer_id)
                peer_labels.append({"node_id": peer_id, "name": pname, "ip": pip})
        result["peer_count"] = len(peers)
        result["peers"] = peer_labels
        result["edge_total"] = int(data.get("total") or len(items))
        result["peers_complete"] = int(data.get("total") or 0) <= len(items)
    return result


HTTP_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "getTopologyTree",
        "description": "Get topology folder tree (sites/regions) with nested views — start here; create region with createTopologyFolder if missing.",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "listTopologyViews",
        "description": "List topology canvas views (maps).",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "getTopologyView",
        "description": "Get a topology view graph (nodes + edges + positions) by view_id.",
        "inputSchema": {
            "type": "object",
            "properties": {"view_id": {"type": "string"}},
            "required": ["view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "createTopologyFolder",
        "description": (
            "Create a region folder under the topology root. Returns folder id for createTopologyView. "
            "Does not create a canvas — call createTopologyView next. Requires ne:write."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Region display name"},
                "parent_id": {
                    "type": "string",
                    "description": "Optional; must be topology root id (default: root)",
                },
                "sort_order": {"type": "integer", "default": 0},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "createTopologyView",
        "description": "Create a topology canvas under a folder (folder_id from getTopologyTree or createTopologyFolder).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "folder_id": {"type": "string"},
                "remark": {"type": "string"},
                "kind": {"type": "string", "enum": ["physical", "custom"], "default": "custom"},
                "role": {"type": "string", "default": "core"},
                "sort_order": {"type": "integer", "default": 0},
                "filter": {"type": "object"},
            },
            "required": ["name", "folder_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "addTopologyViewNodes",
        "description": (
            "Bulk-place existing fabric nodes on a view. Prefer server filters "
            "(keyword/role/vendor/link_status + limit/offset); API selects ids — do not pull then re-send huge id lists. "
            "Returns a summary (added/truncated/next_offset). Canvas hard cap 2000. Never pass managed/UME ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {"type": "string"},
                "keyword": {"type": "string"},
                "role": {"type": "string"},
                "vendor": {"type": "string"},
                "link_status": {
                    "type": "string",
                    "enum": ["linked", "orphaned", "managed", "ume", "both"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 500},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "fabric_node_ids": {"type": "array", "items": {"type": "string"}},
                "layout": {"type": "string", "enum": ["grid", "keep"], "default": "grid"},
            },
            "required": ["view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "removeTopologyViewNodes",
        "description": (
            "Remove placements from a view (not fabric). Prefer filters (keyword/role/vendor/link_status) "
            "so the API selects matches; or pass fabric_node_ids. Returns a summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {"type": "string"},
                "keyword": {"type": "string"},
                "role": {"type": "string"},
                "vendor": {"type": "string"},
                "link_status": {
                    "type": "string",
                    "enum": ["linked", "orphaned", "managed", "ume", "both"],
                },
                "fabric_node_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "updateTopologyViewPositions",
        "description": (
            "Move nodes on a view. Prefer layout=grid|offset|stack with optional filters "
            "(API selects matches and computes coords). Use positions[] only for small manual tweaks. Returns a summary."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {"type": "string"},
                "layout": {"type": "string", "enum": ["grid", "offset", "stack"]},
                "keyword": {"type": "string"},
                "role": {"type": "string"},
                "vendor": {"type": "string"},
                "link_status": {
                    "type": "string",
                    "enum": ["linked", "orphaned", "managed", "ume", "both"],
                },
                "fabric_node_ids": {"type": "array", "items": {"type": "string"}},
                "origin_x": {"type": "number", "default": 40},
                "origin_y": {"type": "number", "default": 40},
                "gap_x": {"type": "number", "default": 180},
                "gap_y": {"type": "number", "default": 120},
                "cols": {"type": "integer", "minimum": 0, "default": 0},
                "dx": {"type": "number", "default": 0},
                "dy": {"type": "number", "default": 0},
                "positions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fabric_node_id": {"type": "string"},
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "label": {"type": "string"},
                            "locked": {"type": "boolean"},
                        },
                        "required": ["fabric_node_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "projectTopologyNeighbors",
        "description": (
            "Project existing LLDP fabric neighbors of nodes already on the view onto the canvas. "
            "Only places nodes that already exist in fabric."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"view_id": {"type": "string"}},
            "required": ["view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "getTopologyFabricSummary",
        "description": "Fabric inventory summary (node/edge counts).",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "listTopologyFabricNodes",
        "description": "Paged fabric nodes (keyword/role/link_status filters).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "role": {"type": "string"},
                "link_status": {
                    "type": "string",
                    "enum": ["linked", "orphaned", "managed", "ume", "both"],
                },
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "searchTopologyFabricNodes",
        "description": "Quick search fabric nodes by name/IP/id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string"},
                "keyword": {"type": "string", "description": "Alias of q"},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "limit": {"type": "integer", "description": "Alias of page_size"},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "queryTopologyNeighborhood",
        "description": "Neighborhood around a fabric node (depth 1–3).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1},
                "layer": {"type": "string", "default": "physical"},
            },
            "required": ["node_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "queryTopologyEdges",
        "description": (
            "Query fabric LLDP/manual links. Pass node_id for edges of NE A plus peer_count. "
            "Raise page_size if peers_complete is false."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "keyword": {"type": "string"},
                "layer": {"type": "string", "default": "physical"},
                "status": {"type": "string", "enum": ["active", "missing", "stale"]},
                "source": {"type": "string", "enum": ["lldp", "manual"]},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]

_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "getTopologyTree": _get_topology_tree,
    "listTopologyViews": _list_topology_views,
    "getTopologyView": _get_topology_view,
    "createTopologyFolder": _create_topology_folder,
    "createTopologyView": _create_topology_view,
    "addTopologyViewNodes": _add_topology_view_nodes,
    "removeTopologyViewNodes": _remove_topology_view_nodes,
    "updateTopologyViewPositions": _update_topology_view_positions,
    "projectTopologyNeighbors": _project_topology_neighbors,
    "getTopologyFabricSummary": _get_topology_fabric_summary,
    "listTopologyFabricNodes": _list_topology_fabric_nodes,
    "searchTopologyFabricNodes": _search_topology_fabric_nodes,
    "queryTopologyNeighborhood": _query_topology_neighborhood,
    "queryTopologyEdges": _query_topology_edges,
}

TOOL_REQUIRED_SCOPE: dict[str, str] = {
    "getTopologyTree": "ne:read",
    "listTopologyViews": "ne:read",
    "getTopologyView": "ne:read",
    "createTopologyFolder": "ne:write",
    "createTopologyView": "ne:write",
    "addTopologyViewNodes": "ne:write",
    "removeTopologyViewNodes": "ne:write",
    "updateTopologyViewPositions": "ne:write",
    "projectTopologyNeighbors": "ne:write",
    "getTopologyFabricSummary": "ne:read",
    "listTopologyFabricNodes": "ne:read",
    "searchTopologyFabricNodes": "ne:read",
    "queryTopologyNeighborhood": "ne:read",
    "queryTopologyEdges": "ne:read",
}


def tools_for_scopes(scopes: list[str] | set[str] | frozenset[str] | None) -> list[dict[str, Any]]:
    if scopes is None:
        return list(HTTP_MCP_TOOLS)
    granted = {str(s).strip().lower() for s in scopes if str(s).strip()}
    if not granted:
        return []
    out: list[dict[str, Any]] = []
    for tool in HTTP_MCP_TOOLS:
        name = str(tool.get("name") or "")
        need = TOOL_REQUIRED_SCOPE.get(name)
        if need is None or need in granted:
            out.append(tool)
    return out


def call_http_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = _HANDLERS.get(str(name or "").strip())
    if not fn:
        raise ValueError(f"unknown tool: {name}")
    return mcp_from_handler_result(fn(dict(args or {})))
