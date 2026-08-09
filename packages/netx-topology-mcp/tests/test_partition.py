"""Soft partition: hub territory (always) + optional igraph."""

from __future__ import annotations

from netx_topology_mcp.layout_ops import (
    build_state_from_nodes_edges,
    igraph_available,
    list_blocks,
    pack_block_centers,
    partition_soft_blocks,
)
from netx_topology_mcp.layout_structure import analyze_graph_structure


def _star_graph():
    # Two hubs + private stubs + shared leftover chain
    nodes = [
        {"fabric_node_id": "h1", "name": "H1-AN-1", "x": 0, "y": 0},
        {"fabric_node_id": "h2", "name": "H2-AN-1", "x": 100, "y": 0},
        {"fabric_node_id": "a1", "name": "A1-EN-1", "x": 0, "y": 20},
        {"fabric_node_id": "a2", "name": "A2-EN-1", "x": 0, "y": 40},
        {"fabric_node_id": "b1", "name": "B1-EN-1", "x": 100, "y": 20},
        {"fabric_node_id": "b2", "name": "B2-EN-1", "x": 100, "y": 40},
        {"fabric_node_id": "z1", "name": "Z1-EN-1", "x": 50, "y": 80},
        {"fabric_node_id": "z2", "name": "Z2-EN-1", "x": 50, "y": 100},
    ]
    edges = [
        {"a_node_id": "h1", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "h2", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "h1", "b_node_id": "h2"},
        {"a_node_id": "a2", "b_node_id": "z1"},
        {"a_node_id": "z1", "b_node_id": "z2"},
    ]
    return nodes, edges


def test_hub_territory_splits_by_hub() -> None:
    nodes, edges = _star_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    blocks = partition_soft_blocks(st, mode="hub_territory")
    methods = {b.method for b in blocks}
    assert "hub_territory" in methods
    by_hub = {b.hub_id: set(b.node_ids) for b in blocks if b.hub_id}
    assert "h1" in by_hub and "h2" in by_hub
    assert "a1" in by_hub["h1"] and "a2" in by_hub["h1"]
    assert "b1" in by_hub["h2"] and "b2" in by_hub["h2"]
    # z* claimed via a2 → h1 territory (first BFS from stubs)
    assert "z1" in by_hub["h1"] or any(
        "z1" in b.node_ids for b in blocks if b.method == "leftover"
    )


def test_list_blocks_hub_territory_mode() -> None:
    nodes, edges = _star_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    sets = list_blocks(st, mode="hub_territory")
    assert len(sets) >= 2
    assert all(isinstance(s, set) and s for s in sets)


def test_pack_block_centers_grid_fallback() -> None:
    nodes, edges = _star_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    blocks = partition_soft_blocks(st, mode="hub_territory")
    centers = pack_block_centers(st, blocks)
    assert len(centers) == len(blocks)
    assert all(isinstance(xy, tuple) and len(xy) == 2 for xy in centers.values())


def test_structure_includes_soft_blocks() -> None:
    nodes, edges = _star_graph()
    struct = analyze_graph_structure(nodes, edges)
    sb = struct.get("soft_blocks") or {}
    assert sb.get("block_count", 0) >= 2
    assert "blocks" in sb
    assert "igraph" in sb
    assert struct["advice"].get("decompose_soft_blocks") in {True, False}


def test_leiden_mode_falls_back_without_crash() -> None:
    nodes, edges = _star_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    blocks = partition_soft_blocks(st, mode="leiden")
    assert blocks
    # With or without igraph, we still get a partition.
    assert sum(len(b.node_ids) for b in blocks) >= len(st.positions)
    _ = igraph_available()  # smoke


def test_resolve_auto_small_graph_stays_component() -> None:
    from netx_topology_mcp.layout_ops import resolve_block_mode

    nodes, edges = _star_graph()
    st = build_state_from_nodes_edges(nodes, edges)
    # 8 nodes < 40 → auto keeps component
    assert resolve_block_mode(st, "auto") == "component"


def test_pack_soft_blocks_moves_territories_apart() -> None:
    from netx_topology_mcp.layout_ops import pack_soft_blocks

    nodes, edges = _star_graph()
    # Stack both hubs on top of each other so pack must separate
    for n in nodes:
        if n["fabric_node_id"] in {"h1", "a1", "a2"}:
            n["x"], n["y"] = 0.0, 0.0
        else:
            n["x"], n["y"] = 5.0, 5.0
    st = build_state_from_nodes_edges(nodes, edges)
    before = dict(st.positions)
    out = pack_soft_blocks(st, mode="hub_territory")
    assert out.op == "pack_soft_blocks"
    assert len(out.moved) >= 2
    # centroids of the two hub blocks should diverge
    blocks = partition_soft_blocks(out.state, mode="hub_territory")
    hubs = [b for b in blocks if b.hub_id]
    assert len(hubs) >= 2

    def centroid(ids):
        pts = [out.state.positions[i] for i in ids if i in out.state.positions]
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )

    c0, c1 = centroid(hubs[0].node_ids), centroid(hubs[1].node_ids)
    dist = ((c0[0] - c1[0]) ** 2 + (c0[1] - c1[1]) ** 2) ** 0.5
    assert dist > 50.0
    # something moved vs stacked start
    assert any(out.state.positions[n] != before[n] for n in out.moved)


def test_pack_soft_blocks_preserves_spread_constellation() -> None:
    from netx_topology_mcp.layout_ops import pack_soft_blocks

    nodes, edges = _star_graph()
    # Already-spread hubs: preserve strategy densifies but keeps left/right order
    for n in nodes:
        fid = n["fabric_node_id"]
        if fid in {"h1", "a1", "a2"}:
            n["x"], n["y"] = 0.0, 0.0
        elif fid in {"h2", "b1", "b2"}:
            n["x"], n["y"] = 2000.0, 0.0
        else:
            n["x"], n["y"] = 1000.0, 400.0
    st = build_state_from_nodes_edges(nodes, edges)
    out = pack_soft_blocks(st, mode="hub_territory", gap=420.0, strategy="auto")
    assert out.params.get("strategy") == "preserve"
    h1 = out.state.positions["h1"]
    h2 = out.state.positions["h2"]
    assert h1[0] < h2[0]
    # densified toward gap, not remapped onto a fresh grid origin
    assert abs(h2[0] - h1[0]) < 2000.0
    assert abs(h2[0] - h1[0]) > 200.0


def test_compact_soft_recipe_unpublished() -> None:
    from netx_topology_mcp.layout_tool import resolve_recipe

    try:
        resolve_recipe("compact_soft")
        raise AssertionError("compact_soft should be unpublished")
    except ValueError as e:
        assert "unknown_recipe" in str(e)
