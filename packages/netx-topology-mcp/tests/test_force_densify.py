"""Force densify: edge attract + local repulse + semi-rigid bodies."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops.force_densify import force_densify_round
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.rigid_units import groups_from_membership
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_tool import ACTIONS


def test_actions_include_force_densify() -> None:
    assert "force_densify" not in ACTIONS


def _sparse_two_units():
    nodes = [
        {"fabric_node_id": "p", "name": "P", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "A1", "x": 120, "y": 40},
        {"fabric_node_id": "a2", "name": "A2", "x": 240, "y": 0},
        {"fabric_node_id": "b1", "name": "B1", "x": 4200, "y": 0},
        {"fabric_node_id": "b2", "name": "B2", "x": 4400, "y": 80},
        {"fabric_node_id": "b3", "name": "B3", "x": 4600, "y": -60},
    ]
    edges = [
        {"a_node_id": "p", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b1", "b_node_id": "b3"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    groups = groups_from_membership(
        [
            ("va", ["p", "a1", "a2"]),
            ("vb", ["b1", "b2", "b3"]),
        ]
    )
    st.meta = {"compose_views": {"rigid_groups": groups}}
    return st, groups


def test_force_densify_shrinks_bridge() -> None:
    st, groups = _sparse_two_units()
    L0 = math.hypot(
        st.positions["b1"][0] - st.positions["a2"][0],
        st.positions["b1"][1] - st.positions["a2"][1],
    )
    before = score_state(st, fast=True)
    util0 = float((before.get("summary") or {}).get("util") or 0.0)

    op = force_densify_round(
        st,
        groups=groups,
        iters=16,
        step=0.5,
        max_step=280.0,
        ideal_len=280.0,
        nn_floor=90.0,
        attract_k=1.4,
        repulse_k=0.6,
        gravity_k=0.9,
        rigid_strength=0.95,
        deform=0.05,
        protect_rigid="off",
        x_slack=500,
    )
    assert op.op == "force_densify"
    assert not (op.params or {}).get("reverted"), op.note
    L1 = math.hypot(
        op.state.positions["b1"][0] - op.state.positions["a2"][0],
        op.state.positions["b1"][1] - op.state.positions["a2"][1],
    )
    assert L1 < L0 * 0.92, f"bridge not shortened: {L0:.0f}->{L1:.0f}"
    after = score_state(op.state, fast=True)
    util1 = float((after.get("summary") or {}).get("util") or 0.0)
    assert util1 >= util0 - 1e-6


def test_edge_spring_pushes_crushed_edge() -> None:
    """Too-short edge should lengthen (push), not only pull long ones."""
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 40, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 2000, "y": 0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    L0 = math.hypot(
        st.positions["b"][0] - st.positions["a"][0],
        st.positions["b"][1] - st.positions["a"][1],
    )
    op = force_densify_round(
        st,
        groups=[],
        iters=12,
        step=0.45,
        max_step=80.0,
        ideal_len=160.0,
        nn_floor=80.0,
        attract_k=1.5,
        repulse_k=0.4,
        gravity_k=0.0,
        rigid_strength=0.0,
        deform=0.0,
        protect_rigid="off",
        x_slack=200,
    )
    L1 = math.hypot(
        op.state.positions["b"][0] - op.state.positions["a"][0],
        op.state.positions["b"][1] - op.state.positions["a"][1],
    )
    assert L1 > L0 + 15.0, f"crushed edge not pushed: {L0:.1f}->{L1:.1f}"


def test_semi_rigid_preserves_internal_ratios() -> None:
    st, groups = _sparse_two_units()

    def _pair(pos):
        d12 = math.hypot(pos["b2"][0] - pos["b1"][0], pos["b2"][1] - pos["b1"][1])
        d13 = math.hypot(pos["b3"][0] - pos["b1"][0], pos["b3"][1] - pos["b1"][1])
        return d12 / max(d13, 1e-6)

    r0 = _pair(st.positions)
    op = force_densify_round(
        st,
        groups=groups,
        iters=10,
        rigid_strength=1.0,
        deform=0.0,
        ideal_len=180.0,
        attract_k=1.2,
        x_slack=500,
    )
    r1 = _pair(op.state.positions)
    assert abs(r1 - r0) < 0.05, f"rigid shape drifted: {r0:.3f}->{r1:.3f}"
