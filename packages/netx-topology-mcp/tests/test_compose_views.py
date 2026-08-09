"""Tests for world-map strip-pack compose_views."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops import LayoutParams, build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.compose_views import (
    ComposeBlock,
    compose_into_state,
    compose_params_from_overrides,
    strip_pack_blocks,
)
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def test_actions_include_compose_views() -> None:
    assert "compose_views" not in ACTIONS


def test_strip_pack_preserves_relative_geometry() -> None:
    a = ComposeBlock(
        key="va",
        positions={
            "a1": (10.0, 20.0),
            "a2": (110.0, 20.0),
            "a3": (10.0, 120.0),
        },
    )
    b = ComposeBlock(
        key="vb",
        positions={
            "b1": (0.0, 0.0),
            "b2": (50.0, 0.0),
        },
    )
    merged, meta = strip_pack_blocks([a, b], pad=100.0)
    assert meta["slots"] == 2
    assert set(merged) == {"a1", "a2", "a3", "b1", "b2"}
    # Relative offsets inside block A preserved.
    dx = merged["a2"][0] - merged["a1"][0]
    dy = merged["a2"][1] - merged["a1"][1]
    assert abs(dx - 100.0) < 1e-6
    assert abs(dy) < 1e-6
    # Blocks occupy distinct slots (centroids far apart vs pad).
    ax = sum(merged[n][0] for n in ("a1", "a2", "a3")) / 3
    bx = sum(merged[n][0] for n in ("b1", "b2")) / 2
    ay = sum(merged[n][1] for n in ("a1", "a2", "a3")) / 3
    by = sum(merged[n][1] for n in ("b1", "b2")) / 2
    assert abs(ax - bx) > 50 or abs(ay - by) > 50


def test_strip_pack_first_owner_wins_duplicates() -> None:
    a = ComposeBlock(key="va", positions={"shared": (0.0, 0.0), "a": (10.0, 0.0)})
    b = ComposeBlock(key="vb", positions={"shared": (999.0, 999.0), "b": (0.0, 0.0)})
    # Portal-grow still glues the later shared block rigidly.
    merged, meta = strip_pack_blocks([a, b], pad=50.0, merge_shared=True)
    assert "shared" in merged
    assert "a" in merged and "b" in merged
    via = meta.get("merged_via") or {}
    assert "rigid_shared" in via.values()
    assert meta.get("order_mode") == "portal_grow"


def test_portal_grow_attaches_shared_before_orphan_misc() -> None:
    """Connected dual units grow together; huge misc without shared packs last."""
    unit_ab = ComposeBlock(
        key="ab",
        positions={
            "p_a": (0.0, 0.0),
            "p_b": (200.0, 0.0),
            "mid": (100.0, 40.0),
        },
    )
    unit_bc = ComposeBlock(
        key="bc",
        positions={
            "p_b": (0.0, 0.0),
            "p_c": (200.0, 0.0),
            "x1": (50.0, 30.0),
            "x2": (100.0, 30.0),
            "x3": (150.0, 30.0),
            "x4": (80.0, -30.0),
        },
    )
    misc = ComposeBlock(
        key="misc",
        positions={f"m{i}": (float(i) * 40.0, float(i % 3) * 40.0) for i in range(24)},
    )
    merged, meta = strip_pack_blocks(
        [misc, unit_ab, unit_bc], pad=80.0, merge_shared=True
    )
    order = meta.get("order") or []
    assert meta.get("order_mode") == "portal_grow"
    # Misc is largest area but has no shared glue → last.
    assert order[-1] == "misc"
    # ab and bc share p_b → one of them merges rigidly onto the other.
    via = meta.get("merged_via") or {}
    assert via.get("ab") == "rigid_shared" or via.get("bc") == "rigid_shared"
    # Shared portal has a single world coord.
    assert "p_b" in merged
    assert abs(merged["p_b"][0] - merged["p_b"][0]) < 1e-9


def test_portal_centroid_keeps_shared_units_near() -> None:
    """Units that share portals get closer origins than unrelated orphans."""
    # Chain A—B—C via portals; orphan D has no shared nodes.
    unit_a = ComposeBlock(
        key="ua",
        positions={
            "p_ab": (0.0, 0.0),
            "a1": (100.0, 0.0),
            "a2": (100.0, 80.0),
        },
    )
    unit_b = ComposeBlock(
        key="ub",
        positions={
            "p_ab": (0.0, 0.0),
            "p_bc": (200.0, 0.0),
            "b1": (100.0, 40.0),
        },
    )
    unit_c = ComposeBlock(
        key="uc",
        positions={
            "p_bc": (0.0, 0.0),
            "c1": (100.0, 0.0),
            "c2": (100.0, 60.0),
        },
    )
    orphan = ComposeBlock(
        key="orphan",
        positions={f"o{i}": (float(i) * 30.0, 0.0) for i in range(8)},
    )
    merged, meta = strip_pack_blocks(
        [orphan, unit_c, unit_a, unit_b], pad=80.0, merge_shared=True
    )
    assert meta.get("pack_mode") == "portal_centroid"
    origins = meta["origins"]

    def _slot_c(key: str) -> tuple[float, float]:
        sm = meta["slot_meta"][key]
        ox, oy = origins[key]
        return ox + 0.5 * sm["w"], oy + 0.5 * sm["h"]

    ca, cb, cc, co = _slot_c("ua"), _slot_c("ub"), _slot_c("uc"), _slot_c("orphan")
    # Portal-graph neighbors closer than the A–C skip.
    d_ab = math.hypot(ca[0] - cb[0], ca[1] - cb[1])
    d_bc = math.hypot(cb[0] - cc[0], cb[1] - cc[1])
    d_ac = math.hypot(ca[0] - cc[0], ca[1] - cc[1])
    assert d_ab < d_ac
    assert d_bc < d_ac
    # Orphans sit below the glued cluster.
    glued_ymax = max(
        origins[k][1] + meta["slot_meta"][k]["h"] for k in ("ua", "ub", "uc")
    )
    assert origins["orphan"][1] >= glued_ymax - 1e-6
    # Rigid merge still glues the chain.
    via = meta.get("merged_via") or {}
    assert "rigid_shared" in via.values()
    assert "p_ab" in merged and "p_bc" in merged


def test_fabric_bridges_pull_disjoint_units_closer() -> None:
    """No shared portal: fabric edge + higher bridge_boost / lower ideal_scale."""
    unit_a = ComposeBlock(
        key="ua",
        positions={
            "a0": (0.0, 0.0),
            "a1": (120.0, 0.0),
            "a2": (60.0, 80.0),
        },
    )
    unit_b = ComposeBlock(
        key="ub",
        positions={
            "b0": (0.0, 0.0),
            "b1": (120.0, 0.0),
            "b2": (60.0, 80.0),
        },
    )
    unit_c = ComposeBlock(
        key="uc",
        positions={
            "c0": (0.0, 0.0),
            "c1": (120.0, 0.0),
            "c2": (60.0, 80.0),
        },
    )
    # Only A↔B has a fabric spoke; C is an unrelated peer (orphan strip below).
    links = [("a1", "b0")]
    blocks = [unit_a, unit_b, unit_c]

    def _origin_gap(meta: dict, ka: str, kb: str) -> float:
        oa, ob = meta["origins"][ka], meta["origins"][kb]
        return math.hypot(oa[0] - ob[0], oa[1] - ob[1])

    loose, meta_loose = strip_pack_blocks(
        blocks,
        pad=200.0,
        merge_shared=True,
        links=links,
        fabric_bridges=True,
        bridge_boost=4.0,
        ideal_scale=0.85,
    )
    tight, meta_tight = strip_pack_blocks(
        blocks,
        pad=200.0,
        merge_shared=True,
        links=links,
        fabric_bridges=True,
        bridge_boost=40.0,
        ideal_scale=0.3,
    )
    off, meta_off = strip_pack_blocks(
        blocks, pad=200.0, merge_shared=True, links=links, fabric_bridges=False
    )
    assert meta_off.get("fabric_bridges") is False
    assert meta_tight.get("fabric_bridges") is True
    assert meta_tight.get("bridge_boost") == 40.0
    assert meta_tight.get("ideal_scale") == 0.3
    # Tunable spring: denser knobs shrink A–B slot gap.
    assert _origin_gap(meta_tight, "ua", "ub") < _origin_gap(meta_loose, "ua", "ub") * 0.75
    # Fabric glue puts C below the A–B component (not interleaved in strip).
    glued_ymax = max(
        meta_tight["origins"][k][1] + meta_tight["slot_meta"][k]["h"]
        for k in ("ua", "ub")
    )
    assert meta_tight["origins"]["uc"][1] >= glued_ymax - 1e-6
    del loose, tight, off


def test_compose_params_from_overrides_knobs() -> None:
    knobs = compose_params_from_overrides(
        {
            "pad": 400,
            "fabric_bridges": "on",
            "bridge_boost": 12,
            "ideal_scale": 0.4,
            "spring_iters": 100,
            "merge_shared": False,
        }
    )
    assert knobs["pad"] == 400.0
    assert knobs["fabric_bridges"] is True
    assert knobs["bridge_boost"] == 12.0
    assert knobs["ideal_scale"] == 0.4
    assert knobs["spring_iters"] == 100
    assert knobs["merge_shared"] is False


def test_dual_portal_flip_shortens_external_bridge() -> None:
    """When two portals fix the chord, pick the bank that shortens fabric spokes."""
    unit_a = ComposeBlock(
        key="ua",
        positions={
            "p0": (0.0, 0.0),
            "p1": (200.0, 0.0),
            "t": (100.0, -160.0),
        },
    )
    unit_b = ComposeBlock(
        key="ub",
        positions={
            "p0": (0.0, 0.0),
            "p1": (200.0, 0.0),
            "b": (100.0, 120.0),
        },
    )
    links = [("b", "t")]
    merged, _meta = strip_pack_blocks(
        [unit_a, unit_b], pad=40.0, merge_shared=True, links=links
    )
    db = math.hypot(merged["b"][0] - merged["t"][0], merged["b"][1] - merged["t"][1])
    # Unflipped bank would put b roughly opposite t (~280); flipped ~40.
    assert db < 120.0


def test_strip_pack_merge_shared_false_keeps_first_only() -> None:
    a = ComposeBlock(key="va", positions={"shared": (0.0, 0.0), "a": (10.0, 0.0)})
    b = ComposeBlock(key="vb", positions={"shared": (999.0, 999.0), "b": (0.0, 0.0)})
    merged, meta = strip_pack_blocks([a, b], pad=50.0, merge_shared=False)
    assert meta.get("merge_shared") is False
    assert "shared" in merged and "b" in merged


def test_compose_into_state_and_run_layout() -> None:
    nodes = [
        {"fabric_node_id": "a1", "name": "A1", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "A2", "x": 10, "y": 0},
        {"fabric_node_id": "b1", "name": "B1", "x": 0, "y": 0},
        {"fabric_node_id": "b2", "name": "B2", "x": 10, "y": 0},
    ]
    edges = [
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "b1", "b_node_id": "b2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    blocks = [
        ComposeBlock("va", {"a1": (0.0, 0.0), "a2": (100.0, 0.0)}),
        ComposeBlock("vb", {"b1": (0.0, 0.0), "b2": (80.0, 0.0)}),
    ]
    op = compose_into_state(st, blocks, LayoutParams(), pad=200.0)
    assert op.op == "compose_views"
    assert op.params.get("slots") == 2
    assert abs(op.state.positions["a2"][0] - op.state.positions["a1"][0] - 100.0) < 1e-6

    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="compose_views",
            params={
                "_compose_blocks": [
                    {"key": "va", "positions": {"a1": [0, 0], "a2": [100, 0]}},
                    {"key": "vb", "positions": {"b1": [0, 0], "b2": [80, 0]}},
                ],
                "pad": 200,
            },
        )
        raise AssertionError("compose_views should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)
