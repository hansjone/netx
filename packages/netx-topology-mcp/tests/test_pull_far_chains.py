"""Tests for gated pull_far_chains."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.orbit_sweep import _has_any_footprint_overlap
from netx_topology_mcp.layout_ops.pull_far_chains import pull_far_chains


def test_pull_far_chains_shortens_corridor_without_raising_crossings():
    # Portal pair + hub + long deg-2 tail stretching south.
    nodes = [
        {"fabric_node_id": "p1", "name": "P1", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "p2", "name": "P2", "x": 1000.0, "y": 0.0},
        {"fabric_node_id": "hub", "name": "Hub", "x": 500.0, "y": 200.0},
        {"fabric_node_id": "a", "name": "A", "x": 500.0, "y": 1200.0},
        {"fabric_node_id": "b", "name": "B", "x": 500.0, "y": 2400.0},
        {"fabric_node_id": "c", "name": "C", "x": 500.0, "y": 3600.0},
        {"fabric_node_id": "tip", "name": "Tip", "x": 500.0, "y": 4800.0},
    ]
    edges = [
        {"a_node_id": "p1", "b_node_id": "hub"},
        {"a_node_id": "p2", "b_node_id": "hub"},
        {"a_node_id": "hub", "b_node_id": "a"},
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "c", "b_node_id": "tip"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    g0 = count_edge_crossings(st.positions, st.links)
    y0 = st.positions["tip"][1]
    area0 = abs(
        (max(p[0] for p in st.positions.values()) - min(p[0] for p in st.positions.values()))
        * (max(p[1] for p in st.positions.values()) - min(p[1] for p in st.positions.values()))
    )
    op = pull_far_chains(
        st,
        portal_ids=["p1", "p2"],
        max_chains=4,
        min_tip_radius=1000.0,
        scales=(0.9, 0.8, 0.7),
        max_clearance_slack=100,
    )
    assert op.params.get("chains_accepted", 0) >= 1
    assert op.state.positions["tip"][1] < y0
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    assert g1 <= g0
    assert not _has_any_footprint_overlap(op.state.positions, op.state.names)
    area1 = abs(
        (
            max(p[0] for p in op.state.positions.values())
            - min(p[0] for p in op.state.positions.values())
        )
        * (
            max(p[1] for p in op.state.positions.values())
            - min(p[1] for p in op.state.positions.values())
        )
    )
    assert area1 < area0


def test_pull_far_chains_moves_isolates():
    nodes = [
        {"fabric_node_id": "p1", "name": "P1", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "p2", "name": "P2", "x": 1000.0, "y": 0.0},
        {"fabric_node_id": "iso", "name": "Iso", "x": 500.0, "y": 5000.0},
    ]
    edges = [
        {"a_node_id": "p1", "b_node_id": "p2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    y0 = st.positions["iso"][1]
    op = pull_far_chains(
        st,
        portal_ids=["p1", "p2"],
        max_chains=4,
        min_tip_radius=1000.0,
        scales=(0.8, 0.7),
        max_clearance_slack=100,
        pull_isolates=True,
    )
    assert op.params.get("isolates_pulled", 0) >= 1
    assert op.state.positions["iso"][1] < y0
