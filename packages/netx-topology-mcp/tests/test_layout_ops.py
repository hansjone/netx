"""Unit tests for composable layout_ops atoms."""

from __future__ import annotations

import math

from netx_topology_mcp.layout_metrics import grade_layout
from netx_topology_mcp.layout_ops import (
    LayoutParams,
    build_state_from_nodes_edges,
    run_recipe,
    score_state,
)
from netx_topology_mcp.layout_ops.transforms import (
    resolve_overlaps,
    scale_region,
    select_pins,
)


def _line_graph(n: int = 6, gap: float = 40.0):
    nodes = []
    edges = []
    for i in range(n):
        nodes.append(
            {
                "fabric_node_id": f"n{i}",
                "name": f"X-EN{i}-Y",
                "x": i * gap,
                "y": 0.0,
            }
        )
        if i:
            edges.append({"a_node_id": f"n{i-1}", "b_node_id": f"n{i}"})
    return nodes, edges


def test_scale_region_only_unpinned() -> None:
    nodes, edges = _line_graph(4, gap=100.0)
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    st.pinned = {"n0", "n1"}
    before = dict(st.positions)
    out = scale_region(st, LayoutParams(), sx=2.0, sy=1.0, only_unpinned=True)
    assert out.state.positions["n0"] == before["n0"]
    assert out.state.positions["n1"] == before["n1"]
    assert out.state.positions["n2"] != before["n2"]
    assert "n2" in out.moved


def test_resolve_overlaps_clears_stack() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "AAAAAA", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "BBBBBB", "x": 5, "y": 0},
        {"fabric_node_id": "c", "name": "CCCCCC", "x": 200, "y": 200},
    ]
    edges = [{"a_node_id": "a", "b_node_id": "c"}]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    st.pinned = {"c"}
    m0 = score_state(st)
    assert m0["footprint_overlap_pairs"] >= 1
    out = resolve_overlaps(st, LayoutParams(overlap_iters=120, overlap_step=12.0))
    m1 = score_state(out.state)
    assert m1["footprint_overlap_pairs"] == 0
    assert out.state.positions["c"] == st.positions["c"]


def test_select_pins_modes() -> None:
    nodes, edges = _line_graph(8)
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    st.layers = {n["fabric_node_id"]: "access" for n in nodes}
    st.layers["n0"] = "agg"
    st.spine = {"n1", "n2", "n3"}
    r = select_pins(st, mode="spine")
    assert "n0" in r.state.pinned
    assert "n1" in r.state.pinned


def test_grade_hard_zero_overlap() -> None:
    m = {
        "node_count": 10,
        "link_count": 9,
        "edge_crossings": 0,
        "crossings_per_link": 0.0,
        "footprint_overlap_pairs": 2,
        "label_overlap_pairs": 0,
        "nn_p50": 160,
        "space_utilization": 0.2,
    }
    g = grade_layout(m)
    assert g["spacing_grade"] == "fail"
    g2 = grade_layout(m, ume_reference=True)
    assert g2["spacing_grade"] in {"ok", "warn", "fail"}


def test_select_scope_limits_moves() -> None:
    from netx_topology_mcp.layout_ops.scope import select_scope
    from netx_topology_mcp.layout_ops.transforms import scale_region

    nodes, edges = _line_graph(6, gap=100.0)
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    st = select_scope(st, mode="ids", node_ids={"n0", "n1", "n2"}).state
    before = dict(st.positions)
    out = scale_region(st, LayoutParams(), sx=2.0, sy=1.0, only_unpinned=False)
    assert out.state.positions["n5"] == before["n5"]
    assert out.state.positions["n0"] != before["n0"]


def test_explode_clusters_breaks_stack() -> None:
    nodes = [
        {"fabric_node_id": "a", "name": "X-EN0-A", "x": 0, "y": 0},
        {"fabric_node_id": "b", "name": "X-EN1-B", "x": 1, "y": 0},
        {"fabric_node_id": "c", "name": "X-EN2-C", "x": 400, "y": 0},
    ]
    edges = [
        {"a_node_id": "a", "b_node_id": "c"},
        {"a_node_id": "b", "b_node_id": "c"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    st.positions = {n["fabric_node_id"]: (float(n["x"]), float(n["y"])) for n in nodes}
    from netx_topology_mcp.layout_ops.transforms import explode_clusters

    out = explode_clusters(st, LayoutParams(), thr=5.0, gap=50.0)
    d = math.hypot(
        out.state.positions["a"][0] - out.state.positions["b"][0],
        out.state.positions["a"][1] - out.state.positions["b"][1],
    )
    assert d >= 40.0


def test_place_on_rect_edge_axis_aligned() -> None:
    from netx_topology_mcp.layout_ops.sugiyama import _place_on_rect_edge

    order = [f"e{i}" for i in range(8)]
    pos = _place_on_rect_edge(order, 0.0, 0.0, 400.0, 200.0)
    assert len(pos) == 8
    for x, y in pos.values():
        on_h = (abs(y - 0.0) < 1e-6 or abs(y - 200.0) < 1e-6) and -1e-6 <= x <= 400.0 + 1e-6
        on_v = (abs(x - 0.0) < 1e-6 or abs(x - 400.0) < 1e-6) and -1e-6 <= y <= 200.0 + 1e-6
        assert on_h or on_v


def test_extract_dangling_feeder_leaf_to_attach() -> None:
    from netx_topology_mcp.layout_ops.sugiyama import _extract_dangling_feeders

    # Leaf e4 → … → attach e0 (on ring/hub). Not a ring corridor.
    ens = [f"e{i}" for i in range(5)]
    adj = {
        "e0": {"e1", "r1", "r2"},
        "e1": {"e0", "e2"},
        "e2": {"e1", "e3"},
        "e3": {"e2", "e4"},
        "e4": {"e3"},
        "r1": {"e0", "r2"},
        "r2": {"e0", "r1"},
    }
    names = {n: n for n in adj}
    chains = _extract_dangling_feeders(
        ens + ["r1", "r2"], adj, names, attach_set={"e0", "r1", "r2"}, min_len=2
    )
    assert chains
    long = max(chains, key=len)
    assert long[0] == "e4"
    assert long[-1] == "e0"
    assert long == ["e4", "e3", "e2", "e1", "e0"]


def test_eject_portal_neighbor_outside_ring() -> None:
    """PNBR-like stub on a portal must leave the ring interior."""
    from netx_topology_mcp.layout_ops.sugiyama import _eject_intruders_from_rings
    from netx_topology_mcp.layout_ops.state import LayoutParams

    # Trapezoid ring a—b with bottom corridor; stub s attached to a sits inside.
    groups = [
        {
            "portals": ("a", "b"),
            "paths": [["a", "b"], ["a", "p1", "p2", "b"]],
        }
    ]
    pos = {
        "a": (0.0, 0.0),
        "b": (400.0, 0.0),
        "p1": (100.0, 150.0),
        "p2": (300.0, 150.0),
        "s": (120.0, 40.0),  # inside polygon
    }
    adj = {"a": {"b", "p1", "s"}, "b": {"a", "p2"}, "p1": {"a", "p2"}, "p2": {"p1", "b"}, "s": {"a"}}
    edges = [("a", "b"), ("a", "p1"), ("p1", "p2"), ("p2", "b"), ("a", "s")]
    out = _eject_intruders_from_rings(pos, groups, adj, LayoutParams(), edges)
    # Outside: left of portal a (outer side away from b).
    assert out["s"][0] < out["a"][0]


def test_triangle_ring_stub_must_not_pierce_chord() -> None:
    """Minimal triangle ring: portal stub edge must not cross the third side."""
    from netx_topology_mcp.layout_ops.sugiyama import (
        _eject_intruders_from_rings,
        _stub_crosses_ring,
    )
    from netx_topology_mcp.layout_ops.state import LayoutParams

    # Triangle a-v-b with direct a—b; stub s on b pierces a—v.
    groups = [{"portals": ("a", "b"), "paths": [["a", "b"], ["a", "v", "b"]]}]
    pos = {
        "a": (0.0, 0.0),
        "b": (300.0, 0.0),
        "v": (150.0, 120.0),
        "s": (50.0, 80.0),  # s—b crosses a—v
        "out": (-200.0, 0.0),
    }
    adj = {
        "a": {"b", "v"},
        "b": {"a", "v", "s"},
        "v": {"a", "b"},
        "s": {"b", "out"},
        "out": {"s"},
    }
    edges = [("a", "b"), ("a", "v"), ("v", "b"), ("b", "s"), ("s", "out")]
    assert _stub_crosses_ring("s", "b", pos, ["a", "b", "v"])
    out = _eject_intruders_from_rings(pos, groups, adj, LayoutParams(), edges)
    assert not _stub_crosses_ring("s", "b", out, ["a", "b", "v"])


def test_triangle_apex_flips_off_foreign_chord() -> None:
    """VOTI-like apex must sit on the side that foreign portal edges do not hit."""
    from netx_topology_mcp.layout_ops.sugiyama import _orient_ring_sides
    from netx_topology_mcp.layout_metrics import segments_properly_intersect

    groups = [{"portals": ("a", "b"), "paths": [["a", "b"], ["a", "v", "b"]]}]
    # Foreign f—b runs under the chord; apex v below is pierced / crossed.
    pos = {
        "a": (0.0, 0.0),
        "b": (300.0, 0.0),
        "v": (150.0, 120.0),
        "f": (-100.0, 60.0),
    }
    edges = [("a", "b"), ("a", "v"), ("v", "b"), ("f", "b")]
    assert segments_properly_intersect(pos["a"], pos["v"], pos["f"], pos["b"])
    out = _orient_ring_sides(
        pos, groups, edges, pinned={"a", "b", "v"}, max_interiors=3, push=40.0
    )
    assert out["v"][1] < 0.0  # flipped above the chord
    assert not segments_properly_intersect(out["a"], out["v"], out["f"], out["b"])


def test_eject_one_ring_must_not_pierce_another() -> None:
    """Ejecting from a large ring must not park the stub through a triangle."""
    from netx_topology_mcp.layout_ops.sugiyama import (
        _eject_intruders_from_rings,
        _stub_crosses_ring,
    )
    from netx_topology_mcp.layout_ops.state import LayoutParams

    # Big ring a—b via p1-p2; triangle a-v-b; stub s inside big ring.
    groups = [
        {"portals": ("a", "b"), "paths": [["a", "b"], ["a", "p1", "p2", "b"]]},
        {"portals": ("a", "b"), "paths": [["a", "b"], ["a", "v", "b"]]},
    ]
    pos = {
        "a": (0.0, 0.0),
        "b": (400.0, 0.0),
        "p1": (80.0, 200.0),
        "p2": (320.0, 200.0),
        "v": (200.0, -120.0),
        "s": (120.0, 40.0),  # inside big ring; naive eject can cross a—v
    }
    adj = {
        "a": {"b", "p1", "v", "s"},
        "b": {"a", "p2", "v"},
        "p1": {"a", "p2"},
        "p2": {"p1", "b"},
        "v": {"a", "b"},
        "s": {"a"},
    }
    edges = [
        ("a", "b"),
        ("a", "p1"),
        ("p1", "p2"),
        ("p2", "b"),
        ("a", "v"),
        ("v", "b"),
        ("a", "s"),
    ]
    out = _eject_intruders_from_rings(pos, groups, adj, LayoutParams(), edges)
    assert not _stub_crosses_ring("s", "a", out, ["a", "b", "v"])
    assert not _stub_crosses_ring("s", "a", out, ["a", "b", "p2", "p1"])


def test_an_side_ring_not_mistaken_for_feeder() -> None:
    """AN—EN ring leg must be a ring unit, not a dangling feeder (no X with chain)."""
    from netx_topology_mcp.layout_ops.sugiyama import (
        _extract_dangling_feeders,
        _find_two_portal_ring_groups,
        _ring_nodes_from_groups,
    )

    # SPB(AN)-TNM direct + SPB-SRIN-ADAK-JROS-TNM; chain TNM-TNMS-SMDA
    nodes = {
        "spb": "X-AN-SPB",
        "tnm": "X-EN-TNM",
        "tnms": "X-EN-TNMS",
        "smda": "X-EN-SMDA",
        "jros": "X-EN-JROS",
        "adak": "X-EN-ADAK",
        "srin": "X-EN-SRIN",
    }
    adj = {
        "spb": {"tnm", "srin", "other"},
        "tnm": {"spb", "tnms", "jros"},
        "tnms": {"tnm", "smda"},
        "smda": {"tnms"},
        "jros": {"tnm", "adak"},
        "adak": {"jros", "srin"},
        "srin": {"adak", "spb"},
        "other": {"spb"},
    }
    ens = ["tnm", "tnms", "smda", "jros", "adak", "srin"]
    an_set = {"spb"}
    groups = _find_two_portal_ring_groups(ens, adj, nodes, an_set)
    assert groups
    ring = _ring_nodes_from_groups(groups)
    assert {"tnm", "jros", "adak", "srin", "spb"} <= ring
    attach = ring | an_set
    feeders = _extract_dangling_feeders(
        ens, adj, nodes, attach, min_len=2, an_set=an_set
    )
    bodies = {n for f in feeders for n in f[:-1]}
    # Ring leg must not be consumed as a feeder body.
    assert not ({"jros", "adak", "srin"} & bodies)
    # True dangling chain off TNM remains.
    assert any(f[0] == "smda" or "smda" in f for f in feeders)


def test_two_portal_rings_nest_smallest_inner() -> None:
    """Shared portals: shortest corridor innermost, longer outward."""
    from netx_topology_mcp.layout_ops.sugiyama import (
        _find_two_portal_ring_groups,
        _place_two_portal_ring_groups,
    )
    from netx_topology_mcp.layout_ops.state import LayoutParams

    # Portals p1,p2 with three disjoint corridors (sizes 1 / 2 / 3 mids).
    ens = ["p1", "p2", "a1", "b1", "b2", "c1", "c2", "c3"]
    adj = {
        "p1": {"a1", "b1", "c1", "an"},
        "p2": {"a1", "b2", "c3", "x"},
        "a1": {"p1", "p2"},
        "b1": {"p1", "b2"},
        "b2": {"b1", "p2"},
        "c1": {"p1", "c2"},
        "c2": {"c1", "c3"},
        "c3": {"c2", "p2"},
        "an": {"p1"},
        "x": {"p2"},
    }
    names = {n: n for n in list(adj)}
    groups = _find_two_portal_ring_groups(ens, adj, names, {"an"})
    assert groups
    g0 = next(g for g in groups if set(g["portals"]) == {"p1", "p2"})
    paths = g0["paths"]
    assert len(paths) >= 2
    # Shortest path first.
    assert all(len(paths[i]) <= len(paths[i + 1]) for i in range(len(paths) - 1))

    pos = {
        "p1": (0.0, 0.0),
        "p2": (600.0, 0.0),
        "a1": (300.0, 10.0),
        "b1": (200.0, 20.0),
        "b2": (400.0, 20.0),
        "c1": (150.0, 30.0),
        "c2": (300.0, 30.0),
        "c3": (450.0, 30.0),
    }
    edges = [("p1", "a1"), ("a1", "p2"), ("p1", "b1"), ("b1", "b2"), ("b2", "p2"),
             ("p1", "c1"), ("c1", "c2"), ("c2", "c3"), ("c3", "p2")]
    out, pinned = _place_two_portal_ring_groups(
        pos, ens, adj, names, {"an"}, LayoutParams(), edges
    )
    assert pinned
    # Trapezoid nest: shortest path closer to midline; outer band wider.
    my = (out["p1"][1] + out["p2"][1]) / 2
    assert abs(out["a1"][1] - my) <= abs(out["c2"][1] - my) + 1e-6
    outer_span = abs(out["c3"][0] - out["c1"][0])
    mid_span = abs(out["b2"][0] - out["b1"][0])
    assert outer_span + 1e-6 >= mid_span


def test_chain_first_keeps_feeder_sequential() -> None:
    """Pure feeder chain stays a coherent polyline after rings recipe."""
    nodes = [{"fabric_node_id": "an", "name": "X-AN1-Y"}]
    edges = []
    for i in range(5):
        nodes.append({"fabric_node_id": f"e{i}", "name": f"X-EN{i}-Y"})
    edges.append({"a_node_id": "an", "b_node_id": "e0"})
    for i in range(4):
        edges.append({"a_node_id": f"e{i}", "b_node_id": f"e{i+1}"})
    st = build_state_from_nodes_edges(nodes, edges)
    st2, _, final = run_recipe(st, "agg_rings_v1", LayoutParams())
    assert len(st2.positions) == 6
    # Consecutive chain edges should stay roughly axis-aligned (not hop-scrambled).
    axis = 0
    for i in range(4):
        a, b = st2.positions[f"e{i}"], st2.positions[f"e{i+1}"]
        dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
        if dx < 1e-6 or dy < 1e-6:
            axis += 1
    assert axis >= 3
    assert int(final.get("edge_crossings") or 0) == 0


def test_agg_rings_recipe_places_dual_agg_ring() -> None:
    # Two ANs + 4-EN ring + one chain stub → dual-hub min-rings
    nodes = [
        {"fabric_node_id": "an1", "name": "X-AN1-Y"},
        {"fabric_node_id": "an2", "name": "X-AN2-Y"},
        {"fabric_node_id": "e0", "name": "X-EN0-Y"},
        {"fabric_node_id": "e1", "name": "X-EN1-Y"},
        {"fabric_node_id": "e2", "name": "X-EN2-Y"},
        {"fabric_node_id": "e3", "name": "X-EN3-Y"},
        {"fabric_node_id": "e4", "name": "X-EN4-Y"},
    ]
    edges = [
        {"a_node_id": "an1", "b_node_id": "an2"},
        {"a_node_id": "an1", "b_node_id": "e0"},
        {"a_node_id": "an2", "b_node_id": "e2"},
        {"a_node_id": "e0", "b_node_id": "e1"},
        {"a_node_id": "e1", "b_node_id": "e2"},
        {"a_node_id": "e2", "b_node_id": "e3"},
        {"a_node_id": "e3", "b_node_id": "e0"},
        {"a_node_id": "e1", "b_node_id": "e4"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    params = LayoutParams()
    st2, trace, final = run_recipe(st, "agg_rings_v1", params)
    assert any(t.get("op") == "build_ring_skeleton" for t in trace)
    assert st2.meta.get("rings_mode") == "min_rings"
    assert len(st2.positions) == 7
    # Dual hubs on a horizontal bar; corridors nest above/below
    assert abs(st2.positions["an1"][1] - st2.positions["an2"][1]) < 1e-6
    assert st2.positions["an1"][0] < st2.positions["an2"][0]
    assert "edge_crossings" in final
    assert int(final.get("edge_crossings") or 0) == 0
    assert int(final.get("footprint_overlap_pairs") or 0) == 0


def test_min_rings_dual_hub_parallel_paths_zero_cross() -> None:
    """Classic dual-AN parallel corridors + side stubs → 0 crossings via rings."""
    # anL / anR with 3 disjoint corridors + left stub chain off anL
    nodes = [
        {"fabric_node_id": "anL", "name": "Z-PLAU-AN1-X"},
        {"fabric_node_id": "anR", "name": "Z-ATP-AN1-X"},
        {"fabric_node_id": "orphan", "name": "Z-BSR-AN1-X"},
    ]
    edges = []
    # short upper: anL-a1-a2-anR
    for i, nid in enumerate(["a1", "a2"]):
        nodes.append({"fabric_node_id": nid, "name": f"Z-{nid.upper()}-EN1-X"})
    edges += [
        {"a_node_id": "anL", "b_node_id": "a1"},
        {"a_node_id": "a1", "b_node_id": "a2"},
        {"a_node_id": "a2", "b_node_id": "anR"},
    ]
    # mid lower: anL-b1-b2-b3-anR
    for nid in ["b1", "b2", "b3"]:
        nodes.append({"fabric_node_id": nid, "name": f"Z-{nid.upper()}-EN1-X"})
    edges += [
        {"a_node_id": "anL", "b_node_id": "b1"},
        {"a_node_id": "b1", "b_node_id": "b2"},
        {"a_node_id": "b2", "b_node_id": "b3"},
        {"a_node_id": "b3", "b_node_id": "anR"},
    ]
    # long upper-outer: anL-c1..c4-anR
    for nid in ["c1", "c2", "c3", "c4"]:
        nodes.append({"fabric_node_id": nid, "name": f"Z-{nid.upper()}-EN1-X"})
    edges += [
        {"a_node_id": "anL", "b_node_id": "c1"},
        {"a_node_id": "c1", "b_node_id": "c2"},
        {"a_node_id": "c2", "b_node_id": "c3"},
        {"a_node_id": "c3", "b_node_id": "c4"},
        {"a_node_id": "c4", "b_node_id": "anR"},
    ]
    # side stub off anL
    for nid in ["s1", "s2"]:
        nodes.append({"fabric_node_id": nid, "name": f"Z-{nid.upper()}-EN1-X"})
    edges += [
        {"a_node_id": "anL", "b_node_id": "s1"},
        {"a_node_id": "s1", "b_node_id": "s2"},
    ]

    st = build_state_from_nodes_edges(nodes, edges)
    st2, _, final = run_recipe(st, "agg_rings_v1", LayoutParams())
    assert st2.meta.get("rings_mode") == "min_rings"
    assert int(final.get("edge_crossings") or 0) == 0
    assert int(final.get("footprint_overlap_pairs") or 0) == 0
    # orphan AN parked, not on the bar mid
    assert "orphan" in st2.positions
    assert abs(st2.positions["anL"][1] - st2.positions["anR"][1]) < 1e-6


def test_tiny_recipe_runs() -> None:
    # Mini AN+EN star for skeleton path
    nodes = [
        {"fabric_node_id": "an", "name": "X-AN1-Y"},
        {"fabric_node_id": "e0", "name": "X-EN0-Y"},
        {"fabric_node_id": "e1", "name": "X-EN1-Y"},
        {"fabric_node_id": "e2", "name": "X-EN2-Y"},
    ]
    edges = [
        {"a_node_id": "an", "b_node_id": "e0"},
        {"a_node_id": "e0", "b_node_id": "e1"},
        {"a_node_id": "e1", "b_node_id": "e2"},
    ]
    st = build_state_from_nodes_edges(nodes, edges)
    params = LayoutParams(width_mul=2.0, height_mul=1.5, lane=120.0, target_util=0.05)
    st2, trace, final = run_recipe(st, "smd_corridor_v1", params)
    assert len(trace) >= 5
    assert len(st2.positions) == 4
    assert "edge_crossings" in final
    assert "space_utilization" in final
    # Zero-overlap is a tuning target of resolve_overlaps; recipe must complete.
    assert all("op" in t for t in trace)
