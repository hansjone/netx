"""Structure / gravity detection for layout planning."""

from __future__ import annotations

from netx_topology_mcp.layout_structure import analyze_graph_structure


def _nodes_edges_core_bar():
    """Two cores own access rings; ANs are low-degree decorative."""
    nodes = [
        {"fabric_node_id": "c1", "name": "X-CN1-a", "role": "core", "x": 0, "y": 0},
        {"fabric_node_id": "c2", "name": "X-CN2-a", "role": "core", "x": 200, "y": 0},
        {"fabric_node_id": "a1", "name": "X-AN1-a", "role": "aggregation", "x": 100, "y": 50},
        {"fabric_node_id": "a2", "name": "X-AN2-a", "role": "aggregation", "x": 300, "y": 50},
    ]
    edges = [
        {"a_node_id": "c1", "b_node_id": "c2"},
        {"a_node_id": "c1", "b_node_id": "a1"},
        {"a_node_id": "c2", "b_node_id": "a2"},
    ]
    # Access petals hanging on cores
    for i in range(12):
        eid = f"e{i}"
        nodes.append(
            {
                "fabric_node_id": eid,
                "name": f"X-EN{i}-a",
                "role": "access",
                "x": i * 40,
                "y": 200,
            }
        )
        hub = "c1" if i < 6 else "c2"
        edges.append({"a_node_id": hub, "b_node_id": eid})
        if i % 2 == 1:
            edges.append({"a_node_id": f"e{i - 1}", "b_node_id": eid})
    return nodes, edges


def _nodes_edges_agg_bar():
    """No cores; ANs own access rings."""
    nodes = [
        {"fabric_node_id": "a1", "name": "Y-AN1-a", "role": "aggregation", "x": 0, "y": 0},
        {"fabric_node_id": "a2", "name": "Y-AN2-a", "role": "aggregation", "x": 400, "y": 0},
    ]
    edges = [{"a_node_id": "a1", "b_node_id": "a2"}]
    for i in range(16):
        eid = f"e{i}"
        nodes.append(
            {
                "fabric_node_id": eid,
                "name": f"Y-EN{i}-a",
                "role": "access",
                "x": i * 30,
                "y": 200,
            }
        )
        hub = "a1" if i < 8 else "a2"
        edges.append({"a_node_id": hub, "b_node_id": eid})
        if i % 2 == 1:
            edges.append({"a_node_id": f"e{i - 1}", "b_node_id": eid})
    return nodes, edges


def test_core_bar_gravity() -> None:
    nodes, edges = _nodes_edges_core_bar()
    s = analyze_graph_structure(nodes, edges)
    assert s["gravity"]["type"] == "core_bar"
    assert s["gravity"]["anchor_layer"] == "core"
    assert "agg" in s["gravity"]["decorative_layers"] or s["layers"]["agg"]["territory_frac"] < 0.2
    assert s["gravity"]["recipe_preference"][0] in {"compact", "corridor"}
    assert s["advice"]["skip_rings_first"] is True
    assert s["gravity"]["geometry_hint"] in {"core_center", "core_top"}


def test_agg_bar_gravity() -> None:
    nodes, edges = _nodes_edges_agg_bar()
    s = analyze_graph_structure(nodes, edges)
    assert s["gravity"]["type"] == "agg_bar"
    assert s["gravity"]["anchor_layer"] == "agg"
    assert s["gravity"]["recipe_preference"][0] == "rings"
    assert s["advice"]["skip_rings_first"] is False
    assert len(s["hubs"]) >= 2


def test_hubs_include_stubs() -> None:
    nodes, edges = _nodes_edges_agg_bar()
    s = analyze_graph_structure(nodes, edges, hub_top_k=4, stub_top_k=10)
    assert s["hubs"][0]["access_neighbors"] >= 1
    assert s["stubs"]
    assert "headline" in s


def _nodes_edges_chains():
    """Several path components (SID-TOB-like): mostly deg≤2, not hub-spoke."""
    nodes: list[dict] = []
    edges: list[dict] = []
    # three chains of lengths 12 / 8 / 5; one has a CN/AN on the path (still chain)
    chains = [
        [("c0", "Z-CN1-a", "core")]
        + [(f"a{i}", f"Z-EN{i}-a", "access") for i in range(1, 12)],
        [(f"b{i}", f"Y-EN{i}-a", "access") for i in range(8)],
        [(f"d{i}", f"X-EN{i}-a", "access") for i in range(5)],
    ]
    for ch in chains:
        for fid, name, role in ch:
            nodes.append({"fabric_node_id": fid, "name": name, "role": role, "x": 0, "y": 0})
        for i in range(len(ch) - 1):
            edges.append({"a_node_id": ch[i][0], "b_node_id": ch[i + 1][0]})
    return nodes, edges


def test_chains_gravity_before_hub_fallback() -> None:
    nodes, edges = _nodes_edges_chains()
    s = analyze_graph_structure(nodes, edges)
    assert s["shape"]["primary"] == "chains"
    assert s["gravity"]["type"] == "chains"
    assert s["gravity"]["geometry_hint"] == "chain_rows"
    assert s["gravity"]["recipe_preference"][0] == "corridor"
    assert s["advice"]["skip_rings_first"] is True
    assert s["advice"]["decompose_by_component"] is True
    assert s["shape"]["component_count"] == 3
    assert all(b["shape"] in {"chain", "tiny"} for b in s["shape"]["blocks"])
