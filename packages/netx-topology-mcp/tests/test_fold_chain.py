"""Tests for fold_chain sector sweep + tentacle fold."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops.fold_chain import fold_chain_into_sector
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.state import LayoutParams
from netx_topology_mcp.layout_tool import list_layout_catalog, run_layout_on_graph


def _star_with_tentacle() -> tuple[list[dict], list[dict]]:
    """Hub H with ring of leaves + long deg2 tentacle S0..S4."""
    nodes = [
        {"fabric_node_id": "H", "name": "PLG-HUB-AN1-Z", "x": 0, "y": 0},
        {"fabric_node_id": "A", "name": "PLG-AAA-EN1-Z", "x": 200, "y": 0},
        {"fabric_node_id": "B", "name": "PLG-BBB-EN1-Z", "x": 0, "y": 200},
        {"fabric_node_id": "C", "name": "PLG-CCC-EN1-Z", "x": -200, "y": 0},
        {"fabric_node_id": "D", "name": "PLG-DDD-EN1-Z", "x": 0, "y": -200},
        # tentacle stretched far east (pierces through ring)
        {"fabric_node_id": "S0", "name": "PLG-S0-EN1-Z", "x": 80, "y": 0},
        {"fabric_node_id": "S1", "name": "PLG-S1-EN1-Z", "x": 260, "y": 0},
        {"fabric_node_id": "S2", "name": "PLG-S2-EN1-Z", "x": 440, "y": 0},
        {"fabric_node_id": "S3", "name": "PLG-S3-EN1-Z", "x": 620, "y": 0},
        {"fabric_node_id": "S4", "name": "PLG-S4-EN1-Z", "x": 800, "y": 0},
        # filler leaf in south gap candidate
        {"fabric_node_id": "E", "name": "PLG-EEE-EN1-Z", "x": 140, "y": 140},
        {"fabric_node_id": "F", "name": "PLG-FFF-EN1-Z", "x": -140, "y": 140},
    ]
    edges = [
        {"a_node_id": "H", "b_node_id": "A"},
        {"a_node_id": "H", "b_node_id": "B"},
        {"a_node_id": "H", "b_node_id": "C"},
        {"a_node_id": "H", "b_node_id": "D"},
        {"a_node_id": "H", "b_node_id": "E"},
        {"a_node_id": "H", "b_node_id": "F"},
        {"a_node_id": "H", "b_node_id": "S0"},
        {"a_node_id": "S0", "b_node_id": "S1"},
        {"a_node_id": "S1", "b_node_id": "S2"},
        {"a_node_id": "S2", "b_node_id": "S3"},
        {"a_node_id": "S3", "b_node_id": "S4"},
    ]
    return nodes, edges


def test_fold_chain_places_whole_chain_on_arc() -> None:
    nodes, edges = _star_with_tentacle()
    st = build_state_from_nodes_edges(nodes, edges)
    op = fold_chain_into_sector(
        st,
        LayoutParams(target_nn=155.0),
        hub_id="H",
        stub_id="S0",
        prefer_mid_deg=90.0,
        r_arc=300.0,
        chord=180.0,
    )
    meta = op.state.meta["fold_chain"]
    assert meta["hub_id"] == "H"
    assert meta["chain"] == ["S0", "S1", "S2", "S3", "S4"]
    assert len(meta["folded"]) == 5
    hx, hy = op.state.positions["H"]
    radii = [
        math.hypot(op.state.positions[n][0] - hx, op.state.positions[n][1] - hy)
        for n in meta["chain"]
    ]
    assert all(abs(r - 300.0) < 1.0 for r in radii)
    # Chain should not remain as a straight east ray
    assert op.state.positions["S4"][0] < 700


def test_fold_chain_action_unpublished() -> None:
    nodes, edges = _star_with_tentacle()
    try:
        run_layout_on_graph(
            nodes,
            edges,
            action="fold_chain",
            params={"hub_id": "H", "stub_id": "S0", "prefer_mid_deg": 120},
        )
        raise AssertionError("fold_chain should be unpublished")
    except ValueError as e:
        assert "unknown_action" in str(e)


def test_catalog_omits_fold_chain() -> None:
    cat = list_layout_catalog()
    assert "fold_chain" not in cat["actions"]
