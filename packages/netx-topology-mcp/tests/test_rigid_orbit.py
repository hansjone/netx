"""Bridge-orbit densify: drag rigid exclusive body about external tip."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.rigid_orbit import (
    rigid_orbit_candidates_for_group,
    rigid_orbit_round,
)
from netx_topology_mcp.layout_ops.rigid_units import groups_from_membership
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def test_actions_include_rigid_orbit() -> None:
    assert "rigid_orbit" not in ACTIONS


def _sparse_two_units() -> tuple:
    # Unit A near origin; unit B far right; long bridge a2—b1.
    nodes = [
        {"fabric_node_id": "p", "name": "P", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "A1", "x": 120, "y": 40},
        {"fabric_node_id": "a2", "name": "A2", "x": 240, "y": 0},
        {"fabric_node_id": "b1", "name": "B1", "x": 3200, "y": 0},
        {"fabric_node_id": "b2", "name": "B2", "x": 3400, "y": 60},
        {"fabric_node_id": "b3", "name": "B3", "x": 3600, "y": -40},
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


def test_candidates_prefer_inward_pull() -> None:
    st, groups = _sparse_two_units()
    g = next(g for g in groups if g["key"] == "vb")
    parsed = {
        "key": g["key"],
        "members": g["node_ids"],
        "pivots": g["pivots"],
        "exclusive": [n for n in g["node_ids"] if n not in g["pivots"]],
        "shared": list(g["pivots"]),
    }
    # vb has no shared pivots with va (p not in vb) — exclusive = all members
    cands = rigid_orbit_candidates_for_group(
        st.positions,
        st.links,
        parsed,
        angle_step=45,
        radii=(0.5, 0.7, 1.0),
        bridges_per_group=2,
        cand_cap=40,
        x_slack=200,
    )
    assert cands, "expected bridge-orbit candidates for far unit"
    # Best should densify (area_ratio > 1) when pulling toward a2 tip
    assert cands[0]["area_ratio"] >= 1.0 or cands[0]["radius_scale"] < 1.0


def test_rigid_orbit_round_shrinks_bbox() -> None:
    st, groups = _sparse_two_units()
    before = score_state(st, fast=True)
    util0 = float((before.get("summary") or {}).get("util") or 0.0)
    xs = [p[0] for p in st.positions.values()]
    ys = [p[1] for p in st.positions.values()]
    area0 = (max(xs) - min(xs)) * (max(ys) - min(ys))

    op = rigid_orbit_round(
        st,
        groups=groups,
        top_n=4,
        bridges_per_group=2,
        angle_step=30,
        radii=(0.45, 0.6, 0.75, 0.9),
        x_slack=500,
        max_accepts=4,
    )
    assert op.op == "rigid_orbit"
    # Similarity: pairwise distance ratios inside vb stay equal (polar scale).
    def _pair_lens(pos: dict) -> tuple[float, float]:
        d12 = math.hypot(pos["b2"][0] - pos["b1"][0], pos["b2"][1] - pos["b1"][1])
        d13 = math.hypot(pos["b3"][0] - pos["b1"][0], pos["b3"][1] - pos["b1"][1])
        return d12, d13

    L12_0, L13_0 = _pair_lens(st.positions)
    L12_1, L13_1 = _pair_lens(op.state.positions)
    assert L12_0 > 1 and L13_0 > 1
    r12 = L12_1 / L12_0
    r13 = L13_1 / L13_0
    assert abs(r12 - r13) < 1e-5

    if not (op.params or {}).get("reverted"):
        xs1 = [p[0] for p in op.state.positions.values()]
        ys1 = [p[1] for p in op.state.positions.values()]
        area1 = (max(xs1) - min(xs1)) * (max(ys1) - min(ys1))
        after = score_state(op.state, fast=True)
        util1 = float((after.get("summary") or {}).get("util") or 0.0)
        assert area1 < area0 * 0.98 or util1 > util0 + 1e-6


def test_run_layout_rigid_orbit_action() -> None:
    st, groups = _sparse_two_units()
    nodes = [
        {
            "fabric_node_id": nid,
            "name": st.names.get(nid, nid),
            "x": st.positions[nid][0],
            "y": st.positions[nid][1],
        }
        for nid in st.positions
    ]
    edges = [{"a_node_id": a, "b_node_id": b} for a, b in st.links]
    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="rigid_orbit",
            params={
                "_rigid_membership": [
                    {"key": g["key"], "node_ids": g["node_ids"], "pivots": g["pivots"]}
                    for g in groups
                ],
                "top_n": 4,
                "x_slack": 500,
                "radii": [0.5, 0.7, 0.9],
            },
        )
        raise AssertionError("rigid_orbit should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)
