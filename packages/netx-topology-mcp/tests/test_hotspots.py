"""Local hotspot / overlap fix tests."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops import (
    LayoutParams,
    build_state_from_nodes_edges,
    fix_overlaps_local,
    relax_hotspots,
)
from netx_topology_mcp.layout_ops.hotspots import overlapping_nodes
from netx_topology_mcp.layout_tool import run_layout_on_graph


def test_fix_overlaps_local_pulls_apart() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 3, "y": 0},
        {"fabric_node_id": "c", "name": "CCCCCC-EN-3", "x": 2000, "y": 0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    assert len(overlapping_nodes(st)) >= 2
    far_before = st.positions["c"]
    out = fix_overlaps_local(st, LayoutParams(overlap_iters=200, overlap_step=5.0))
    assert len(overlapping_nodes(out.state)) == 0
    # distant node should barely move (outside 1-hop of a-b... c is 1-hop from b)
    # c may move if in expand; at least a/b separated
    assert abs(out.state.positions["a"][0] - out.state.positions["b"][0]) > 10


def test_run_layout_action_pack_utilization_unpublished() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 8000, "y": 0},
        {"fabric_node_id": "c", "name": "CCCCCC-EN-3", "x": 16000, "y": 0},
        {"fabric_node_id": "d", "name": "DDDDDD-AN-1", "x": 8000, "y": 8000},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "b", "b_node_id": "d"},
    ]
    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="pack_utilization",
            preset="balanced",
            params={
                "target_util": 0.15,
                "pack_min_scale": 0.2,
                "pack_iters": 6,
                "shrink_corridors": True,
                "corridor_cap": 2000,
                "pull": 0.6,
            },
        )
        raise AssertionError("pack_utilization should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)


def test_pack_utilization_transform_still_works() -> None:
    from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
    from netx_topology_mcp.layout_ops.state import LayoutParams
    from netx_topology_mcp.layout_ops.transforms import pack_utilization

    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 8000, "y": 0},
        {"fabric_node_id": "c", "name": "CCCCCC-EN-3", "x": 16000, "y": 0},
        {"fabric_node_id": "d", "name": "DDDDDD-AN-1", "x": 8000, "y": 8000},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "b", "b_node_id": "d"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    op = pack_utilization(
        st,
        LayoutParams(target_util=0.15, pack_min_scale=0.2, pack_iters=6),
    )
    assert op.op == "pack_utilization"
    xs = [p[0] for p in op.state.positions.values()]
    ys = [p[1] for p in op.state.positions.values()]
    assert max(xs) - min(xs) < 16000
    assert max(ys) - min(ys) < 8000
    local = {"mode": (op.params or {}).get("mode"), "op": op.params or {}}
    op = local.get("op") or {}
    # Corridor shrink reports bbox_area_ratio; uniform reports util_after.
    if op.get("bbox_area_ratio") is not None:
        assert float(op["bbox_area_ratio"]) >= 1.0
    else:
        assert float(op.get("util_after") or 0) > float(op.get("util_before") or 0)


def test_shrink_long_corridors_pulls_islands() -> None:
    from netx_topology_mcp.layout_ops.rigid_units import shrink_long_corridors

    # Two dense islands connected by one long bridge.
    nodes = [
        {"fabric_node_id": "a1", "name": "A1-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "A2-EN-2", "x": 200, "y": 0},
        {"fabric_node_id": "a3", "name": "A3-EN-3", "x": 0, "y": 200},
        {"fabric_node_id": "b1", "name": "B1-EN-1", "x": 8000, "y": 8000},
        {"fabric_node_id": "b2", "name": "B2-EN-2", "x": 8200, "y": 8000},
        {"fabric_node_id": "b3", "name": "B3-EN-3", "x": 8000, "y": 8200},
    ]
    edges = [
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a1", "b_node_id": "a3"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b1", "b_node_id": "b3"},
        {"a_node_id": "a1", "b_node_id": "b1"},  # long corridor
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    d0 = math.hypot(
        st.positions["a1"][0] - st.positions["b1"][0],
        st.positions["a1"][1] - st.positions["b1"][1],
    )
    out = shrink_long_corridors(
        st,
        edge_len_cap=1200,
        pull=0.6,
        iters=3,
        min_island=3,
        max_bridges=4,
        accept_crossings=False,
    )
    d1 = math.hypot(
        out.state.positions["a1"][0] - out.state.positions["b1"][0],
        out.state.positions["a1"][1] - out.state.positions["b1"][1],
    )
    assert d1 < d0 * 0.55
    # Intra-island spacing preserved.
    assert abs(
        math.hypot(
            out.state.positions["a1"][0] - out.state.positions["a2"][0],
            out.state.positions["a1"][1] - out.state.positions["a2"][1],
        )
        - 200.0
    ) < 1.0
    assert float((out.params or {}).get("bbox_area_ratio") or 0) > 1.5


def test_shrink_long_corridors_unit_exclusive() -> None:
    from netx_topology_mcp.layout_ops.rigid_units import shrink_long_corridors

    # Shared portal + two exclusive clusters far apart (metro dual-unit shape).
    nodes = [
        {"fabric_node_id": "p", "name": "PORTAL-AN-1", "x": 5000, "y": 5000},
        {"fabric_node_id": "a1", "name": "A1-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "A2-EN-2", "x": 200, "y": 0},
        {"fabric_node_id": "a3", "name": "A3-EN-3", "x": 0, "y": 200},
        {"fabric_node_id": "b1", "name": "B1-EN-1", "x": 10000, "y": 10000},
        {"fabric_node_id": "b2", "name": "B2-EN-2", "x": 10200, "y": 10000},
        {"fabric_node_id": "b3", "name": "B3-EN-3", "x": 10000, "y": 10200},
    ]
    edges = [
        {"a_node_id": "p", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a1", "b_node_id": "a3"},
        {"a_node_id": "p", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b1", "b_node_id": "b3"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    groups = [
        {"key": "u0", "node_ids": ["p", "a1", "a2", "a3"], "pivots": ["p"]},
        {"key": "u1", "node_ids": ["p", "b1", "b2", "b3"], "pivots": ["p"]},
    ]
    d0 = math.hypot(
        st.positions["a1"][0] - st.positions["b1"][0],
        st.positions["a1"][1] - st.positions["b1"][1],
    )
    out = shrink_long_corridors(
        st,
        edge_len_cap=1500,
        pull=0.55,
        iters=4,
        min_island=3,
        max_bridges=4,
        groups=groups,
        accept_crossings=True,
    )
    d1 = math.hypot(
        out.state.positions["a1"][0] - out.state.positions["b1"][0],
        out.state.positions["a1"][1] - out.state.positions["b1"][1],
    )
    assert (out.params or {}).get("island_mode") == "unit_exclusive"
    assert d1 < d0 * 0.75
    assert float((out.params or {}).get("bbox_area_ratio") or 0) > 1.2
    # Portal may move less / stay; exclusives keep relative spacing.
    assert abs(
        math.hypot(
            out.state.positions["a1"][0] - out.state.positions["a2"][0],
            out.state.positions["a1"][1] - out.state.positions["a2"][1],
        )
        - 200.0
    ) < 1.0


def test_densify_rigid_groups_raises_util() -> None:
    from netx_topology_mcp.layout_ops.rigid_units import densify_rigid_groups

    nodes = [
        {"fabric_node_id": "p", "name": "PORTAL-AN-1", "x": 5000, "y": 5000},
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "CCCCCC-EN-3", "x": 10000, "y": 10000},
        {"fabric_node_id": "d", "name": "DDDDDD-EN-4", "x": 10200, "y": 10000},
    ]
    edges = [
        {"a_node_id": "p", "b_node_id": "a"},
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "p", "b_node_id": "c"},
        {"a_node_id": "c", "b_node_id": "d"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    groups = [
        {"key": "u0", "node_ids": ["p", "a", "b"], "pivots": ["p"]},
        {"key": "u1", "node_ids": ["p", "c", "d"], "pivots": ["p"]},
    ]
    xs0 = [st.positions[n][0] for n in st.positions]
    ys0 = [st.positions[n][1] for n in st.positions]
    area0 = (max(xs0) - min(xs0)) * (max(ys0) - min(ys0))
    out = densify_rigid_groups(st, groups, scale=0.5)
    xs1 = [out.state.positions[n][0] for n in out.state.positions]
    ys1 = [out.state.positions[n][1] for n in out.state.positions]
    area1 = (max(xs1) - min(xs1)) * (max(ys1) - min(ys1))
    assert area1 < area0 * 0.85
    # Intra-unit gap preserved (a-b still ~200).
    assert abs(
        math.hypot(
            out.state.positions["a"][0] - out.state.positions["b"][0],
            out.state.positions["a"][1] - out.state.positions["b"][1],
        )
        - 200.0
    ) < 1.0


def test_run_layout_pack_compress_long_edges_unpublished() -> None:
    nodes = [
        {"fabric_node_id": "p", "name": "PORTAL-AN-1", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 5000, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 0, "y": 5000},
    ]
    edges = [
        {"a_node_id": "p", "b_node_id": "a"},
        {"a_node_id": "p", "b_node_id": "b"},
    ]
    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="pack_utilization",
            preset="balanced",
            params={
                "portal_ids": ["p"],
                "shrink_corridors": True,
                "corridor_cap": 900,
                "pull": 0.6,
                "compress_iters": 4,
            },
        )
        raise AssertionError("pack_utilization should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)
def test_run_layout_action_fix_overlaps() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA-EN-1", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB-EN-2", "x": 2, "y": 1},
        {"fabric_node_id": "c", "name": "CCCCCC-AN-1", "x": 400, "y": 0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "b"},
        {"a_node_id": "b", "b_node_id": "c"},
    ]
    out = run_layout_on_graph(nodes, edges, action="fix_overlaps", preset="balanced")
    assert out["ok"] is True
    assert out["action"] == "fix_overlaps"
    assert out["overlap"]["footprint_pairs"] == 0
    # Should not rebuild corridor skeleton to origin-scale extremes
    xs = [p["x"] for p in out["positions"]]
    assert max(xs) - min(xs) < 5000


def test_relax_hotspots_moves_only_dense() -> None:
    # 6 coinciding + 1 disconnected far island
    nodes = [
        {"fabric_node_id": f"n{i}", "name": f"N{i}-EN-x", "x": float(i % 2), "y": 0.0}
        for i in range(6)
    ]
    nodes.append({"fabric_node_id": "far", "name": "FAR-EN-z", "x": 8000.0, "y": 0.0})
    nodes.append({"fabric_node_id": "far2", "name": "FAR2-EN-z", "x": 8200.0, "y": 0.0})
    edges = [{"a_node_id": f"n{i}", "b_node_id": f"n{i+1}"} for i in range(5)]
    edges.append({"a_node_id": "far", "b_node_id": "far2"})
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    far0 = st.positions["far"]
    out = relax_hotspots(st, LayoutParams(cluster_gap=40.0, target_util=0.1))
    # disconnected far island should stay put (not in dense hotspot)
    assert abs(out.state.positions["far"][0] - far0[0]) < 1.0
