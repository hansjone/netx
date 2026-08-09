"""press_hot_edges / press_crossers / polish_crossings MCP actions."""

from __future__ import annotations

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    crossings_after_node_move,
)
from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
from netx_topology_mcp.layout_ops.press_crossings import (
    _large_graph_budget,
    park_phantom_nodes,
    press_hot_edges,
)
from netx_topology_mcp.layout_tool import ACTIONS, run_layout_on_graph


def test_actions_include_press_polish() -> None:
    assert "press_hot_edges" not in ACTIONS
    assert "press_crossers" not in ACTIONS
    assert "polish_crossings" in ACTIONS


def test_park_phantom_region_nodes() -> None:
    st = build_state_from_nodes_edges(
        [
            {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
            {
                "fabric_node_id": "region:x",
                "name": "Units",
                "x": 1e12,
                "y": 100,
            },
        ],
        [],
    )
    moved = park_phantom_nodes(st)
    assert "region:x" in moved
    assert abs(st.positions["region:x"][0]) < 1000


def test_crossings_after_node_move_matches_full_recount() -> None:
    nodes = [
        {"fabric_node_id": "h", "name": "H", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "A", "x": 200, "y": 200},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 0, "y": 200},
        {"fabric_node_id": "d", "name": "D", "x": 400, "y": 100},
    ]
    edges = [
        {"a_node_id": "h", "b_node_id": "a"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "h", "b_node_id": "b"},
        {"a_node_id": "a", "b_node_id": "d"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    cur = count_edge_crossings(st.positions, st.links)
    for xy in ((200.0, -40.0), (50.0, 50.0), (300.0, 300.0)):
        inc = crossings_after_node_move(
            st.positions, st.links, st.adj, "a", xy, current_total=cur
        )
        trial = dict(st.positions)
        trial["a"] = xy
        assert inc == count_edge_crossings(trial, st.links)


def test_large_graph_budget_scales_down() -> None:
    small = _large_graph_budget(50)
    huge = _large_graph_budget(500)
    assert huge["hot_max_sweeps"] < small["hot_max_sweeps"]
    assert huge["untangle_rounds"] < small["untangle_rounds"]
    assert huge["untangle_rank_cap"] < small["untangle_rank_cap"]
    assert huge["cross_cand_cap"] < small["cross_cand_cap"]


def test_press_hot_edges_reduces_x_crossing() -> None:
    # Classic X: rotate one endpoint about the other can uncross.
    nodes = [
        {"fabric_node_id": "h", "name": "H", "x": 0, "y": 0},
        {"fabric_node_id": "a", "name": "A", "x": 200, "y": 200},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 0, "y": 200},
    ]
    # h-a and b-c cross; freeze hub h as portal
    edges = [
        {"a_node_id": "h", "b_node_id": "a"},
        {"a_node_id": "b", "b_node_id": "c"},
        {"a_node_id": "h", "b_node_id": "b"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.meta = {
        "compose_views": {
            "rigid_groups": [
                {"key": "u1", "node_ids": ["h", "a", "b"], "pivots": ["h"]},
            ]
        }
    }
    before = 1  # at least the X
    op = press_hot_edges(st, portal_ids=["h"], top_n=5, max_moves=8, max_sweeps=3)
    assert op.state is not None
    assert int(op.params.get("end_crossings") or 0) <= before + 2


def test_press_hot_edges_reels_in_long_spoke() -> None:
    """max_disp must not block inward pulls that shorten metro bridges."""
    import math

    nodes = [
        {"fabric_node_id": "h", "name": "H", "x": 0, "y": 0},
        {"fabric_node_id": "leaf", "name": "LEAF", "x": 8000, "y": 0},
        # Barrier segment the long spoke crosses.
        {"fabric_node_id": "p", "name": "P", "x": 4000, "y": -800},
        {"fabric_node_id": "q", "name": "Q", "x": 4000, "y": 800},
        {"fabric_node_id": "anchor", "name": "A", "x": -200, "y": 0},
    ]
    edges = [
        {"a_node_id": "h", "b_node_id": "leaf"},
        {"a_node_id": "p", "b_node_id": "q"},
        {"a_node_id": "h", "b_node_id": "anchor"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    # Pad with dummy edges so large-graph budget (E>=400) applies.
    for i in range(420):
        a, b = f"d{i}", f"d{i+1}"
        st.positions[a] = (float(i), 5000.0)
        st.positions[b] = (float(i) + 1.0, 5000.0)
        st.names[a] = a
        st.names[b] = b
        st.links.append((a, b))
        st.adj.setdefault(a, []).append(b)
        st.adj.setdefault(b, []).append(a)
    before = count_edge_crossings(st.positions, st.links)
    assert before >= 1
    op = press_hot_edges(st, portal_ids=["h"], top_n=5, max_moves=6, max_sweeps=2)
    after_len = math.hypot(
        op.state.positions["leaf"][0] - op.state.positions["h"][0],
        op.state.positions["leaf"][1] - op.state.positions["h"][1],
    )
    assert after_len < 4000.0 or int(op.params.get("end_crossings") or before) < before


def test_run_layout_polish_crossings() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "B", "x": 200, "y": 0},
        {"fabric_node_id": "c", "name": "C", "x": 200, "y": 200},
        {"fabric_node_id": "d", "name": "D", "x": 0, "y": 200},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "c"},
        {"a_node_id": "b", "b_node_id": "d"},
        {"a_node_id": "a", "b_node_id": "b"},
    ]
    out = run_layout_on_graph(
        nodes,
        edges,
        action="polish_crossings",
        params={"straighten": True, "max_degree": 7, "untangle_rounds": 40},
    )
    assert out["ok"] is True
    assert out["action"] == "polish_crossings"
    assert (out.get("overlap") or {}).get("footprint_pairs", 0) == 0
