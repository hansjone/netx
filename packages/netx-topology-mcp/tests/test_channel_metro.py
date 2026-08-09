"""Tests for channel_metro (channels + ring faces + recipe)."""

from __future__ import annotations

from netx_topology_mcp.layout_ops import LayoutParams, build_state_from_nodes_edges, run_recipe
from netx_topology_mcp.layout_ops.channel_metro import build_channel_metro_skeleton
from netx_topology_mcp.layout_ops.channels import extract_channels, place_channel_ray
from netx_topology_mcp.layout_ops.ring_faces import place_ring_rectangle
from netx_topology_mcp.layout_tool import RECIPE_ALIASES, run_layout_on_graph


def _core_bar_channels():
    """Two cores, two ANs, each with a deg-2 corridor + one triangle ring."""
    nodes = [
        {"fabric_node_id": "c0", "name": "X-CN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "c1", "name": "X-CN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a0", "name": "X-AN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "X-AN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e0", "name": "X-EN0-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e1", "name": "X-EN1-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e2", "name": "X-EN2-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e3", "name": "X-EN3-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e4", "name": "X-EN4-Y", "x": 0, "y": 0},
        {"fabric_node_id": "e5", "name": "X-EN5-Y", "x": 0, "y": 0},
    ]
    edges = [
        {"a_node_id": "c0", "b_node_id": "c1"},
        {"a_node_id": "c0", "b_node_id": "a0"},
        {"a_node_id": "c1", "b_node_id": "a1"},
        # corridor a0-e0-e1
        {"a_node_id": "a0", "b_node_id": "e0"},
        {"a_node_id": "e0", "b_node_id": "e1"},
        # corridor a1-e2-e3
        {"a_node_id": "a1", "b_node_id": "e2"},
        {"a_node_id": "e2", "b_node_id": "e3"},
        # triangle ring on a0 side
        {"a_node_id": "a0", "b_node_id": "e4"},
        {"a_node_id": "e4", "b_node_id": "e5"},
        {"a_node_id": "e5", "b_node_id": "a0"},
    ]
    return nodes, edges


def test_extract_channels() -> None:
    nodes, edges = _core_bar_channels()
    st = build_state_from_nodes_edges(nodes, edges)
    ch = extract_channels(st)
    assert len(ch) >= 1
    assert any(c.length >= 3 for c in ch)


def test_place_channel_ray() -> None:
    placed = place_channel_ray(
        ["h", "a", "b"],
        origin=(0.0, 0.0),
        ux=1.0,
        uy=0.0,
        step=100.0,
        pinned={"h"},
    )
    assert "h" not in placed
    assert placed["a"] == (100.0, 0.0)
    assert placed["b"] == (200.0, 0.0)


def test_ring_rectangle() -> None:
    from netx_topology_mcp.layout_ops.ring_faces import RingFace

    face = RingFace(("a", "b", "c", "d"))
    pos = place_ring_rectangle(face, center=(0, 0), width=200, height=100)
    assert set(pos) == {"a", "b", "c", "d"}


def test_build_channel_metro_places_all() -> None:
    nodes, edges = _core_bar_channels()
    st = build_state_from_nodes_edges(nodes, edges)
    op = build_channel_metro_skeleton(st, LayoutParams())
    assert op.op == "build_channel_metro_skeleton"
    assert set(op.state.positions) == {n["fabric_node_id"] for n in nodes}
    assert op.state.meta.get("rings_mode") == "channel_metro"
    # Cores present; beam pin may or may not accept depending on crossings
    assert "c0" in op.state.positions and "c1" in op.state.positions


def test_recipe_alias_and_run_unpublished() -> None:
    assert "channel_metro" not in RECIPE_ALIASES
    nodes, edges = _core_bar_channels()
    try:
        run_layout_on_graph(nodes, edges, action="layout", recipe="channel_metro")
        raise AssertionError("channel_metro should be unpublished")
    except ValueError as e:
        assert "unknown_recipe" in str(e)


def test_protect_rings_action_unpublished() -> None:
    nodes, edges = _core_bar_channels()
    for i, n in enumerate(nodes):
        n["x"] = float(i * 40)
        n["y"] = float((i % 3) * 30)
    try:
        run_layout_on_graph(nodes, edges, action="protect_rings")
        raise AssertionError("protect_rings should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)


def test_run_recipe_channel_metro_v1_unpublished() -> None:
    nodes, edges = _core_bar_channels()
    st = build_state_from_nodes_edges(nodes, edges)
    try:
        run_recipe(st, "channel_metro_v1", LayoutParams())
        raise AssertionError("channel_metro_v1 should be unpublished")
    except Exception as e:
        assert "channel" in str(e).lower() or "unknown" in str(e).lower() or "recipe" in str(e).lower()


def test_straighten_channels_action() -> None:
    nodes, edges = _core_bar_channels()
    # bent corridor a0-e0-e1
    coords = {
        "c0": (0, 0),
        "c1": (400, 0),
        "a0": (100, 200),
        "a1": (500, 200),
        "e0": (50, 400),
        "e1": (300, 450),
        "e2": (500, 400),
        "e3": (700, 420),
        "e4": (150, 300),
        "e5": (200, 250),
    }
    for n in nodes:
        x, y = coords[n["fabric_node_id"]]
        n["x"], n["y"] = x, y
    out = run_layout_on_graph(nodes, edges, action="straighten_channels")
    assert out["ok"] is True
    assert out["action"] == "straighten_channels"
