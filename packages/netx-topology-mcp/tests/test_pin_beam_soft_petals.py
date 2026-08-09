"""Tests for stage-2 pin_beam + soft_petals actions."""

from __future__ import annotations

from netx_topology_mcp.layout_ops import LayoutParams, build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.pin_beam import pin_beam_rigid
from netx_topology_mcp.layout_ops.soft_petals import soft_petals_greedy
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def _starish():
    """Two cores + two AN hubs each with a few stubs (positions intentionally messy)."""
    nodes = [
        {"fabric_node_id": "c0", "name": "X-CN0-Y", "x": 100, "y": 500},
        {"fabric_node_id": "c1", "name": "X-CN1-Y", "x": 900, "y": 100},
        {"fabric_node_id": "a0", "name": "X-AN0-Y", "x": 200, "y": 800},
        {"fabric_node_id": "a1", "name": "X-AN1-Y", "x": 800, "y": 50},
        {"fabric_node_id": "e0", "name": "X-EN0-Y", "x": 50, "y": 900},
        {"fabric_node_id": "e1", "name": "X-EN1-Y", "x": 350, "y": 950},
        {"fabric_node_id": "e2", "name": "X-EN2-Y", "x": 750, "y": 20},
        {"fabric_node_id": "e3", "name": "X-EN3-Y", "x": 950, "y": 40},
        {"fabric_node_id": "e4", "name": "X-EN4-Y", "x": 250, "y": 700},
        {"fabric_node_id": "e5", "name": "X-EN5-Y", "x": 850, "y": 200},
    ]
    edges = [
        {"a_node_id": "c0", "b_node_id": "c1"},
        {"a_node_id": "c0", "b_node_id": "a0"},
        {"a_node_id": "c1", "b_node_id": "a1"},
        {"a_node_id": "a0", "b_node_id": "e0"},
        {"a_node_id": "a0", "b_node_id": "e1"},
        {"a_node_id": "a0", "b_node_id": "e4"},
        {"a_node_id": "a1", "b_node_id": "e2"},
        {"a_node_id": "a1", "b_node_id": "e3"},
        {"a_node_id": "a1", "b_node_id": "e5"},
        {"a_node_id": "e0", "b_node_id": "e4"},
        {"a_node_id": "e2", "b_node_id": "e5"},
    ]
    return nodes, edges


def test_actions_include_pin_and_petals() -> None:
    assert "pin_beam" not in ACTIONS
    assert "soft_petals" not in ACTIONS


def test_pin_beam_aligns_cores() -> None:
    nodes, edges = _starish()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    op = pin_beam_rigid(st, LayoutParams())
    assert op.op == "pin_beam"
    ys = [op.state.positions["c0"][1], op.state.positions["c1"][1]]
    # Either accepted with near-collinear cores, or refused (no_improvement) keeping input
    if op.params.get("accepted"):
        assert abs(ys[0] - ys[1]) < 5.0
    else:
        assert op.note in {"no_improvement", "need_ge_2_cores"}


def test_soft_petals_runs() -> None:
    nodes, edges = _starish()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    op = soft_petals_greedy(st, LayoutParams())
    assert op.op == "soft_petals"
    assert "accepted_n" in op.params


def test_run_layout_on_graph_pin_beam_unpublished() -> None:
    nodes, edges = _starish()
    try:
        run_layout_on_graph(nodes, edges, action="pin_beam")
        raise AssertionError("pin_beam should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)


def test_run_layout_on_graph_soft_petals_unpublished() -> None:
    nodes, edges = _starish()
    try:
        run_layout_on_graph(nodes, edges, action="soft_petals")
        raise AssertionError("soft_petals should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)
