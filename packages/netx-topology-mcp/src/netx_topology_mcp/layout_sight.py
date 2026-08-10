"""Layout "sight" for agents: crossing hotspots + spatial blocks with coords.

Gives enough geometry to hand-drag like a human without loading the full canvas
into chat. Used by analyzeTopologyViewLayout(detail=hotspots|blocks).
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    collapse_links,
    segments_properly_intersect,
    top_crossing_edges,
    top_crossing_nodes,
)


def _short_name(name: str) -> str:
    parts = (name or "").split("-")
    return parts[1] if len(parts) >= 2 else (name or "")[:16]


def _pos_map(nodes: list[dict[str, Any]]) -> dict[str, tuple[float, float, str]]:
    out: dict[str, tuple[float, float, str]] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("fabric_node_id") or "").strip()
        if not nid:
            continue
        try:
            x, y = float(n.get("x")), float(n.get("y"))
        except (TypeError, ValueError):
            continue
        out[nid] = (x, y, str(n.get("name") or nid))
    return out


def list_crossings(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    limit: int = 40,
) -> dict[str, Any]:
    pos = _pos_map(nodes)
    links = [(a, b) for a, b in collapse_links(edges) if a in pos and b in pos]
    crosses: list[dict[str, Any]] = []
    hit: dict[str, int] = {}
    edge_hit: dict[tuple[str, str], int] = {}
    total_cross = 0
    for i, (a, b) in enumerate(links):
        p1, p2 = (pos[a][0], pos[a][1]), (pos[b][0], pos[b][1])
        e1 = (a, b) if a < b else (b, a)
        for c, d in links[i + 1 :]:
            if len({a, b, c, d}) < 4:
                continue
            p3, p4 = (pos[c][0], pos[c][1]), (pos[d][0], pos[d][1])
            if not segments_properly_intersect(p1, p2, p3, p4):
                continue
            total_cross += 1
            e2 = (c, d) if c < d else (d, c)
            for nid in (a, b, c, d):
                hit[nid] = hit.get(nid, 0) + 1
            edge_hit[e1] = edge_hit.get(e1, 0) + 1
            edge_hit[e2] = edge_hit.get(e2, 0) + 1
            if len(crosses) < limit:
                crosses.append(
                    {
                        "e1": {
                            "a": a,
                            "b": b,
                            "a_name": _short_name(pos[a][2]),
                            "b_name": _short_name(pos[b][2]),
                        },
                        "e2": {
                            "a": c,
                            "b": d,
                            "a_name": _short_name(pos[c][2]),
                            "b_name": _short_name(pos[d][2]),
                        },
                    }
                )
    # Rank drag candidates: high crossing participation / low degree.
    adj: dict[str, set[str]] = {n: set() for n in pos}
    for a, b in links:
        adj[a].add(b)
        adj[b].add(a)

    from netx_topology_mcp.layout_ops.orbit_sweep import orbit_lite_suggest

    pos_xy = {k: (v[0], v[1]) for k, v in pos.items()}
    names = {k: v[2] for k, v in pos.items()}
    candidates = []
    for nid, cnt in sorted(hit.items(), key=lambda kv: -kv[1]):
        deg = len(adj.get(nid, ()))
        if deg >= 8:
            continue
        x, y, name = pos[nid]
        suggest = orbit_lite_suggest(
            nid,
            pos_xy,
            links,
            adj,
            names,
            max_jump=360.0,
            angle_step=45,
            top_k=3,
        )
        best_delta = suggest[0]["delta_crossings_est"] if suggest else 0
        candidates.append(
            {
                "fabric_node_id": nid,
                "name": name,
                "short": _short_name(name),
                "x": round(x, 1),
                "y": round(y, 1),
                "degree": deg,
                "in_crossings": cnt,
                "score": round(cnt / max(deg, 1), 3),
                "suggest_xy": suggest[:3],
                "delta_crossings_est": best_delta,
            }
        )
        if len(candidates) >= min(30, limit):
            break
    top5 = top_crossing_nodes(
        pos_xy, links, names=names, adj=adj, top_n=5, participation=hit
    )
    top_e = top_crossing_edges(
        pos_xy, links, names=names, top_n=5, edge_participation=edge_hit
    )
    # Fallback: when no drag_candidates (all hot nodes are high-degree hubs),
    # suggest from edge_clearance.top and crossing top_nodes regardless of degree.
    if not candidates:
        from netx_topology_mcp.layout_metrics import compute_edge_clearance

        ec = compute_edge_clearance(pos_xy, links, names=names, top_n=5)
        for eh in (ec.get("top_edge_hits") or [])[:5]:
            nid = str(eh.get("fabric_node_id") or "")
            if not nid or nid not in pos:
                continue
            x, y, name = pos[nid]
            deg = len(adj.get(nid, ()))
            candidates.append({
                "fabric_node_id": nid,
                "name": name,
                "short": _short_name(name),
                "x": round(x, 1),
                "y": round(y, 1),
                "degree": deg,
                "in_crossings": hit.get(nid, 0),
                "score": 0.0,
                "suggest_xy": [],
                "delta_crossings_est": 0,
                "reason": "edge_clearance_hit",
            })
        for tn in top5:
            nid = str(tn.get("fabric_node_id") or "")
            if not nid or nid not in pos:
                continue
            if any(c["fabric_node_id"] == nid for c in candidates):
                continue
            x, y, name = pos[nid]
            deg = len(adj.get(nid, ()))
            candidates.append({
                "fabric_node_id": nid,
                "name": name,
                "short": _short_name(name),
                "x": round(x, 1),
                "y": round(y, 1),
                "degree": deg,
                "in_crossings": tn.get("crossing_hits", 0),
                "score": round(tn.get("crossing_hits", 0) / max(deg, 1), 3),
                "suggest_xy": [],
                "delta_crossings_est": 0,
                "reason": "top_crossing_high_degree",
            })
    return {
        "edge_crossings": total_cross,
        "crossings_listed": len(crosses),
        "crossings": crosses,
        "top_nodes": top5,
        "top_edges": top_e,
        "drag_candidates": candidates,
        "tip": (
            "先看 top_nodes / top_edges（交叉最重前5网元与前5边）；"
            "再动 drag_candidates（低度数可挪点，suggest_xy=orbit_lite）；"
            "完整扫角用 layoutTopologyView action=orbit_sweep(preview,node_id)→pick。"
            "勿本地穷举坐标。"
        ),
    }


def spatial_blocks(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    cell: float = 600.0,
    top_k: int = 12,
) -> dict[str, Any]:
    """Bucket nodes into coarse grid cells; report densest / most-crossing blocks."""
    pos = _pos_map(nodes)
    if not pos or cell <= 1:
        return {"blocks": [], "cell": cell}
    links = [(a, b) for a, b in collapse_links(edges) if a in pos and b in pos]

    def cell_key(x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / cell)), int(math.floor(y / cell)))

    buckets: dict[tuple[int, int], list[str]] = {}
    for nid, (x, y, _) in pos.items():
        buckets.setdefault(cell_key(x, y), []).append(nid)

    # Count crossings whose both midpoints fall in a cell (or either endpoint).
    cell_cross: dict[tuple[int, int], int] = {k: 0 for k in buckets}
    for i, (a, b) in enumerate(links):
        p1 = (pos[a][0], pos[a][1])
        p2 = (pos[b][0], pos[b][1])
        for c, d in links[i + 1 :]:
            if len({a, b, c, d}) < 4:
                continue
            p3 = (pos[c][0], pos[c][1])
            p4 = (pos[d][0], pos[d][1])
            if not segments_properly_intersect(p1, p2, p3, p4):
                continue
            mx = (p1[0] + p2[0] + p3[0] + p4[0]) / 4
            my = (p1[1] + p2[1] + p3[1] + p4[1]) / 4
            ck = cell_key(mx, my)
            cell_cross[ck] = cell_cross.get(ck, 0) + 1

    blocks = []
    for (cx, cy), ids in buckets.items():
        xs = [pos[i][0] for i in ids]
        ys = [pos[i][1] for i in ids]
        sample = sorted(ids, key=lambda i: pos[i][2])[:8]
        blocks.append(
            {
                "cell": [cx, cy],
                "bbox": [
                    round(min(xs), 1),
                    round(min(ys), 1),
                    round(max(xs), 1),
                    round(max(ys), 1),
                ],
                "node_count": len(ids),
                "crossings_near": cell_cross.get((cx, cy), 0),
                "sample_nodes": [
                    {
                        "fabric_node_id": i,
                        "short": _short_name(pos[i][2]),
                        "x": round(pos[i][0], 1),
                        "y": round(pos[i][1], 1),
                    }
                    for i in sample
                ],
            }
        )
    blocks.sort(key=lambda b: (-int(b["crossings_near"]), -int(b["node_count"])))
    return {
        "cell": cell,
        "blocks": blocks[:top_k],
        "tip": (
            "分块手拖：选 crossings_near 高的 block，"
            "getTopologyView / queryTopologyNeighborhood 看邻接，"
            "再 updateTopologyViewPositions 挪该块内的点。"
        ),
    }


def build_sight(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    mode: str = "hotspots",
    limit: int = 40,
    cell: float = 600.0,
) -> dict[str, Any]:
    mode = (mode or "hotspots").strip().lower()
    out: dict[str, Any] = {"mode": mode}
    if mode in {"hotspots", "both", "all"}:
        out["hotspots"] = list_crossings(nodes, edges, limit=limit)
    if mode in {"blocks", "both", "all"}:
        out["blocks"] = spatial_blocks(nodes, edges, cell=cell, top_k=min(16, max(6, limit // 3)))
    return out
