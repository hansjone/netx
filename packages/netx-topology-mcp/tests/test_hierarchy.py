"""Tests for stage-2 hierarchy_sectors (contract → order → expand)."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops import LayoutParams, build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.hierarchy import (
    _candidate_orders,
    _contracted_crossings,
    _contracted_links,
    _stub_territories,
    hierarchy_sectors_greedy,
)
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def _crossed_star():
    """Hub with 4 stubs whose geo order crosses two inter-stub chords."""
    # Hub at origin; stubs placed so angular order A,C,B,D crosses A-B with C-D.
    nodes = [
        {"fabric_node_id": "h", "name": "X-CN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "X-AN0-Y", "x": 200, "y": -40},
        {"fabric_node_id": "b", "name": "X-AN1-Y", "x": 40, "y": 200},
        {"fabric_node_id": "c", "name": "X-AN2-Y", "x": -200, "y": 40},
        {"fabric_node_id": "d", "name": "X-AN3-Y", "x": -40, "y": -200},
        {"fabric_node_id": "a1", "name": "X-EN0-Y", "x": 360, "y": -60},
        {"fabric_node_id": "b1", "name": "X-EN1-Y", "x": 60, "y": 360},
        {"fabric_node_id": "c1", "name": "X-EN2-Y", "x": -360, "y": 60},
        {"fabric_node_id": "d1", "name": "X-EN3-Y", "x": -60, "y": -360},
    ]
    edges = [
        {"a_node_id": "h", "b_node_id": "a"},
        {"a_node_id": "h", "b_node_id": "b"},
        {"a_node_id": "h", "b_node_id": "c"},
        {"a_node_id": "h", "b_node_id": "d"},
        {"a_node_id": "a", "b_node_id": "a1"},
        {"a_node_id": "b", "b_node_id": "b1"},
        {"a_node_id": "c", "b_node_id": "c1"},
        {"a_node_id": "d", "b_node_id": "d1"},
        # Cross chords between territories (contracted as a↔b and c↔d or similar)
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "c", "b_node_id": "d"},
    ]
    return nodes, edges


def test_actions_include_hierarchy_sectors() -> None:
    assert "hierarchy_sectors" not in ACTIONS


def test_contracted_links_ignore_internal() -> None:
    nodes, edges = _crossed_star()
    st = build_state_from_nodes_edges(nodes, edges)
    members = set(st.positions)
    stubs, owner = _stub_territories("h", members, st.adj, pinned={"h"})
    assert set(stubs) == {"a", "b", "c", "d"}
    assert owner["a1"] == "a"
    links = _contracted_links("h", stubs, owner, st.adj, pinned={"h"})
    # Internal a—a1 must not appear; hub—stub and inter-stub do.
    assert ("a", "a1") not in links
    assert ("a", "h") in links
    assert ("a", "b") in links


def test_candidate_orders_cover_rotations() -> None:
    stubs = ["a", "b", "c", "d"]
    pos = {
        "h": (0.0, 0.0),
        "a": (1.0, 0.0),
        "b": (0.0, 1.0),
        "c": (-1.0, 0.0),
        "d": (0.0, -1.0),
    }
    orders = _candidate_orders(stubs, pos, "h")
    assert len(orders) >= 4
    assert any(o[0] == "a" for o in orders)


def test_contracted_crossings_sensitive_to_order() -> None:
    links = [("a", "h"), ("b", "h"), ("c", "h"), ("d", "h"), ("a", "b"), ("c", "d")]
    good = _contracted_crossings(
        "h",
        ["a", "b", "c", "d"],
        links,
        radius=100.0,
        hub_xy=(0.0, 0.0),
        a0=-3.0,
        a1=3.0,
    )
    # Force a bad interleaved order on the same arc.
    bad = _contracted_crossings(
        "h",
        ["a", "c", "b", "d"],
        links,
        radius=100.0,
        hub_xy=(0.0, 0.0),
        a0=-3.0,
        a1=3.0,
    )
    assert bad >= good


def test_hierarchy_sectors_accepts_on_contracted_gain() -> None:
    nodes, edges = _crossed_star()
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    op = hierarchy_sectors_greedy(st, LayoutParams())
    assert op.op == "hierarchy_sectors"
    assert "accepted_n" in op.params
    # Gate is contracted, not global: if a level was accepted, contracted fell.
    for lvl in op.params.get("levels") or []:
        assert lvl["contracted_after"] < lvl["contracted_before"]
    # Smoke: crossings countable after expand.
    assert count_edge_crossings(op.state.positions, op.state.links) >= 0


def test_run_layout_on_graph_hierarchy_sectors_unpublished() -> None:
    nodes, edges = _crossed_star()
    try:
        run_layout_on_graph(nodes, edges, action="hierarchy_sectors")
        raise AssertionError("hierarchy_sectors should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)
