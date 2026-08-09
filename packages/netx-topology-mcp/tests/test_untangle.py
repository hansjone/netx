"""Untangle must not fling nodes across the canvas."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_ops import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.untangle import _MAX_JUMP, _candidates, untangle_crossings


def test_candidates_stay_near_node() -> None:
    pos = {
        "a": (0.0, 0.0),
        "b": (5000.0, 0.0),  # far neighbor — old code used dx*3.5 → 17k jump
        "c": (2500.0, 800.0),
    }
    adj = {"c": {"a", "b"}, "a": {"c"}, "b": {"c"}}
    import random

    cands = _candidates(pos, "c", adj, random.Random(1))
    assert cands
    x0, y0 = pos["c"]
    for x, y in cands:
        assert math.hypot(x - x0, y - y0) <= _MAX_JUMP + 1e-6


def test_untangle_preserves_bbox_order() -> None:
    # Long chain with one intentional crossing-ish layout; moves stay local.
    nodes = []
    edges = []
    for i in range(8):
        nodes.append(
            {
                "fabric_node_id": f"n{i}",
                "name": f"N{i}-EN-1",
                "x": float(i * 200),
                "y": 0.0 if i % 2 == 0 else 40.0,
            }
        )
        if i:
            edges.append({"a_node_id": f"n{i-1}", "b_node_id": f"n{i}"})
    # add a long chord that crosses nothing badly but gives degree
    edges.append({"a_node_id": "n0", "b_node_id": "n7"})
    st = build_state_from_nodes_edges(nodes, edges)
    before = dict(st.positions)
    xs0 = [p[0] for p in before.values()]
    ys0 = [p[1] for p in before.values()]
    span0 = max(max(xs0) - min(xs0), max(ys0) - min(ys0))
    out = untangle_crossings(st, max_rounds=40, max_degree=7, target_crossings=0)
    xs1 = [p[0] for p in out.state.positions.values()]
    ys1 = [p[1] for p in out.state.positions.values()]
    span1 = max(max(xs1) - min(xs1), max(ys1) - min(ys1))
    # Must not explode bbox by >2x
    assert span1 <= span0 * 2.0 + 800.0
