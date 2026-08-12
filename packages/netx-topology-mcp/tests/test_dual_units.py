"""Dual-portal eye units: detect, CN-first max-cover layout, shared-portal compose."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.compose_views import ComposeBlock, strip_pack_blocks
from netx_topology_mcp.layout_ops.dual_units import (
    find_dual_portal_units,
    layout_dual_unit,
)
from netx_topology_mcp.layout_ops.state import LayoutParams
from netx_topology_mcp.layout_structure import analyze_graph_structure
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def _eye_graph():
    """Two AN portals + three interior-disjoint access corridors."""
    nodes = [
        {"fabric_node_id": "p1", "name": "BTM-AN1-P", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "p2", "name": "BTM-AN2-P", "role": "an", "x": 100, "y": 0},
        {"fabric_node_id": "a1", "name": "BTM-EN-A1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "BTM-EN-A2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b1", "name": "BTM-EN-B1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b2", "name": "BTM-EN-B2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "c1", "name": "BTM-EN-C1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "c2", "name": "BTM-EN-C2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "t1", "name": "BTM-EN-T1", "role": "en", "x": 0, "y": 0},
    ]
    edges = [
        {"a_node_id": "p1", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "p2"},
        {"a_node_id": "p1", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b2", "b_node_id": "p2"},
        {"a_node_id": "p1", "b_node_id": "c1"},
        {"a_node_id": "c1", "b_node_id": "c2"},
        {"a_node_id": "c2", "b_node_id": "p2"},
        {"a_node_id": "a1", "b_node_id": "t1"},  # tail off corridor
    ]
    return nodes, edges


def _cn_eye_graph():
    """CN pair covers more NEs than a small AN ring — detection must prefer CN."""
    nodes = [
        {"fabric_node_id": "cn1", "name": "BTM-CN1", "role": "cn", "x": 0, "y": 0},
        {"fabric_node_id": "cn2", "name": "BTM-CN2", "role": "cn", "x": 100, "y": 0},
        {"fabric_node_id": "an1", "name": "BTM-AN1", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "an2", "name": "BTM-AN2", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "e1", "name": "BTM-EN1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "e2", "name": "BTM-EN2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "e3", "name": "BTM-EN3", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "e4", "name": "BTM-EN4", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "e5", "name": "BTM-EN5", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "e6", "name": "BTM-EN6", "role": "en", "x": 0, "y": 0},
    ]
    # Two CN↔CN corridors via AN+EN, plus a tiny AN–AN eye that would steal
    # interiors if AN rings were claimed first.
    edges = [
        {"a_node_id": "cn1", "b_node_id": "cn2"},
        {"a_node_id": "cn1", "b_node_id": "an1"},
        {"a_node_id": "an1", "b_node_id": "e1"},
        {"a_node_id": "e1", "b_node_id": "e2"},
        {"a_node_id": "e2", "b_node_id": "an2"},
        {"a_node_id": "an2", "b_node_id": "cn2"},
        {"a_node_id": "cn1", "b_node_id": "e3"},
        {"a_node_id": "e3", "b_node_id": "e4"},
        {"a_node_id": "e4", "b_node_id": "cn2"},
        {"a_node_id": "an1", "b_node_id": "e5"},
        {"a_node_id": "e5", "b_node_id": "e6"},
        {"a_node_id": "e6", "b_node_id": "an2"},
    ]
    return nodes, edges


def test_actions_include_layout_dual_unit() -> None:
    assert "layout_dual_unit" in ACTIONS


def test_find_dual_portal_units_eye() -> None:
    nodes, edges = _eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    units = find_dual_portal_units(st)
    assert len(units) >= 1
    u = units[0]
    assert {u.portal_a, u.portal_b} == {"p1", "p2"}
    assert len(u.paths) >= 2
    interiors = set()
    for p in u.paths:
        interiors |= set(p[1:-1])
    assert "a1" in interiors or "b1" in interiors or "c1" in interiors


def test_find_prefers_cn_eye_max_cover() -> None:
    nodes, edges = _cn_eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    units = find_dual_portal_units(st)
    assert units
    u0 = units[0]
    assert {u0.portal_a, u0.portal_b} == {"cn1", "cn2"}
    assert len(u0.member_ids()) >= 8


def test_layout_dual_unit_zero_crossings() -> None:
    nodes, edges = _eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = layout_dual_unit(st, LayoutParams())
    assert op.params.get("accepted") is True
    members = set()
    unit = op.params.get("unit") or {}
    members.update(unit.get("node_ids") or [])
    unit_links = [e for e in op.state.links if e[0] in members and e[1] in members]
    x = count_edge_crossings(op.state.positions, unit_links)
    # Path-planar 3-corridor eye should stay clean under ellipse bands.
    assert x == 0
    assert op.params.get("zero_cross") is True


def test_petal_ellipse_hollow_axis_portals_only() -> None:
    """Shared portal-adj nodes stay off the mid-chord (CN gravity / hollow eye)."""
    nodes = [
        {"fabric_node_id": "p1", "name": "P1", "role": "cn", "x": 0, "y": 0},
        {"fabric_node_id": "p2", "name": "P2", "role": "cn", "x": 0, "y": 0},
        {"fabric_node_id": "s", "name": "SHARED-AN", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "u1", "name": "U1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "u2", "name": "U2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "d1", "name": "D1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "d2", "name": "D2", "role": "en", "x": 0, "y": 0},
    ]
    # Two corridors share first hop `s`: p1-s-u1-u2-p2 and p1-s-d1-d2-p2.
    edges = [
        {"a_node_id": "p1", "b_node_id": "s"},
        {"a_node_id": "s", "b_node_id": "u1"},
        {"a_node_id": "u1", "b_node_id": "u2"},
        {"a_node_id": "u2", "b_node_id": "p2"},
        {"a_node_id": "s", "b_node_id": "d1"},
        {"a_node_id": "d1", "b_node_id": "d2"},
        {"a_node_id": "d2", "b_node_id": "p2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    op = layout_dual_unit(st, LayoutParams(pitch=200.0, lane=300.0))
    pos = op.state.positions
    # Axis = portals only; shared AN sits on an arc band.
    assert abs(pos["p1"][1]) < 1e-6 and abs(pos["p2"][1]) < 1e-6
    assert abs(pos["s"][1]) > 50.0
    assert pos["u1"][1] * pos["d1"][1] < 0
    assert abs(pos["u1"][1]) > 200.0
    assert abs(pos["d1"][1]) > 200.0


def test_short_corridor_an_not_on_mid_axis() -> None:
    """CN–AN–CN must not drop the AN onto (0,0) mid-chord."""
    nodes = [
        {"fabric_node_id": "cn1", "name": "CN1", "role": "cn", "x": 0, "y": 0},
        {"fabric_node_id": "cn2", "name": "CN2", "role": "cn", "x": 0, "y": 0},
        {"fabric_node_id": "an1", "name": "AN1", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "an2", "name": "AN2", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "e1", "name": "E1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "e2", "name": "E2", "role": "en", "x": 0, "y": 0},
    ]
    edges = [
        {"a_node_id": "cn1", "b_node_id": "an1"},
        {"a_node_id": "an1", "b_node_id": "cn2"},
        {"a_node_id": "cn1", "b_node_id": "e1"},
        {"a_node_id": "e1", "b_node_id": "e2"},
        {"a_node_id": "e2", "b_node_id": "cn2"},
        {"a_node_id": "cn1", "b_node_id": "an2"},
        {"a_node_id": "an2", "b_node_id": "cn2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    op = layout_dual_unit(
        st, LayoutParams(pitch=200.0, lane=300.0), portal_a="cn1", portal_b="cn2"
    )
    pos = op.state.positions
    assert abs(pos["an1"][1]) > 50.0
    assert abs(pos["an2"][1]) > 50.0
    on_axis = {n for n, (_x, y) in pos.items() if abs(y) < 1e-6}
    assert on_axis <= {"cn1", "cn2"}
    # Hollow-ish: short ANs should not sit at exact mid vertical.
    assert abs(pos["an1"][0]) > 1.0
    assert abs(pos["an2"][0]) > 1.0


def test_long_tail_parks_outside_eye_rings() -> None:
    """Long deg≤2 tails must sit outside corridor envelope (no ring pierce)."""
    nodes = [
        {"fabric_node_id": "p1", "name": "P1", "role": "cn", "x": 0, "y": 0},
        {"fabric_node_id": "p2", "name": "P2", "role": "cn", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "A1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "A2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b1", "name": "B1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b2", "name": "B2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "t0", "name": "T0", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "t1", "name": "T1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "t2", "name": "T2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "t3", "name": "T3", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "t4", "name": "T4", "role": "en", "x": 0, "y": 0},
    ]
    edges = [
        {"a_node_id": "p1", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "p2"},
        {"a_node_id": "p1", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b2", "b_node_id": "p2"},
        # Long tail hanging off upper corridor mid a1.
        {"a_node_id": "a1", "b_node_id": "t0"},
        {"a_node_id": "t0", "b_node_id": "t1"},
        {"a_node_id": "t1", "b_node_id": "t2"},
        {"a_node_id": "t2", "b_node_id": "t3"},
        {"a_node_id": "t3", "b_node_id": "t4"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    op = layout_dual_unit(st, LayoutParams(pitch=200.0, lane=300.0))
    pos = op.state.positions
    corridor = {"p1", "p2", "a1", "a2", "b1", "b2"}
    eye_ry = max(abs(pos[n][1]) for n in corridor if n in pos)
    eye_rx = max(abs(pos[n][0]) for n in corridor if n in pos)
    for tid in ("t0", "t1", "t2", "t3", "t4"):
        x, y = pos[tid]
        assert abs(x) >= eye_rx * 0.9 or abs(y) >= eye_ry * 0.9, (tid, x, y, eye_rx, eye_ry)


def test_layout_dual_unit_accepts_residual_cross_by_default() -> None:
    """With require_zero_cross=false (default), accepted stays true even if x>0."""
    nodes, edges = _cn_eye_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    op = layout_dual_unit(
        st,
        LayoutParams(),
        portal_a="cn1",
        portal_b="cn2",
        require_zero_cross=False,
    )
    assert op.params.get("accepted") is True
    assert "unit_crossings" in op.params
    # Hard gate still available for callers that want the old behavior.
    op_hard = layout_dual_unit(
        st,
        LayoutParams(),
        portal_a="cn1",
        portal_b="cn2",
        require_zero_cross=True,
    )
    if int(op_hard.params.get("unit_crossings") or 0) > 0:
        assert op_hard.params.get("accepted") is False
    else:
        assert op_hard.params.get("accepted") is True


def test_structure_reports_dual_units() -> None:
    nodes, edges = _eye_graph()
    report = analyze_graph_structure(nodes, edges)
    du = report.get("dual_units") or {}
    assert int(du.get("unit_count") or 0) >= 1


def test_shared_portal_compose_preserves_relative() -> None:
    a = ComposeBlock(
        key="unit-p1-p2",
        positions={
            "p1": (0.0, 0.0),
            "p2": (200.0, 0.0),
            "a1": (100.0, 80.0),
        },
    )
    b = ComposeBlock(
        key="unit-p2-p3",
        positions={
            "p2": (0.0, 0.0),
            "p3": (200.0, 0.0),
            "b1": (100.0, -60.0),
        },
    )
    merged, meta = strip_pack_blocks([a, b], pad=100.0, merge_shared=True)
    assert meta.get("merge_shared") is True
    assert "p2" in merged and "p1" in merged and "p3" in merged
    # Shared portal merge keeps both blocks; relative spans stay order-of-magnitude.
    span_p = ((merged["p3"][0] - merged["p2"][0]) ** 2 + (merged["p3"][1] - merged["p2"][1]) ** 2) ** 0.5
    span_b = ((merged["b1"][0] - merged["p2"][0]) ** 2 + (merged["b1"][1] - merged["p2"][1]) ** 2) ** 0.5
    assert abs(span_p - 200.0) < 10.0
    assert abs(span_b - ((100.0**2 + 60.0**2) ** 0.5)) < 10.0
