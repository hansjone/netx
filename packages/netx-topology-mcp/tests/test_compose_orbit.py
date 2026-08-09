"""Tests for incremental Prim+orbit compose_orbit."""

from __future__ import annotations

from netx_topology_mcp.layout_ops import LayoutParams, build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.compose_orbit import (
    compose_orbit_into_state,
    compose_orbit_params_from_overrides,
    crossings_touching,
    orbit_pack_blocks,
)
from netx_topology_mcp.layout_ops.compose_views import ComposeBlock
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def test_actions_include_compose_orbit() -> None:
    assert "compose_orbit" not in ACTIONS


def test_orbit_params_parse_knobs() -> None:
    knobs = compose_orbit_params_from_overrides(
        {
            "source_view_ids": ["a", "b"],
            "pad": 400,
            "angle_step": 45,
            "radii": [1.0, 1.5],
            "cand_cap": 40,
        }
    )
    assert knobs["pad"] == 400
    assert knobs["angle_step"] == 45
    assert knobs["radii"] == (1.0, 1.5)
    assert knobs["cand_cap"] == 40
    assert knobs["source_view_ids"] == ["a", "b"]


def test_orbit_pack_glues_shared_portal() -> None:
    unit_a = ComposeBlock(
        key="ua",
        positions={
            "p": (0.0, 0.0),
            "a1": (120.0, 0.0),
            "a2": (120.0, 80.0),
        },
    )
    unit_b = ComposeBlock(
        key="ub",
        positions={
            "p": (0.0, 0.0),
            "b1": (120.0, 0.0),
            "b2": (120.0, -80.0),
        },
    )
    links = [
        ("p", "a1"),
        ("a1", "a2"),
        ("p", "b1"),
        ("b1", "b2"),
        ("a2", "b2"),
    ]
    merged, meta = orbit_pack_blocks(
        [unit_a, unit_b],
        pad=200.0,
        links=links,
        fabric_bridges=True,
        angle_step=30,
        cand_cap=60,
    )
    assert meta["mode"] == "compose_orbit"
    assert meta["pack_mode"] == "orbit_attach"
    assert meta["slots"] == 2
    assert set(merged) == {"p", "a1", "a2", "b1", "b2"}
    # Shared portal is a single point (first-owner / align).
    assert "p" in merged
    via = meta.get("merged_via") or {}
    assert "orbit" in " ".join(str(v) for v in via.values()) or any(
        "shared" in str(v) or "orbit" in str(v) for v in via.values()
    )


def test_crossings_touching_partial() -> None:
    # Two segments crossing; focus only on one endpoint set.
    pos = {
        "a": (0.0, 0.0),
        "b": (10.0, 10.0),
        "c": (0.0, 10.0),
        "d": (10.0, 0.0),
    }
    links = [("a", "b"), ("c", "d")]
    assert crossings_touching(pos, links, {"a", "b"}) == 1
    assert crossings_touching(pos, links, {"z"}) == 0


def test_compose_orbit_into_state_and_run_layout() -> None:
    nodes = [
        {"id": "p", "x": 0, "y": 0},
        {"id": "a1", "x": 100, "y": 0},
        {"id": "b1", "x": 0, "y": 100},
    ]
    edges = [{"source": "p", "target": "a1"}, {"source": "p", "target": "b1"}]
    st = build_state_from_nodes_edges(nodes, edges)
    blocks = [
        ComposeBlock(key="ua", positions={"p": (0.0, 0.0), "a1": (100.0, 0.0)}),
        ComposeBlock(key="ub", positions={"p": (0.0, 0.0), "b1": (100.0, 0.0)}),
    ]
    op = compose_orbit_into_state(st, blocks, LayoutParams(), pad=150.0)
    assert op.op == "compose_orbit"
    assert "compose_views" in (op.state.meta or {})
    assert (op.state.meta.get("compose_views") or {}).get("mode") == "compose_orbit"

    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="compose_orbit",
            params={
                "_compose_blocks": [
                    {"key": "ua", "positions": {"p": [0, 0], "a1": [100, 0]}},
                    {"key": "ub", "positions": {"p": [0, 0], "b1": [100, 0]}},
                ],
                "rigid_polish": False,
                "soft_polish": False,
                "pad": 150,
                "angle_step": 45,
            },
        )
        raise AssertionError("compose_orbit should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)
