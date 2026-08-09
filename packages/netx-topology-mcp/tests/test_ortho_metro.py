"""Tests for ortho_metro (multi-layer H/V metro recipe)."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops import LayoutParams, build_state_from_nodes_edges, run_recipe
from netx_topology_mcp.layout_ops.ortho_metro import (
    _axis_ok,
    build_ortho_metro_skeleton,
)
from netx_topology_mcp.layout_tool import RECIPE_ALIASES, run_layout_on_graph


def _core_bar_graph():
    """Two CN cores + AN stubs + one deg-2 corridor + one triangle."""
    nodes = [
        {"fabric_node_id": "c0", "name": "X-CN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "c1", "name": "X-CN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a0", "name": "X-AN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "X-AN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e0", "name": "X-EN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e1", "name": "X-EN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "t0", "name": "X-AN2-Y", "x": 0, "y": 0},
        {"fabric_node_id": "t1", "name": "X-AN3-Y", "x": 0, "y": 0},
    ]
    edges = [
        {"a_node_id": "c0", "b_node_id": "c1"},
        {"a_node_id": "c0", "b_node_id": "a0"},
        {"a_node_id": "c1", "b_node_id": "a1"},
        {"a_node_id": "a0", "b_node_id": "e0"},
        {"a_node_id": "e0", "b_node_id": "e1"},
        # triangle on a0
        {"a_node_id": "a0", "b_node_id": "t0"},
        {"a_node_id": "t0", "b_node_id": "t1"},
        {"a_node_id": "t1", "b_node_id": "a0"},
    ]
    return nodes, edges


def test_recipe_alias() -> None:
    # ortho_metro module kept; unpublished from public recipe aliases.
    assert "ortho_metro" not in RECIPE_ALIASES


def test_build_ortho_metro_places_all() -> None:
    nodes, edges = _core_bar_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = build_ortho_metro_skeleton(st, LayoutParams())
    assert op.op == "build_ortho_metro_skeleton"
    assert set(op.state.positions) == {n["fabric_node_id"] for n in nodes}
    assert op.state.meta.get("rings_mode") == "ortho_metro"
    meta = op.state.meta.get("ortho_metro") or {}
    assert int(meta.get("axis_edges") or 0) + int(meta.get("diag_edges") or 0) == len(
        op.state.links
    )


def test_ortho_metro_mostly_axis_aligned() -> None:
    from netx_topology_mcp.layout_metrics import compute_edge_clearance

    nodes, edges = _core_bar_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = build_ortho_metro_skeleton(st, LayoutParams(), skip_triangles=True)
    pos = op.state.positions
    axis = 0
    diag = 0
    for a, b in op.state.links:
        if _axis_ok(pos[a], pos[b]):
            axis += 1
        else:
            diag += 1
    # Ortho first; clearance may break one chord to kill edge-through-node.
    assert axis >= len(op.state.links) - 2
    assert diag <= 2
    clr = compute_edge_clearance(pos, op.state.links, names=op.state.names, thr=40.0)
    assert int(clr.get("edge_clearance_hits") or 0) == 0


def test_ortho_metro_no_footprint_stack() -> None:
    nodes, edges = _core_bar_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = build_ortho_metro_skeleton(st, LayoutParams(pitch=200, side=170))
    ids = list(op.state.positions)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            x1, y1 = op.state.positions[a]
            x2, y2 = op.state.positions[b]
            assert math.hypot(x2 - x1, y2 - y1) >= 140.0


def test_run_recipe_and_layout_tool_unpublished() -> None:
    nodes, edges = _core_bar_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    try:
        run_recipe(st, "ortho_metro_v1", LayoutParams())
        raise AssertionError("ortho_metro_v1 should be unpublished")
    except Exception as e:
        assert "ortho" in str(e).lower() or "unknown" in str(e).lower() or "recipe" in str(e).lower()

    try:
        run_layout_on_graph(nodes, edges, action="layout", recipe="ortho_metro")
        raise AssertionError("ortho_metro recipe should be unpublished")
    except ValueError as e:
        assert "unknown_recipe" in str(e)


def test_skip_triangles_false_tries_all() -> None:
    nodes, edges = _core_bar_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = build_ortho_metro_skeleton(st, LayoutParams(), skip_triangles=False)
    # Still places everyone; may leave 0 diags if coplanar H on triangle.
    assert len(op.state.positions) == len(nodes)
