"""Tests for gated compact_bbox."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.compact_bbox import compact_bbox
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.orbit_sweep import _has_any_footprint_overlap


def test_compact_bbox_shrinks_without_raising_crossings():
    # Two portals + a far leaf; shrink should pull leaf inward.
    nodes = [
        {"fabric_node_id": "p1", "name": "P1", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "p2", "name": "P2", "x": 1000.0, "y": 0.0},
        {"fabric_node_id": "a", "name": "A", "x": 500.0, "y": 0.0},
        {"fabric_node_id": "leaf", "name": "Leaf", "x": 500.0, "y": 4000.0},
    ]
    edges = [
        {"a_fabric_node_id": "p1", "b_fabric_node_id": "a"},
        {"a_fabric_node_id": "a", "b_fabric_node_id": "p2"},
        {"a_fabric_node_id": "a", "b_fabric_node_id": "leaf"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    g0 = count_edge_crossings(st.positions, st.links)
    op = compact_bbox(
        st,
        portal_ids=["p1", "p2"],
        min_scale=0.7,
        step=0.05,
        max_clearance_slack=50,
    )
    assert op.params.get("accepted") is True
    assert op.params.get("scale", 1.0) < 1.0
    assert abs(op.state.positions["p1"][0] - 0.0) < 1e-6
    assert abs(op.state.positions["p2"][0] - 1000.0) < 1e-6
    assert op.state.positions["leaf"][1] < 4000.0
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    assert g1 <= g0
    assert not _has_any_footprint_overlap(op.state.positions, op.state.names)
