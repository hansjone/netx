"""edge_clearance metric + clear_edge_hits action."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import (
    EDGE_CLEARANCE_THR,
    compute_edge_clearance,
    count_edge_crossings,
)
from netx_topology_mcp.layout_ops.clear_edge_hits import clear_edge_hits
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState
from netx_topology_mcp.layout_stats import analyze_layout_stats, score_layout_components
from netx_topology_mcp.layout_tool import run_layout_on_graph


def _mksr_style() -> tuple[list[dict], list[dict]]:
    """M1MJ—MEPL horizontal trunk with MKSR sitting on the segment; DALH stub."""
    nodes = [
        {"fabric_node_id": "m1mj", "name": "M1MJ", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "mepl", "name": "MEPL", "x": 400.0, "y": 0.0},
        {"fabric_node_id": "mksr", "name": "MKSR", "x": 200.0, "y": 6.0},  # on trunk
        {"fabric_node_id": "dalh", "name": "DALH", "x": 200.0, "y": 200.0},
        {"fabric_node_id": "bbsn", "name": "BBSN", "x": 600.0, "y": 0.0},
    ]
    edges = [
        {"a_node_id": "m1mj", "b_node_id": "mepl"},
        {"a_node_id": "mepl", "b_node_id": "bbsn"},
        {"a_node_id": "bbsn", "b_node_id": "mksr"},
        {"a_node_id": "mksr", "b_node_id": "dalh"},
    ]
    return nodes, edges


def test_collinear_midpoint_scores_edge_clearance_hit() -> None:
    nodes, edges = _mksr_style()
    m = analyze_layout_stats(nodes, edges)
    assert int(m["edge_clearance_hits"] or 0) >= 1
    assert float(m["edge_clearance_score"]) < 1.0
    assert "edge_clearance" in m["score"]["parts"]
    assert abs(m["score"]["weights"]["edge_clearance"] - 0.08) < 1e-9
    assert m["report"]["edge_clearance"]["status"] in {"warn", "fail"}
    top = m.get("top_edge_hits") or []
    assert any(r.get("fabric_node_id") == "mksr" for r in top)


def test_cleared_layout_has_zero_hits() -> None:
    nodes = [
        {"fabric_node_id": "m1mj", "name": "M1MJ", "x": 0.0, "y": 0.0},
        {"fabric_node_id": "mepl", "name": "MEPL", "x": 400.0, "y": 0.0},
        {"fabric_node_id": "mksr", "name": "MKSR", "x": 600.0, "y": 200.0},
        {"fabric_node_id": "dalh", "name": "DALH", "x": 600.0, "y": 400.0},
        {"fabric_node_id": "bbsn", "name": "BBSN", "x": 600.0, "y": 0.0},
    ]
    edges = [
        {"a_node_id": "m1mj", "b_node_id": "mepl"},
        {"a_node_id": "mepl", "b_node_id": "bbsn"},
        {"a_node_id": "bbsn", "b_node_id": "mksr"},
        {"a_node_id": "mksr", "b_node_id": "dalh"},
    ]
    m = analyze_layout_stats(nodes, edges)
    assert int(m["edge_clearance_hits"] or 0) == 0
    assert float(m["edge_clearance_score"]) == 1.0
    assert m["report"]["edge_clearance"]["status"] == "ok"


def test_clear_edge_hits_moves_mksr_off_trunk() -> None:
    nodes, edges = _mksr_style()
    pos = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    names = {n["fabric_node_id"]: n["name"] for n in nodes}
    links = [("m1mj", "mepl"), ("mepl", "bbsn"), ("bbsn", "mksr"), ("mksr", "dalh")]
    st = LayoutState(positions=pos, names=names, links=links)
    x0 = count_edge_crossings(st.positions, st.links)
    clr0 = compute_edge_clearance(st.positions, st.links, thr=EDGE_CLEARANCE_THR)
    assert int(clr0["edge_clearance_hits"] or 0) >= 1

    op = clear_edge_hits(st, LayoutParams(), thr=EDGE_CLEARANCE_THR, margin=20.0)
    assert "mksr" in op.moved or int(op.params.get("hits_after") or 0) < int(
        op.params.get("hits_before") or 0
    )
    x1 = count_edge_crossings(op.state.positions, op.state.links)
    assert x1 <= x0
    clr1 = compute_edge_clearance(op.state.positions, op.state.links, thr=EDGE_CLEARANCE_THR)
    assert int(clr1["edge_clearance_hits"] or 0) < int(clr0["edge_clearance_hits"] or 0)


def test_run_layout_clear_edge_hits_apply_path() -> None:
    nodes, edges = _mksr_style()
    out = run_layout_on_graph(
        nodes,
        edges,
        action="clear_edge_hits",
        params={"thr": 40.0, "margin": 20.0, "top_n": 8},
    )
    assert out.get("ok") is not False
    assert out.get("action") == "clear_edge_hits"
    local = out.get("local") or {}
    note = str(local.get("note") or "")
    assert "clear_edge_hits" in note


def test_score_weights_include_edge_clearance() -> None:
    s = score_layout_components(
        {
            "node_count": 40,
            "edge_crossings": 0,
            "crossings_per_link": 0.0,
            "footprint_overlap_pairs": 0,
            "label_overlap_pairs": 0,
            "nn_p50": 170,
            "space_utilization": 0.2,
            "hull_utilization": 0.25,
            "grid_occupancy": 0.4,
            "edge_stretch_p50": 1.2,
            "whitespace_index": 0.2,
            "chain_score": 1.0,
            "rings_score": 1.0,
            "edge_clearance_score": 0.2,
            "edge_axis_score": 0.2,
        }
    )
    assert abs(s["weights"]["edge_clearance"] - 0.08) < 1e-9
    assert abs(s["weights"]["edge_axis"] - 0.06) < 1e-9
    assert abs(s["weights"]["grid"] - 0.04) < 1e-9
    assert abs(s["weights"]["nn"] - 0.04) < 1e-9
    assert abs(sum(s["weights"].values()) - 1.0) < 1e-9
    good = score_layout_components(
        {
            "node_count": 40,
            "edge_crossings": 0,
            "crossings_per_link": 0.0,
            "footprint_overlap_pairs": 0,
            "label_overlap_pairs": 0,
            "nn_p50": 170,
            "space_utilization": 0.2,
            "hull_utilization": 0.25,
            "grid_occupancy": 0.4,
            "edge_stretch_p50": 1.2,
            "whitespace_index": 0.2,
            "chain_score": 1.0,
            "rings_score": 1.0,
            "edge_clearance_score": 1.0,
            "edge_axis_score": 1.0,
        }
    )
    assert good["total"] > s["total"]
