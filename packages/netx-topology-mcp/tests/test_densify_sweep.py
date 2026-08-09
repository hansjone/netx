"""Tests for inward densify_sweep top-3 / round / corridor scan."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.densify_sweep import (
    apply_densify_pick,
    densify_corridor_scan,
    densify_sweep_node,
    densify_sweep_round,
)
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.orbit_sweep import _incident_stretch
from netx_topology_mcp.layout_ops.state import LayoutParams
from netx_topology_mcp.layout_tool import list_layout_catalog, run_layout_on_graph


def _sparse_spoke():
    """Hub + three near stubs + one far spoke (stretchy)."""
    nodes = [
        {"fabric_node_id": "h", "name": "HHHHHH-EN-H", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "a", "name": "AAAAAA-EN-A", "x": 180.0, "y": 0.0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-B", "x": 0.0, "y": 180.0},
        {"fabric_node_id": "c", "name": "CCCCCC-EN-C", "x": -180.0, "y": 0.0},
        {"fabric_node_id": "far", "name": "FFFFFF-EN-F", "x": 2400.0, "y": 0.0},
    ]
    edges = [
        {"a_node_id": "h", "b_node_id": "a"},
        {"a_node_id": "h", "b_node_id": "b"},
        {"a_node_id": "h", "b_node_id": "c"},
        {"a_node_id": "h", "b_node_id": "far"},
    ]
    return nodes, edges


def _two_island_bridge():
    """Two compact islands linked by a long bridge — corridor densify target."""
    nodes = [
        {"fabric_node_id": "a1", "name": "A1A1A1-EN-1", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "a2", "name": "A2A2A2-EN-2", "x": 160.0, "y": 0.0},
        {"fabric_node_id": "a3", "name": "A3A3A3-EN-3", "x": 80.0, "y": 140.0},
        {"fabric_node_id": "b1", "name": "B1B1B1-EN-1", "x": 4000.0, "y": 0.0},
        {"fabric_node_id": "b2", "name": "B2B2B2-EN-2", "x": 4160.0, "y": 0.0},
        {"fabric_node_id": "b3", "name": "B3B3B3-EN-3", "x": 4080.0, "y": 140.0},
    ]
    edges = [
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "a3"},
        {"a_node_id": "a3", "b_node_id": "a1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b2", "b_node_id": "b3"},
        {"a_node_id": "b3", "b_node_id": "b1"},
        {"a_node_id": "a2", "b_node_id": "b1"},
    ]
    return nodes, edges


def test_densify_sweep_node_pulls_inward() -> None:
    nodes, edges = _sparse_spoke()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    g0 = count_edge_crossings(st.positions, st.links)
    out = densify_sweep_node(
        st,
        "far",
        protect_rigid="off",
        max_pull=2000,
        nn_floor=40.0,
    )
    assert out["ok"] is True
    cands = out["candidates"]
    assert 1 <= len(cands) <= 3
    best = cands[0]
    # Moved toward hub (x decreases).
    assert best["x"] < 2400.0 - 50.0
    assert best["crossings"]["global"] <= g0
    assert best["delta"]["global"] <= 0
    # Closer to hub than start.
    d0 = 2400.0
    d1 = abs(best["x"] - 0.0)  # hub at 0
    assert d1 < d0


def test_densify_nn_floor_rejects_crush() -> None:
    nodes, edges = _sparse_spoke()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    # Huge nn_floor should leave few/no candidates (can't get near hub).
    out = densify_sweep_node(
        st,
        "far",
        protect_rigid="off",
        max_pull=2000,
        nn_floor=500.0,
    )
    assert out["ok"] is True
    for c in out["candidates"]:
        # Any accepted candidate must keep nn to all others ≥ floor.
        trial = dict(st.positions)
        trial["far"] = (c["x"], c["y"])
        for oid, (ox, oy) in trial.items():
            if oid == "far":
                continue
            d = ((c["x"] - ox) ** 2 + (c["y"] - oy) ** 2) ** 0.5
            assert d >= 500.0 - 1e-3


def test_apply_densify_pick_moves() -> None:
    nodes, edges = _sparse_spoke()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    sweep = densify_sweep_node(st, "far", protect_rigid="off", max_pull=1800, nn_floor=40)
    assert sweep["ok"] and sweep["candidates"]
    op = apply_densify_pick(st, sweep, pick=1)
    assert "far" in op.moved
    chosen = sweep["candidates"][0]
    assert abs(op.state.positions["far"][0] - chosen["x"]) < 0.2


def test_densify_round_lowers_stretch_not_x() -> None:
    nodes, edges = _sparse_spoke()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    g0 = count_edge_crossings(st.positions, st.links)
    params = LayoutParams(target_nn=155.0)
    stretch0 = _incident_stretch("far", st.positions, st.adj, 155.0)
    op = densify_sweep_round(
        st,
        params=params,
        top_n=4,
        max_degree=9,
        protect_rigid="off",
        max_pull=2000,
        nn_floor=40.0,
        focus_ids=["far"],
    )
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    assert g1 <= g0
    stretch1 = _incident_stretch("far", op.state.positions, op.state.adj, 155.0)
    if op.moved:
        assert stretch1 < stretch0


def test_corridor_scan_raises_util_or_reverts_clean() -> None:
    nodes, edges = _two_island_bridge()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    groups = [
        {"key": "ua", "node_ids": ["a1", "a2", "a3"], "pivots": []},
        {"key": "ub", "node_ids": ["b1", "b2", "b3"], "pivots": []},
    ]
    st.meta["compose_views"] = {"rigid_groups": groups}
    g0 = count_edge_crossings(st.positions, st.links)
    op = densify_corridor_scan(
        st,
        groups=groups,
        corridor_caps=[800.0, 1600.0, 3200.0],
        pulls=[0.5, 0.65],
        iters=4,
    )
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    meta = op.params or {}
    assert g1 <= g0 + int(meta.get("x_slack") or 5)
    if not meta.get("reverted"):
        assert float(meta.get("end_util") or 0) >= float(meta.get("start_util") or 0)
        assert meta.get("chosen")


def test_run_layout_densify_unpublished() -> None:
    nodes, edges = _sparse_spoke()
    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="densify_sweep",
            params={"node_id": "far", "protect_rigid": "off", "max_pull": 1800, "nn_floor": 40},
        )
        raise AssertionError("densify_sweep should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)


def test_catalog_omits_densify_sweep() -> None:
    cat = list_layout_catalog()
    assert "densify_sweep" not in cat["actions"]


def test_densify_default_protect_off_may_move_shared_portal() -> None:
    from netx_topology_mcp.layout_ops.densify_sweep import densify_params_from_overrides

    knobs = densify_params_from_overrides({"node_id": "hub"})
    assert knobs["protect_rigid"] == "off"
    nodes = [
        {"fabric_node_id": "hub", "name": "HUBHUB-EN-1", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "a1", "name": "AAAAAA-EN-1", "x": 200.0, "y": 0.0},
        {"fabric_node_id": "b1", "name": "BBBBBB-EN-1", "x": 2000.0, "y": 0.0},
        {"fabric_node_id": "b2", "name": "BBBBBB-EN-2", "x": 2200.0, "y": 0.0},
    ]
    edges = [
        {"a_node_id": "hub", "b_node_id": "a1"},
        {"a_node_id": "hub", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    groups = [
        {"key": "ua", "node_ids": ["hub", "a1"], "pivots": ["hub"]},
        {"key": "ub", "node_ids": ["hub", "b1", "b2"], "pivots": ["hub"]},
    ]
    st.meta["compose_views"] = {"rigid_groups": groups}
    # Default off: shared portal may densify (not shared_portal error).
    ok = densify_sweep_node(st, "hub", groups=groups, max_pull=1800, nn_floor=40)
    assert ok["ok"] is True
    # Opt-in freeze still works.
    blocked = densify_sweep_node(
        st, "hub", protect_rigid="portals", groups=groups, frozen_ids={"hub"}
    )
    assert blocked["ok"] is False
    assert blocked.get("error") in {"frozen", "shared_portal"}
