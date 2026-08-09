"""MCP tool schemas and HTTP handlers for netx topology canvas / fabric."""

from __future__ import annotations

import sys
from typing import Any, Callable

from netx_topology_mcp.http_client import http_json, http_json_many, mcp_from_handler_result
from netx_topology_mcp.layout_jobs import (
    cancel_job,
    job_public,
    raise_if_cancelled,
    report_progress,
    start_job,
)
from netx_topology_mcp.layout_sight import build_sight
from netx_topology_mcp.layout_stats import analyze_layout_stats
from netx_topology_mcp.layout_structure import analyze_graph_structure
from netx_topology_mcp.layout_tool import list_layout_catalog, run_layout_on_graph


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


_FABRIC_KEEP = (
    "id",
    "name",
    "ip",
    "vendor",
    "device_type",
    "role",
    "link_status",
    "region_folder_id",
    "managed_ne_id",
    "ume_ne_id",
)


def _compact_fabric_item(item: dict[str, Any]) -> dict[str, Any]:
    """Drop bulky attrs / coordinate noise for agent context."""
    out = {k: item.get(k) for k in _FABRIC_KEEP if k in item}
    views = item.get("views")
    if isinstance(views, list):
        slim: list[dict[str, Any]] = []
        for v in views[:12]:
            if not isinstance(v, dict):
                continue
            slim.append(
                {
                    "view_id": v.get("view_id") or v.get("id") or "",
                    "view_name": v.get("view_name") or v.get("name") or "",
                    "folder_name": v.get("folder_name") or "",
                    "kind": v.get("kind") or "",
                }
            )
        if slim:
            out["views"] = slim
            if len(views) > 12:
                out["views_truncated"] = True
    return out


def _compact_fabric_page(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok"):
        return payload
    items = payload.get("items")
    if not isinstance(items, list):
        return payload
    out = dict(payload)
    out["items"] = [_compact_fabric_item(x) if isinstance(x, dict) else x for x in items]
    return out


def _compact_folder_node(node: dict[str, Any], *, depth: int, max_depth: int | None) -> dict[str, Any]:
    views_in = [v for v in (node.get("views") or []) if isinstance(v, dict)]
    views = [
        {
            "id": v.get("id") or "",
            "name": v.get("name") or "",
            "kind": v.get("kind") or "",
            "node_count": v.get("node_count"),
        }
        for v in views_in
    ]
    out: dict[str, Any] = {
        "id": node.get("id") or "",
        "parent_id": node.get("parent_id") or "",
        "kind": node.get("kind") or "",
        "name": node.get("name") or "",
        "ne_count": int(node.get("ne_count") or 0),
        "is_system": bool(node.get("is_system")),
        "views": views,
    }
    children_in = [c for c in (node.get("children") or []) if isinstance(c, dict)]
    if max_depth is not None and depth >= max_depth:
        if children_in:
            out["children_truncated"] = len(children_in)
            out["children"] = []
        else:
            out["children"] = []
        return out
    out["children"] = [
        _compact_folder_node(c, depth=depth + 1, max_depth=max_depth) for c in children_in
    ]
    return out


def _edge_endpoints(edge: dict[str, Any]) -> tuple[str, str]:
    a = str(edge.get("a_node_id") or edge.get("a") or "").strip()
    b = str(edge.get("b_node_id") or edge.get("b") or "").strip()
    return a, b


def _collapse_edges_to_links(
    edges: list[dict[str, Any]],
    *,
    include_names: bool = False,
) -> list[dict[str, Any]]:
    """Collapse parallel port-level edges to one undirected NE↔NE link + link_count.

    Canvas drawing only needs existence (and optionally multiplicity), not ports/labels.
    """
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for e in edges:
        if not isinstance(e, dict):
            continue
        a, b = _edge_endpoints(e)
        if not a or not b or a == b:
            continue
        key = (a, b) if a < b else (b, a)
        row = buckets.get(key)
        if row is None:
            lo, hi = key
            row = {"a_node_id": lo, "b_node_id": hi, "link_count": 0}
            if include_names:
                # Prefer names matching the canonical endpoint order.
                if a == lo:
                    row["a_name"] = str(e.get("a_name") or "")
                    row["b_name"] = str(e.get("b_name") or "")
                else:
                    row["a_name"] = str(e.get("b_name") or "")
                    row["b_name"] = str(e.get("a_name") or "")
            buckets[key] = row
        row["link_count"] = int(row["link_count"]) + 1
    return sorted(buckets.values(), key=lambda r: (r["a_node_id"], r["b_node_id"]))


def _summarize_view_graph(graph: dict[str, Any], *, sample: int = 20) -> dict[str, Any]:
    """Shrink getTopologyView / projectNeighbors payloads for agents."""
    view = graph.get("view") if isinstance(graph.get("view"), dict) else {}
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    peers = graph.get("outside_peers") if isinstance(graph.get("outside_peers"), list) else []
    sample_n = max(0, min(200, int(sample)))
    links = _collapse_edges_to_links(edges)
    return {
        "ok": True,
        "detail": "summary",
        "view": {
            "id": view.get("id") or graph.get("view_id") or "",
            "name": view.get("name") or "",
            "folder_id": view.get("folder_id") or "",
            "kind": view.get("kind") or "",
            "node_count": int(view.get("node_count") or len(nodes)),
        },
        "node_count": len(nodes),
        "edge_count": len(edges),
        "link_count": len(links),
        "links": links,
        "truncated": bool(graph.get("truncated")),
        "truncate_reason": str(graph.get("truncate_reason") or ""),
        "outside_peer_count": len(peers),
        "sample_nodes": [
            {
                "fabric_node_id": n.get("fabric_node_id") or n.get("id") or "",
                "name": n.get("name") or n.get("label") or "",
                "ip": n.get("ip") or "",
                "x": n.get("x"),
                "y": n.get("y"),
            }
            for n in nodes[:sample_n]
        ],
        "hint": (
            "links[] = undirected NE pairs (one canvas edge each); edge_count is raw port-level. "
            "Use sample>=node_count for full id/name coverage when laying out. "
            "Place from links[] + names. detail=full only for every membership field."
        ),
    }


def _get_topology_tree(args: dict[str, Any]) -> dict[str, Any]:
    out = _data(http_json("GET", "/v1/topology/tree"))
    if not out.get("ok"):
        return out
    compact = str(args.get("compact") if args.get("compact") is not None else "true").strip().lower()
    if compact in {"0", "false", "no", "full"}:
        return out
    max_depth_raw = args.get("max_depth")
    max_depth: int | None = None
    if max_depth_raw is not None and str(max_depth_raw).strip() != "":
        try:
            max_depth = max(0, int(max_depth_raw))
        except (TypeError, ValueError):
            max_depth = None
    root = out.get("root") if isinstance(out.get("root"), dict) else None
    if root is None:
        return out
    return {
        "ok": True,
        "compact": True,
        "max_depth": max_depth,
        "root": _compact_folder_node(root, depth=0, max_depth=max_depth),
    }


def _get_topology_view(args: dict[str, Any]) -> dict[str, Any]:
    view_id = str(args.get("view_id") or "").strip()
    if not view_id:
        return {"ok": False, "error": "view_id_required"}
    out = _data(http_json("GET", f"/v1/topology/views/{view_id}"))
    if not out.get("ok"):
        return out
    detail = str(args.get("detail") or "summary").strip().lower()
    if detail in {"full", "raw", "graph"}:
        out["detail"] = "full"
        return out
    sample = int(args.get("sample") or 20)
    return _summarize_view_graph(out, sample=sample)


def _collect_physical_views(
    node: dict[str, Any] | None,
    *,
    out: list[dict[str, Any]],
    max_views: int,
) -> None:
    if not isinstance(node, dict) or len(out) >= max_views:
        return
    for v in node.get("views") or []:
        if not isinstance(v, dict):
            continue
        if str(v.get("kind") or "").strip().lower() not in {"", "physical"}:
            continue
        vid = str(v.get("id") or "").strip()
        if not vid:
            continue
        out.append(
            {
                "view_id": vid,
                "view_name": str(v.get("name") or ""),
                "folder_id": str(node.get("id") or ""),
                "folder_name": str(node.get("name") or ""),
                "ne_count": int(node.get("ne_count") or v.get("node_count") or 0),
            }
        )
        if len(out) >= max_views:
            return
    for child in node.get("children") or []:
        if isinstance(child, dict):
            _collect_physical_views(child, out=out, max_views=max_views)


def _analyze_one_view(
    view_id: str,
    *,
    with_meta: bool = False,
    detail: str = "summary",
    sight_limit: int = 40,
    sight_cell: float = 600.0,
) -> dict[str, Any]:
    graph = _data(http_json("GET", f"/v1/topology/views/{view_id}"))
    if not graph.get("ok"):
        return {"ok": False, "view_id": view_id, "error": graph.get("error") or "view_fetch_failed"}
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    view = graph.get("view") if isinstance(graph.get("view"), dict) else {}
    stats = analyze_layout_stats(nodes, edges, with_meta=with_meta)
    report = dict(stats.get("report") or {})
    out: dict[str, Any] = {
        "ok": True,
        "view_id": view_id,
        "view_name": str(view.get("name") or ""),
        "folder_id": str(view.get("folder_id") or ""),
        **report,
        # Compact one-liner for agents / folder samples
        "summary": stats.get("summary") or {},
    }
    # detail=structure → graph gravity / layer attachment (phase 0.5, no coords needed)
    # detail=hotspots|blocks|both → agent "sight" for hand-drag (phase 2)
    d = (detail or "summary").strip().lower()
    if d in {"structure", "gravity", "plan", "both", "all"}:
        try:
            hub_top_k = max(4, min(40, int(sight_limit or 12)))
        except (TypeError, ValueError):
            hub_top_k = 12
        out["structure"] = analyze_graph_structure(
            nodes, edges, hub_top_k=hub_top_k, stub_top_k=max(12, hub_top_k * 2)
        )
    if d in {"hotspots", "blocks", "both", "all", "sight"}:
        mode = "both" if d in {"both", "all", "sight"} else d
        out["sight"] = build_sight(
            nodes, edges, mode=mode, limit=max(5, min(80, int(sight_limit))), cell=float(sight_cell)
        )
        # Prefer exact crossing count from score metrics when present.
        metrics = (stats.get("metrics") or {}) if isinstance(stats, dict) else {}
        if isinstance(out.get("sight"), dict) and isinstance(out["sight"].get("hotspots"), dict):
            if metrics.get("edge_crossings") is not None:
                out["sight"]["hotspots"]["edge_crossings"] = metrics.get("edge_crossings")
            elif isinstance(report.get("crossing"), dict):
                out["sight"]["hotspots"]["edge_crossings"] = report["crossing"].get(
                    "edge_crossings"
                )
    return out


def _patch_positions_chunked(view_id: str, positions: list[dict[str, Any]]) -> dict[str, Any]:
    updated = 0
    chunks = 0
    # Larger chunks cut round-trips on 1k+ node canvases (Cursor MCP ~60s budget).
    step = 250
    total = len(positions)
    for i in range(0, total, step):
        raise_if_cancelled()
        chunk = positions[i : i + step]
        report_progress(
            "apply",
            pct=90.0 + 9.0 * (i / max(total, 1)),
            message=f"PATCH {i + len(chunk)}/{total}",
            step=i + len(chunk),
            total_steps=total,
        )
        out = _data(
            http_json(
                "PATCH",
                f"/v1/topology/views/{view_id}/positions",
                body={"positions": chunk, "return_graph": False},
                timeout=180.0,
            )
        )
        if not out.get("ok"):
            return {
                "ok": False,
                "error": out.get("error") or "positions_patch_failed",
                "updated": updated,
                "detail": out,
            }
        updated += int(out.get("updated") or len(chunk))
        chunks += 1
    return {"ok": True, "updated": updated, "chunks": chunks}


def _slim_layout_payload(out: dict[str, Any]) -> dict[str, Any]:
    """Trim giant layout responses so MCP JSON stays under host timeouts."""
    n = int(out.get("node_count") or 0)
    action = str(out.get("action") or "")
    heavy = n >= 400 or action in {
        "polish_crossings",
        "orbit_sweep",
        "clear_edge_hits",
        "layout_dual_unit",
    }
    if not heavy:
        return out
    slim = dict(out)
    slim.pop("guide", None)
    slim.pop("tried", None)
    slim.pop("params_used", None)
    # Keep summary + compact crossing tops; drop bulky sub-reports.
    for k in ("spacing", "sparsity", "edges", "chains", "rings", "size"):
        if k in slim and isinstance(slim[k], dict):
            slim[k] = {
                kk: slim[k].get(kk)
                for kk in ("status", "score", "edge_crossings", "space_utilization", "nn_p50")
                if kk in slim[k]
            } or slim[k]
    crossing = slim.get("crossing")
    if isinstance(crossing, dict):
        slim["crossing"] = {
            "status": crossing.get("status"),
            "score": crossing.get("score"),
            "edge_crossings": crossing.get("edge_crossings"),
            "crossings_per_link": crossing.get("crossings_per_link"),
            "top_nodes": (crossing.get("top_nodes") or [])[:5],
            "top_edges": (crossing.get("top_edges") or [])[:5],
        }
    local = slim.get("local")
    if isinstance(local, dict):
        meta = local.get("meta")
        if isinstance(meta, dict):
            # Drop bulky slot_meta / origins from compose echo.
            keep_meta = {
                kk: meta.get(kk)
                for kk in (
                    "slots",
                    "nodes",
                    "pad",
                    "merge_shared",
                    "order_mode",
                    "pack_mode",
                    "tip",
                )
                if kk in meta
            }
            # Keep rigid_groups keys only (ids stay in compose_session).
            rg = meta.get("rigid_groups")
            if isinstance(rg, list):
                keep_meta["rigid_groups_n"] = len(rg)
            local = {**local, "meta": keep_meta}
            slim["local"] = local
    slim["slim"] = True
    return slim


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def _fetch_views_parallel(view_ids: list[str]) -> dict[str, dict[str, Any]]:
    """GET many views in parallel; return map view_id → payload (_data shape)."""
    uniq: list[str] = []
    seen: set[str] = set()
    for vid in view_ids:
        s = str(vid or "").strip()
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    if not uniq:
        return {}
    reqs = [
        {"method": "GET", "path": f"/v1/topology/views/{sid}", "key": sid}
        for sid in uniq
    ]
    print(f"[netx-topology] parallel GET views n={len(reqs)}", file=sys.stderr, flush=True)
    raw = http_json_many(reqs, max_workers=min(16, len(reqs)), timeout=120.0)
    out: dict[str, dict[str, Any]] = {}
    for sid, envelope in zip(uniq, raw):
        out[sid] = _data(envelope)
    return out


def _layout_topology_view(args: dict[str, Any]) -> dict[str, Any]:
    """Generic layout / local polish: preview or apply onto a canvas."""
    if str(args.get("catalog") or "").strip().lower() in {"1", "true", "yes"} or (
        args.get("catalog") is True
    ):
        return {"ok": True, **list_layout_catalog()}

    action = str(args.get("action") or "layout").strip().lower() or "layout"
    overrides0 = args.get("params") if isinstance(args.get("params"), dict) else {}

    # Public surface only (legacy modules may remain in-tree but are not exposed).
    _public = {
        "layout",
        "fix_overlaps",
        "resolve_overlaps",
        "untangle",
        "straighten_channels",
        "layout_dual_unit",
        "polish_crossings",
        "clear_edge_hits",
        "orbit_sweep",
        "move_nodes",
        "sink_nodes",
        "job_status",
        "job_cancel",
    }
    if action not in _public:
        return {
            "ok": False,
            "error": f"unknown_action:{action}",
            "hint": (
                "Public actions: layout|layout_dual_unit|move_nodes|orbit_sweep|"
                "polish_crossings|clear_edge_hits|fix_overlaps|untangle|"
                "straighten_channels|job_status|job_cancel. "
                "Main path: sinkTopologyDualUnits → orbit_sweep → polish → clear."
            ),
            **list_layout_catalog(),
        }
    if action == "resolve_overlaps":
        action = "fix_overlaps"
        args = {**args, "action": "fix_overlaps"}

    # Bidirectional membership move: view_id=TO, source_view_id=FROM.
    if action in {"move_nodes", "sink_nodes"}:
        return _move_topology_view_nodes(args)

    # Background job poll / cancel (no view_id required).
    if action in {"job_status", "job_cancel"}:
        job_id = str(
            overrides0.get("job_id") or args.get("job_id") or ""
        ).strip()
        if not job_id:
            return {
                "ok": False,
                "error": "job_id_required",
                "hint": "params.job_id from polish/orbit/layout_dual_unit background start.",
            }
        if action == "job_cancel":
            cancelled = cancel_job(job_id)
            # Return public snapshot after arming cancel.
            snap = job_public(job_id) or {}
            return {
                **cancelled,
                "action": "job_cancel",
                "progress": snap.get("progress"),
                "elapsed_ms": snap.get("elapsed_ms"),
                "heartbeat_age_ms": snap.get("heartbeat_age_ms"),
            }
        job = job_public(job_id)
        if not job:
            return {
                "ok": False,
                "error": "job_not_found",
                "job_id": job_id,
                "hint": (
                    "Unknown job_id. Jobs are in-process on this MCP worker; "
                    "restart or another instance cannot see them."
                ),
            }
        status = str(job.get("status") or "unknown")
        out: dict[str, Any] = {
            "ok": True,
            "action": "job_status",
            "job_id": job_id,
            "status": status,
            "view_id": job.get("view_id"),
            "job_action": job.get("action"),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "elapsed_ms": job.get("elapsed_ms"),
            "heartbeat_at": job.get("heartbeat_at"),
            "heartbeat_age_ms": job.get("heartbeat_age_ms"),
            "stale": bool(job.get("stale")),
            "cancel_requested": bool(job.get("cancel_requested")),
            "progress": job.get("progress") or {},
            "meta": job.get("meta"),
        }
        if status in {"running", "cancelling"}:
            prog = out["progress"] if isinstance(out["progress"], dict) else {}
            phase = prog.get("phase") or status
            pct = prog.get("pct")
            pct_s = f" {pct:.0f}%" if isinstance(pct, (int, float)) else ""
            if bool(out.get("stale")):
                out["hint"] = (
                    f"WARNING stale=true (no heartbeat ≥90s). "
                    f"Last phase={phase}{pct_s}; elapsed_ms={out['elapsed_ms']}. "
                    "job_cancel to arm cooperative stop; restart MCP if it never ends."
                )
            else:
                out["hint"] = (
                    f"Running phase={phase}{pct_s}; elapsed_ms={out['elapsed_ms']}; "
                    f"heartbeat_age_ms={out['heartbeat_age_ms']}. "
                    "Re-poll job_status; job_cancel to request cooperative stop."
                )
            return out
        result = job.get("result")
        if isinstance(result, dict):
            out["result"] = _slim_layout_payload(result)
            out["ok"] = bool(result.get("ok")) and status == "done"
            if not out["ok"]:
                out["error"] = result.get("error") or job.get("error")
        elif status == "error":
            out["ok"] = False
            out["error"] = job.get("error") or "job_failed"
        elif status == "cancelled":
            out["ok"] = False
            out["error"] = "cancelled"
        return out

    view_id = str(args.get("view_id") or "").strip()
    if not view_id:
        return {
            "ok": False,
            "error": "view_id_required",
            "hint": "Pass view_id, or catalog=true for recipe/preset list.",
            **list_layout_catalog(),
        }
    # Local polish always reads current target coords (ignore foreign source)
    source_id = str(args.get("source_view_id") or "").strip() or view_id
    if action in {
        "fix_overlaps",
        "untangle",
        "straighten_channels",
        "layout_dual_unit",
        "polish_crossings",
        "clear_edge_hits",
        "orbit_sweep",
    }:
        source_id = view_id
    recipe = str(args.get("recipe") or "rings").strip().lower() or "rings"
    preset = str(args.get("preset") or "balanced").strip().lower() or "balanced"
    mode = str(args.get("mode") or "preview").strip().lower() or "preview"
    if mode not in {"preview", "apply"}:
        return {"ok": False, "error": "mode_invalid", "hint": "mode=preview|apply"}
    tune = str(args.get("tune") or "").strip().lower() in {"1", "true", "yes"} or (
        args.get("tune") is True
    )
    overrides = args.get("params") if isinstance(args.get("params"), dict) else None
    if overrides is not None:
        overrides = dict(overrides)

    # compose_views / compose_orbit: load staging canvases, inject blocks.
    if action in {"compose_views", "compose_orbit"}:
        from netx_topology_mcp.layout_ops.compose_views import compose_params_from_overrides

        knobs = compose_params_from_overrides(overrides)
        src_ids = list(knobs.get("source_view_ids") or [])
        if not src_ids:
            return {
                "ok": False,
                "error": "source_view_ids_required",
                "hint": "params.source_view_ids=[...] staging view ids to compose.",
            }
        # Giant compose often exceeds Cursor MCP ~60s tool timeout — run async
        # unless caller forces sync (background job / params.sync=true).
        force_sync = _truthy((overrides or {}).get("sync")) or _truthy(
            (overrides or {}).get("_force_sync")
        )
        want_bg = _truthy((overrides or {}).get("background")) or (
            mode == "apply" and len(src_ids) >= 12 and not force_sync
        )
        if want_bg and mode == "apply":
            sync_args = dict(args)
            sync_params = dict(overrides or {})
            sync_params["_force_sync"] = True
            sync_params.pop("background", None)
            sync_args["params"] = sync_params
            sync_args["mode"] = "apply"
            sync_args["action"] = action
            job_id = start_job(
                action=action,
                view_id=view_id,
                tool_args=sync_args,
                meta={"source_n": len(src_ids)},
            )
            print(
                f"[netx-topology] {action} background job_id={job_id} "
                f"sources={len(src_ids)}",
                file=sys.stderr,
                flush=True,
            )
            return {
                "ok": True,
                "action": action,
                "view_id": view_id,
                "mode": "apply",
                "applied": False,
                "status": "running",
                "job_id": job_id,
                "source_count": len(src_ids),
                "hint": (
                    f"{action} durable worker job_id={job_id}. "
                    "Poll job_status (progress/heartbeat); survives MCP restart."
                ),
            }

        report_progress(
            "fetch_sources",
            pct=5.0,
            message=f"loading {len(src_ids)} staging views",
            step=0,
            total_steps=len(src_ids),
        )
        raise_if_cancelled()
        fetched = _fetch_views_parallel(src_ids)
        report_progress(
            "fetch_sources",
            pct=20.0,
            message=f"loaded {len(fetched)} staging views",
            step=len(src_ids),
            total_steps=len(src_ids),
        )
        compose_blocks: list[dict[str, Any]] = []
        for sid in src_ids:
            sg = fetched.get(sid) or {}
            if not sg.get("ok"):
                return {
                    "ok": False,
                    "error": "compose_source_fetch_failed",
                    "source_view_id": sid,
                    "detail": sg.get("error"),
                }
            pos_map: dict[str, list[float]] = {}
            for n in sg.get("nodes") or []:
                if not isinstance(n, dict):
                    continue
                nid = str(n.get("fabric_node_id") or "").strip()
                if not nid:
                    continue
                try:
                    pos_map[nid] = [float(n.get("x") or 0), float(n.get("y") or 0)]
                except (TypeError, ValueError):
                    continue
            if pos_map:
                compose_blocks.append({"key": sid, "positions": pos_map})
        if len(compose_blocks) < 1:
            return {"ok": False, "error": "compose_sources_empty"}
        overrides = overrides or {}
        overrides["source_view_ids"] = src_ids
        overrides["_compose_blocks"] = compose_blocks
        for k in (
            "pad",
            "merge_shared",
            "fabric_bridges",
            "bridge_boost",
            "ideal_scale",
            "spring_iters",
        ):
            if k in knobs:
                overrides[k] = knobs[k]
        # Staging may have emptied the target — restore members before scoring.
        if mode == "apply":
            all_ids: list[str] = []
            seen_ids: set[str] = set()
            for blk in compose_blocks:
                for nid in (blk.get("positions") or {}):
                    sid = str(nid)
                    if sid and sid not in seen_ids:
                        seen_ids.add(sid)
                        all_ids.append(sid)
            dst0 = _data(http_json("GET", f"/v1/topology/views/{view_id}", timeout=120.0))
            have = {
                str(n.get("fabric_node_id") or "")
                for n in (dst0.get("nodes") or [])
                if isinstance(n, dict)
            }
            missing0 = [i for i in all_ids if i not in have]
            if missing0:
                add_back0 = _add_topology_view_nodes(
                    {
                        "view_id": view_id,
                        "fabric_node_ids": missing0,
                        "layout": "keep",
                    }
                )
                overrides["_restored_early"] = int(
                    add_back0.get("added") or len(missing0)
                )

    # Staging membership → portal freeze for polish / untangle.
    # orbit_sweep intentionally omits auto portal freeze (default protect_rigid=off).
    if action in {
        "untangle",
        "polish_crossings",
    }:
        from netx_topology_mcp.layout_ops.compose_views import compose_params_from_overrides

        knobs = compose_params_from_overrides(overrides)
        src_ids = list(knobs.get("source_view_ids") or [])
        overrides = overrides or {}
        # Prefer session echo from compose (skip 77× GET).
        sess = overrides.get("compose_session")
        if isinstance(sess, dict):
            if isinstance(sess.get("portal_ids"), list) and "portal_ids" not in overrides:
                overrides["portal_ids"] = [str(x) for x in sess["portal_ids"] if str(x)]
            if isinstance(sess.get("mass_groups"), list) and "mass_groups" not in overrides:
                overrides["mass_groups"] = sess["mass_groups"]
            if isinstance(sess.get("rigid_groups"), list) and "_rigid_membership" not in overrides:
                membership = []
                for g in sess["rigid_groups"]:
                    if not isinstance(g, dict):
                        continue
                    key = str(g.get("key") or "").strip()
                    ids = [str(x) for x in (g.get("node_ids") or []) if str(x)]
                    pivots = [str(x) for x in (g.get("pivots") or []) if str(x)]
                    if key and len(ids) >= 2:
                        membership.append(
                            {"key": key, "node_ids": ids, "pivots": pivots}
                        )
                if membership:
                    overrides["_rigid_membership"] = membership
                    src_ids = []  # skip re-fetch
        if src_ids and "_rigid_membership" not in overrides:
            # polish with only portal_ids: skip membership fetch
            if (
                action == "polish_crossings"
                and isinstance(overrides.get("portal_ids"), list)
                and overrides.get("portal_ids")
            ):
                overrides["source_view_ids"] = list(
                    knobs.get("source_view_ids") or src_ids
                )
            else:
                fetched = _fetch_views_parallel(src_ids)
                membership = []
                counts: dict[str, int] = {}
                for sid in src_ids:
                    sg = fetched.get(sid) or {}
                    if not sg.get("ok"):
                        return {
                            "ok": False,
                            "error": "rigid_source_fetch_failed",
                            "source_view_id": sid,
                            "detail": sg.get("error"),
                        }
                    ids = [
                        str(n.get("fabric_node_id") or "")
                        for n in (sg.get("nodes") or [])
                        if isinstance(n, dict)
                        and n.get("fabric_node_id")
                        and not str(n.get("fabric_node_id")).startswith("region:")
                    ]
                    for nid in ids:
                        counts[nid] = counts.get(nid, 0) + 1
                    if ids:
                        membership.append({"key": sid, "node_ids": ids, "pivots": []})
                shared = {nid for nid, c in counts.items() if c > 1}
                for row in membership:
                    row["pivots"] = sorted(n for n in row["node_ids"] if n in shared)
                overrides["source_view_ids"] = src_ids
                overrides["_rigid_membership"] = membership
                if shared and "portal_ids" not in overrides:
                    overrides["portal_ids"] = sorted(shared)

    report_progress("load_canvas", pct=25.0, message=f"GET view {source_id[:12]}…")
    raise_if_cancelled()
    graph = _data(http_json("GET", f"/v1/topology/views/{source_id}", timeout=120.0))
    if not graph.get("ok"):
        return {
            "ok": False,
            "error": graph.get("error") or "source_view_fetch_failed",
            "source_view_id": source_id,
        }
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    if len(nodes) < 2:
        return {"ok": False, "error": "too_few_nodes", "node_count": len(nodes)}
    report_progress(
        "load_canvas",
        pct=30.0,
        message=f"canvas n={len(nodes)} e={len(edges)}",
        nodes=len(nodes),
        edges=len(edges),
    )

    # Giant-canvas polish/untangle often exceeds host ~60s — background apply.
    force_sync = _truthy((overrides or {}).get("sync")) or _truthy(
        (overrides or {}).get("_force_sync")
    )
    heavy_bg = action in {
        "polish_crossings",
        "orbit_sweep",
        "clear_edge_hits",
        "layout_dual_unit",
    }
    if (
        mode == "apply"
        and heavy_bg
        and not force_sync
        and (len(nodes) >= 600 or _truthy((overrides or {}).get("background")))
    ):
        sync_args = dict(args)
        sync_params = dict(overrides or {})
        sync_params["_force_sync"] = True
        # Keep already-resolved membership so the worker skips re-fetch.
        if overrides and overrides.get("_rigid_membership"):
            sync_params["_rigid_membership"] = overrides["_rigid_membership"]
        if overrides and overrides.get("portal_ids"):
            sync_params["portal_ids"] = overrides["portal_ids"]
        sync_params.pop("background", None)
        sync_args["params"] = sync_params
        sync_args["mode"] = "apply"
        sync_args["action"] = action
        sync_args["view_id"] = view_id
        job_id = start_job(
            action=action,
            view_id=view_id,
            tool_args=sync_args,
            meta={"node_count": len(nodes)},
        )
        print(
            f"[netx-topology] {action} background job_id={job_id} n={len(nodes)}",
            file=sys.stderr,
            flush=True,
        )
        return {
            "ok": True,
            "action": action,
            "view_id": view_id,
            "mode": "apply",
            "applied": False,
            "status": "running",
            "job_id": job_id,
            "node_count": len(nodes),
            "hint": (
                f"{action} durable worker job_id={job_id}. "
                "Poll job_status (progress/heartbeat); survives MCP restart."
            ),
        }

    # orbit_sweep: preview suggests only; apply defaults pick=1 (unless round).
    if action == "orbit_sweep" and mode == "apply":
        overrides = dict(overrides or {})
        if (
            not overrides.get("round")
            and overrides.get("pick") is None
        ):
            if overrides.get("node_id") or overrides.get("fabric_node_id"):
                overrides["pick"] = 1

    try:
        report_progress(
            "compute",
            pct=40.0,
            message=f"action={action}",
            action=action,
        )
        raise_if_cancelled()
        result = run_layout_on_graph(
            nodes,
            edges,
            action=action,
            recipe=recipe,
            preset=preset,
            params=overrides,
            tune=tune,
        )
        report_progress("compute", pct=75.0, message=f"{action} finished")
    except ValueError as e:
        return {"ok": False, "error": str(e), **list_layout_catalog()}

    positions = result.pop("positions", [])
    ov = int((result.get("overlap") or {}).get("footprint_pairs") or 0)
    lbl = int((result.get("overlap") or {}).get("label_pairs") or 0)
    report_progress(
        "score",
        pct=80.0,
        message=f"ov={ov} lbl={lbl} positions={len(positions)}",
        overlaps=ov,
        label_overlaps=lbl,
        positions=len(positions),
    )
    out = {
        **result,
        "view_id": view_id,
        "source_view_id": source_id,
        "mode": mode,
        "applied": False,
    }
    if mode == "preview":
        out["hint"] = (
            "Preview only. Re-call with mode=apply to PATCH. "
            "If overlaps remain: action=fix_overlaps. "
            "Main path: orbit_sweep → polish_crossings → clear_edge_hits."
        )
        return _slim_layout_payload(out)

    # Dual-unit: gate on unit crossings only. Staging eyes often have label
    # footprint touches that fix_overlaps would re-cross; allow apply when
    # accepted (unit-internal crossings=0).
    if action == "layout_dual_unit":
        loc = result.get("local") or {}
        if not loc.get("accepted", False):
            return {
                **out,
                "ok": False,
                "error": "dual_unit_crossings",
                "hint": (
                    "layout_dual_unit requires unit-internal crossings=0. "
                    "Check membership (portals+corridors+tails) or re-detect dual_units."
                ),
                "local": loc,
            }
    elif ov > 0 or lbl > 0:
        return _slim_layout_payload(
            {
                **out,
                "ok": False,
                "error": "overlaps_remain",
                "hint": (
                    "Refusing apply while footprints/labels overlap. "
                    "Call layoutTopologyView with action=fix_overlaps, mode=apply."
                ),
            }
        )

    # Local polish must not silently worsen the canvas.
    if action in {
        "untangle",
        "straighten_channels",
    }:
        before_x = int(
            (analyze_layout_stats(nodes, edges).get("summary") or {}).get("crossings") or 0
        )
        after_x = int((result.get("crossing") or {}).get("edge_crossings") or 0)
        slack = 0 if action == "straighten_channels" else max(40, int(before_x * 0.15))
        if after_x > before_x + slack:
            return {
                **out,
                "ok": False,
                "error": "crossing_regression",
                "before_crossings": before_x,
                "after_crossings": after_x,
                "slack": slack,
                "hint": (
                    "Refusing apply: local polish raised crossings. "
                    "Keep current coords; preview first, or try orbit_sweep → "
                    "polish_crossings → clear_edge_hits, then surgical "
                    "updateTopologyViewPositions."
                ),
            }

    # compose_*: staging may have emptied the target — restore members first.
    restored = 0
    if action in {"compose_views", "compose_orbit"}:
        dst = _data(http_json("GET", f"/v1/topology/views/{view_id}"))
        if not dst.get("ok"):
            return {"ok": False, "error": dst.get("error") or "target_view_fetch_failed", "view_id": view_id}
        dst_ids = {
            str(n.get("fabric_node_id") or "")
            for n in (dst.get("nodes") or [])
            if isinstance(n, dict)
        }
        missing = [
            str(p.get("fabric_node_id") or "")
            for p in positions
            if str(p.get("fabric_node_id") or "") and str(p.get("fabric_node_id")) not in dst_ids
        ]
        if missing:
            add_back = _add_topology_view_nodes(
                {
                    "view_id": view_id,
                    "fabric_node_ids": missing,
                    "layout": "keep",
                }
            )
            if not add_back.get("ok") and add_back.get("error"):
                # Some APIs return ok via summary fields only — continue if added.
                if int(add_back.get("added") or 0) <= 0 and not add_back.get("ok"):
                    return {
                        **out,
                        "ok": False,
                        "error": "compose_restore_members_failed",
                        "missing_count": len(missing),
                        "detail": add_back,
                    }
            restored = int(add_back.get("added") or len(missing))
            out["restored_members"] = restored

    if source_id != view_id:
        dst = _data(http_json("GET", f"/v1/topology/views/{view_id}"))
        if not dst.get("ok"):
            return {"ok": False, "error": dst.get("error") or "target_view_fetch_failed", "view_id": view_id}
        dst_ids = {
            str(n.get("fabric_node_id") or "")
            for n in (dst.get("nodes") or [])
            if isinstance(n, dict)
        }
        missing = [p["fabric_node_id"] for p in positions if str(p.get("fabric_node_id")) not in dst_ids]
        if missing:
            return {
                "ok": False,
                "error": "target_missing_nodes",
                "missing_count": len(missing),
                "hint": "addTopologyViewNodes onto view_id first (or set source_view_id=view_id).",
                "sample_missing": missing[:10],
            }

    raise_if_cancelled()
    report_progress(
        "apply",
        pct=90.0,
        message=f"PATCH {len(positions)} positions",
        positions=len(positions),
    )
    patch = _patch_positions_chunked(view_id, positions)
    if not patch.get("ok"):
        return _slim_layout_payload(
            {**out, "ok": False, "error": patch.get("error"), "patch": patch}
        )
    out["applied"] = True
    out["updated"] = patch.get("updated")
    report_progress("done", pct=100.0, message="positions applied")
    out["hint"] = (
        "Positions applied. Main path: analyze(structure) → "
        "sinkTopologyDualUnits (or move_nodes park) → orbit_sweep(round) → "
        "polish_crossings → clear_edge_hits → updateTopologyViewPositions. "
        "Small graphs: layout(compact|corridor|rings). "
        "Large jobs: poll job_status; job_cancel for cooperative stop."
    )
    return _slim_layout_payload(out)


def _analyze_topology_view_layout(args: dict[str, Any]) -> dict[str, Any]:
    """Unified layout QA: overlap / crossing / spacing / sparsity / edges + score."""
    view_id = str(args.get("view_id") or "").strip()
    folder_id = str(args.get("folder_id") or "").strip()
    with_meta = str(args.get("with_meta") or "").strip().lower() in {"1", "true", "yes"}
    detail = str(args.get("detail") or "summary").strip().lower() or "summary"
    try:
        sight_limit = max(5, min(80, int(args.get("sight_limit") or 40)))
    except (TypeError, ValueError):
        sight_limit = 40
    try:
        sight_cell = float(args.get("sight_cell") or 600.0)
    except (TypeError, ValueError):
        sight_cell = 600.0
    try:
        max_views = max(1, min(80, int(args.get("max_views") or 25)))
    except (TypeError, ValueError):
        max_views = 25
    try:
        min_nodes = max(0, int(args.get("min_nodes") or 5))
    except (TypeError, ValueError):
        min_nodes = 5
    try:
        max_nodes = max(1, min(2000, int(args.get("max_nodes") or 800)))
    except (TypeError, ValueError):
        max_nodes = 800

    if view_id:
        return _analyze_one_view(
            view_id,
            with_meta=with_meta,
            detail=detail,
            sight_limit=sight_limit,
            sight_cell=sight_cell,
        )

    if not folder_id:
        return {
            "ok": False,
            "error": "view_id_or_folder_id_required",
            "hint": "Pass view_id for one canvas, or folder_id to sample physical views under it.",
        }

    tree = _data(http_json("GET", "/v1/topology/tree"))
    if not tree.get("ok"):
        return tree
    folder = _find_folder_in_tree(tree, folder_id)
    if folder is None:
        return {"ok": False, "error": "folder_not_found", "folder_id": folder_id}

    candidates: list[dict[str, Any]] = []
    _collect_physical_views(folder, out=candidates, max_views=max_views * 4)
    # Prefer mid-size engineer canvases; skip empties / huge worlds
    filtered = [
        c
        for c in candidates
        if min_nodes <= int(c.get("ne_count") or 0) <= max_nodes
    ]
    filtered.sort(key=lambda c: int(c.get("ne_count") or 0))
    # stride sample across size range
    if len(filtered) > max_views:
        step = len(filtered) / max_views
        picked = [filtered[min(len(filtered) - 1, int(i * step))] for i in range(max_views)]
    else:
        picked = filtered

    rows: list[dict[str, Any]] = []
    for c in picked:
        one = _analyze_one_view(str(c["view_id"]), with_meta=with_meta)
        if not one.get("ok"):
            rows.append(
                {
                    "view_id": c.get("view_id"),
                    "folder_name": c.get("folder_name"),
                    "error": one.get("error"),
                }
            )
            continue
        size = one.get("size") or {}
        crossing = one.get("crossing") or {}
        spacing = one.get("spacing") or {}
        sparsity = one.get("sparsity") or {}
        overlap = one.get("overlap") or {}
        verdict = one.get("verdict") or {}
        rows.append(
            {
                "view_id": one.get("view_id"),
                "view_name": one.get("view_name"),
                "folder_name": c.get("folder_name"),
                "verdict": {
                    "overall": verdict.get("overall"),
                    "total": verdict.get("total"),
                    "headline": verdict.get("headline"),
                },
                "size": size,
                "overlap": {
                    "status": overlap.get("status"),
                    "footprint_pairs": overlap.get("footprint_pairs"),
                    "label_pairs": overlap.get("label_pairs"),
                },
                "crossing": {
                    "status": crossing.get("status"),
                    "edge_crossings": crossing.get("edge_crossings"),
                    "crossings_per_link": crossing.get("crossings_per_link"),
                    "top_nodes": [
                        {
                            "name": r.get("name"),
                            "hits": r.get("crossing_hits"),
                            "id": r.get("fabric_node_id"),
                        }
                        for r in (crossing.get("top_nodes") or [])[:5]
                    ],
                    "top_edges": [
                        {
                            "label": r.get("label"),
                            "hits": r.get("crossing_hits"),
                            "a": r.get("a_name"),
                            "b": r.get("b_name"),
                        }
                        for r in (crossing.get("top_edges") or [])[:5]
                    ],
                },
                "spacing": {
                    "status": spacing.get("status"),
                    "nn_p50": spacing.get("nn_p50"),
                },
                "sparsity": {
                    "status": sparsity.get("status"),
                    "space_utilization": sparsity.get("space_utilization"),
                    "grid_occupancy": sparsity.get("grid_occupancy"),
                    "whitespace_index": sparsity.get("whitespace_index"),
                },
            }
        )

    ok_rows = [r for r in rows if (r.get("crossing") or {}).get("edge_crossings") is not None]

    def _pct(vals: list[float], p: float) -> float | None:
        if not vals:
            return None
        s = sorted(vals)
        return round(s[min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))], 4)

    cpl = [
        float(r["crossing"]["crossings_per_link"])
        for r in ok_rows
        if (r.get("crossing") or {}).get("crossings_per_link") is not None
    ]
    cross = [
        float(r["crossing"]["edge_crossings"])
        for r in ok_rows
        if (r.get("crossing") or {}).get("edge_crossings") is not None
    ]
    nn = [
        float(r["spacing"]["nn_p50"])
        for r in ok_rows
        if (r.get("spacing") or {}).get("nn_p50") is not None
    ]
    util = [
        float(r["sparsity"]["space_utilization"])
        for r in ok_rows
        if (r.get("sparsity") or {}).get("space_utilization") is not None
    ]
    totals = [
        float(r["verdict"]["total"])
        for r in ok_rows
        if (r.get("verdict") or {}).get("total") is not None
    ]
    by_bucket: dict[str, list[dict[str, Any]]] = {"1-50": [], "51-200": [], "201-500": [], "501+": []}
    for r in ok_rows:
        n = int((r.get("size") or {}).get("nodes") or 0)
        if n <= 50:
            by_bucket["1-50"].append(r)
        elif n <= 200:
            by_bucket["51-200"].append(r)
        elif n <= 500:
            by_bucket["201-500"].append(r)
        else:
            by_bucket["501+"].append(r)
    bucket_summary = {}
    for key, items in by_bucket.items():
        if not items:
            bucket_summary[key] = {"views": 0}
            continue
        bucket_summary[key] = {
            "views": len(items),
            "score_total_p50": _pct(
                [float(i["verdict"]["total"]) for i in items if (i.get("verdict") or {}).get("total") is not None],
                0.5,
            ),
            "crossings_per_link_p50": _pct(
                [float(i["crossing"]["crossings_per_link"]) for i in items], 0.5
            ),
            "crossings_per_link_p90": _pct(
                [float(i["crossing"]["crossings_per_link"]) for i in items], 0.9
            ),
            "nn_p50_median": _pct(
                [float(i["spacing"]["nn_p50"]) for i in items if (i.get("spacing") or {}).get("nn_p50") is not None],
                0.5,
            ),
            "util_p50": _pct(
                [
                    float(i["sparsity"]["space_utilization"])
                    for i in items
                    if (i.get("sparsity") or {}).get("space_utilization") is not None
                ],
                0.5,
            ),
            "edge_crossings_p50": _pct(
                [float(i["crossing"]["edge_crossings"]) for i in items], 0.5
            ),
        }

    return {
        "ok": True,
        "mode": "folder_sample",
        "folder_id": folder_id,
        "folder_name": str(folder.get("name") or ""),
        "sampled_views": len(ok_rows),
        "candidate_views": len(candidates),
        "distribution": {
            "score_total_p50": _pct(totals, 0.5),
            "score_total_p90": _pct(totals, 0.9),
            "crossings_per_link_p50": _pct(cpl, 0.5),
            "crossings_per_link_p90": _pct(cpl, 0.9),
            "edge_crossings_p50": _pct(cross, 0.5),
            "edge_crossings_p90": _pct(cross, 0.9),
            "nn_p50_median": _pct(nn, 0.5),
            "space_utilization_p50": _pct(util, 0.5),
            "by_node_bucket": bucket_summary,
        },
        "guide": {
            "how_to_read": (
                "同一工具：每张图看 verdict + overlap/crossing/spacing/sparsity/chains/rings。"
                "对照 distribution：同规模 bucket 的 cpl ≤ 参考 p50 为优、勿差于 p90；"
                "overlap 硬零；util 勿远低于参考 p50。"
            ),
            "acceptance_hint": (
                "Agent layouts: ≤ reference p50 crossings_per_link for size bucket, never worse than p90; "
                "footprint/label overlap = 0; raise util without stacking."
            ),
        },
        "views": rows,
    }


_ROOT_MAP_NAMES = frozenset({"根图", "Root map"})


def _walk_folders(node: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    out = [node]
    for child in node.get("children") or []:
        if isinstance(child, dict):
            out.extend(_walk_folders(child))
    return out


def _find_folder_in_tree(tree: dict[str, Any], folder_id: str) -> dict[str, Any] | None:
    fid = str(folder_id or "").strip()
    if not fid:
        return None
    root = tree.get("root") if isinstance(tree.get("root"), dict) else tree
    if not isinstance(root, dict):
        return None
    for folder in _walk_folders(root):
        if str(folder.get("id") or "") == fid:
            return folder
    return None


def _pick_view(folder: dict[str, Any]) -> dict[str, Any] | None:
    views = [v for v in (folder.get("views") or []) if isinstance(v, dict)]
    if not views:
        return None
    for v in views:
        if str(v.get("kind") or "").strip().lower() == "physical":
            return v
    return views[0]


def _is_root_map_folder(folder: dict[str, Any]) -> bool:
    name = str(folder.get("name") or "").strip()
    if name in _ROOT_MAP_NAMES:
        return True
    return bool(folder.get("is_system")) and not str(folder.get("external_ref") or "").strip()


def resolve_draw_target(tree: dict[str, Any], folder_id: str) -> dict[str, Any]:
    """Resolve canvas folder + view_id for drawing under the new 根/根图 model."""
    folder = _find_folder_in_tree(tree, folder_id)
    if folder is None:
        return {}
    view = _pick_view(folder)
    if view is not None:
        return {
            "canvas_folder_id": str(folder.get("id") or ""),
            "view_id": str(view.get("id") or ""),
            "ne_count": int(folder.get("ne_count") or 0),
        }
    # Nav-only「根」: views empty — draw on auto「根图」/ Root map child.
    for child in folder.get("children") or []:
        if not isinstance(child, dict) or not _is_root_map_folder(child):
            continue
        child_view = _pick_view(child)
        if child_view is None:
            continue
        return {
            "canvas_folder_id": str(child.get("id") or ""),
            "view_id": str(child_view.get("id") or ""),
            "ne_count": int(child.get("ne_count") or 0),
        }
    return {}


def _create_topology_folder(args: dict[str, Any]) -> dict[str, Any]:
    """Create a region; API auto-spawns 根图 / region canvas — return draw view_id."""
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
    locale = str(args.get("locale") or "").strip()
    if locale:
        body["locale"] = locale
    created = _data(http_json("POST", "/v1/topology/folders", body=body))
    if not created.get("ok"):
        return created
    folder_id = str(created.get("id") or "").strip()
    if not folder_id:
        return created
    tree = _data(http_json("GET", "/v1/topology/tree"))
    if not tree.get("ok"):
        created["hint"] = (
            "Folder created but tree refresh failed; call getTopologyTree and use the "
            "根图 / region canvas view_id."
        )
        return created
    tip = resolve_draw_target(tree, folder_id)
    if tip.get("view_id"):
        created["canvas_folder_id"] = tip["canvas_folder_id"]
        created["view_id"] = tip["view_id"]
        created["ne_count"] = tip.get("ne_count", 0)
        created["hint"] = (
            "Use view_id with addTopologyViewNodes on this physical canvas. "
            "Under 根图 use createTopologyFolder for sub-regions."
        )
    else:
        created["hint"] = (
            "Folder created; call getTopologyTree to locate the canvas view_id before drawing."
        )
    return created


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


def _ensure_view_max_nodes(view_id: str, max_nodes: int) -> dict[str, Any]:
    """Raise membership.max_nodes on a view (≤2000) without wiping other filter fields."""
    cap = max(1, min(2000, int(max_nodes)))
    got = _data(http_json("GET", f"/v1/topology/views/{view_id}"))
    view = got.get("view") if isinstance(got.get("view"), dict) else None
    if not isinstance(view, dict):
        return {"ok": False, "error": "view_get_failed", "detail": got}
    filt = dict(view.get("filter") or {})
    mem = dict(filt.get("membership") or {})
    try:
        cur = int(mem.get("max_nodes") or 0)
    except (TypeError, ValueError):
        cur = 0
    if cur >= cap:
        return {"ok": True, "max_nodes": cur, "changed": False}
    mem["max_nodes"] = cap
    filt["membership"] = mem
    patched = _data(
        http_json("PATCH", f"/v1/topology/views/{view_id}", body={"filter": filt})
    )
    if isinstance(patched, dict) and patched.get("ok") is False:
        return patched
    return {"ok": True, "max_nodes": cap, "changed": True, "previous": cur}


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
    if args.get("max_nodes") is not None:
        bump = _ensure_view_max_nodes(view_id, int(args.get("max_nodes") or 2000))
        if isinstance(bump, dict) and bump.get("ok") is False:
            return bump
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
    body: dict[str, Any] = {}
    seeds = args.get("seed_fabric_node_ids") or args.get("fabric_node_ids") or []
    if isinstance(seeds, list) and seeds:
        body["seed_fabric_node_ids"] = [str(x).strip() for x in seeds if str(x).strip()]
    mids = args.get("managed_ne_ids") or []
    if isinstance(mids, list) and mids:
        body["managed_ne_ids"] = [str(x).strip() for x in mids if str(x).strip()]
    region_folder_id = str(args.get("region_folder_id") or "").strip()
    if region_folder_id:
        body["region_folder_id"] = region_folder_id
    # API still returns the full graph; large canvases easily exceed the 60s default.
    out = _data(
        http_json(
            "POST",
            f"/v1/topology/views/{view_id}/project-neighbors",
            body=body,
            timeout=180.0,
        )
    )
    if not out.get("ok"):
        return out
    detail = str(args.get("detail") or "summary").strip().lower()
    skipped = int(out.get("out_of_region_skipped") or 0)
    skipped_sample = out.get("out_of_region_sample") or []
    if not isinstance(skipped_sample, list):
        skipped_sample = []
    if detail in {"full", "raw", "graph"}:
        out["detail"] = "full"
        out["region_folder_id"] = region_folder_id or None
        out["out_of_region_skipped"] = skipped
        out["out_of_region_sample"] = skipped_sample[:20]
        if region_folder_id and skipped:
            out["hint"] = (
                f"Projected with region_folder_id={region_folder_id}; "
                f"skipped {skipped} out-of-region neighbors (not added)."
            )
        return out
    summary = _summarize_view_graph(out, sample=int(args.get("sample") or 20))
    summary["view_id"] = view_id
    summary["projected"] = True
    summary["region_folder_id"] = region_folder_id or None
    summary["out_of_region_skipped"] = skipped
    summary["out_of_region_sample"] = [
        x if isinstance(x, dict) else {"fabric_node_id": str(x)}
        for x in skipped_sample[:20]
    ]
    if region_folder_id:
        summary["hint"] = (
            (summary.get("hint") or "")
            + f" region_folder_id={region_folder_id} filters peers; "
            f"out_of_region_skipped={skipped}."
        ).strip()
    return summary


def _query_topology_fabric_nodes(args: dict[str, Any]) -> dict[str, Any]:
    """Unified fabric inventory: mode=summary|list|search (replaces 3 tools)."""
    mode = str(args.get("mode") or "").strip().lower()
    q = str(args.get("q") or "").strip()
    keyword = str(args.get("keyword") or "").strip()
    if not mode:
        if _truthy(args.get("summary")):
            mode = "summary"
        elif q:
            mode = "search"
        else:
            mode = "list"
    if mode in {"summary", "stats", "count"}:
        out = _data(http_json("GET", "/v1/topology/fabric/summary"))
        if isinstance(out, dict) and out.get("ok") is not False:
            out = dict(out)
            out["mode"] = "summary"
        return out
    if mode in {"search", "find"}:
        needle = q or keyword
        if not needle:
            return {
                "ok": False,
                "error": "q_required",
                "hint": "mode=search needs q (or keyword).",
            }
        params: dict[str, Any] = {
            "q": needle,
            "page": max(1, int(args.get("page") or 1)),
            "page_size": min(
                200, max(1, int(args.get("page_size") or args.get("limit") or 50))
            ),
        }
        page = _compact_fabric_page(
            _data(http_json("GET", "/v1/topology/fabric/nodes/search", params=params))
        )
        if isinstance(page, dict):
            page = dict(page)
            page["mode"] = "search"
        return page
    # mode=list (default): paged filter browse
    page_n = max(1, int(args.get("page") or 1))
    page_size = min(500, max(1, int(args.get("page_size") or args.get("limit") or 50)))
    params = {"page": page_n, "page_size": page_size}
    # list path historically used keyword=; accept q as alias
    filt = keyword or q
    if filt:
        params["keyword"] = filt
    if str(args.get("role") or "").strip():
        params["role"] = str(args.get("role")).strip()
    if str(args.get("link_status") or "").strip():
        params["link_status"] = str(args.get("link_status")).strip()
    if str(args.get("region_folder_id") or "").strip():
        params["region_folder_id"] = str(args.get("region_folder_id")).strip()
    page = _compact_fabric_page(
        _data(http_json("GET", "/v1/topology/fabric/nodes", params=params))
    )
    if isinstance(page, dict):
        page = dict(page)
        page["mode"] = "list"
    return page


def _query_topology_neighborhood(args: dict[str, Any]) -> dict[str, Any]:
    node_id = str(args.get("node_id") or "").strip()
    if not node_id:
        return {"ok": False, "error": "node_id_required"}
    params: dict[str, Any] = {
        "node_id": node_id,
        "depth": min(3, max(1, int(args.get("depth") or 1))),
        "layer": str(args.get("layer") or "physical").strip() or "physical",
    }
    out = _data(http_json("GET", "/v1/topology/fabric/neighborhood", params=params))
    if not out.get("ok"):
        return out
    nodes_in = [n for n in (out.get("nodes") or []) if isinstance(n, dict)]
    edges_in = [e for e in (out.get("edges") or []) if isinstance(e, dict)]
    links = _collapse_edges_to_links(edges_in)
    return {
        "ok": True,
        "center_node_id": out.get("center_node_id") or node_id,
        "depth": out.get("depth") or params["depth"],
        "node_count": len(nodes_in),
        "edge_count": len(edges_in),
        "link_count": len(links),
        "nodes": [_compact_fabric_item(n) for n in nodes_in],
        "links": links,
        "hint": "links[] = undirected NE pairs for drawing; ignore ports. edge_count is raw.",
    }


def _query_topology_edges(args: dict[str, Any]) -> dict[str, Any]:
    """Adjacency for agents: NE↔NE links (+ link_count). Ports only when detail=ports."""
    page = max(1, int(args.get("page") or 1))
    page_size = min(500, max(1, int(args.get("page_size") or 100)))
    node_id = str(args.get("node_id") or "").strip()
    detail = str(args.get("detail") or "adjacency").strip().lower()
    want_ports = detail in {"ports", "raw", "full"}
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
    items = [e for e in (data.get("items") or []) if isinstance(e, dict)]
    edge_total = int(data.get("total") or len(items))
    links = _collapse_edges_to_links(items, include_names=True)

    peers: list[dict[str, Any]] = []
    if node_id:
        peer_map: dict[str, dict[str, Any]] = {}
        for e in items:
            a_id = str(e.get("a_node_id") or "")
            b_id = str(e.get("b_node_id") or "")
            if a_id == node_id:
                peer_id, pname, pip = b_id, str(e.get("b_name") or ""), str(e.get("b_ip") or "")
            elif b_id == node_id:
                peer_id, pname, pip = a_id, str(e.get("a_name") or ""), str(e.get("a_ip") or "")
            else:
                continue
            if not peer_id:
                continue
            row = peer_map.get(peer_id)
            if row is None:
                peer_map[peer_id] = {"node_id": peer_id, "name": pname, "ip": pip, "link_count": 1}
            else:
                row["link_count"] = int(row["link_count"]) + 1
        peers = sorted(peer_map.values(), key=lambda p: str(p.get("name") or p.get("node_id") or ""))

    result: dict[str, Any] = {
        "ok": True,
        "detail": "ports" if want_ports else "adjacency",
        "total": edge_total,
        "page": data.get("page") or page,
        "page_size": data.get("page_size") or page_size,
        "link_count": len(links),
        "links": links,
        "hint": (
            "Default adjacency: one undirected NE↔NE link (+ link_count). "
            "Canvas draws one edge per link. Pass detail=ports only for port-level rows."
        ),
    }
    if node_id:
        result["peer_count"] = len(peers)
        result["peers"] = peers
        result["edge_total"] = edge_total
        result["peers_complete"] = edge_total <= len(items)
    if want_ports:
        slim_items: list[dict[str, Any]] = []
        for e in items:
            row = {
                k: e.get(k)
                for k in (
                    "id",
                    "layer",
                    "a_node_id",
                    "b_node_id",
                    "a_port",
                    "b_port",
                    "a_name",
                    "b_name",
                    "source",
                    "status",
                )
                if k in e
            }
            slim_items.append(row)
        result["items"] = slim_items
    return result


def _view_node_id_pos(payload: dict[str, Any]) -> tuple[list[str], dict[str, tuple[float, float]]]:
    ids: list[str] = []
    pos: dict[str, tuple[float, float]] = {}
    for n in payload.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        fid = str(n.get("fabric_node_id") or "").strip()
        if not fid or fid.startswith("region:"):
            continue
        ids.append(fid)
        if n.get("x") is not None and n.get("y") is not None:
            try:
                pos[fid] = (float(n["x"]), float(n["y"]))
            except (TypeError, ValueError):
                pass
    return ids, pos


def _fabric_bridges_into(
    probe_ids: list[str],
    peer_ids: set[str],
    *,
    probe_cap: int = 16,
) -> list[tuple[str, str]]:
    """Fabric NE↔NE pairs from probes into peer_ids (cross-canvas bridges).

    View GET edges miss these when one endpoint already left the source canvas.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    peers = {p for p in peer_ids if p}
    for nid in probe_ids[: max(1, int(probe_cap))]:
        sid = str(nid or "").strip()
        if not sid:
            continue
        nb = _query_topology_neighborhood({"node_id": sid, "depth": 1})
        if not nb.get("ok"):
            continue
        for link in nb.get("links") or []:
            if not isinstance(link, dict):
                continue
            a = str(link.get("a_node_id") or "").strip()
            b = str(link.get("b_node_id") or "").strip()
            if not a or not b or a == b:
                continue
            other = b if a == sid else a if b == sid else ""
            if not other or other not in peers:
                continue
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def _sink_topology_dual_units(args: dict[str, Any]) -> dict[str, Any]:
    """Move a dual_unit batch from source (root) canvas onto sink (sub-region).

    One call = one batch by default. Set until_empty=true to loop until the
    source has no transferable nodes (or max_batches). Default layout_batch
    runs layout_dual_unit per unit before parking (old staging flow). Global
    polish still via layoutTopologyView on the sink.
    """
    from netx_topology_mcp.layout_ops.dual_units import find_dual_portal_units
    from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
    from netx_topology_mcp.layout_ops.sink_dual_units import (
        batch_node_ids,
        layout_and_pack_batch,
        leftover_batch_ids,
        merge_view_links,
        park_positions,
        positions_to_patch,
        select_dual_unit_batch,
        units_as_batch_rows,
    )

    source_view_id = str(
        args.get("source_view_id") or args.get("root_view_id") or ""
    ).strip()
    sink_view_id = str(
        args.get("sink_view_id") or args.get("target_view_id") or ""
    ).strip()
    if not source_view_id or not sink_view_id:
        return {
            "ok": False,
            "error": "source_view_id_and_sink_view_id_required",
            "hint": "Pass root physical view_id + child-region physical view_id.",
        }
    if source_view_id == sink_view_id:
        return {"ok": False, "error": "source_and_sink_must_differ"}

    max_units = max(1, min(20, int(args.get("max_units") or 3)))
    min_nodes = max(2, min(200, int(args.get("min_nodes") or 8)))
    max_nodes = max(min_nodes, min(400, int(args.get("max_nodes") or 80)))
    max_batch_nodes = max(max_nodes, min(800, int(args.get("max_batch_nodes") or 120)))
    until_empty = bool(args.get("until_empty"))
    include_leftovers = bool(
        args.get("include_leftovers")
        if args.get("include_leftovers") is not None
        else True
    )
    dry_run = bool(args.get("dry_run"))
    max_batches = max(1, min(200, int(args.get("max_batches") or (1 if not until_empty else 50))))
    pad = float(args.get("pad") or 280.0)
    detect_max = max(20, min(300, int(args.get("detect_max_units") or 120)))
    # Default on: per-unit layout_dual_unit before park (staging-style).
    layout_batch = bool(
        True if args.get("layout_batch") is None else args.get("layout_batch")
    )
    unit_gap = float(args.get("unit_gap") or 220.0)

    batches: list[dict[str, Any]] = []
    source_remaining = -1
    sink_count = -1
    dry_removed: set[str] = set()
    dry_sink_extra: set[str] = set()

    for bi in range(max_batches):
        report_progress(
            "sink_batch",
            pct=min(95.0, 5.0 + 90.0 * bi / max(1, max_batches)),
            message=f"batch {bi + 1}/{max_batches}",
            batch=bi + 1,
        )
        raise_if_cancelled()
        src = _data(http_json("GET", f"/v1/topology/views/{source_view_id}", timeout=120.0))
        if not src.get("ok"):
            return {
                "ok": False,
                "error": src.get("error") or "source_view_fetch_failed",
                "batches": batches,
            }
        snk = _data(http_json("GET", f"/v1/topology/views/{sink_view_id}", timeout=120.0))
        if not snk.get("ok"):
            return {
                "ok": False,
                "error": snk.get("error") or "sink_view_fetch_failed",
                "batches": batches,
            }

        src_ids, src_pos = _view_node_id_pos(src)
        snk_ids, snk_pos = _view_node_id_pos(snk)
        if dry_run and dry_removed:
            src_ids = [i for i in src_ids if i not in dry_removed]
            snk_ids = list(dict.fromkeys(list(snk_ids) + sorted(dry_sink_extra)))
        source_remaining = len(src_ids)
        sink_count = len(snk_ids)
        if source_remaining <= 0:
            break

        nodes = [
            n
            for n in (src.get("nodes") or [])
            if isinstance(n, dict)
            and str(n.get("fabric_node_id") or "") not in dry_removed
        ]
        edges = [
            e
            for e in (src.get("edges") or [])
            if isinstance(e, dict)
            and str(e.get("a_node_id") or e.get("source") or "") not in dry_removed
            and str(e.get("b_node_id") or e.get("target") or "") not in dry_removed
        ]
        st = build_state_from_nodes_edges(nodes, edges)
        units = find_dual_portal_units(st, max_units=detect_max)
        picked = select_dual_unit_batch(
            units,
            max_units=max_units,
            min_nodes=min_nodes,
            max_nodes=max_nodes,
            max_batch_nodes=max_batch_nodes,
            exclude_ids=set(snk_ids),
        )
        move_ids = batch_node_ids(picked)
        mode = "dual_units"
        if not move_ids:
            # Relax size band once before leftovers.
            picked = select_dual_unit_batch(
                units,
                max_units=max_units,
                min_nodes=2,
                max_nodes=max(max_nodes, 200),
                max_batch_nodes=max_batch_nodes,
                exclude_ids=set(snk_ids),
            )
            move_ids = batch_node_ids(picked)
        if not move_ids and include_leftovers:
            mode = "leftovers"
            move_ids = leftover_batch_ids(
                src_ids,
                max_batch_nodes=max_batch_nodes,
                exclude_ids=set(snk_ids),
            )
            # leftovers may already be on sink; still remove from source
            if not move_ids:
                move_ids = leftover_batch_ids(
                    src_ids, max_batch_nodes=max_batch_nodes, exclude_ids=set()
                )

        if not move_ids:
            break

        need_cap = sink_count + len([i for i in move_ids if i not in set(snk_ids)])
        if need_cap > 2000:
            return {
                "ok": False,
                "error": "sink_capacity_exceeded",
                "sink_nodes": sink_count,
                "batch_nodes": len(move_ids),
                "hint": "Create another child region sink or lower max_batch_nodes.",
                "batches": batches,
                "source_remaining": source_remaining,
            }

        batch_row: dict[str, Any] = {
            "batch_index": bi + 1,
            "mode": mode,
            "unit_count": len(picked),
            "units": units_as_batch_rows(picked, st.names),
            "node_ids": move_ids,
            "node_count": len(move_ids),
            "source_before": source_remaining,
            "sink_before": sink_count,
        }

        if dry_run:
            batches.append(batch_row)
            dry_removed.update(move_ids)
            dry_sink_extra.update(move_ids)
            source_remaining = max(0, source_remaining - len(move_ids))
            sink_count = len(set(snk_ids) | dry_sink_extra)
            if not until_empty:
                break
            continue

        bump = _ensure_view_max_nodes(sink_view_id, min(2000, max(need_cap + 20, 200)))
        if isinstance(bump, dict) and bump.get("ok") is False:
            return {**bump, "batches": batches}

        add_ids = [i for i in move_ids if i not in set(snk_ids)]
        add_out: dict[str, Any] = {"ok": True, "added": 0, "skipped_existing": len(move_ids)}
        if add_ids:
            add_out = _add_topology_view_nodes(
                {
                    "view_id": sink_view_id,
                    "fabric_node_ids": add_ids,
                    "layout": "keep",
                    "max_nodes": min(2000, max(need_cap + 20, 200)),
                }
            )
            if add_out.get("ok") is False:
                return {
                    "ok": False,
                    "error": add_out.get("error") or "sink_add_failed",
                    "detail": add_out,
                    "batches": batches,
                    "batch": batch_row,
                }

        layout_reports: list[dict[str, Any]] = []
        attach_meta: dict[str, Any] = {}
        pos_patch: list[dict[str, Any]] = []
        # View edges + fabric bridges into already-sunk peers (cross-canvas tips).
        attach_links = merge_view_links(src, snk)
        probe_ids: list[str] = []
        if mode == "dual_units" and picked:
            for u in picked:
                for p in (u.portal_a, u.portal_b):
                    if p and p not in probe_ids:
                        probe_ids.append(p)
        for mid in move_ids:
            if mid not in probe_ids:
                probe_ids.append(mid)
        fabric_bridges = _fabric_bridges_into(
            probe_ids, set(snk_ids), probe_cap=16
        )
        if fabric_bridges:
            have = set(attach_links)
            for br in fabric_bridges:
                if br not in have:
                    attach_links.append(br)
                    have.add(br)
        if layout_batch and mode == "dual_units" and picked:
            report_progress(
                "sink_layout",
                pct=min(95.0, 20.0 + 70.0 * bi / max(1, max_batches)),
                message=f"layout_dual_unit + orbit_attach x{len(picked)}",
                batch=bi + 1,
            )
            world, layout_reports, attach_meta = layout_and_pack_batch(
                st,
                picked,
                sink_pos=snk_pos,
                pad=pad,
                unit_gap=unit_gap,
                links=attach_links,
            )
            pos_patch = positions_to_patch(world) if world else []
            # Fill any members missing from unit layout (shared skip / fail).
            have = {str(p.get("fabric_node_id") or "") for p in pos_patch}
            missing = [i for i in move_ids if i not in have]
            if missing:
                pos_patch.extend(
                    park_positions(
                        src_pos,
                        missing,
                        sink_pos=snk_pos,
                        pad=pad,
                        links=attach_links,
                    )
                )
        else:
            pos_patch = park_positions(
                src_pos,
                move_ids,
                sink_pos=snk_pos,
                pad=pad,
                links=attach_links,
            )

        patch_out: dict[str, Any] = {"ok": True, "updated": 0}
        if pos_patch:
            patch_out = _patch_positions_chunked(sink_view_id, pos_patch)

        rm_out = _remove_topology_view_nodes(
            {"view_id": source_view_id, "fabric_node_ids": move_ids}
        )
        if rm_out.get("ok") is False:
            return {
                "ok": False,
                "error": rm_out.get("error") or "source_remove_failed",
                "detail": rm_out,
                "hint": "Nodes may already be on sink; fix source membership manually.",
                "batches": batches,
                "batch": batch_row,
            }

        # Refresh counts
        src2 = _data(http_json("GET", f"/v1/topology/views/{source_view_id}", timeout=120.0))
        snk2 = _data(http_json("GET", f"/v1/topology/views/{sink_view_id}", timeout=120.0))
        src_ids2, _ = _view_node_id_pos(src2 if src2.get("ok") else {})
        snk_ids2, _ = _view_node_id_pos(snk2 if snk2.get("ok") else {})
        source_remaining = len(src_ids2) if src2.get("ok") else max(0, source_remaining - len(move_ids))
        sink_count = len(snk_ids2) if snk2.get("ok") else sink_count + len(add_ids)

        batch_row.update(
            {
                "added": int(add_out.get("added") or len(add_ids)),
                "positions_updated": int(patch_out.get("updated") or len(pos_patch)),
                "removed_from_source": int(rm_out.get("removed") or len(move_ids)),
                "source_after": source_remaining,
                "sink_after": sink_count,
                "max_nodes_bump": bump,
                "layout_batch": layout_batch and mode == "dual_units",
                "unit_layouts": layout_reports,
                "units_accepted": sum(
                    1 for r in layout_reports if r.get("accepted")
                ),
                "orbit_attach": {
                    **(attach_meta or {}),
                    "fabric_bridge_n": len(fabric_bridges),
                },
            }
        )
        batches.append(batch_row)

        if not until_empty or source_remaining <= 0:
            break

    done = source_remaining <= 0
    return {
        "ok": True,
        "action": "sinkTopologyDualUnits",
        "source_view_id": source_view_id,
        "sink_view_id": sink_view_id,
        "dry_run": dry_run,
        "until_empty": until_empty,
        "batches_run": len(batches),
        "batches": batches,
        "source_remaining": source_remaining,
        "sink_nodes": sink_count,
        "done": done,
        "layout_batch": layout_batch,
        "hint": (
            "Source empty — sink membership complete. Batches already had "
            "layout_dual_unit when layout_batch=true; finish with polish_crossings / "
            "straighten / clear_edge_hits (avoid chord straighten after clear)."
            if done
            else (
                "Call again for the NEXT batch only after polish/clear on sink. "
                "Do NOT set until_empty — one batch → tune → next batch."
            )
        ),
    }


def _move_topology_view_nodes(args: dict[str, Any]) -> dict[str, Any]:
    """Move (or copy) explicit fabric ids from source_view_id onto view_id.

    Bidirectional: swap view_id / source_view_id to send nodes back.
    Caller supplies fabric_node_ids — no auto CN/dual-unit selection.
    """
    dest_view_id = str(
        args.get("view_id")
        or args.get("sink_view_id")
        or args.get("target_view_id")
        or args.get("to_view_id")
        or ""
    ).strip()
    source_view_id = str(
        args.get("source_view_id")
        or args.get("from_view_id")
        or args.get("root_view_id")
        or ""
    ).strip()
    if not dest_view_id or not source_view_id:
        return {
            "ok": False,
            "error": "view_id_and_source_view_id_required",
            "hint": (
                "action=move_nodes: source_view_id=FROM, view_id=TO; "
                "params.fabric_node_ids=[...]. Swap ids to reverse."
            ),
        }
    if source_view_id == dest_view_id:
        return {"ok": False, "error": "source_and_dest_must_differ"}

    overrides = args.get("params") if isinstance(args.get("params"), dict) else {}
    raw_ids = overrides.get("fabric_node_ids")
    if raw_ids is None:
        raw_ids = args.get("fabric_node_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return {
            "ok": False,
            "error": "fabric_node_ids_required",
            "hint": "Pass params.fabric_node_ids with the exact NE ids to move.",
        }
    want: list[str] = []
    seen: set[str] = set()
    for x in raw_ids:
        sid = str(x or "").strip()
        if not sid or sid.startswith("region:") or sid in seen:
            continue
        seen.add(sid)
        want.append(sid)
    if not want:
        return {"ok": False, "error": "fabric_node_ids_empty"}

    mode = str(args.get("mode") or "preview").strip().lower() or "preview"
    dry_run = bool(overrides.get("dry_run") if "dry_run" in overrides else args.get("dry_run"))
    if mode != "apply":
        dry_run = True
    copy_positions = bool(
        True
        if overrides.get("copy_positions") is None and args.get("copy_positions") is None
        else (
            overrides.get("copy_positions")
            if "copy_positions" in overrides
            else args.get("copy_positions")
        )
    )
    park = bool(
        overrides.get("park") if "park" in overrides else args.get("park")
    )
    remove_from_source = bool(
        True
        if overrides.get("remove_from_source") is None
        and args.get("remove_from_source") is None
        else (
            overrides.get("remove_from_source")
            if "remove_from_source" in overrides
            else args.get("remove_from_source")
        )
    )
    pad = float(overrides.get("pad") or args.get("pad") or 280.0)
    offset_x = float(overrides.get("offset_x") or args.get("offset_x") or 0.0)
    offset_y = float(overrides.get("offset_y") or args.get("offset_y") or 0.0)

    src = _data(http_json("GET", f"/v1/topology/views/{source_view_id}", timeout=120.0))
    if not src.get("ok"):
        return {"ok": False, "error": src.get("error") or "source_view_fetch_failed"}
    dst = _data(http_json("GET", f"/v1/topology/views/{dest_view_id}", timeout=120.0))
    if not dst.get("ok"):
        return {"ok": False, "error": dst.get("error") or "dest_view_fetch_failed"}

    src_ids, src_pos = _view_node_id_pos(src)
    dst_ids, dst_pos = _view_node_id_pos(dst)
    src_set = set(src_ids)
    move_ids = [i for i in want if i in src_set]
    missing = [i for i in want if i not in src_set]
    already = [i for i in move_ids if i in set(dst_ids)]
    add_ids = [i for i in move_ids if i not in set(dst_ids)]
    need_cap = len(dst_ids) + len(add_ids)
    if need_cap > 2000:
        return {
            "ok": False,
            "error": "dest_capacity_exceeded",
            "dest_nodes": len(dst_ids),
            "would_add": len(add_ids),
            "hint": "Dest soft max is 2000; move a smaller batch.",
        }

    names: dict[str, str] = {}
    for n in src.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        fid = str(n.get("fabric_node_id") or "").strip()
        if fid in seen:
            names[fid] = str(n.get("name") or "")

    base: dict[str, Any] = {
        "ok": True,
        "action": "move_nodes",
        "source_view_id": source_view_id,
        "view_id": dest_view_id,
        "requested": len(want),
        "move_ids": move_ids,
        "move_count": len(move_ids),
        "names": [{"fabric_node_id": i, "name": names.get(i) or ""} for i in move_ids],
        "missing_on_source": missing,
        "already_on_dest": already,
        "would_add": len(add_ids),
        "copy_positions": copy_positions,
        "park": park,
        "remove_from_source": remove_from_source,
        "source_before": len(src_ids),
        "dest_before": len(dst_ids),
        "mode": "preview" if dry_run else "apply",
    }
    if not move_ids:
        return {
            **base,
            "ok": False,
            "error": "none_of_ids_on_source",
            "hint": "Those fabric_node_ids are not on source_view_id.",
        }
    if dry_run:
        return {
            **base,
            "dry_run": True,
            "hint": (
                "Preview only. Re-call with mode=apply to add onto view_id "
                + ("and remove from source." if remove_from_source else "(copy; source kept).")
            ),
        }

    bump = _ensure_view_max_nodes(dest_view_id, min(2000, max(need_cap + 20, 200)))
    if isinstance(bump, dict) and bump.get("ok") is False:
        return {**bump, **base}

    add_out: dict[str, Any] = {"ok": True, "added": 0, "skipped_existing": len(already)}
    if add_ids:
        add_out = _add_topology_view_nodes(
            {
                "view_id": dest_view_id,
                "fabric_node_ids": add_ids,
                "layout": "keep",
                "max_nodes": min(2000, max(need_cap + 20, 200)),
            }
        )
        if add_out.get("ok") is False:
            return {
                "ok": False,
                "error": add_out.get("error") or "dest_add_failed",
                "detail": add_out,
                **{k: base[k] for k in ("source_view_id", "view_id", "move_ids")},
            }

    from netx_topology_mcp.layout_ops.sink_dual_units import (
        merge_view_links,
        park_positions,
    )

    pos_patch: list[dict[str, Any]] = []
    if park:
        attach_links = merge_view_links(src, dst)
        pos_patch = park_positions(
            src_pos,
            move_ids,
            sink_pos=dst_pos,
            pad=pad,
            links=attach_links,
        )
    elif copy_positions:
        pos_patch = [
            {
                "fabric_node_id": nid,
                "x": float(src_pos[nid][0] + offset_x),
                "y": float(src_pos[nid][1] + offset_y),
            }
            for nid in move_ids
            if nid in src_pos
        ]

    patch_out: dict[str, Any] = {"ok": True, "updated": 0}
    if pos_patch:
        patch_out = _patch_positions_chunked(dest_view_id, pos_patch)

    rm_out: dict[str, Any] = {"ok": True, "removed": 0}
    if remove_from_source:
        rm_out = _remove_topology_view_nodes(
            {"view_id": source_view_id, "fabric_node_ids": move_ids}
        )
        if rm_out.get("ok") is False:
            return {
                "ok": False,
                "error": rm_out.get("error") or "source_remove_failed",
                "detail": rm_out,
                "hint": "Nodes may already be on dest; fix source membership manually.",
                "added": int(add_out.get("added") or len(add_ids)),
                **{k: base[k] for k in ("source_view_id", "view_id", "move_ids")},
            }

    src2 = _data(http_json("GET", f"/v1/topology/views/{source_view_id}", timeout=120.0))
    dst2 = _data(http_json("GET", f"/v1/topology/views/{dest_view_id}", timeout=120.0))
    src_after, _ = _view_node_id_pos(src2 if src2.get("ok") else {})
    dst_after, _ = _view_node_id_pos(dst2 if dst2.get("ok") else {})
    return {
        **base,
        "dry_run": False,
        "added": int(add_out.get("added") or len(add_ids)),
        "positions_updated": int(patch_out.get("updated") or len(pos_patch)),
        "removed_from_source": int(rm_out.get("removed") or (len(move_ids) if remove_from_source else 0)),
        "source_after": len(src_after) if src2.get("ok") else None,
        "dest_after": len(dst_after) if dst2.get("ok") else None,
        "max_nodes_bump": bump,
        "hint": (
            "Moved onto view_id"
            + ("; removed from source_view_id." if remove_from_source else "; source kept (copy).")
            + " Swap view_id/source_view_id to send them back."
        ),
    }


def _copy_topology_view_nodes(args: dict[str, Any]) -> dict[str, Any]:
    """Copy fabric placements (+ coords) from one canvas onto another.

    Source membership is unchanged. For test sandboxes: clone a known-good
    canvas onto a fresh child region without re-adding ids by hand.
    """
    source_view_id = str(
        args.get("source_view_id") or args.get("from_view_id") or ""
    ).strip()
    target_view_id = str(
        args.get("target_view_id") or args.get("to_view_id") or ""
    ).strip()
    if not source_view_id or not target_view_id:
        return {
            "ok": False,
            "error": "source_view_id_and_target_view_id_required",
        }
    if source_view_id == target_view_id:
        return {"ok": False, "error": "source_and_target_must_differ"}

    copy_positions = bool(
        True if args.get("copy_positions") is None else args.get("copy_positions")
    )
    clear_target = bool(args.get("clear_target"))
    dry_run = bool(args.get("dry_run"))
    offset_x = float(args.get("offset_x") or 0.0)
    offset_y = float(args.get("offset_y") or 0.0)
    limit = args.get("limit")
    try:
        limit_n = int(limit) if limit is not None else 0
    except (TypeError, ValueError):
        limit_n = 0

    src = _data(http_json("GET", f"/v1/topology/views/{source_view_id}", timeout=120.0))
    if not src.get("ok"):
        return {"ok": False, "error": src.get("error") or "source_view_fetch_failed"}
    dst = _data(http_json("GET", f"/v1/topology/views/{target_view_id}", timeout=120.0))
    if not dst.get("ok"):
        return {"ok": False, "error": dst.get("error") or "target_view_fetch_failed"}

    src_ids, src_pos = _view_node_id_pos(src)
    dst_ids, _ = _view_node_id_pos(dst)
    if limit_n > 0:
        src_ids = src_ids[:limit_n]
    if not src_ids:
        return {
            "ok": True,
            "action": "copyTopologyViewNodes",
            "source_view_id": source_view_id,
            "target_view_id": target_view_id,
            "copied": 0,
            "hint": "Source canvas has no fabric nodes.",
        }

    need = len(set(dst_ids) | set(src_ids)) if not clear_target else len(src_ids)
    if need > 2000:
        return {
            "ok": False,
            "error": "target_capacity_exceeded",
            "source_nodes": len(src_ids),
            "target_nodes": len(dst_ids),
            "hint": "Target soft max is 2000; clear_target or copy a subset (limit).",
        }

    if dry_run:
        return {
            "ok": True,
            "action": "copyTopologyViewNodes",
            "dry_run": True,
            "source_view_id": source_view_id,
            "target_view_id": target_view_id,
            "source_nodes": len(src_ids),
            "target_nodes_before": len(dst_ids),
            "would_add": len([i for i in src_ids if i not in set(dst_ids)]),
            "would_clear_target": clear_target,
            "copy_positions": copy_positions,
        }

    cleared = 0
    if clear_target and dst_ids:
        rm = _remove_topology_view_nodes(
            {"view_id": target_view_id, "fabric_node_ids": list(dst_ids)}
        )
        if rm.get("ok") is False:
            return {
                "ok": False,
                "error": rm.get("error") or "clear_target_failed",
                "detail": rm,
            }
        cleared = int(rm.get("removed") or len(dst_ids))
        dst_ids = []

    bump = _ensure_view_max_nodes(target_view_id, min(2000, max(need + 20, 200)))
    if isinstance(bump, dict) and bump.get("ok") is False:
        return bump

    add_ids = [i for i in src_ids if i not in set(dst_ids)]
    add_out: dict[str, Any] = {"ok": True, "added": 0}
    if add_ids:
        add_out = _add_topology_view_nodes(
            {
                "view_id": target_view_id,
                "fabric_node_ids": add_ids,
                "layout": "keep",
                "max_nodes": min(2000, max(need + 20, 200)),
            }
        )
        if add_out.get("ok") is False:
            return {
                "ok": False,
                "error": add_out.get("error") or "target_add_failed",
                "detail": add_out,
            }

    patch_out: dict[str, Any] = {"ok": True, "updated": 0}
    if copy_positions:
        positions = [
            {
                "fabric_node_id": nid,
                "x": float(src_pos[nid][0] + offset_x),
                "y": float(src_pos[nid][1] + offset_y),
            }
            for nid in src_ids
            if nid in src_pos
        ]
        if positions:
            patch_out = _patch_positions_chunked(target_view_id, positions)

    dst2 = _data(http_json("GET", f"/v1/topology/views/{target_view_id}", timeout=120.0))
    dst_after, _ = _view_node_id_pos(dst2 if dst2.get("ok") else {})
    return {
        "ok": True,
        "action": "copyTopologyViewNodes",
        "source_view_id": source_view_id,
        "target_view_id": target_view_id,
        "source_nodes": len(src_ids),
        "cleared": cleared,
        "added": int(add_out.get("added") or len(add_ids)),
        "positions_updated": int(patch_out.get("updated") or 0),
        "copy_positions": copy_positions,
        "target_nodes": len(dst_after) if dst2.get("ok") else need,
        "max_nodes_bump": bump,
        "hint": (
            "Clone done — source unchanged. Run layoutTopologyView / analyze on target."
        ),
    }


HTTP_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "getTopologyTree",
        "description": (
            "Get topology folder tree. Top-level folders are nav「根」; each has a unique "
            "「根图」/Root map canvas (physical view). Nested regions are canvases themselves. "
            "Each folder has ne_count (distinct fabric NEs in subtree). Default compact=true "
            "(slim fields for agents). Optional max_depth prunes deep children. Start here; "
            "createTopologyFolder if you need a new root/region."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "compact": {
                    "type": "boolean",
                    "default": True,
                    "description": "Default true — slim folder/view fields. Pass false for raw API tree.",
                },
                "max_depth": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Optional depth limit from root (0 = root only). Omitted = full depth.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "getTopologyView",
        "description": (
            "Get a topology view by view_id. Default detail=summary: counts, sample_nodes (x/y), "
            "and links[] (undirected NE pairs + link_count for one canvas edge each). "
            "For UME layout study on small regions, set sample >= node_count. "
            "Pass detail=full only when you need every membership field."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {"type": "string"},
                "detail": {
                    "type": "string",
                    "enum": ["summary", "full"],
                    "default": "summary",
                },
                "sample": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 200,
                    "default": 20,
                    "description": "Sample node count when detail=summary (use >=node_count for layout study).",
                },
            },
            "required": ["view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "createTopologyFolder",
        "description": (
            "ONLY way to create canvases. Top-level: nav「根」+ auto「根图」physical + view_id. "
            "Under 根图/region: creates a sub-region canvas (another physical view_id). "
            "Returns folder id + view_id / canvas_folder_id for addTopologyViewNodes. "
            "Optional locale zh|en for Root map naming. Requires ne:write."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Root or sub-region display name"},
                "parent_id": {
                    "type": "string",
                    "description": (
                        "Omit for new top-level「根」+根图. Pass 根图/region folder id to "
                        "create a sub-region under it (not a sibling custom view)."
                    ),
                },
                "locale": {
                    "type": "string",
                    "description": "Optional zh|en — labels auto「根图」vs Root map",
                },
                "sort_order": {"type": "integer", "default": 0},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "addTopologyViewNodes",
        "description": (
            "Bulk-place existing fabric nodes on a view. Prefer server filters "
            "(keyword/role/vendor/link_status + limit/offset); API selects ids — do not pull then re-send huge id lists. "
            "Returns a summary (added/truncated/next_offset). Soft max_nodes is per-view "
            "(physical default 2000). Pass max_nodes to raise/clamp the view's "
            "membership limit on the SAME physical canvas. "
            "Never pass managed/UME ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {"type": "string"},
                "max_nodes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "description": (
                        "Optional: PATCH this view's membership.max_nodes before add "
                        "(use on physical 根图/region when soft cap blocks members)."
                    ),
                },
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
        "name": "sinkTopologyDualUnits",
        "description": (
            "Drain a root physical canvas into a child-region sink: detect dual_units on "
            "source_view_id, layout_dual_unit each unit (layout_batch default true), "
            "then compose_orbit-style block sweep to park onto sink (best partial "
            "crossings / overlap / bridge — not fixed right), "
            "then remove those fabric ids from the root. Default one batch/call; "
            "until_empty=true loops until source empty (or leftovers). Global polish "
            "still via layoutTopologyView. dry_run previews selection. Requires ne:write."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_view_id": {
                    "type": "string",
                    "description": "Root / source physical view_id to drain.",
                },
                "sink_view_id": {
                    "type": "string",
                    "description": "Child-region physical view_id receiving batches.",
                },
                "max_units": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 3,
                    "description": "Max dual_units per batch (default 3).",
                },
                "min_nodes": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 200,
                    "default": 8,
                },
                "max_nodes": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 400,
                    "default": 80,
                    "description": "Max nodes per dual_unit candidate.",
                },
                "max_batch_nodes": {
                    "type": "integer",
                    "minimum": 8,
                    "maximum": 800,
                    "default": 120,
                },
                "layout_batch": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Default true: run layout_dual_unit per unit before parking "
                        "(old staging/block layout). false = copy source coords only."
                    ),
                },
                "unit_gap": {
                    "type": "number",
                    "default": 220,
                    "description": "Gap between laid-out units in a batch strip.",
                },
                "until_empty": {
                    "type": "boolean",
                    "default": False,
                    "description": "Loop batches until source empty (watch MCP timeout).",
                },
                "max_batches": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Safety cap when until_empty (default 50).",
                },
                "include_leftovers": {
                    "type": "boolean",
                    "default": True,
                    "description": "When dual_units exhausted, move leftover NE chunks.",
                },
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                },
                "pad": {
                    "type": "number",
                    "default": 280,
                    "description": "Gap between sink hull and parked batch.",
                },
                "detect_max_units": {
                    "type": "integer",
                    "minimum": 20,
                    "maximum": 300,
                    "default": 120,
                },
            },
            "required": ["source_view_id", "sink_view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "copyTopologyViewNodes",
        "description": (
            "One-shot clone: copy all fabric placements from source_view_id onto "
            "target_view_id (optional clear_target first), preserving x/y when "
            "copy_positions=true. Source canvas is unchanged — use for test sandboxes "
            "instead of re-adding ids. Soft max 2000. Requires ne:write."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_view_id": {
                    "type": "string",
                    "description": "Canvas to copy FROM.",
                },
                "target_view_id": {
                    "type": "string",
                    "description": "Canvas to copy TO.",
                },
                "copy_positions": {
                    "type": "boolean",
                    "default": True,
                    "description": "Copy x/y from source (default true).",
                },
                "clear_target": {
                    "type": "boolean",
                    "default": False,
                    "description": "Remove existing target members before copy.",
                },
                "offset_x": {
                    "type": "number",
                    "default": 0,
                    "description": "Shift copied x by this amount.",
                },
                "offset_y": {
                    "type": "number",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "description": "Optional cap on how many source nodes to copy.",
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            "required": ["source_view_id", "target_view_id"],
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
            "Project existing fabric neighbors (LLDP/UME) of nodes already on the view onto the canvas. "
            "Only places nodes that already exist in fabric. Default detail=summary (not full graph). "
            "Optional seed_fabric_node_ids / managed_ne_ids limit expansion to those seeds; "
            "omit to expand from every node on the view. "
            "Pass region_folder_id to keep only peers whose fabric.region_folder_id matches "
            "(reports out_of_region_skipped); use this on regional verify canvases."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {"type": "string"},
                "seed_fabric_node_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional fabric node ids already on the view to expand from",
                },
                "managed_ne_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional managed NE ids to resolve to on-view fabric seeds",
                },
                "region_folder_id": {
                    "type": "string",
                    "description": (
                        "If set, only add neighbors with matching fabric.region_folder_id; "
                        "skipped peers are counted in out_of_region_skipped."
                    ),
                },
                "detail": {
                    "type": "string",
                    "enum": ["summary", "full"],
                    "default": "summary",
                },
                "sample": {"type": "integer", "minimum": 0, "maximum": 100, "default": 20},
            },
            "required": ["view_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "queryTopologyFabricNodes",
        "description": (
            "Fabric inventory (read-only). mode=summary|list|search. "
            "Default: q→search, else list. "
            "summary=node/edge counts; list=paged filters "
            "(keyword/role/link_status/region_folder_id); "
            "search=quick name/IP/id (needs q). Replaces getTopologyFabricSummary / "
            "listTopologyFabricNodes / searchTopologyFabricNodes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["summary", "list", "search"],
                    "description": "Omit to auto-pick: q→search, else list.",
                },
                "q": {
                    "type": "string",
                    "description": "Search needle (mode=search); also aliases keyword for list.",
                },
                "keyword": {
                    "type": "string",
                    "description": "List filter keyword; alias of q for search.",
                },
                "role": {"type": "string"},
                "region_folder_id": {
                    "type": "string",
                    "description": "Filter by topo folder id (UME region / canvas folder).",
                },
                "link_status": {
                    "type": "string",
                    "enum": ["linked", "orphaned", "managed", "ume", "both"],
                },
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
                "limit": {"type": "integer", "description": "Alias of page_size"},
                "summary": {
                    "type": "boolean",
                    "default": False,
                    "description": "Shortcut for mode=summary",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "queryTopologyNeighborhood",
        "description": (
            "Neighborhood around a fabric node (depth 1–3). Returns compact nodes + links[] "
            "(undirected NE pairs + link_count). No port/label spam."
        ),
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
            "Fabric adjacency for drawing: default detail=adjacency returns links[] "
            "(a_node_id, b_node_id, link_count [, names]) — one canvas edge per pair. "
            "With node_id also returns peers[]. Raise page_size if peers_complete is false. "
            "Pass detail=ports only when you need port-level rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "keyword": {"type": "string"},
                "layer": {"type": "string", "default": "physical"},
                "status": {"type": "string", "enum": ["active", "missing", "stale"]},
                "source": {"type": "string", "enum": ["lldp", "ume", "manual"]},
                "detail": {
                    "type": "string",
                    "enum": ["adjacency", "ports"],
                    "default": "adjacency",
                    "description": "adjacency (default) = NE pairs; ports = raw port rows in items[].",
                },
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyzeTopologyViewLayout",
        "description": (
            "Layout QA + structure planning (read-only). Returns verdict + overlap/crossing/"
            "spacing/sparsity/edges + mid-tier chains(直链成一体)/rings(最小环不被穿) "
            "+ score.total∈[0,100] (chain/rings each weight 0.10). Pass view_id, or folder_id to sample. "
            "detail=structure: graph stats for gravity (core_bar|agg_bar|mixed), hubs, stubs, "
            "dual_units (two-portal eye units), soft_blocks, "
            "geometry_hint, recipe_preference (compact|corridor|rings) — call BEFORE layout. "
            "detail=hotspots|blocks|both: sight{} for hand-drag (crossings, drag_candidates, blocks); "
            "both also includes structure{}. "
            "For writing positions use layoutTopologyView / updateTopologyViewPositions. Needs ne:read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {
                    "type": "string",
                    "description": "Analyze a single topology view",
                },
                "folder_id": {
                    "type": "string",
                    "description": "Sample physical views under this folder (tree walk)",
                },
                "detail": {
                    "type": "string",
                    "enum": ["summary", "structure", "hotspots", "blocks", "both"],
                    "default": "summary",
                    "description": (
                        "summary=score only; structure=gravity/hubs/recipe hint (phase 0.5); "
                        "hotspots|blocks=sight; both=structure+sight (hand-drag)"
                    ),
                },
                "sight_limit": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 80,
                    "default": 40,
                    "description": "Max crossings / drag candidates when detail≠summary",
                },
                "sight_cell": {
                    "type": "number",
                    "default": 600,
                    "description": "Grid cell size (px) for detail=blocks",
                },
                "max_views": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 80,
                    "default": 25,
                },
                "min_nodes": {"type": "integer", "minimum": 0, "default": 5},
                "max_nodes": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 800},
                "with_meta": {
                    "type": "boolean",
                    "default": False,
                    "description": "Include IP/vendor caption line in overlap boxes",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "layoutTopologyView",
        "description": (
            "Layout / local polish for a canvas. Prefer local actions over global crush. "
            "action=layout: recipe=rings|corridor|compact|unstick (small graphs). "
            "action=layout_dual_unit: eye-shaped dual-portal unit (require crossings=0). "
            "action=move_nodes (alias sink_nodes): move fabric_node_ids from "
            "source_view_id→view_id; park=true for orbit attach; swap views to reverse. "
            "Prefer sinkTopologyDualUnits for dual_units batches. "
            "action=orbit_sweep: crossing orbit; preview+node_id / apply+pick / round=true. "
            "action=polish_crossings: one-shot straighten→press→untangle (no temp scripts). "
            "action=clear_edge_hits: eject nodes on non-incident edges (H/V). "
            "action=fix_overlaps|resolve_overlaps: pull apart overlaps. "
            "action=untangle / straighten_channels: surgical polish. "
            "action=job_status|job_cancel: poll/cancel background jobs. "
            "preset: loose|balanced|dense. mode: preview|apply. "
            "Workflow: analyze(structure) → sinkTopologyDualUnits → orbit_sweep → "
            "polish_crossings → clear_edge_hits → hand drag. Needs ne:write for apply."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "view_id": {
                    "type": "string",
                    "description": "Target canvas to layout (and write when mode=apply)",
                },
                "action": {
                    "type": "string",
                    "enum": [
                        "layout",
                        "fix_overlaps",
                        "resolve_overlaps",
                        "layout_dual_unit",
                        "straighten_channels",
                        "untangle",
                        "polish_crossings",
                        "clear_edge_hits",
                        "orbit_sweep",
                        "move_nodes",
                        "sink_nodes",
                        "job_status",
                        "job_cancel",
                    ],
                    "default": "layout",
                    "description": (
                        "layout=full recipe; layout_dual_unit=dual-portal eye; "
                        "move_nodes|sink_nodes=migrate fabric_node_ids; "
                        "orbit_sweep=polar sweep; polish_crossings=one-shot cut crossings; "
                        "clear_edge_hits=eject edge hits; fix_overlaps|resolve_overlaps; "
                        "untangle/straighten_channels; job_status|job_cancel."
                    ),
                },
                "source_view_id": {
                    "type": "string",
                    "description": (
                        "For action=layout: optional load graph (default=view_id). "
                        "For action=move_nodes: required FROM canvas (view_id is TO)."
                    ),
                },
                "recipe": {
                    "type": "string",
                    "enum": [
                        "rings",
                        "corridor",
                        "compact",
                        "unstick",
                    ],
                    "default": "rings",
                    "description": (
                        "Only for action=layout. rings=petals/min-rings; "
                        "corridor/compact/unstick=Tutte corridor variants."
                    ),
                },
                "preset": {
                    "type": "string",
                    "enum": ["loose", "balanced", "dense"],
                    "default": "balanced",
                },
                "mode": {
                    "type": "string",
                    "enum": ["preview", "apply"],
                    "default": "preview",
                    "description": "preview=no write; apply=PATCH positions",
                },
                "tune": {
                    "type": "boolean",
                    "default": False,
                    "description": "Small param sweep for action=layout; zero-overlap first",
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Overrides. layout: target_nn/target_util/…. "
                        "job_status|job_cancel: job_id (required). "
                        "layout_dual_unit: unit_id (optional). "
                        "untangle: max_rounds/max_degree/protect_rigid/focus_ids[]. "
                        "polish_crossings: portal_ids[]/source_view_ids[], "
                        "top_n/max_moves/max_sweeps/straighten/untangle_rounds. "
                        "clear_edge_hits: top_n/thr/margin/max_moves. "
                        "orbit_sweep: node_id/pick/round/top_n/max_jump/angle_step/"
                        "nn_floor/min_angle_sep; protect_rigid default off. "
                        "move_nodes|sink_nodes: fabric_node_ids[] (required), "
                        "copy_positions (default true), park, remove_from_source "
                        "(default true), pad/offset_x/offset_y."
                    ),
                    "additionalProperties": True,
                },
                "catalog": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, only return action/recipe/preset catalog",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]

_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "getTopologyTree": _get_topology_tree,
    "getTopologyView": _get_topology_view,
    "createTopologyFolder": _create_topology_folder,
    "addTopologyViewNodes": _add_topology_view_nodes,
    "removeTopologyViewNodes": _remove_topology_view_nodes,
    "sinkTopologyDualUnits": _sink_topology_dual_units,
    "copyTopologyViewNodes": _copy_topology_view_nodes,
    "updateTopologyViewPositions": _update_topology_view_positions,
    "projectTopologyNeighbors": _project_topology_neighbors,
    "queryTopologyFabricNodes": _query_topology_fabric_nodes,
    "queryTopologyNeighborhood": _query_topology_neighborhood,
    "queryTopologyEdges": _query_topology_edges,
    "analyzeTopologyViewLayout": _analyze_topology_view_layout,
    "layoutTopologyView": _layout_topology_view,
}

TOOL_REQUIRED_SCOPE: dict[str, str] = {
    "getTopologyTree": "ne:read",
    "getTopologyView": "ne:read",
    "createTopologyFolder": "ne:write",
    "addTopologyViewNodes": "ne:write",
    "removeTopologyViewNodes": "ne:write",
    "sinkTopologyDualUnits": "ne:write",
    "copyTopologyViewNodes": "ne:write",
    "updateTopologyViewPositions": "ne:write",
    "projectTopologyNeighbors": "ne:write",
    "queryTopologyFabricNodes": "ne:read",
    "queryTopologyNeighborhood": "ne:read",
    "queryTopologyEdges": "ne:read",
    "analyzeTopologyViewLayout": "ne:read",
    "layoutTopologyView": "ne:write",
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
