"""Tests for sinkTopologyDualUnits selection helpers + tool registration."""

from __future__ import annotations

from netx_topology_mcp.http_tools import HTTP_MCP_TOOLS
from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.dual_units import DualUnit, find_dual_portal_units
from netx_topology_mcp.layout_ops.sink_dual_units import (
    batch_node_ids,
    layout_and_pack_batch,
    leftover_batch_ids,
    park_positions,
    select_dual_unit_batch,
)


def _u(uid: int, a: str, b: str, *mids: str) -> DualUnit:
    path = [a, *mids, b] if mids else [a, b]
    return DualUnit(portal_a=a, portal_b=b, paths=[path], tails=[], unit_id=uid)


def test_select_prefers_less_shared_portals() -> None:
    units = [
        _u(1, "X", "A", "m1", "m2", "m3", "m4", "m5", "m6"),
        _u(2, "X", "B", "n1", "n2", "n3", "n4", "n5", "n6"),
        _u(3, "C", "D", "p1", "p2", "p3", "p4", "p5", "p6"),
    ]
    picked = select_dual_unit_batch(units, max_units=1, min_nodes=8, max_nodes=80)
    assert len(picked) == 1
    assert picked[0].unit_id == 3


def test_select_respects_batch_cap() -> None:
    units = [
        _u(1, "A", "B", "m1", "m2", "m3", "m4", "m5", "m6"),
        _u(2, "C", "D", "n1", "n2", "n3", "n4", "n5", "n6"),
        _u(3, "E", "F", "p1", "p2", "p3", "p4", "p5", "p6"),
    ]
    picked = select_dual_unit_batch(
        units, max_units=3, min_nodes=8, max_nodes=80, max_batch_nodes=20
    )
    ids = batch_node_ids(picked)
    assert len(ids) <= 20
    assert len(picked) >= 1


def test_leftover_and_park() -> None:
    left = leftover_batch_ids(
        ["a", "region:x", "b", "c"], max_batch_nodes=2, exclude_ids={"b"}
    )
    assert left == ["a", "c"]
    pos = park_positions(
        {"a": (10.0, 10.0), "c": (30.0, 10.0)},
        ["a", "c"],
        sink_pos={"z": (100.0, 50.0)},
        pad=40.0,
    )
    assert len(pos) == 2
    # Orbit block-sweep: not fixed right — just leave sink tip with clearance.
    assert all("x" in p and "y" in p for p in pos)
    assert max(abs(p["x"] - 100.0) for p in pos) >= 40.0 or max(
        abs(p["y"] - 50.0) for p in pos
    ) >= 40.0


def test_layout_and_pack_batch_zero_cross_units() -> None:
    nodes = [
        {"fabric_node_id": "p1", "name": "BTM-AN1-P", "role": "an", "x": 0, "y": 0},
        {"fabric_node_id": "p2", "name": "BTM-AN2-P", "role": "an", "x": 100, "y": 0},
        {"fabric_node_id": "a1", "name": "BTM-EN-A1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "BTM-EN-A2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b1", "name": "BTM-EN-B1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "b2", "name": "BTM-EN-B2", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "c1", "name": "BTM-EN-C1", "role": "en", "x": 0, "y": 0},
        {"fabric_node_id": "c2", "name": "BTM-EN-C2", "role": "en", "x": 0, "y": 0},
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
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    units = find_dual_portal_units(st)
    assert units
    world, reports, attach = layout_and_pack_batch(
        st, units[:1], sink_pos={"z": (500.0, 0.0)}, pad=50.0, links=list(st.links)
    )
    assert world
    assert attach.get("via") in {"orbit_orphan", "orbit_portal", "orbit_dual", "seed"}
    assert reports and reports[0].get("accepted") is True
    members = units[0].member_ids()
    unit_links = [e for e in st.links if e[0] in members and e[1] in members]
    assert count_edge_crossings(world, unit_links) == 0


def test_tools_registered() -> None:
    names = {str(t.get("name") or "") for t in HTTP_MCP_TOOLS}
    assert "sinkTopologyDualUnits" in names
    assert "copyTopologyViewNodes" in names
    assert len(names) == 14
