"""edge_axis: prefer H/V edges, horizontal over vertical."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import compute_edge_axis
from netx_topology_mcp.layout_stats import analyze_layout_stats


def test_all_horizontal_scores_one() -> None:
    pos = {"a": (0.0, 0.0), "b": (200.0, 0.0), "c": (400.0, 2.0)}  # 2px ≈ H
    links = [("a", "b"), ("b", "c")]
    m = compute_edge_axis(pos, links)
    assert m["horiz_n"] == 2
    assert m["diag_n"] == 0
    assert float(m["edge_axis_score"]) == 1.0


def test_diagonal_scores_zero() -> None:
    pos = {"a": (0.0, 0.0), "b": (100.0, 100.0)}
    m = compute_edge_axis(pos, [("a", "b")])
    assert m["diag_n"] == 1
    assert float(m["edge_axis_score"]) == 0.0
    assert m["top_skew_edges"]


def test_horizontal_beats_vertical() -> None:
    h = compute_edge_axis(
        {"a": (0.0, 0.0), "b": (200.0, 0.0)},
        [("a", "b")],
    )
    v = compute_edge_axis(
        {"a": (0.0, 0.0), "b": (0.0, 200.0)},
        [("a", "b")],
    )
    assert float(h["edge_axis_score"]) > float(v["edge_axis_score"])
    assert float(v["edge_axis_score"]) == 0.75


def test_analyze_report_includes_edge_axis() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "b", "name": "B", "x": 100.0, "y": 100.0},
        {"fabric_node_id": "c", "name": "C", "x": 200.0, "y": 0.0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
    ]
    m = analyze_layout_stats(nodes, edges)
    assert "edge_axis" in m["score"]["parts"]
    assert m["report"]["edge_axis"]["status"] in {"warn", "fail"}
    assert int(m["diag_n"] or 0) >= 1
