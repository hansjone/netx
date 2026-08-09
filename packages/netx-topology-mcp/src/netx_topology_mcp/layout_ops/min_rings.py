"""Dual-hub minimal-ring layering (PLAU↔ATP style).

When two aggregation hubs share ≥2 nearly interior-disjoint corridors,
nest those paths on alternating ellipse bands and park leftover side
chains in free left/right sectors. Preferable to per-AN petal pack for
agg_bar metro rings (0-crossing when the graph is path-planar).
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _all_simple_paths(
    src: str,
    dst: str,
    adj: dict[str, set[str]],
    *,
    cutoff: int = 16,
    forbid: set[str] | None = None,
) -> list[list[str]]:
    forbid = set(forbid or ())
    paths: list[list[str]] = []
    stack: list[tuple[str, list[str]]] = [(src, [src])]
    while stack:
        u, path = stack.pop()
        if len(path) > cutoff:
            continue
        for v in adj.get(u, ()):
            if v in path or v in forbid:
                continue
            np = path + [v]
            if v == dst:
                paths.append(np)
            else:
                stack.append((v, np))
    return paths


def _path_core(path: list[str]) -> set[str]:
    """Exclusive midpoints (drop portal-adjacent hops that rings often share)."""
    mid = path[1:-1]
    if not mid:
        return set()
    if len(mid) <= 2:
        return set(mid)
    return set(mid[1:-1])


def cover_hub_paths(
    hub_a: str,
    hub_b: str,
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    cutoff: int = 16,
    forbid: set[str] | None = None,
) -> list[list[str]]:
    """Greedy path cover between hubs; cores disjoint (portal-adjacent may share)."""
    paths = _all_simple_paths(hub_a, hub_b, adj, cutoff=cutoff, forbid=forbid)
    paths = [p for p in paths if p[1:-1]]  # skip direct hub-hub
    paths.sort(key=lambda p: (len(p), "".join(names.get(x, x) for x in p)))
    picked: list[list[str]] = []
    used_core: set[str] = set()
    for p in paths:
        core = _path_core(p)
        if not core or core & used_core:
            continue
        picked.append(p)
        used_core |= core
    picked.sort(key=len)
    return picked


def pick_dual_hubs(state: LayoutState) -> tuple[str, str] | None:
    """Top two agg (else core) hubs by degree; need both present in graph."""
    cand = [
        n
        for n, ly in state.layers.items()
        if ly == "agg" and n in state.adj
    ]
    if len(cand) < 2:
        cand = [
            n
            for n, ly in state.layers.items()
            if ly in ("agg", "core") and n in state.adj
        ]
    if len(cand) < 2:
        return None
    cand.sort(
        key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n))
    )
    a, b = cand[0], cand[1]
    if a == b:
        return None
    # Prefer name-stable left/right
    if state.names.get(a, a) > state.names.get(b, b):
        a, b = b, a
    return a, b


def min_rings_eligible(
    state: LayoutState,
    *,
    min_paths: int = 2,
    min_cover_frac: float = 0.35,
) -> dict[str, Any] | None:
    """Return plan dict if dual-hub path cover is worth running."""
    hubs = pick_dual_hubs(state)
    if not hubs:
        return None
    hub_a, hub_b = hubs
    # Ignore other high-layer nodes as path interiors (orphan ANs park later).
    other_hubs = {
        n
        for n, ly in state.layers.items()
        if ly in ("agg", "core") and n not in (hub_a, hub_b)
    }
    paths = cover_hub_paths(
        hub_a, hub_b, state.adj, state.names, forbid=other_hubs
    )
    if len(paths) < min_paths:
        return None
    used = {n for p in paths for n in p[1:-1]}
    access = {n for n, ly in state.layers.items() if ly == "access"}
    if not access:
        return None
    cover = len(used & access) / max(1, len(access))
    # Low cover with many paths is still a star/hub petal job (e.g. BTM),
    # not dual-hub corridor nesting — fall through to ume_petals.
    if cover < min_cover_frac:
        return None
    return {
        "hub_a": hub_a,
        "hub_b": hub_b,
        "paths": paths,
        "cover_frac": cover,
        "path_count": len(paths),
    }


def _comps(nodes: set[str], adj: dict[str, set[str]], names: dict[str, str]) -> list[list[str]]:
    seen: set[str] = set()
    out: list[list[str]] = []
    for n in sorted(nodes, key=lambda x: names.get(x, x)):
        if n in seen:
            continue
        q = deque([n])
        seen.add(n)
        c: list[str] = []
        while q:
            u = q.popleft()
            c.append(u)
            for v in adj.get(u, ()):
                if v in nodes and v not in seen:
                    seen.add(v)
                    q.append(v)
        out.append(c)
    return out


def _attach_of(
    comp: list[str], on: set[str], adj: dict[str, set[str]], fallback: str
) -> tuple[str, str]:
    for n in comp:
        for v in adj.get(n, ()):
            if v in on:
                return v, n
    return fallback, comp[0]


def _order_chain(
    comp: list[str], start: str, adj: dict[str, set[str]], names: dict[str, str]
) -> list[str]:
    s = set(comp)
    if start not in s:
        start = sorted(comp, key=lambda n: names.get(n, n))[0]
    ordered = [start]
    seen = {start}
    prev = None
    cur = start
    while len(ordered) < len(comp):
        nbs = [v for v in adj.get(cur, ()) if v in s and v not in seen]
        if not nbs:
            rest = [n for n in comp if n not in seen]
            if not rest:
                break
            cur = sorted(rest, key=lambda n: names.get(n, n))[0]
            ordered.append(cur)
            seen.add(cur)
            prev = None
            continue
        nbs.sort(
            key=lambda v: (
                len([x for x in adj.get(v, ()) if x in s]),
                names.get(v, v),
            )
        )
        if prev in nbs and len(nbs) > 1:
            nbs = [v for v in nbs if v != prev] + [prev]
        prev, cur = cur, nbs[0]
        ordered.append(cur)
        seen.add(cur)
    return ordered


def layout_min_rings_positions(
    state: LayoutState,
    params: LayoutParams,
    plan: dict[str, Any] | None = None,
) -> dict[str, tuple[float, float]] | None:
    """Compute positions; None if plan ineligible."""
    plan = plan or min_rings_eligible(state)
    if not plan:
        return None
    hub_a = str(plan["hub_a"])
    hub_b = str(plan["hub_b"])
    paths: list[list[str]] = list(plan["paths"])  # type: ignore[arg-type]
    adj, names, layers = state.adj, state.names, state.layers

    rx = max(params.an_gap * 2.5, params.pitch * 7.0, 1100.0)
    ry_step = max(params.side * 1.6, params.lane * 0.9, 280.0)
    pitch = max(params.pitch * 0.8, 160.0)
    base_r = max(params.side * 1.3, 220.0)

    # Prefer denser bands when graph is small.
    n_access = sum(1 for ly in layers.values() if ly == "access")
    if n_access <= 55:
        ry_step = min(ry_step, 320.0)
        pitch = min(pitch, 170.0)

    pos: dict[str, tuple[float, float]] = {
        hub_a: (-rx, 0.0),
        hub_b: (rx, 0.0),
    }
    # Nodes that appear as portal-adjacent on ≥2 paths sit on the hub chord.
    hop_count: dict[str, int] = defaultdict(int)
    for p in paths:
        mid = p[1:-1]
        if not mid:
            continue
        hop_count[mid[0]] += 1
        if len(mid) > 1:
            hop_count[mid[-1]] += 1
    shared_hops = {n for n, c in hop_count.items() if c >= 2}

    used: set[str] = set()
    for i, p in enumerate(paths):
        side = 1 if i % 2 == 0 else -1
        ry = ry_step * ((i // 2) + 1)
        m = len(p)
        for j, n in enumerate(p):
            if n in (hub_a, hub_b):
                continue
            t = j / (m - 1) if m > 1 else 0.5
            if n in shared_hops:
                # Keep on axis so corridor arcs do not cross the hub chord.
                if n not in pos:
                    pos[n] = (-rx + 2 * rx * t, 0.0)
                used.add(n)
                continue
            ang = math.pi * (1 - t)
            if side < 0:
                ang = -ang
            pos[n] = (rx * math.cos(ang), ry * math.sin(ang))
            used.add(n)
    on = used | {hub_a, hub_b}

    # Orphan hubs (other agg/core) + leftover access comps → free sectors
    leftovers = {n for n in names if n not in on}
    orphan_hubs = [
        n
        for n in leftovers
        if layers.get(n) in ("agg", "core")
    ]
    left_access = leftovers - set(orphan_hubs)
    lcomps = _comps(left_access, adj, names)

    by_hub: dict[str, list[tuple[list[str], str]]] = defaultdict(list)
    for c in lcomps:
        att, seed = _attach_of(c, on, adj, hub_a)
        by_hub[att].append((c, seed))

    left_angs = [math.pi, 2.55, 3.55, -2.55]
    right_angs = [0.0, 0.5, -0.5, 0.95]
    for hub, groups in by_hub.items():
        groups = sorted(groups, key=lambda g: -len(g[0]))
        if hub == hub_a:
            angs = left_angs
        elif hub == hub_b:
            angs = right_angs
        else:
            angs = None
        hx, hy = pos.get(hub, (0.0, 0.0))
        for gi, (comp, seed) in enumerate(groups):
            if angs is not None:
                ang = angs[gi % len(angs)]
            else:
                ang = math.atan2(hy, hx or 1e-9) + (gi - (len(groups) - 1) / 2) * 0.25
            for i, n in enumerate(_order_chain(comp, seed, adj, names)):
                r = base_r + i * pitch
                pos[n] = (hx + r * math.cos(ang), hy + r * math.sin(ang))

    # Park orphan hubs above/below clear of bands
    ymin = min((y for _, y in pos.values()), default=0.0)
    ymax = max((y for _, y in pos.values()), default=0.0)
    for i, n in enumerate(sorted(orphan_hubs, key=lambda x: names.get(x, x))):
        pos[n] = (80.0 * (i - (len(orphan_hubs) - 1) / 2), ymin - 280.0 - i * 40)

    for n in names:
        if n not in pos:
            pos[n] = (0.0, ymax + 200.0)

    # Normalize to positive margin
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    x0, y0 = min(xs), min(ys)
    pad = params.margin
    return {n: (x - x0 + pad, y - y0 + pad) for n, (x, y) in pos.items()}


def build_min_ring_skeleton(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult | None:
    """Return OpResult if dual-hub min-rings applies; else None (caller falls back)."""
    params = params or LayoutParams()
    plan = min_rings_eligible(state)
    if not plan:
        return None
    pos = layout_min_rings_positions(state, params, plan)
    if not pos:
        return None

    st = state.copy()
    st.positions = pos
    st.pinned = {plan["hub_a"], plan["hub_b"]}
    # Also pin path portals' immediate corridor order lightly via spine
    spine: set[str] = {plan["hub_a"], plan["hub_b"]}
    for p in plan["paths"]:
        spine.update(p)
    st.spine = spine
    st.last_moved = set(pos.keys())
    cross = count_edge_crossings(pos, st.links)
    st.meta["rings_mode"] = "min_rings"
    st.meta["min_rings"] = {
        "hub_a": plan["hub_a"],
        "hub_b": plan["hub_b"],
        "path_count": plan["path_count"],
        "cover_frac": round(float(plan["cover_frac"]), 3),
        "edge_crossings": cross,
    }
    st.meta["components"] = 1
    return OpResult(
        state=st,
        moved=set(pos.keys()),
        op="build_min_ring_skeleton",
        params={
            "mode": "min_rings",
            "path_count": plan["path_count"],
            "cover_frac": plan["cover_frac"],
            "hubs": [plan["hub_a"], plan["hub_b"]],
        },
        note=(
            f"min-rings dual-hub paths={plan['path_count']} "
            f"cover={plan['cover_frac']:.2f} cross={cross}"
        ),
    )
