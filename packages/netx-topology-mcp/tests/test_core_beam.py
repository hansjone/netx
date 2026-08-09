"""core_bar beam-first placement in sugiyama._place_component."""

from __future__ import annotations

from netx_topology_mcp.layout_ops import LayoutParams, build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.sugiyama import _place_component


def _dual_core_multi_an():
    """Two cores on a bar, two ANs each with a small access petal."""
    # c0 -- a0 -- e0
    #  |     |
    # c1 -- a1 -- e1
    nodes = [
        {"fabric_node_id": "c0", "name": "X-CN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "c1", "name": "X-CN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a0", "name": "X-AN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "X-AN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e0", "name": "X-EN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e1", "name": "X-EN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e2", "name": "X-EN2-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e3", "name": "X-EN3-Y", "x": 0, "y": 0},
    ]
    edges = [
        {"a_node_id": "c0", "b_node_id": "c1"},
        {"a_node_id": "c0", "b_node_id": "a0"},
        {"a_node_id": "c1", "b_node_id": "a1"},
        {"a_node_id": "a0", "b_node_id": "e0"},
        {"a_node_id": "a0", "b_node_id": "e2"},
        {"a_node_id": "a1", "b_node_id": "e1"},
        {"a_node_id": "a1", "b_node_id": "e3"},
    ]
    return nodes, edges


def test_place_component_core_beam_collinear_cores() -> None:
    nodes, edges = _dual_core_multi_an()
    st = build_state_from_nodes_edges(nodes, edges)
    assert st.layers["c0"] == "core" and st.layers["c1"] == "core"
    assert st.layers["a0"] == "agg" and st.layers["a1"] == "agg"
    ids = [n["fabric_node_id"] for n in nodes]
    out = _place_component(ids, st, LayoutParams())
    assert set(out) == set(ids)
    # Cores approximately collinear on a horizontal beam
    y0, y1 = out["c0"][1], out["c1"][1]
    assert abs(y0 - y1) < 1.0
    assert abs(out["c0"][0] - out["c1"][0]) > 100.0
    # ANs hang off the beam (not packed into a core column): mean AN y away from beam
    beam_y = (y0 + y1) / 2
    an_y = (out["a0"][1] + out["a1"][1]) / 2
    assert abs(an_y - beam_y) > 80.0
    # Beam-first: both cores share one y; ANs are not co-located with cores as a column pack
    assert abs(out["a0"][1] - beam_y) > 40.0
    assert abs(out["a1"][1] - beam_y) > 40.0


def test_place_component_single_core_still_packs() -> None:
    """One core keeps the legacy AN-column pack path (no beam branch)."""
    nodes = [
        {"fabric_node_id": "c0", "name": "X-CN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a0", "name": "X-AN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "X-AN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e0", "name": "X-EN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e1", "name": "X-EN1-Y", "x": 0, "y": 0},
    ]
    edges = [
        {"a_node_id": "c0", "b_node_id": "a0"},
        {"a_node_id": "c0", "b_node_id": "a1"},
        {"a_node_id": "a0", "b_node_id": "e0"},
        {"a_node_id": "a1", "b_node_id": "e1"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    ids = [n["fabric_node_id"] for n in nodes]
    out = _place_component(ids, st, LayoutParams())
    assert set(out) == set(ids)
    assert "c0" in out
