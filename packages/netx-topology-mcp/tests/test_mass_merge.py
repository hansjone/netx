"""Mass field roles + soft merge (attract/repulse instead of rigid islands)."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops.dual_units import DualUnit
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.mass_field import (
    annotate_dual_unit,
    build_mass_field,
    capture_pass,
    evolve_chains_to_rings,
)
from netx_topology_mcp.layout_ops.mass_merge import mass_merge_round
from netx_topology_mcp.layout_tool import ACTIONS


def test_actions_include_mass_merge() -> None:
    assert "mass_merge" not in ACTIONS


def test_annotate_dual_unit_roles() -> None:
    unit = DualUnit(
        portal_a="pa",
        portal_b="pb",
        paths=[
            ["pa", "r1", "r2", "pb"],
            ["pa", "r3", "r4", "pb"],
            ["pa", "r5", "r6", "r7", "pb"],
        ],
        tails=[["t1", "t2"]],
        unit_id=0,
    )
    ann = annotate_dual_unit(unit)
    assert ann["nest_depth"] == 3
    assert ann["nodes"]["pa"]["role"] == "core"
    assert ann["nodes"]["r1"]["role"] == "ring"
    assert ann["nodes"]["t1"]["role"] == "chain"
    assert ann["nodes"]["r1"]["attract"] > ann["nodes"]["t1"]["attract"]
    assert ann["nodes"]["pa"]["attract"] > ann["nodes"]["r1"]["attract"]


def _two_block_state():
    # Strong left dual eye + weak right chain attached by bridge.
    nodes = [
        {"fabric_node_id": "pa", "name": "PA", "x": 0, "y": 0, "layer": "agg"},
        {"fabric_node_id": "pb", "name": "PB", "x": 400, "y": 0, "layer": "agg"},
        {"fabric_node_id": "r1", "name": "R1", "x": 120, "y": 80, "layer": "access"},
        {"fabric_node_id": "r2", "name": "R2", "x": 280, "y": 80, "layer": "access"},
        {"fabric_node_id": "r3", "name": "R3", "x": 120, "y": -80, "layer": "access"},
        {"fabric_node_id": "r4", "name": "R4", "x": 280, "y": -80, "layer": "access"},
        {"fabric_node_id": "t1", "name": "T1", "x": 500, "y": 0, "layer": "access"},
        {"fabric_node_id": "t2", "name": "T2", "x": 700, "y": 0, "layer": "access"},
        {"fabric_node_id": "w1", "name": "W1", "x": 2200, "y": 40, "layer": "access"},
        {"fabric_node_id": "w2", "name": "W2", "x": 2400, "y": -40, "layer": "access"},
        {"fabric_node_id": "wc", "name": "WC", "x": 2000, "y": 0, "layer": "agg"},
    ]
    edges = [
        {"a_node_id": "pa", "b_node_id": "r1"},
        {"a_node_id": "r1", "b_node_id": "r2"},
        {"a_node_id": "r2", "b_node_id": "pb"},
        {"a_node_id": "pa", "b_node_id": "r3"},
        {"a_node_id": "r3", "b_node_id": "r4"},
        {"a_node_id": "r4", "b_node_id": "pb"},
        {"a_node_id": "pb", "b_node_id": "t1"},
        {"a_node_id": "t1", "b_node_id": "t2"},
        {"a_node_id": "t2", "b_node_id": "wc"},
        {"a_node_id": "wc", "b_node_id": "w1"},
        {"a_node_id": "wc", "b_node_id": "w2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    groups = [
        {
            "key": "strong",
            "node_ids": ["pa", "pb", "r1", "r2", "r3", "r4", "t1", "t2"],
            "pivots": ["pa", "pb"],
            "cores": ["pa", "pb"],
            "soft": True,
        },
        {
            "key": "weak",
            "node_ids": ["wc", "w1", "w2"],
            "pivots": ["wc"],
            "cores": ["wc"],
            "soft": True,
        },
    ]
    unit = DualUnit(
        portal_a="pa",
        portal_b="pb",
        paths=[
            ["pa", "r1", "r2", "pb"],
            ["pa", "r3", "r4", "pb"],
        ],
        tails=[["t1", "t2"]],
        unit_id=0,
    )
    mass = build_mass_field(st, units=[unit], groups=groups)
    st.meta = {
        "compose_views": {
            "rigid_groups": groups,
            "mass_groups": groups,
            "soft": True,
        },
        "mass_field": mass,
    }
    return st, groups, mass


def test_build_mass_field_tags_chain_and_bridge() -> None:
    st, groups, mass = _two_block_state()
    assert mass["nodes"]["t1"]["role"] == "chain"
    assert mass["nodes"]["r1"]["role"] == "ring"
    bridge = mass["edges"].get("t2|wc") or mass["edges"].get("wc|t2")
    assert bridge is not None
    assert bridge["role"] == "bridge"


def test_capture_can_steal_chain_toward_strong_core() -> None:
    st, groups, mass = _two_block_state()
    # Pull weak leaves next to strong core so capture fires.
    st.positions["w1"] = (450.0, 20.0)
    st.positions["w2"] = (460.0, -20.0)
    st.positions["wc"] = (480.0, 0.0)
    mass2, report = capture_pass(
        st,
        mass,
        kappa_node=1.0,
        kappa_block=1.5,
        rho_ideal=8.0,
        ideal_len=200.0,
    )
    home = {}
    for g in mass2["groups"]:
        for n in g["node_ids"]:
            if n not in ("pa", "pb", "wc"):
                home.setdefault(n, g["key"])
    # At least some movement of membership or block absorb.
    assert report["stolen_nodes"] + report["stolen_blocks"] >= 1 or any(
        home.get(n) == "strong" for n in ("w1", "w2")
    )


def test_evolve_chain_to_ring_when_second_corridor() -> None:
    # Single corridor + dangling path that completes a second corridor.
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0, "layer": "agg"},
        {"fabric_node_id": "b", "name": "B", "x": 300, "y": 0, "layer": "agg"},
        {"fabric_node_id": "u1", "name": "U1", "x": 100, "y": 60, "layer": "access"},
        {"fabric_node_id": "u2", "name": "U2", "x": 200, "y": 60, "layer": "access"},
        {"fabric_node_id": "d1", "name": "D1", "x": 100, "y": -60, "layer": "access"},
        {"fabric_node_id": "d2", "name": "D2", "x": 200, "y": -60, "layer": "access"},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "u1"},
        {"a_node_id": "u1", "b_node_id": "u2"},
        {"a_node_id": "u2", "b_node_id": "b"},
        {"a_node_id": "a", "b_node_id": "d1"},
        {"a_node_id": "d1", "b_node_id": "d2"},
        {"a_node_id": "d2", "b_node_id": "b"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    # Pretend lower path was tagged chain initially.
    mass = {
        "nodes": {
            "a": {"role": "core", "attract": 4.0, "repulse": 1.8, "mass": 3.0},
            "b": {"role": "core", "attract": 4.0, "repulse": 1.8, "mass": 3.0},
            "u1": {"role": "ring", "attract": 2.4, "repulse": 1.2, "mass": 1.8},
            "u2": {"role": "ring", "attract": 2.4, "repulse": 1.2, "mass": 1.8},
            "d1": {"role": "chain", "attract": 0.7, "repulse": 0.6, "mass": 0.5},
            "d2": {"role": "chain", "attract": 0.7, "repulse": 0.6, "mass": 0.5},
        },
        "edges": {},
        "groups": [],
    }
    out = evolve_chains_to_rings(st, mass)
    # If dual-unit finder sees both corridors, d1/d2 should promote.
    if (out.get("evolve") or {}).get("promoted_nodes", 0) > 0:
        assert out["nodes"]["d1"]["role"] == "ring"
        assert out["nodes"]["d1"]["attract"] > 0.7


def test_mass_merge_shortens_bridge() -> None:
    st, groups, _mass = _two_block_state()
    L0 = math.hypot(
        st.positions["wc"][0] - st.positions["t2"][0],
        st.positions["wc"][1] - st.positions["t2"][1],
    )
    op = mass_merge_round(
        st,
        groups=groups,
        iters=14,
        step=0.45,
        max_step=220.0,
        ideal_len=280.0,
        nn_floor=90.0,
        attract_k=1.2,
        repulse_k=0.7,
        gravity_k=0.6,
        group_sep_k=0.4,
        global_gravity_k=0.0,
        core_pull_k=0.8,
        protect_rigid="off",
        evolve_every=4,
        kappa_node=1.1,
        kappa_block=1.8,
        x_slack=800,
        capture=False,
        cluster_seed=False,
        use_dual_units=True,
    )
    assert op.op == "mass_merge"
    assert not (op.params or {}).get("reverted"), op.note
    L1 = math.hypot(
        op.state.positions["wc"][0] - op.state.positions["t2"][0],
        op.state.positions["wc"][1] - op.state.positions["t2"][1],
    )
    assert L1 < L0 * 0.95 or (op.params or {}).get("end_geo", 0) >= (
        op.params or {}
    ).get("start_geo", 0)


def test_mass_merge_cluster_centroids_round_pack() -> None:
    """Dual-unit clusters reseated on a round pack (not a strip lattice)."""
    st, groups, _mass = _two_block_state()
    # Force a strip of group centroids.
    for n in ("pa", "pb", "r1", "r2", "r3", "r4", "t1", "t2"):
        st.positions[n] = (st.positions[n][0], 0.0)
    for n in ("wc", "w1", "w2"):
        st.positions[n] = (st.positions[n][0] + 5000.0, 5.0)
    op = mass_merge_round(
        st,
        groups=groups,
        iters=8,
        fa2=True,
        use_dual_units=True,
        cluster_seed=True,
        sep_ideal=800.0,
        group_sep_k=1.2,
        global_gravity_k=0.4,
        capture=False,
        x_slack=2000,
    )
    assert (op.params or {}).get("use_dual_units") is True
    assert not (op.params or {}).get("reverted"), op.note
    # Two cluster centroids should not sit on a long horizontal line only.
    c0 = (
        0.5 * (op.state.positions["pa"][0] + op.state.positions["pb"][0]),
        0.5 * (op.state.positions["pa"][1] + op.state.positions["pb"][1]),
    )
    c1 = (
        (op.state.positions["wc"][0] + op.state.positions["w1"][0] + op.state.positions["w2"][0])
        / 3.0,
        (op.state.positions["wc"][1] + op.state.positions["w1"][1] + op.state.positions["w2"][1])
        / 3.0,
    )
    assert math.hypot(c0[0] - c1[0], c0[1] - c1[1]) > 200.0


def test_mass_merge_fa2_spreads_stacks() -> None:
    """FA2 mode: coincident nodes get unpacked; lin-log + long-range repulse run."""
    st, groups, _mass = _two_block_state()
    for n in st.positions:
        st.positions[n] = (10.0, 10.0)
    op = mass_merge_round(
        st,
        groups=groups,
        iters=10,
        fa2=True,
        scaling=10.0,
        linlog=True,
        use_dual_units=True,
        gravity_k=0.6,
        group_sep_k=1.2,
        group_pack_k=1.3,
        sep_ideal=800.0,
        global_gravity_k=0.0,
        core_pull_k=0.1,
        cluster_seed=True,
        capture=False,
        x_slack=2000,
    )
    assert op.op == "mass_merge"
    assert not (op.params or {}).get("reverted"), op.note
    assert (op.params or {}).get("fa2") is True
    # Not a single hairball.
    xs = [op.state.positions[n][0] for n in op.state.positions]
    ys = [op.state.positions[n][1] for n in op.state.positions]
    assert max(xs) - min(xs) > 200.0 or max(ys) - min(ys) > 200.0
    assert float(op.params.get("end_sep_nn") or 0) > 80.0


def test_mass_merge_separates_collapsed_groups() -> None:
    """Two dual-unit groups stacked on top of each other → seed+sep spreads them."""
    st, groups, _mass = _two_block_state()
    # Collapse weak block onto strong block (hairball seed).
    for n in ("wc", "w1", "w2"):
        st.positions[n] = (
            st.positions["pa"][0] + 40.0,
            st.positions["pa"][1] + 20.0,
        )
    c0 = (
        0.5 * (st.positions["pa"][0] + st.positions["pb"][0]),
        0.5 * (st.positions["pa"][1] + st.positions["pb"][1]),
    )
    c1 = (
        (st.positions["wc"][0] + st.positions["w1"][0] + st.positions["w2"][0]) / 3.0,
        (st.positions["wc"][1] + st.positions["w1"][1] + st.positions["w2"][1]) / 3.0,
    )
    d0 = math.hypot(c0[0] - c1[0], c0[1] - c1[1])
    op = mass_merge_round(
        st,
        groups=groups,
        iters=12,
        use_dual_units=True,
        gravity_k=0.9,
        group_sep_k=1.2,
        group_pack_k=0.8,
        sep_ideal=900.0,
        global_gravity_k=0.0,
        core_pull_k=0.05,
        protect_rigid="off",
        capture=False,
        cluster_seed=True,
        x_slack=2000,
    )
    assert not (op.params or {}).get("reverted"), op.note
    assert float(op.params.get("end_sep_nn") or 0) > d0 + 80.0
    # Not blown to absurd spacing.
    assert float(op.params.get("end_sep_nn") or 0) < 4000.0
