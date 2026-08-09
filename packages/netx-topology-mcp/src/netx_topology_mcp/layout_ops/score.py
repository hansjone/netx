"""Score a LayoutState with layout_metrics + layout_stats composite."""

from __future__ import annotations

from typing import Any

from netx_topology_mcp.layout_ops.state import LayoutState, OpResult
from netx_topology_mcp.layout_stats import analyze_layout_stats


def score_state(
    state: LayoutState,
    *,
    ume_reference: bool = False,
    fast: bool | None = None,
) -> dict[str, Any]:
    nodes = [
        {
            "fabric_node_id": n,
            "name": state.names.get(n, n),
            "x": state.positions[n][0],
            "y": state.positions[n][1],
        }
        for n in state.positions
    ]
    edges = [{"a_node_id": a, "b_node_id": b} for a, b in state.links]
    # Giant canvases: skip ring pierce (O(rings·E²)-ish) unless forced full.
    if fast is None:
        fast = len(nodes) >= 600 or len(state.links) >= 700
    return analyze_layout_stats(
        nodes, edges, ume_reference=ume_reference, fast=bool(fast)
    )


def score_op(state: LayoutState, *, ume_reference: bool = False) -> OpResult:
    st = state.copy()
    m = score_state(st, ume_reference=ume_reference)
    st.meta["last_score"] = m
    report = m.get("report") or {}
    verdict = report.get("verdict") or m.get("summary") or {}
    return OpResult(
        state=st,
        moved=set(),
        op="score",
        params={
            "total": verdict.get("total"),
            "overall": verdict.get("overall"),
            "headline": verdict.get("headline"),
            "overlap": (report.get("overlap") or {}).get("status"),
            "crossing": (report.get("crossing") or {}).get("status"),
            "sparsity": (report.get("sparsity") or {}).get("status"),
            "edge_crossings": m.get("edge_crossings"),
            "nn_p50": m.get("nn_p50"),
            "space_utilization": m.get("space_utilization"),
        },
        note=f"score={verdict.get('total')} {verdict.get('headline')}",
    )
