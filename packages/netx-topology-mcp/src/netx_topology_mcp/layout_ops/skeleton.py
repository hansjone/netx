"""Atomic op: build corridor skeleton (Tutte + island pack)."""

from __future__ import annotations

from collections import defaultdict, deque
from itertools import product

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.graph_util import (
    bbox,
    connected_components,
    order_ans,
    spine_backbone,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _place_component_tutte(
    comp: list[str],
    state: LayoutState,
    params: LayoutParams,
) -> tuple[dict[str, tuple[float, float]], list[str]]:
    """Tutte embedding for one component. Returns positions + spine order (EN)."""
    g, layers, names = state.adj, state.layers, state.names
    edge_pairs = state.links
    ans = sorted([n for n in comp if layers[n] == "agg"], key=lambda n: names[n])
    ens = [n for n in comp if layers[n] == "access"]
    cores = [n for n in comp if layers[n] == "core"]
    ens_set = set(ens)
    an_order = order_ans(ans, ens_set, g, names)

    pinned: dict[str, tuple[float, float]] = {}
    for i, a in enumerate(an_order):
        pinned[a] = (i * params.an_gap, params.an_y)
    for i, c in enumerate(sorted(cores, key=lambda n: names[n])):
        pinned[c] = (
            (len(an_order) - 1) * params.an_gap / 2 + i * params.an_gap,
            params.an_y - params.an_gap,
        )

    pos: dict[str, list[float]] = {
        n: [float(pinned[n][0]), float(pinned[n][1])] for n in pinned
    }
    if ens:
        dist: dict[str, int] = {}
        nearest: dict[str, str] = {}
        q: deque[str] = deque()
        for a in an_order:
            for nb in g.get(a, ()):
                if nb in ens_set and nb not in dist:
                    dist[nb] = 1
                    nearest[nb] = a
                    q.append(nb)
        while q:
            u = q.popleft()
            for v in g.get(u, ()):
                if v in ens_set and v not in dist:
                    dist[v] = dist[u] + 1
                    nearest[v] = nearest[u]
                    q.append(v)
        for n in ens:
            if n not in dist:
                dist[n] = max(dist.values(), default=1) + 1
                nearest[n] = an_order[0] if an_order else n
        buckets: dict[tuple[str, int], list[str]] = defaultdict(list)
        for n in ens:
            buckets[(nearest[n], dist[n])].append(n)
        for (an, d), group in buckets.items():
            group.sort(key=lambda n: names[n])
            ax = pinned[an][0] if an in pinned else 0.0
            for k, n in enumerate(group):
                pos[n] = [ax + (k - (len(group) - 1) / 2) * 0.35, float(d)]

        for _ in range(80):
            for n in ens:
                neigh = [v for v in g.get(n, ()) if v in pos]
                if not neigh:
                    continue
                pos[n][0] = sum(pos[v][0] for v in neigh) / len(neigh)
                pos[n][1] = sum(pos[v][1] for v in neigh) / len(neigh)
            for n, xy in pinned.items():
                pos[n][0], pos[n][1] = float(xy[0]), float(xy[1])
            for n in ens:
                if pos[n][1] < params.an_y + 0.4:
                    pos[n][1] = params.an_y + 0.4

    raw = {n: (pos[n][0], pos[n][1]) for n in pos}
    x0, y0, x1, y1 = bbox(raw)
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    unit = {n: ((x - x0) / bw, (y - y0) / bh) for n, (x, y) in raw.items()}
    w = max(params.an_gap * max(len(an_order), 1), 1120.0) * params.width_mul
    h = max(140.0 * (1 + len(ens) ** 0.5), 840.0) * params.height_mul
    node_set = set(comp)
    internal = [(a, b) for a, b in edge_pairs if a in node_set and b in node_set]

    best, best_c = None, 10**9
    for fx, fy in product([False, True], repeat=2):
        p = {
            n: (((1 - x) if fx else x) * w, ((1 - y) if fy else y) * h)
            for n, (x, y) in unit.items()
        }
        if an_order and ens:
            if sum(p[a][1] for a in an_order) / len(an_order) > sum(
                p[e][1] for e in ens
            ) / len(ens):
                continue
        c = count_edge_crossings(p, internal)
        if c < best_c:
            best_c, best = c, p
    assert best is not None
    x0, y0, _, _ = bbox(best)
    placed = {n: (x - x0, y - y0) for n, (x, y) in best.items()}
    spine = spine_backbone(ens, g, names) if ens else []
    return placed, spine


def _pack_islands(
    islands: list[dict[str, tuple[float, float]]],
    edge_pairs: list[tuple[str, str]],
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    islands = sorted(islands, key=lambda p: -len(p))
    cols = 2 if len(islands) >= 3 else 1
    n = len(islands)

    def build(flips: list[bool]) -> dict[str, tuple[float, float]]:
        cells: list[list[dict]] = [[] for _ in range(cols)]
        for i, isl in enumerate(islands):
            loc = dict(isl)
            if flips[i]:
                x0, _, x1, _ = bbox(loc)
                mid = (x0 + x1) / 2
                loc = {nid: (2 * mid - x, y) for nid, (x, y) in loc.items()}
            cells[i % cols].append(loc)
        col_w = []
        for c in range(cols):
            w = 0.0
            for loc in cells[c]:
                x0, y0, x1, y1 = bbox(loc)
                w = max(w, x1 - x0 + params.island_pad_x)
            col_w.append(max(w, 1.0))
        out: dict[str, tuple[float, float]] = {}
        for c in range(cols):
            oy = 0.0
            ox = sum(col_w[:c])
            for loc in cells[c]:
                x0, y0, x1, y1 = bbox(loc)
                pad = (col_w[c] - params.island_pad_x - (x1 - x0)) / 2
                for nid, (x, y) in loc.items():
                    out[nid] = (ox + pad + (x - x0), oy + (y - y0))
                oy += (y1 - y0) + params.island_pad_y
        return out

    k = min(n, 5)
    best, best_c = None, 10**9
    for bits in product([False, True], repeat=k):
        flips = list(bits) + [False] * (n - k)
        p = build(flips)
        c = count_edge_crossings(p, edge_pairs)
        if c < best_c:
            best_c, best = c, p
    assert best is not None
    return best


def build_skeleton(state: LayoutState, params: LayoutParams | None = None) -> OpResult:
    """Place Tutte corridor skeleton; pin agg+core+spine; leave sides for next op."""
    params = params or LayoutParams()
    st = state.copy()
    ids = list(st.meta.get("ids") or st.names.keys())
    active = {i for i in ids if st.layers.get(i) in ("core", "agg", "access")}
    others = sorted(
        [i for i in ids if st.layers.get(i) == "other"], key=lambda n: st.names[n]
    )
    comps = connected_components(active, st.adj)
    islands: list[dict[str, tuple[float, float]]] = []
    spine_all: set[str] = set()
    for c in comps:
        placed, spine = _place_component_tutte(c, st, params)
        if placed:
            islands.append(placed)
            spine_all.update(spine)
    pos = _pack_islands(islands, st.links, params) if islands else {}

    if others:
        if pos:
            x0, y0, x1, y1 = bbox(pos)
            fallback_x, fallback_y = x1 + params.island_pad_x * 0.4, y0
        else:
            fallback_x, fallback_y = 0.0, 0.0
        for i, n in enumerate(others):
            neigh = [v for v in st.adj.get(n, ()) if v in pos]
            if neigh:
                mx = sum(pos[v][0] for v in neigh) / len(neigh)
                my = sum(pos[v][1] for v in neigh) / len(neigh)
                pos[n] = (
                    mx + params.pitch * (1.0 + (i % 3) * 0.35),
                    my + params.side * ((i % 5) - 2) * 0.25,
                )
            else:
                pos[n] = (
                    fallback_x + (i % 3) * params.pitch,
                    fallback_y + (i // 3) * params.side,
                )
    for n in ids:
        if n not in pos:
            pos[n] = (0.0, 0.0)

    st.positions = pos
    st.spine = spine_all
    pin = {
        n
        for n in pos
        if st.layers.get(n) in ("agg", "core") or n in spine_all
    }
    st.pinned = pin
    st.last_moved = set(pos.keys())
    st.meta["components"] = len(comps)
    return OpResult(
        state=st,
        moved=set(pos.keys()),
        op="build_skeleton",
        params={
            "width_mul": params.width_mul,
            "height_mul": params.height_mul,
            "an_gap": params.an_gap,
            "spine_n": len(spine_all),
            "pinned_n": len(pin),
        },
        note="Tutte islands packed; pinned=agg+core+spine",
    )
