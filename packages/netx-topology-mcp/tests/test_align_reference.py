"""Tests for align_to_reference."""

from __future__ import annotations

from netx_topology_mcp.layout_ops.align_reference import align_to_reference
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges


def test_align_similarity_maps_shared_nodes_to_portal_frame():
    # Target: portals at 0 and 1000. Reference: same topology scaled/rotated.
    nodes = [
        {"fabric_node_id": "p1", "name": "P1", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "p2", "name": "P2", "x": 1000.0, "y": 0.0},
        {"fabric_node_id": "a", "name": "A", "x": 500.0, "y": 2000.0},
    ]
    edges = [
        {"a_node_id": "p1", "b_node_id": "a"},
        {"a_node_id": "a", "b_node_id": "p2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    # Reference: portals horizontal length 500, leaf above mid.
    ref = {
        "p1": (10.0, 10.0),
        "p2": (510.0, 10.0),
        "a": (260.0, 210.0),
    }
    op = align_to_reference(
        st,
        reference=ref,
        portal_ids=["p1", "p2"],
        mode="similarity",
        freeze_portals=True,
    )
    assert op.params.get("shared_n") == 3
    assert abs(op.state.positions["p1"][0] - 0.0) < 1e-6
    assert abs(op.state.positions["p2"][0] - 1000.0) < 1e-6
    # Leaf should land near mid-x, positive y (scaled 2x from ref dy=200 → 400)
    assert abs(op.state.positions["a"][0] - 500.0) < 1.0
    assert op.state.positions["a"][1] > 100.0


def test_align_adopt_copies_reference_coords():
    nodes = [
        {"fabric_node_id": "p1", "name": "P1", "x": 999.0, "y": 999.0},
        {"fabric_node_id": "p2", "name": "P2", "x": 1999.0, "y": 999.0},
        {"fabric_node_id": "a", "name": "A", "x": 1500.0, "y": 1500.0},
    ]
    edges = [{"a_node_id": "p1", "b_node_id": "p2"}]
    st = build_state_from_nodes_edges(nodes, edges)
    ref = {"p1": (100.0, 200.0), "p2": (300.0, 200.0), "a": (200.0, 400.0)}
    op = align_to_reference(st, reference=ref, mode="adopt", park_missing=False)
    # origin normalized: min was 100,200 → pad 40
    assert abs(op.state.positions["p1"][0] - 40.0) < 1e-6
    assert abs(op.state.positions["p1"][1] - 40.0) < 1e-6
    assert abs(op.state.positions["a"][1] - 240.0) < 1e-6
