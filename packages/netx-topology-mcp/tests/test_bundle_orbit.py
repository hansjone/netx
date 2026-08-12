"""Tests for chain / ring+chain bundle orbit (contract → sweep → expand)."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.bundle_orbit import (
    detect_chain_bundles,
    detect_ring_chain_bundles,
    orbit_bundle,
    apply_bundle_pick,
    bundle_orbit_until_progress,
)
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.orbit_sweep import (
    orbit_params_from_overrides,
    orbit_sweep_until_limit,
)


def _crossed_chain():
    """Hub H with chain leaf that pierces a long chord a—b."""
    # a———b horizontal at y=200; vertical chain crosses it between c1 and c2.
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0.0, "y": 200.0},
        {"fabric_node_id": "b", "name": "B", "x": 2000.0, "y": 200.0},
        {"fabric_node_id": "h", "name": "H", "x": 1000.0, "y": 0.0},
        {"fabric_node_id": "c1", "name": "C1", "x": 1000.0, "y": 80.0},
        {"fabric_node_id": "c2", "name": "C2", "x": 1000.0, "y": 320.0},
        {"fabric_node_id": "c3", "name": "C3", "x": 1000.0, "y": 480.0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "h", "b_node_id": "c1"},
        {"a_node_id": "c1", "b_node_id": "c2"},
        {"a_node_id": "c2", "b_node_id": "c3"},
    ]
    return nodes, edges


def _triangle_with_chain():
    """Triangle ABC with dangling chain off A that crosses foreign chord."""
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 400.0, "y": 0.0},
        {"fabric_node_id": "b", "name": "B", "x": 0.0, "y": 300.0},
        {"fabric_node_id": "c", "name": "C", "x": 800.0, "y": 300.0},
        {"fabric_node_id": "d1", "name": "D1", "x": 400.0, "y": 200.0},
        {"fabric_node_id": "d2", "name": "D2", "x": 400.0, "y": 400.0},
        {"fabric_node_id": "x", "name": "X", "x": 0.0, "y": 200.0},
        {"fabric_node_id": "y", "name": "Y", "x": 800.0, "y": 200.0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "c", "b_node_id": "a"},
        {"a_node_id": "a", "b_node_id": "d1"},
        {"a_node_id": "d1", "b_node_id": "d2"},
        {"a_node_id": "x", "b_node_id": "y"},
    ]
    return nodes, edges


def test_detect_chain_bundle() -> None:
    nodes, edges = _crossed_chain()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    bundles = detect_chain_bundles(st.adj, st.positions, frozen={"h"})
    assert bundles
    members = set(bundles[0].member_ids)
    assert "c1" in members and "c3" in members
    assert "h" not in members


def test_chain_bundle_orbit_cuts_crossing() -> None:
    nodes, edges = _crossed_chain()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    g0 = count_edge_crossings(st.positions, st.links)
    assert g0 >= 1
    bundles = detect_chain_bundles(st.adj, st.positions, frozen={"h", "a", "b"})
    assert bundles
    sweep = orbit_bundle(st, bundles[0], max_jump=2500, cand_cap=300, nn_floor=10.0)
    assert sweep["ok"] is True
    assert sweep.get("expand_mode") == "minimize_probe"
    assert int(sweep.get("improving_n") or 0) >= 1
    best = sweep["candidates"][0]
    assert "placements" in best
    assert float(best.get("expand_scale") or 1.0) <= 1.0
    op = apply_bundle_pick(st, bundles[0], sweep, pick=1)
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    assert g1 < g0
    # Expand must not raise crossings vs before apply
    assert g1 <= g0


def test_expand_refuses_if_crossings_rise() -> None:
    nodes, edges = _crossed_chain()
    st = build_state_from_nodes_edges(nodes, edges)
    # Start cleared (no crossing), then force a bad expand back onto the chord.
    st.positions = {
        "a": (0.0, 200.0),
        "b": (2000.0, 200.0),
        "h": (1000.0, 0.0),
        "c1": (1400.0, 80.0),
        "c2": (1400.0, 200.0),
        "c3": (1400.0, 320.0),
    }
    g0 = count_edge_crossings(st.positions, st.links)
    assert g0 == 0
    bundles = detect_chain_bundles(st.adj, st.positions, frozen={"h", "a", "b"})
    assert bundles
    bad = {
        "candidates": [
            {
                "rank": 1,
                "delta": {"global": -1},
                "crossings": {"global": 0},
                "expand_scale": 1.0,
                "placements": {
                    "c1": (1000.0, 80.0),
                    "c2": (1000.0, 320.0),
                    "c3": (1000.0, 480.0),
                },
            }
        ]
    }
    op = apply_bundle_pick(st, bundles[0], bad, pick=1)
    assert not op.moved
    assert (op.params or {}).get("error") == "expand_raises_crossings"
    assert count_edge_crossings(st.positions, st.links) == g0


def test_ring_chain_detect() -> None:
    nodes, edges = _triangle_with_chain()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    bundles = detect_ring_chain_bundles(st, frozen={"b", "c"})
    assert any(b.kind == "ring_chain" for b in bundles)
    tip = next(b for b in bundles if b.kind == "ring_chain")
    assert tip.tip_id == "a"
    assert "d1" in tip.member_ids or "d2" in tip.member_ids
    assert set(tip.base_ids) >= {"b", "c"}


def test_bundle_until_progress() -> None:
    nodes, edges = _crossed_chain()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    g0 = count_edge_crossings(st.positions, st.links)
    op = bundle_orbit_until_progress(
        st, frozen_ids={"h", "a", "b"}, max_jump=2500, nn_floor=10.0
    )
    assert op.moved
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    assert g1 < g0


def test_until_limit_bundle_default_on() -> None:
    knobs = orbit_params_from_overrides({"until_limit": True})
    assert knobs.get("bundle") is True
    assert knobs.get("bundle_max") == 10


def test_until_limit_uses_bundle_when_points_stall() -> None:
    nodes, edges = _crossed_chain()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    g0 = count_edge_crossings(st.positions, st.links)
    op = orbit_sweep_until_limit(
        st,
        protect_rigid="off",
        frozen_ids={"h", "a", "b"},
        max_jump=2500,
        max_degree=4,
        max_moves=8,
        stall_limit=4,
        nn_floor=10.0,
        bundle=True,
        bundle_max=6,
    )
    meta = op.params or {}
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    assert g1 <= g0
    # Either point or bundle should have moved if crossings existed
    if g0 > 0:
        assert int(meta.get("moves_n") or 0) >= 1 or g1 < g0
