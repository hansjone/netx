"""Tests for agent layout sight (crossing hotspots / blocks)."""

from __future__ import annotations

from netx_topology_mcp.layout_sight import build_sight, list_crossings


def test_list_crossings_and_drag_candidates() -> None:
    # Two segments that properly cross: a—b horizontal, c—d vertical.
    nodes = [
        {"fabric_node_id": "a", "name": "X-A-Y", "x": 0.0, "y": 50.0},
        {"fabric_node_id": "b", "name": "X-B-Y", "x": 100.0, "y": 50.0},
        {"fabric_node_id": "c", "name": "X-C-Y", "x": 50.0, "y": 0.0},
        {"fabric_node_id": "d", "name": "X-D-Y", "x": 50.0, "y": 100.0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "c", "b_node_id": "d"},
    ]
    out = list_crossings(nodes, edges, limit=10)
    assert out["crossings_listed"] == 1
    assert out["crossings"][0]["e1"]["a_name"] == "A"
    assert len(out.get("top_nodes") or []) == 4
    assert out["top_nodes"][0]["crossing_hits"] >= 1
    assert len(out.get("top_edges") or []) == 2
    assert out["top_edges"][0]["crossing_hits"] >= 1
    assert out["drag_candidates"]
    cand = out["drag_candidates"][0]
    assert "suggest_xy" in cand and len(cand["suggest_xy"]) >= 1
    assert "delta_crossings_est" in cand
    assert "x" in cand["suggest_xy"][0] and "delta_crossings_est" in cand["suggest_xy"][0]
    sight = build_sight(nodes, edges, mode="both", limit=10, cell=200.0)
    assert "hotspots" in sight and "blocks" in sight
    assert sight["blocks"]["blocks"]
