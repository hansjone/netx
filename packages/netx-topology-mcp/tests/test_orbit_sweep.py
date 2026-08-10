"""Tests for polar orbit_sweep top-3 suggest / round apply."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.orbit_sweep import (
    apply_orbit_pick,
    orbit_sweep_node,
    orbit_sweep_round,
)
from netx_topology_mcp.layout_tool import run_layout_on_graph


def _crossed_pair():
    # a—b horizontal crosses c—d vertical at center; free node e tethered to c.
    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 0.0, "y": 200.0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 400.0, "y": 200.0},
        {"fabric_node_id": "c", "name": "CCCCCC-EN-3", "x": 200.0, "y": 0.0},
        {"fabric_node_id": "d", "name": "DDDDDD-EN-4", "x": 200.0, "y": 400.0},
        {"fabric_node_id": "e", "name": "EEEEEE-EN-5", "x": 200.0, "y": -80.0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "c", "b_node_id": "d"},
        {"a_node_id": "c", "b_node_id": "e"},
    ]
    return nodes, edges


def test_orbit_sweep_node_returns_top3_with_gain() -> None:
    nodes, edges = _crossed_pair()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    g0 = count_edge_crossings(st.positions, st.links)
    assert g0 >= 1
    # Move endpoint d — polar sweep should find a non-crossing slot.
    out = orbit_sweep_node(
        st,
        "d",
        protect_rigid="off",
        max_jump=500,
        angle_step=15,
        nn_floor=20.0,
    )
    assert out["ok"] is True
    cands = out["candidates"]
    assert 1 <= len(cands) <= 3
    assert cands[0]["rank"] == 1
    assert "x" in cands[0] and "y" in cands[0]
    assert cands[0]["crossings"]["global"] <= g0
    # Prefer at least one improving candidate on this toy cross.
    assert out["improving_n"] >= 1 or cands[0]["delta"]["global"] <= 0


def test_orbit_top3_angular_diversity() -> None:
    nodes, edges = _crossed_pair()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    out = orbit_sweep_node(
        st, "d", protect_rigid="off", max_jump=600, min_angle_sep=35.0
    )
    cands = out["candidates"]
    if len(cands) >= 2:
        for i in range(len(cands)):
            for j in range(i + 1, len(cands)):
                a = float(cands[i]["angle_deg"])
                b = float(cands[j]["angle_deg"])
                d = abs(a - b) % 360.0
                d = d if d <= 180 else 360 - d
                r0 = max(float(cands[i]["r"]), 1.0)
                r1 = max(float(cands[j]["r"]), 1.0)
                ratio = max(r0, r1) / min(r0, r1)
                # Either angularly separated or clearly different radius.
                assert d >= 34.0 or ratio >= 1.29


def test_apply_orbit_pick_moves_to_rank2() -> None:
    nodes, edges = _crossed_pair()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    sweep = orbit_sweep_node(st, "d", protect_rigid="off", max_jump=600)
    assert sweep["ok"]
    cands = sweep["candidates"]
    assert len(cands) >= 1
    pick = 2 if len(cands) >= 2 else 1
    op = apply_orbit_pick(st, sweep, pick=pick)
    assert "d" in op.moved
    chosen = cands[pick - 1]
    assert abs(op.state.positions["d"][0] - chosen["x"]) < 0.2
    assert abs(op.state.positions["d"][1] - chosen["y"]) < 0.2


def test_orbit_sweep_round_does_not_raise_crossings() -> None:
    nodes, edges = _crossed_pair()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    g0 = count_edge_crossings(st.positions, st.links)
    op = orbit_sweep_round(
        st,
        top_n=4,
        max_degree=9,
        protect_rigid="off",
        max_jump=600,
    )
    g1 = count_edge_crossings(op.state.positions, op.state.links)
    assert g1 <= g0
    assert (op.params or {}).get("end_crossings") == g1


def test_run_layout_orbit_sweep_preview_keeps_coords() -> None:
    nodes, edges = _crossed_pair()
    out = run_layout_on_graph(
        nodes,
        edges,
        action="orbit_sweep",
        params={"node_id": "d", "protect_rigid": "off", "max_jump": 600},
    )
    assert out["ok"] is True
    assert out["action"] == "orbit_sweep"
    sweep = (out.get("local") or {}).get("sweep") or {}
    assert sweep.get("candidates")
    by_id = {p["fabric_node_id"]: p for p in out["positions"]}
    assert abs(by_id["d"]["x"] - 200.0) < 0.2
    assert abs(by_id["d"]["y"] - 400.0) < 0.2


def test_run_layout_orbit_sweep_pick_applies() -> None:
    nodes, edges = _crossed_pair()
    out = run_layout_on_graph(
        nodes,
        edges,
        action="orbit_sweep",
        params={
            "node_id": "d",
            "pick": 1,
            "protect_rigid": "off",
            "max_jump": 600,
        },
    )
    assert out["ok"] is True
    pick = (out.get("local") or {}).get("pick")
    assert pick == 1
    sweep = (out.get("local") or {}).get("sweep") or {}
    chosen = (sweep.get("candidates") or [{}])[0]
    by_id = {p["fabric_node_id"]: p for p in out["positions"]}
    # normalize_origin shifts all coords; check relative to fixed peer a.
    assert abs((by_id["d"]["x"] - by_id["a"]["x"]) - (chosen["x"] - 0.0)) < 0.2
    assert abs((by_id["d"]["y"] - by_id["a"]["y"]) - (chosen["y"] - 200.0)) < 0.2


def test_catalog_lists_orbit_sweep() -> None:
    from netx_topology_mcp.layout_tool import list_layout_catalog

    cat = list_layout_catalog()
    assert "orbit_sweep" in cat["actions"]


def test_orbit_default_protect_off_ignores_portal_freeze() -> None:
    from netx_topology_mcp.layout_ops.orbit_sweep import orbit_params_from_overrides

    knobs = orbit_params_from_overrides({"node_id": "d", "portal_ids": ["d"]})
    assert knobs["protect_rigid"] == "off"
    nodes, edges = _crossed_pair()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    # frozen_ids present but protect default off → still sweeps.
    out = orbit_sweep_node(st, "d", frozen_ids={"d"}, max_jump=500, nn_floor=20.0)
    assert out["ok"] is True
    assert out.get("candidates")
    frozen = orbit_sweep_node(
        st, "d", protect_rigid="portals", frozen_ids={"d"}, max_jump=500
    )
    assert frozen["ok"] is False
    assert frozen.get("error") == "frozen"


def test_orbit_objective_total_ranks_clearance_trade() -> None:
    """objective=total may keep a crossing-up move if clearance improves enough."""
    # Hub h near non-incident segment a—b; moving h right cuts clearance hits
    # but can add a mild cross with c—d. Crossing-only ranking would reject it.
    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 400.0, "y": 0.0},
        {"fabric_node_id": "c", "name": "CCCCCC-EN-3", "x": 200.0, "y": -200.0},
        {"fabric_node_id": "d", "name": "DDDDDD-EN-4", "x": 200.0, "y": 200.0},
        {"fabric_node_id": "h", "name": "HHHHHH-EN-5", "x": 200.0, "y": 20.0},
        {"fabric_node_id": "t", "name": "TTTTTT-EN-6", "x": 200.0, "y": 300.0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "c", "b_node_id": "d"},
        {"a_node_id": "h", "b_node_id": "t"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}

    by_cross = orbit_sweep_node(st, "h", max_jump=400, nn_floor=20.0, objective="crossing")
    by_total = orbit_sweep_node(st, "h", max_jump=400, nn_floor=20.0, objective="total")
    assert by_cross["ok"] is True and by_total["ok"] is True
    assert by_total.get("objective") == "total"
    # Tiny graph: clearance runs, so ranked candidates carry verdict_partial.
    assert by_total.get("candidates")
    assert all("verdict_partial" in c for c in by_total["candidates"])
    # y_band plumbing
    banded = orbit_sweep_node(
        st, "h", max_jump=400, nn_floor=20.0, objective="total", y_min=0.0, y_max=80.0
    )
    assert banded["ok"] is True
    assert banded.get("y_band") == [0.0, 80.0]
    for c in banded.get("candidates") or []:
        assert 0.0 <= float(c["y"]) <= 80.0


def test_verdict_partial_weights_match_layout_stats() -> None:
    from netx_topology_mcp.layout_ops.orbit_sweep import (
        _W_CLR,
        _W_CROSS,
        _crossing_part_score,
        _verdict_partial,
    )

    assert abs(_W_CROSS - 0.18) < 1e-9
    assert abs(_W_CLR - 0.08) < 1e-9
    # Zero crossings on small graph → crossing part 1.0
    assert _crossing_part_score(0, n_links=10, n_nodes=10) == 1.0
    # Perfect clearance + perfect crossing
    assert abs(_verdict_partial(0, 1.0, n_links=10, n_nodes=10) - (_W_CROSS + _W_CLR)) < 1e-9
    # Worse crossings lower the partial
    assert _verdict_partial(5, 1.0, n_links=10, n_nodes=10) < _verdict_partial(
        0, 1.0, n_links=10, n_nodes=10
    )
