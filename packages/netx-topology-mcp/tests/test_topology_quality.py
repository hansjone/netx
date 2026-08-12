"""Tests for chain cohesion + min-ring integrity mid-tier metrics."""

from __future__ import annotations

from netx_topology_mcp.layout_stats import analyze_layout_stats, score_layout_components
from netx_topology_mcp.layout_topology_quality import (
    compute_chain_cohesion,
    compute_ring_integrity,
    extract_chain_paths,
)


def _pos_links_from_nodes(nodes, edges):
    pos = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    links = [(e["a_node_id"], e["b_node_id"]) for e in edges]
    return pos, links


def test_straight_chain_scores_high() -> None:
    # hub + straight corridor
    nodes = [
        {"fabric_node_id": "h", "name": "H", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "A", "x": 200, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 400, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 600, "y": 0},
    ]
    edges = [
        {"a_node_id": "h", "b_node_id": "a"},
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
    ]
    pos, links = _pos_links_from_nodes(nodes, edges)
    from collections import defaultdict

    adj: dict[str, set[str]] = defaultdict(set)
    for a, b in links:
        adj[a].add(b)
        adj[b].add(a)
    assert extract_chain_paths(adj)
    q = compute_chain_cohesion(pos, links)
    assert q["score"] >= 0.9
    assert q["kink_count"] == 0


def test_kinked_chain_scores_lower() -> None:
    nodes = [
        {"fabric_node_id": "h", "name": "H", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "A", "x": 200, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 400, "y": 200},
        {"fabric_node_id": "c", "name": "C", "x": 600, "y": 0},
    ]
    edges = [
        {"a_node_id": "h", "b_node_id": "a"},
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
    ]
    pos, links = _pos_links_from_nodes(nodes, edges)
    bent = compute_chain_cohesion(pos, links)
    # straighten
    nodes2 = [
        {"fabric_node_id": "h", "name": "H", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "A", "x": 200, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 400, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 600, "y": 0},
    ]
    pos2, _ = _pos_links_from_nodes(nodes2, edges)
    straight = compute_chain_cohesion(pos2, links)
    assert straight["score"] > bent["score"]
    assert bent["kink_count"] >= 1


def test_ring_pierce_detected() -> None:
    # Square cycle + a chord that crosses through (diagonal would share verts;
    # use an external edge that crosses one side).
    # Cycle: a-b-c-d-a. External: e--f crosses a-b vertically.
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 200, "y": 200},
        {"fabric_node_id": "d", "name": "D", "x": 0, "y": 200},
        {"fabric_node_id": "e", "name": "E", "x": 100, "y": -50},
        {"fabric_node_id": "f", "name": "F", "x": 100, "y": 50},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "c", "b_node_id": "d"},
        {"a_node_id": "d", "b_node_id": "a"},
        {"a_node_id": "e", "b_node_id": "f"},
    ]
    pos, links = _pos_links_from_nodes(nodes, edges)
    pierced = compute_ring_integrity(pos, links)
    assert pierced["ring_count"] >= 1
    assert pierced["rings_pierced"] >= 1
    assert pierced["score"] < 1.0

    # move e-f away → no pierce
    nodes2 = [
        *nodes[:4],
        {"fabric_node_id": "e", "name": "E", "x": 300, "y": -50},
        {"fabric_node_id": "f", "name": "F", "x": 300, "y": 50},
    ]
    pos2, _ = _pos_links_from_nodes(nodes2, edges)
    clean = compute_ring_integrity(pos2, links)
    assert clean["rings_pierced"] == 0
    assert clean["score"] == 1.0


def test_score_includes_mid_tier_weights() -> None:
    s = score_layout_components(
        {
            "node_count": 40,
            "edge_crossings": 0,
            "crossings_per_link": 0.0,
            "footprint_overlap_pairs": 0,
            "label_overlap_pairs": 0,
            "nn_p50": 170,
            "space_utilization": 0.2,
            "hull_utilization": 0.25,
            "grid_occupancy": 0.4,
            "edge_stretch_p50": 1.2,
            "whitespace_index": 0.2,
            "chain_score": 0.2,
            "rings_score": 0.2,
            "edge_clearance_score": 0.2,
            "edge_axis_score": 0.2,
        }
    )
    assert "chain" in s["parts"]
    assert "rings" in s["parts"]
    assert "edge_clearance" in s["parts"]
    assert "edge_axis" in s["parts"]
    assert abs(s["weights"]["chain"] - 0.10) < 1e-9
    assert abs(sum(s["weights"].values()) - 1.0) < 1e-9
    # default profile: rings down, clearance up vs legacy 0.10/0.08
    assert s["weights"]["rings"] <= 0.08
    assert s["weights"]["edge_clearance"] >= 0.08
    eye = score_layout_components(
        {
            "node_count": 200,
            "edge_crossings": 50,
            "crossings_per_link": 0.15,
            "footprint_overlap_pairs": 0,
            "label_overlap_pairs": 0,
            "nn_p50": 170,
            "space_utilization": 0.05,
            "hull_utilization": 0.09,
            "grid_occupancy": 0.05,
            "edge_stretch_p50": 1.2,
            "whitespace_index": 0.5,
            "chain_score": 0.6,
            "rings_score": 0.3,
            "edge_clearance_score": 0.5,
            "edge_axis_score": 0.3,
            "axis_frac": 0.35,
        },
        score_profile="eye",
    )
    assert eye["score_profile"] == "eye"
    assert eye["weights"]["edge_axis"] < s["weights"]["edge_axis"]
    good = score_layout_components(
        {
            "node_count": 40,
            "edge_crossings": 0,
            "crossings_per_link": 0.0,
            "footprint_overlap_pairs": 0,
            "label_overlap_pairs": 0,
            "nn_p50": 170,
            "space_utilization": 0.2,
            "hull_utilization": 0.25,
            "grid_occupancy": 0.4,
            "edge_stretch_p50": 1.2,
            "whitespace_index": 0.2,
            "chain_score": 1.0,
            "rings_score": 1.0,
            "edge_clearance_score": 1.0,
            "edge_axis_score": 1.0,
        }
    )
    assert good["total"] > s["total"]


def test_report_exposes_chains_and_rings() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 400, "y": 0},
        {"fabric_node_id": "d", "name": "D", "x": 200, "y": 200},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "b", "b_node_id": "d"},
    ]
    r = analyze_layout_stats(nodes, edges)["report"]
    assert "chains" in r
    assert "rings" in r
    assert "chain" in r["score"]["parts"]
    assert "rings" in r["score"]["parts"]
    assert r["chains"]["status"] in {"ok", "warn", "fail"}
    assert r["rings"]["status"] in {"ok", "warn", "fail"}
