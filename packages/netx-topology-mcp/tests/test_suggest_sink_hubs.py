"""Tests for suggestSinkHubs batch ranking."""

from __future__ import annotations

from netx_topology_mcp.layout_ops.suggest_sink_hubs import (
    pick_batch,
    suggest_sink_hub_batches,
)
from netx_topology_mcp.layout_structure import analyze_graph_structure


def _star_graph():
    # CN portals + TJP-like agg hub with 3 EN stubs + small GENA hub with 1 stub
    nodes = [
        {"fabric_node_id": "cn1", "name": "X-CN1-Y", "level": 1, "x": 0, "y": 0},
        {"fabric_node_id": "cn2", "name": "X-CN2-Y", "level": 1, "x": 100, "y": 0},
        {"fabric_node_id": "tjp", "name": "X-TJP-AN1-Y", "level": 2, "x": 50, "y": 100},
        {"fabric_node_id": "e1", "name": "X-E1-EN1-Y", "level": 3, "x": 0, "y": 200},
        {"fabric_node_id": "e2", "name": "X-E2-EN1-Y", "level": 3, "x": 50, "y": 200},
        {"fabric_node_id": "e3", "name": "X-E3-EN1-Y", "level": 3, "x": 100, "y": 200},
        {"fabric_node_id": "gena", "name": "X-GENA-AN1-Y", "level": 2, "x": 200, "y": 100},
        {"fabric_node_id": "e4", "name": "X-E4-EN1-Y", "level": 3, "x": 200, "y": 200},
    ]
    edges = [
        {"a_node_id": "cn1", "b_node_id": "cn2"},
        {"a_node_id": "cn1", "b_node_id": "tjp"},
        {"a_node_id": "cn2", "b_node_id": "tjp"},
        {"a_node_id": "tjp", "b_node_id": "e1"},
        {"a_node_id": "tjp", "b_node_id": "e2"},
        {"a_node_id": "tjp", "b_node_id": "e3"},
        {"a_node_id": "cn2", "b_node_id": "gena"},
        {"a_node_id": "gena", "b_node_id": "e4"},
    ]
    return nodes, edges


def test_suggest_ranks_largest_territory_first() -> None:
    nodes, edges = _star_graph()
    struct = analyze_graph_structure(nodes, edges, hub_top_k=10)
    src = {n["fabric_node_id"] for n in nodes}
    report = suggest_sink_hub_batches(
        hubs=struct["hubs"],
        soft_blocks=struct["soft_blocks"],
        source_ids=src,
        sink_ids=set(),
        dual_units=struct.get("dual_units"),
        min_territory=1,
        top_n=5,
    )
    assert report["ok"] is True
    assert report["batch_count"] >= 1
    top = report["batches"][0]
    # TJP should beat GENA; CN portals must not lead
    assert top["hub_id"] == "tjp"
    assert "cn1" not in top["fabric_node_ids"]
    assert "cn2" not in top["fabric_node_ids"]
    assert set(top["fabric_node_ids"]) >= {"tjp", "e1", "e2", "e3"}


def test_suggest_drops_ids_already_on_sink() -> None:
    nodes, edges = _star_graph()
    struct = analyze_graph_structure(nodes, edges, hub_top_k=10)
    src = {n["fabric_node_id"] for n in nodes}
    report = suggest_sink_hub_batches(
        hubs=struct["hubs"],
        soft_blocks=struct["soft_blocks"],
        source_ids=src,
        sink_ids={"e1", "e2"},
        exclude_ids={"cn1", "cn2"},
        min_territory=1,
        top_n=5,
    )
    top = pick_batch(report, 1)
    assert top is not None
    assert top["hub_id"] == "tjp"
    assert "e1" not in top["fabric_node_ids"]
    assert "e2" not in top["fabric_node_ids"]
    assert "e3" in top["fabric_node_ids"]


def test_suggest_skips_portal_as_hub_leader() -> None:
    nodes, edges = _star_graph()
    struct = analyze_graph_structure(nodes, edges, hub_top_k=10)
    src = {n["fabric_node_id"] for n in nodes}
    report = suggest_sink_hub_batches(
        hubs=struct["hubs"],
        soft_blocks=struct["soft_blocks"],
        source_ids=src,
        exclude_ids={"tjp"},  # force skip TJP as leader id
        dual_units={"units": [{"portal_a": "cn1", "portal_b": "cn2"}]},
        min_territory=1,
        top_n=5,
    )
    hubs = [b["hub_id"] for b in report["batches"]]
    assert "cn1" not in hubs and "cn2" not in hubs
    # Excluded hub may still emit stub-only batch (hub id not in move list)
    tjp_batch = next((b for b in report["batches"] if b["hub_id"] == "tjp"), None)
    assert tjp_batch is not None
    assert "tjp" not in tjp_batch["fabric_node_ids"]
    assert set(tjp_batch["fabric_node_ids"]) >= {"e1", "e2", "e3"}


def test_suggest_moves_stubs_under_sunk_hub() -> None:
    nodes, edges = _star_graph()
    struct = analyze_graph_structure(nodes, edges, hub_top_k=10)
    src = {n["fabric_node_id"] for n in nodes}
    report = suggest_sink_hub_batches(
        hubs=struct["hubs"],
        soft_blocks=struct["soft_blocks"],
        source_ids=src,
        sink_ids={"tjp", "e1", "e2"},  # hub already on sink
        exclude_ids={"cn1", "cn2"},
        min_territory=1,
        top_n=5,
    )
    tjp = next((b for b in report["batches"] if b["hub_id"] == "tjp"), None)
    assert tjp is not None
    assert tjp["already_on_sink"] is True
    assert tjp["fabric_node_ids"] == ["e3"]


def test_suggest_orphan_leftovers_batch() -> None:
    nodes, edges = _star_graph()
    # Add disconnected orphans on source
    nodes = list(nodes) + [
        {"fabric_node_id": "iso1", "name": "X-ISO1-EN1-Y", "level": 3, "x": 900, "y": 900},
        {"fabric_node_id": "iso2", "name": "X-ISO2-EN1-Y", "level": 3, "x": 950, "y": 950},
    ]
    struct = analyze_graph_structure(nodes, edges, hub_top_k=10)
    src = {n["fabric_node_id"] for n in nodes}
    # Everything except orphans already on sink
    sink = src - {"iso1", "iso2"}
    report = suggest_sink_hub_batches(
        hubs=struct["hubs"],
        soft_blocks=struct["soft_blocks"],
        source_ids=src,
        sink_ids=sink,
        exclude_ids={"cn1", "cn2"},
        min_territory=0,
        top_n=8,
    )
    assert report["orphan_n"] == 2
    orphan = next((b for b in report["batches"] if b.get("orphan")), None)
    assert orphan is not None
    assert set(orphan["fabric_node_ids"]) == {"iso1", "iso2"}
    assert orphan["block_method"] == "orphan_leftovers"
