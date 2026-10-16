"""Same-canvas dual_mass: petal/straight beautify in place."""

from __future__ import annotations

import math

from netx_api.mcp_remote.topology.layout_metrics import count_edge_crossings
from netx_api.mcp_remote.topology.layout_ops import build_state_from_nodes_edges, run_recipe
from netx_api.mcp_remote.topology.layout_ops.dual_mass import layout_dual_mass
from netx_api.mcp_remote.topology.layout_ops.dual_units import (
    DualUnit,
    beautify_dual_unit_positions,
    classify_dual_unit,
)
from netx_api.mcp_remote.topology.layout_ops.state import LayoutParams
from netx_api.mcp_remote.topology.layout_tool import ACTIONS, RECIPE_ALIASES, run_layout_on_graph


def _two_eye_graph():
    """Two dual-portal petals sharing portal p2 + one leftover."""
    nodes = [
        {"fabric_node_id": "p1", "name": "AN-P1", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "p2", "name": "AN-P2", "role": "an", "x": 100, "y": 0},
        {"fabric_node_id": "p3", "name": "AN-P3", "role": "an", "x": 200, "y": 0},
        {"fabric_node_id": "a1", "name": "EN-A1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "EN-A2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b1", "name": "EN-B1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b2", "name": "EN-B2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "c1", "name": "EN-C1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "c2", "name": "EN-C2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "d1", "name": "EN-D1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "d2", "name": "EN-D2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "z1", "name": "EN-Z1", "role": "en", "x": 999, "y": 999},
    ]
    edges = [
        # eye p1–p2
        {"a_node_id": "p1", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "p2"},
        {"a_node_id": "p1", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b2", "b_node_id": "p2"},
        # eye p2–p3
        {"a_node_id": "p2", "b_node_id": "c1"},
        {"a_node_id": "c1", "b_node_id": "c2"},
        {"a_node_id": "c2", "b_node_id": "p3"},
        {"a_node_id": "p2", "b_node_id": "d1"},
        {"a_node_id": "d1", "b_node_id": "d2"},
        {"a_node_id": "d2", "b_node_id": "p3"},
        # leftover hanging off p3
        {"a_node_id": "p3", "b_node_id": "z1"},
    ]
    return nodes, edges


def test_recipe_alias_dual_mass() -> None:
    # dual_mass kept as module; unpublished from public recipe/action surface.
    assert "dual_mass" not in RECIPE_ALIASES
    assert "dual_mass" not in ACTIONS


def test_classify_petal_and_straight() -> None:
    petal = DualUnit(
        portal_a="p1",
        portal_b="p2",
        paths=[["p1", "a1", "a2", "p2"], ["p1", "b1", "b2", "p2"]],
    )
    assert classify_dual_unit(petal) == "petal"
    chain = DualUnit(
        portal_a="p1",
        portal_b="p2",
        paths=[["p1"] + [f"n{i}" for i in range(10)] + ["p2"]],
    )
    assert classify_dual_unit(chain) == "straight"
    short = DualUnit(
        portal_a="p1",
        portal_b="p2",
        paths=[["p1", "a1", "a2", "p2"]],
    )
    assert classify_dual_unit(short) == "straight"


def test_beautify_petal_ellipse_bands() -> None:
    nodes, edges = _two_eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    unit = DualUnit(
        portal_a="p1",
        portal_b="p2",
        paths=[["p1", "a1", "a2", "p2"], ["p1", "b1", "b2", "p2"]],
    )
    pos = beautify_dual_unit_positions(st, unit, LayoutParams())
    # Portals sit on the x-axis, a left of b (the eye's chord).
    assert abs(pos["p1"][1]) < 1e-6 and abs(pos["p2"][1]) < 1e-6
    assert pos["p1"][0] < pos["p2"][0]
    # Each corridor is one band on its own side of the chord.
    assert pos["a1"][1] * pos["b1"][1] < 0
    for x, y in (("a1", "a2"), ("b1", "b2")):
        assert pos[x][1] * pos[y][1] > 0
        assert abs(pos[x][1]) > 1.0
        # Runs a→b, strictly inside the portals (hollow eye, no node on the chord ends).
        assert pos["p1"][0] < pos[x][0] < pos[y][0] < pos["p2"][0]
    links = [(e["a_node_id"], e["b_node_id"]) for e in edges if e["a_node_id"] in pos and e["b_node_id"] in pos]
    assert count_edge_crossings(pos, links) == 0


def test_dual_mass_refine_reports_drift() -> None:
    nodes, edges = _two_eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    # Seed a spread layout first (optional repark).
    op1 = layout_dual_mass(st, LayoutParams(), mode="full", mass_merge=False)
    op2 = layout_dual_mass(
        op1.state, LayoutParams(), mode="refine", rounds=2, stable_drift=1e9
    )
    assert op2.params.get("mode_first") == "refine"
    assert int(op2.params.get("rounds_ran") or 0) >= 1
    assert isinstance(op2.params.get("centroid_drift"), list)
    kinds = op2.params.get("kinds") or {}
    assert int(kinds.get("petal") or 0) >= 1


def test_layout_dual_mass_beautify_and_meta() -> None:
    nodes, edges = _two_eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = layout_dual_mass(st, LayoutParams(), mode="full")
    assert op.op == "layout_dual_mass"
    assert int(op.params.get("units_n") or 0) >= 1
    assert op.state.meta.get("dual_mass")
    cv = op.state.meta.get("compose_views") or {}
    assert cv.get("mass_groups")
    assert op.state.meta.get("mass_field")
    assert op.params.get("role") == "beautify"

    p1 = op.state.positions["p1"]
    p2 = op.state.positions["p2"]
    dist = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
    assert dist >= 80.0
    assert int(op.params.get("unit_ok") or 0) >= 1


def test_run_recipe_dual_mass_v1_unpublished() -> None:
    nodes, edges = _two_eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    try:
        run_recipe(st, "dual_mass_v1", LayoutParams())
        raise AssertionError("dual_mass_v1 should be unpublished from RECIPES")
    except Exception as e:
        assert "dual_mass" in str(e).lower() or "unknown" in str(e).lower() or "recipe" in str(e).lower()


def test_run_layout_on_graph_dual_mass_unpublished() -> None:
    nodes, edges = _two_eye_graph()
    try:
        run_layout_on_graph(nodes, edges, action="layout", recipe="dual_mass")
        raise AssertionError("dual_mass recipe should be unpublished")
    except ValueError as e:
        assert "unknown_recipe" in str(e)


def test_layout_dual_mass_module_still_works() -> None:
    nodes, edges = _two_eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = layout_dual_mass(st, LayoutParams(), mode="full", mass_merge=False)
    assert op.op == "layout_dual_mass"
    pos = op.state.positions
    assert "p1" in pos and "p2" in pos
    members = {"p1", "p2", "a1", "a2", "b1", "b2"}
    links = [
        (e["a_node_id"], e["b_node_id"])
        for e in edges
        if e["a_node_id"] in members and e["b_node_id"] in members
    ]
    assert count_edge_crossings({k: pos[k] for k in members}, links) == 0
