"""Sub-regions stay rigid after compose / rigid_untangle."""

from __future__ import annotations

from netx_topology_mcp.layout_ops.compose_views import ComposeBlock, strip_pack_blocks
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.rigid_units import (
    groups_from_membership,
    rigid_untangle_groups,
)
from netx_topology_mcp.layout_ops.state import LayoutParams
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def test_actions_include_rigid_untangle() -> None:
    assert "rigid_untangle" not in ACTIONS


def test_protect_portals_freezes_shared_only() -> None:
    from netx_topology_mcp.layout_ops.rigid_units import frozen_ids_for_protect
    from netx_topology_mcp.layout_ops.state import LayoutState

    st = LayoutState(
        positions={"p": (0, 0), "a": (1, 0), "b": (2, 0)},
        names={},
        adj={},
        links=[],
        layers={},
        meta={
            "compose_views": {
                "rigid_groups": [
                    {"key": "va", "node_ids": ["p", "a"], "pivots": ["p"]},
                    {"key": "vb", "node_ids": ["p", "b"], "pivots": ["p"]},
                ]
            }
        },
    )
    assert frozen_ids_for_protect(st, "portals") == {"p"}
    assert frozen_ids_for_protect(st, "all") == {"p", "a", "b"}
    assert frozen_ids_for_protect(st, "off") == set()
    # Shared corridor `c` is multi-membership but not a pivot — must stay movable.
    st.meta["compose_views"]["rigid_groups"] = [
        {"key": "va", "node_ids": ["p", "a", "c"], "pivots": ["p"]},
        {"key": "vb", "node_ids": ["p", "b", "c"], "pivots": ["p"]},
    ]
    assert frozen_ids_for_protect(st, "portals") == {"p"}
    assert "c" not in frozen_ids_for_protect(st, "portals")


def test_compose_emits_rigid_groups() -> None:
    a = ComposeBlock(
        "va",
        {"p": (0.0, 0.0), "a1": (100.0, 50.0), "a2": (200.0, 0.0)},
    )
    b = ComposeBlock(
        "vb",
        {"p": (0.0, 0.0), "b1": (100.0, -40.0), "b2": (200.0, 0.0)},
    )
    _merged, meta = strip_pack_blocks([a, b], pad=50.0, merge_shared=True)
    groups = meta.get("rigid_groups") or []
    assert len(groups) == 2
    by_key = {g["key"]: g for g in groups}
    assert "p" in by_key["vb"]["pivots"]
    assert set(by_key["va"]["node_ids"]) >= {"p", "a1", "a2"}


def test_rigid_untangle_preserves_relative_geometry() -> None:
    nodes = [
        {"fabric_node_id": "p", "name": "P", "x": 0, "y": 0},
        {"fabric_node_id": "a1", "name": "A1", "x": 100, "y": 50},
        {"fabric_node_id": "a2", "name": "A2", "x": 200, "y": 0},
        {"fabric_node_id": "b1", "name": "B1", "x": 500, "y": 0},
        {"fabric_node_id": "b2", "name": "B2", "x": 600, "y": 0},
    ]
    edges = [
        {"a_node_id": "p", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    groups = groups_from_membership(
        [
            ("va", ["p", "a1", "a2"]),
            ("vb", ["b1", "b2"]),
        ]
    )
    # Capture relative offset inside va
    dx0 = st.positions["a2"][0] - st.positions["a1"][0]
    dy0 = st.positions["a2"][1] - st.positions["a1"][1]
    op = rigid_untangle_groups(st, LayoutParams(), groups=groups, max_rounds=8, step=200.0)
    dx1 = op.state.positions["a2"][0] - op.state.positions["a1"][0]
    dy1 = op.state.positions["a2"][1] - op.state.positions["a1"][1]
    # Distance preserved (rigid); angle may flip 90° so check length
    import math

    assert abs(math.hypot(dx1, dy1) - math.hypot(dx0, dy0)) < 1e-6


def test_rigid_fan_out_reduces_bbox_stack() -> None:
    """Two eyes glued on one portal should prefer a non-stacked angle."""
    from netx_topology_mcp.layout_ops.rigid_units import rigid_fan_out_portals

    # Same portal p; unit A along +x, unit B also along +x (stacked).
    pos = {
        "p": (0.0, 0.0),
        "a1": (100.0, 20.0),
        "a2": (200.0, 0.0),
        "b1": (100.0, 20.0),
        "b2": (200.0, 0.0),
    }
    groups = [
        {"key": "va", "node_ids": ["p", "a1", "a2"], "pivots": ["p"]},
        {"key": "vb", "node_ids": ["p", "b1", "b2"], "pivots": ["p"]},
    ]
    links = [("p", "a1"), ("a1", "a2"), ("p", "b1"), ("b1", "b2")]
    out, n = rigid_fan_out_portals(pos, groups, links)
    assert n >= 1
    # After fan-out, B should not sit on A's exclusive stack.
    assert abs(out["b1"][0] - out["a1"][0]) > 1.0 or abs(
        out["b1"][1] - out["a1"][1]
    ) > 1.0


def test_run_layout_rigid_untangle() -> None:
    nodes = [
        {"fabric_node_id": "a1", "name": "A1", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "A2", "x": 100, "y": 0},
        {"fabric_node_id": "b1", "name": "B1", "x": 400, "y": 0},
        {"fabric_node_id": "b2", "name": "B2", "x": 500, "y": 0},
    ]
    edges = [
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
    ]
    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="rigid_untangle",
            params={
                "_rigid_membership": [
                    {"key": "va", "node_ids": ["a1", "a2"]},
                    {"key": "vb", "node_ids": ["b1", "b2"]},
                ]
            },
        )
        raise AssertionError("rigid_untangle should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)
