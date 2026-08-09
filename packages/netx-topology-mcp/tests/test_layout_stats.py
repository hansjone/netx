"""Tests for topology layout statistics / composite score."""

from __future__ import annotations

from netx_topology_mcp.layout_stats import (
    analyze_layout_stats,
    build_layout_report,
    score_layout_components,
)


def test_dense_vs_sparse_score() -> None:
    # Compact 2x2 grid at recommended pitch
    dense_nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 0, "y": 170},
        {"fabric_node_id": "d", "name": "D", "x": 200, "y": 170},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "d"},
        {"a_node_id": "d", "b_node_id": "c"},
        {"a_node_id": "c", "b_node_id": "a"},
    ]
    dense = analyze_layout_stats(dense_nodes, edges)

    sparse_nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 20000, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 0, "y": 17000},
        {"fabric_node_id": "d", "name": "D", "x": 20000, "y": 17000},
    ]
    sparse = analyze_layout_stats(sparse_nodes, edges)

    assert dense["space_utilization"] > sparse["space_utilization"]
    assert dense["whitespace_index"] < sparse["whitespace_index"]
    assert dense["score"]["total"] > sparse["score"]["total"]
    assert dense["grid_occupancy"] > sparse["grid_occupancy"]


def test_overlap_hard_gates_score() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB", "x": 5, "y": 0},
    ]
    edges = [{"a_node_id": "a", "b_node_id": "b"}]
    m = analyze_layout_stats(nodes, edges)
    assert m["footprint_overlap_pairs"] >= 1
    assert m["score"]["parts"]["overlap"] == 0.0
    assert m["score"]["total"] < 20.0


def test_score_components_rank_key() -> None:
    good = {
        "node_count": 100,
        "edge_crossings": 10,
        "crossings_per_link": 0.05,
        "footprint_overlap_pairs": 0,
        "label_overlap_pairs": 0,
        "nn_p50": 170,
        "space_utilization": 0.2,
        "hull_utilization": 0.25,
        "grid_occupancy": 0.4,
        "edge_stretch_p50": 1.2,
        "whitespace_index": 0.2,
    }
    s = score_layout_components(good)
    assert s["total"] > 50
    assert s["rank_key"][0] == 0


def test_top_crossing_nodes_in_summary() -> None:
    # Classic X: AC and BD cross at center → all four endpoints participate.
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 200, "y": 200},
        {"fabric_node_id": "d", "name": "D", "x": 0, "y": 200},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "c"},
        {"a_node_id": "b", "b_node_id": "d"},
    ]
    m = analyze_layout_stats(nodes, edges)
    assert m["edge_crossings"] == 1
    top = m.get("top_crossing_nodes") or []
    assert len(top) == 4
    assert all(int(r["crossing_hits"]) >= 1 for r in top)
    assert (m.get("summary") or {}).get("top_crossing")
    assert (m.get("report") or {}).get("crossing", {}).get("top_nodes")
    top_e = m.get("top_crossing_edges") or []
    assert len(top_e) == 2
    assert all(int(r["crossing_hits"]) == 1 for r in top_e)
    assert (m.get("summary") or {}).get("top_crossing_edges")
    assert (m.get("report") or {}).get("crossing", {}).get("top_edges")


def test_unified_report_facets() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 0, "y": 170},
        {"fabric_node_id": "d", "name": "D", "x": 200, "y": 170},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "d"},
        {"a_node_id": "d", "b_node_id": "c"},
        {"a_node_id": "c", "b_node_id": "a"},
    ]
    full = analyze_layout_stats(nodes, edges)
    r = full["report"]
    assert set(r) >= {
        "verdict",
        "size",
        "overlap",
        "crossing",
        "spacing",
        "sparsity",
        "edges",
        "chains",
        "rings",
        "score",
        "guide",
    }
    assert r["overlap"]["status"] == "ok"
    assert r["sparsity"]["status"] in {"ok", "warn", "fail"}
    assert "chain" in r["score"]["parts"]
    assert "rings" in r["score"]["parts"]
    built = build_layout_report(full)
    assert built["verdict"]["total"] == r["verdict"]["total"]
