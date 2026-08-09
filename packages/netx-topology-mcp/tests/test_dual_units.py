"""Dual-portal eye units: detect, zero-cross layout, shared-portal compose."""

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
    assert x == 0

    out = run_layout_on_graph(nodes, edges, action="layout_dual_unit")
    assert out["ok"] is True
    assert out["action"] == "layout_dual_unit"
    loc = out.get("local") or {}
    assert loc.get("accepted") is True
    assert int((loc.get("op") or {}).get("unit_crossings") or 0) == 0


def test_structure_reports_dual_units() -> None:
    nodes, edges = _eye_graph()
    report = analyze_graph_structure(nodes, edges)
    du = report.get("dual_units") or {}
    assert int(du.get("unit_count") or 0) >= 1
    assert report.get("advice", {}).get("prefer_dual_units") is True or du.get(
        "unit_count", 0
    ) >= 1


def test_compose_merge_shared_portal_unique_coord() -> None:
    """Two units sharing portal p2 → one world coord for p2; B rigidly glued."""
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
    # Relative offset p2→p3 preserved after rigid glue (200 on x in local B).
    dx = merged["p3"][0] - merged["p2"][0]
    dy = merged["p3"][1] - merged["p2"][1]
    assert abs(dx - 200.0) < 1e-6
    assert abs(dy) < 1e-6
    # b1 relative to p2 preserved.
    assert abs(merged["b1"][0] - merged["p2"][0] - 100.0) < 1e-6
    assert abs(merged["b1"][1] - merged["p2"][1] + 60.0) < 1e-6
