"""UME-inspired metro layout: 2D AN anchors + rectangular petals.

Learned from human SMD canvas:
- ANs sit in a 2D constellation (not one top rail)
- Access grows around each AN (above+below), on axis-aligned rectangle edges
- Dangling feeders (非环纯链路): leaf → … → first ring/junction attach; no through-cross
- Two-portal minimal rings are atomic hollow units; nested rings use trapezoid bands
  (shortest/narrowest inner → longer/wider outer)
- Islands stay roughly square (aspect ~1.2–1.7)
"""

from __future__ import annotations

import math
from collections import deque

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.graph_util import (
    bbox,
    chain_order,
    connected_components,
    order_ans,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

def _nearest_an(
    n: str, ans: list[str], adj: dict[str, set[str]], names: dict[str, str]
) -> str:
    if not ans:
        return ""
    an_set = set(ans)
    q: deque[tuple[str, int]] = deque([(n, 0)])
    seen = {n}
    best_d: int | None = None
    cands: list[str] = []
    while q:
        u, d = q.popleft()
        if best_d is not None and d > best_d:
            break
        if u in an_set:
            best_d = d
            cands.append(u)
            continue
        if d >= 24:
            continue
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                q.append((v, d + 1))
    if not cands:
        return min(ans, key=lambda a: names.get(a, a))
    return min(cands, key=lambda a: (-len(adj.get(a, ())), names.get(a, a)))


def _hop_from_roots(
    nodes: list[str], roots: list[str], adj: dict[str, set[str]]
) -> dict[str, int]:
    node_set = set(nodes)
    dist: dict[str, int] = {}
    q: deque[str] = deque()
    for r in roots:
        for nb in adj.get(r, ()):
            if nb in node_set and nb not in dist:
                dist[nb] = 1
                q.append(nb)
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v in node_set and v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    for n in nodes:
        if n not in dist:
            dist[n] = (max(dist.values()) if dist else 0) + 1
    return dist


def _point_on_rect(t: float, x0: float, y0: float, w: float, h: float) -> tuple[float, float]:
    perim = 2.0 * (w + h)
    if perim <= 1e-9:
        return x0, y0
    t = t % perim
    if t <= w:
        return x0 + t, y0
    t -= w
    if t <= h:
        return x0 + w, y0 + t
    t -= h
    if t <= w:
        return x0 + w - t, y0 + h
    t -= w
    return x0, y0 + h - t


def _squarish_rect_dims(n: int, params: LayoutParams) -> tuple[float, float]:
    """Rectangle sized for n edge nodes; keep aspect near UME (~1–1.6)."""
    pitch, side = params.pitch, params.side
    n = max(n, 4)
    perim = n * pitch
    # Solve 2(w+h)=perim with w/h ≈ 1.35
    ratio = 1.35
    h = perim / (2.0 * (ratio + 1.0))
    w = ratio * h
    h = max(side * 1.2, h)
    w = max(pitch * 2.5, w)
    return w, h


def _place_on_rect_edge(
    order: list[str], x0: float, y0: float, w: float, h: float
) -> dict[str, tuple[float, float]]:
    n = len(order)
    if n == 0:
        return {}
    perim = 2.0 * (w + h)
    offset = w / 2.0
    out: dict[str, tuple[float, float]] = {}
    for i, nid in enumerate(order):
        t = offset + (i / n) * perim
        out[nid] = _point_on_rect(t, x0, y0, w, h)
    return out


def _cycle_order(
    ring_nodes: list[str], adj: dict[str, set[str]], names: dict[str, str]
) -> list[str]:
    s = set(ring_nodes)
    if len(s) < 3:
        return chain_order(ring_nodes, adj, names)
    sub = {n: [v for v in adj.get(n, ()) if v in s] for n in s}
    deg2 = [n for n in ring_nodes if len(sub.get(n, ())) == 2]
    start = min(deg2 or ring_nodes, key=lambda n: names.get(n, n))
    order = [start]
    prev = None
    cur = start
    for _ in range(len(s) - 1):
        nxts = [v for v in sub.get(cur, ()) if v != prev]
        fresh = [v for v in nxts if v not in order]
        if not fresh:
            break
        pick = min(fresh, key=lambda n: names.get(n, n))
        order.append(pick)
        prev, cur = cur, pick
    if len(order) < len(s):
        return chain_order(ring_nodes, adj, names)
    return order


def _find_access_rings(
    access: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    min_len: int = 4,
    max_len: int = 24,
) -> list[list[str]]:
    """Prefer small/medium rings (UME rings ~10 nodes), not mega cycles."""
    s = {n for n in access if sum(1 for v in adj.get(n, ()) if v in access) >= 2}
    if len(s) < min_len:
        return []
    sub = {
        n: sorted([v for v in adj.get(n, ()) if v in s], key=lambda x: names.get(x, x))
        for n in s
    }
    seen_sets: set[frozenset[str]] = set()
    rings: list[list[str]] = []
    budget = [8000]

    def dfs(start: str, cur: str, path: list[str], parent: str | None) -> None:
        if budget[0] <= 0 or len(path) > max_len:
            return
        for v in sub.get(cur, ()):
            budget[0] -= 1
            if budget[0] <= 0:
                return
            if v == parent:
                continue
            if v == start and len(path) >= min_len:
                key = frozenset(path)
                if key not in seen_sets:
                    seen_sets.add(key)
                    rings.append(list(path))
                continue
            if v in path or names.get(v, v) < names.get(start, start):
                continue
            path.append(v)
            dfs(start, v, path, cur)
            path.pop()

    for start in sorted(s, key=lambda n: names.get(n, n)):
        if budget[0] <= 0:
            break
        dfs(start, start, [start], None)
    # Prefer UME-like sizes (6–16) first, then longer
    rings.sort(
        key=lambda r: (
            0 if 6 <= len(r) <= 16 else 1,
            -len(r),
            names.get(r[0], r[0]),
        )
    )
    kept: list[list[str]] = []
    used: set[str] = set()
    for r in rings:
        if any(n in used for n in r):
            continue
        kept.append(r)
        used.update(r)
        if len(kept) >= 12:
            break
    return kept


def _assign_layers_column(
    nodes: list[str],
    roots: list[str],
    layers: dict[str, str],
    adj: dict[str, set[str]],
    names: dict[str, str],
) -> dict[str, int]:
    cores = sorted(
        [n for n in nodes if layers.get(n) == "core"], key=lambda n: names[n]
    )
    ans = [n for n in roots if n in nodes]
    ens = [n for n in nodes if layers.get(n) == "access"]
    others = sorted(
        [n for n in nodes if layers.get(n) == "other"], key=lambda n: names[n]
    )
    layer_of: dict[str, int] = {}
    base = 0
    for c in cores:
        layer_of[c] = 0
    if cores:
        base = 1
    for a in ans:
        layer_of[a] = base
    access_base = base + (1 if ans else 0)
    hops = _hop_from_roots(ens, ans, adj) if ens and ans else {e: 1 for e in ens}
    hop_cap = min(max(hops.values(), default=1), 6)
    for e in ens:
        h = min(max(hops.get(e, 1), 1), hop_cap)
        layer_of[e] = access_base + (h - 1)
    other_layer = (max(layer_of.values()) if layer_of else 0) + 1
    for o in others:
        layer_of[o] = other_layer
    for n in nodes:
        if n not in layer_of:
            layer_of[n] = other_layer
    return layer_of


def _count_layer_crossings(
    order_a: list[str], order_b: list[str], adj: dict[str, set[str]]
) -> int:
    pos_a = {n: i for i, n in enumerate(order_a)}
    pos_b = {n: i for i, n in enumerate(order_b)}
    edges: list[tuple[int, int]] = []
    for u in order_a:
        for v in adj.get(u, ()):
            if v in pos_b:
                edges.append((pos_a[u], pos_b[v]))
    edges.sort()
    cross = 0
    for i in range(len(edges)):
        ai, bi = edges[i]
        for j in range(i + 1, len(edges)):
            aj, bj = edges[j]
            if aj == ai:
                continue
            if (ai - aj) * (bi - bj) < 0:
                cross += 1
    return cross


def _minimize_crossings(
    layer_lists: list[list[str]],
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    sweeps: int = 10,
) -> list[list[str]]:
    def total(layers: list[list[str]]) -> int:
        t = 0
        for i in range(len(layers) - 1):
            if layers[i] and layers[i + 1]:
                t += _count_layer_crossings(layers[i], layers[i + 1], adj)
        return t

    best = [list(L) for L in layer_lists]
    best_c = total(best)
    cur = best
    for s in range(sweeps):
        layers = [list(L) for L in cur]
        downward = s % 2 == 0
        indices = range(1, len(layers)) if downward else range(len(layers) - 2, -1, -1)
        ref_delta = -1 if downward else 1
        for i in indices:
            ref = layers[i + ref_delta]
            if not ref or not layers[i]:
                continue
            ref_pos = {n: k for k, n in enumerate(ref)}
            scored = []
            for idx, n in enumerate(layers[i]):
                neigh = [ref_pos[v] for v in adj.get(n, ()) if v in ref_pos]
                bc = sum(neigh) / len(neigh) if neigh else float(idx)
                scored.append((bc, idx, names.get(n, n), n))
            scored.sort()
            layers[i] = [t[3] for t in scored]
        c = total(layers)
        if c < best_c:
            best_c = c
            best = layers
        cur = layers
    return best


def _sugiyama_column(
    nodes: list[str],
    roots: list[str],
    state: LayoutState,
    params: LayoutParams,
    *,
    adj: dict[str, set[str]] | None = None,
) -> dict[str, tuple[float, float]]:
    """Low-cross layered skeleton for one AN territory."""
    g = adj if adj is not None else state.adj
    layers, names = state.layers, state.names
    layer_of = _assign_layers_column(nodes, roots, layers, g, names)
    max_l = max(layer_of.values()) if layer_of else 0
    layer_lists: list[list[str]] = [[] for _ in range(max_l + 1)]
    for n in sorted(nodes, key=lambda x: names.get(x, x)):
        layer_lists[layer_of[n]].append(n)
    while len(layer_lists) > 1 and not layer_lists[-1]:
        layer_lists.pop()
    layer_lists = _minimize_crossings(layer_lists, g, names, sweeps=10)
    # Adaptive pitch: wide fans may tighten, but never below label-safe spacing.
    n_access = sum(1 for n in nodes if layers.get(n) == "access")
    pitch = params.pitch
    if n_access > 40:
        pitch = max(165.0, params.pitch * min(1.0, 32.0 / math.sqrt(n_access)))
    side = params.side
    pos: dict[str, tuple[float, float]] = {}
    for li, L in enumerate(layer_lists):
        for i, n in enumerate(L):
            pos[n] = (i * pitch, float(li * side))
    for _ in range(8):
        for li, L in enumerate(layer_lists):
            if not L:
                continue
            prev = set(layer_lists[li - 1]) if li else set()
            nxt = set(layer_lists[li + 1]) if li + 1 < len(layer_lists) else set()
            new_x = {}
            for n in L:
                xs = [
                    pos[v][0]
                    for v in g.get(n, ())
                    if v in pos and (v in prev or v in nxt)
                ]
                new_x[n] = sum(xs) / len(xs) if xs else pos[n][0]
            order_idx = {n: i for i, n in enumerate(L)}
            ordered = sorted(L, key=lambda n: (new_x[n], order_idx[n]))
            xs = [new_x[n] for n in ordered]
            for i in range(1, len(xs)):
                if xs[i] < xs[i - 1] + pitch:
                    xs[i] = xs[i - 1] + pitch
            for n, x in zip(ordered, xs):
                pos[n] = (x, float(li * side))
            layer_lists[li] = ordered
    return pos


def _an_subtrees(
    an: str, ens: list[str], adj: dict[str, set[str]], names: dict[str, str]
) -> list[list[str]]:
    """Partition access into subtrees hanging off each AN neighbor."""
    ens_set = set(ens)
    children = sorted(
        [v for v in adj.get(an, ()) if v in ens_set],
        key=lambda n: names.get(n, n),
    )
    claimed: set[str] = set()
    trees: list[list[str]] = []
    for c in children:
        if c in claimed:
            continue
        tree: list[str] = []
        q: deque[str] = deque([c])
        seen = {c, an}
        while q:
            u = q.popleft()
            tree.append(u)
            claimed.add(u)
            for v in adj.get(u, ()):
                if v in ens_set and v not in seen:
                    seen.add(v)
                    q.append(v)
        if tree:
            trees.append(tree)
    # Orphans not reached
    rest = [e for e in ens if e not in claimed]
    if rest:
        trees.append(rest)
    trees.sort(key=lambda t: -len(t))
    return trees


def _access_degree(n: str, ens_set: set[str], adj: dict[str, set[str]]) -> int:
    return sum(1 for v in adj.get(n, ()) if v in ens_set)


def _extract_dangling_feeders(
    ens: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    attach_set: set[str],
    *,
    min_len: int = 2,
    an_set: set[str] | None = None,
) -> list[list[str]]:
    """Non-ring pure chains: leaf → corridor → first ring/junction attach.

    Junction↔junction corridors and cycle sides are NOT feeders — those belong
    to rings. A feeder is a dead-end spur that should stay an uncrossed unit.

    Nodes that touch an AN are never leaves (they are ring/backbone portals);
    otherwise AN-side ring legs get misclassified as feeders and cross chains.
    """
    ens_set = set(ens)
    ans = set(an_set or ())
    if len(ens_set) < min_len:
        return []
    deg = {n: _access_degree(n, ens_set, adj) for n in ens_set}
    attach = set(attach_set) & (ens_set | ans)
    used: set[str] = set()
    chains: list[list[str]] = []

    def is_leaf(n: str) -> bool:
        if n in attach or n in ans:
            return False
        if adj.get(n, ()) & ans:
            return False  # hangs on AN → ring/backbone, not a dangling tip
        return deg.get(n, 0) <= 1

    leaves = sorted(
        [n for n in ens_set if is_leaf(n)],
        key=lambda n: names.get(n, n),
    )
    for leaf in leaves:
        if leaf in used:
            continue
        path = [leaf]
        prev: str | None = None
        cur = leaf
        while cur not in attach:
            nbs = [
                v
                for v in adj.get(cur, ())
                if v != prev and (v in ens_set or v in attach)
            ]
            if not nbs:
                break
            # Corridor: unique forward; stop at branch (non-attach junction).
            if len(nbs) != 1:
                hit = [v for v in nbs if v in attach]
                if len(hit) == 1:
                    path.append(hit[0])
                    cur = hit[0]
                    break
                break
            nxt = nbs[0]
            path.append(nxt)
            prev, cur = cur, nxt
            if len(path) > 48:
                break
            if cur not in attach and deg.get(cur, 0) >= 3:
                break
        if len(path) < min_len:
            continue
        if path[-1] not in attach and deg.get(path[-1], 0) < 3:
            continue
        # Mark only the dangling body (keep attach node free for rings).
        body = path[:-1] if path[-1] in attach or deg.get(path[-1], 0) >= 3 else path
        if len(body) < 1:
            continue
        used.update(body)
        chains.append(path)

    chains.sort(key=lambda c: (-len(c), names.get(c[0], c[0])))
    return chains


def _extract_pure_chains(
    ens: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    min_len: int = 2,
    attach_set: set[str] | None = None,
) -> list[list[str]]:
    """Backward-compatible alias → dangling feeders (non-ring only)."""
    ens_set = set(ens)
    if attach_set is None:
        # Fallback attach: junctions + leaves' far hubs (deg≥3).
        attach_set = {
            n
            for n in ens_set
            if _access_degree(n, ens_set, adj) >= 3
        }
    return _extract_dangling_feeders(
        ens, adj, names, attach_set, min_len=min_len
    )


def _orient_feeder_outward(chain: list[str], attach_set: set[str]) -> list[str]:
    """Orient feeder as leaf → … → attach."""
    if not chain:
        return []
    if chain[-1] in attach_set:
        return list(chain)
    if chain[0] in attach_set:
        return list(reversed(chain))
    return list(chain)


def _chain_axis_frac(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 2:
        return 1.0
    ok = 0
    for i in range(len(pts) - 1):
        dx = abs(pts[i + 1][0] - pts[i][0])
        dy = abs(pts[i + 1][1] - pts[i][1])
        if dx < 1.0 or dy < 1.0:
            ok += 1
    return ok / (len(pts) - 1)


def _place_chain_unit_at(
    order: list[str],
    pts: list[tuple[float, float]],
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    """Refit one pure chain to an axis-aligned polyline, preserving step lengths.

    Fixed-pitch packing caused mass overlaps → fix_overlaps blew crossings up.
    """
    if not order or len(order) != len(pts):
        return {}
    min_step = max(params.pitch * 0.85, 1.0)
    out: dict[str, tuple[float, float]] = {order[0]: pts[0]}
    x, y = pts[0]
    for i in range(1, len(order)):
        tx, ty = pts[i]
        dx, dy = tx - x, ty - y
        dist = math.hypot(dx, dy)
        step = max(dist, min_step)
        if abs(dx) >= abs(dy):
            x = x + (step if dx >= 0 else -step)
        else:
            y = y + (step if dy >= 0 else -step)
        out[order[i]] = (x, y)
    return out


def _snap_chains_as_wholes(
    pos: dict[str, tuple[float, float]],
    chains: list[list[str]],
    params: LayoutParams,
    edges: list[tuple[str, str]],
) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """Priority pass: accept each pure chain as one polyline only if crossings hold."""
    if not chains:
        return pos, set()
    cur = dict(pos)
    pinned: set[str] = set()
    c_cur = count_edge_crossings(cur, edges) if edges else 0
    # Longer feeders first — they benefit most from whole-unit treatment.
    ordered = sorted(chains, key=lambda c: (-len(c), c[0] if c else ""))
    for ch in ordered:
        order = [n for n in ch if n in cur]
        if len(order) < 3:
            continue
        pts = [cur[n] for n in order]
        # Already a coherent axis unit — leave as-is, still pin.
        if _chain_axis_frac(pts) >= 0.8:
            pinned.update(order)
            continue
        unit = _place_chain_unit_at(order, pts, params)
        if len(unit) != len(order):
            continue
        trial = dict(cur)
        trial.update(unit)
        c1 = count_edge_crossings(trial, edges) if edges else 0
        # Strict: no crossing regression for a cosmetic straighten.
        if c1 <= c_cur:
            cur = trial
            c_cur = c1
            pinned.update(order)
    return cur, pinned


def _surround_by_an_subtrees(
    pos: dict[str, tuple[float, float]],
    an: str,
    ens: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    pinned: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Balance AN subtrees above/below by node weight (UME surround)."""
    if an not in pos or not ens:
        return pos
    pin = pinned or set()
    out = dict(pos)
    ax, ay = out[an]
    out = {n: (x - ax, y - ay) for n, (x, y) in out.items()}
    trees = _an_subtrees(an, ens, adj, names)
    # Skip trees already claimed by chain-first placement.
    movable = []
    for t in trees:
        free = [n for n in t if n not in pin]
        if free:
            movable.append(free)
    if len(movable) <= 1:
        return out
    north_w = south_w = 0
    for tree in movable:
        # Greedy: put next tree on the lighter side.
        to_north = north_w < south_w
        if to_north:
            north_w += len(tree)
            for n in tree:
                if n in out:
                    x, y = out[n]
                    out[n] = (x, -abs(y) if abs(y) > 1e-6 else -1.0)
        else:
            south_w += len(tree)
            for n in tree:
                if n in out:
                    x, y = out[n]
                    out[n] = (x, abs(y) if abs(y) > 1e-6 else 1.0)
    return out


def _access_junctions(
    ens: list[str],
    adj: dict[str, set[str]],
    an_set: set[str],
) -> list[str]:
    """Portal candidates: access deg≥3, or deg≥2 and touching an AN."""
    ens_set = set(ens)
    out: list[str] = []
    for n in ens:
        adeg = _access_degree(n, ens_set, adj)
        if adeg >= 3 or (adeg >= 2 and (adj.get(n, ()) & an_set)):
            out.append(n)
    return out


def _corridor_paths_between(
    p1: str,
    p2: str,
    ens_set: set[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    max_len: int = 24,
    an_set: set[str] | None = None,
) -> list[list[str]]:
    """All p1→p2 paths whose interior nodes are pure corridor (access-deg ≤2).

    Portals may be ANs (outside ens_set); interiors stay on access.
    """
    ans = set(an_set or ())

    def adeg(n: str) -> int:
        # Count AN touch as a stub so AN-hanging corridor nodes stay walkable.
        return _access_degree(n, ens_set, adj) + (
            1 if (adj.get(n, ()) & ans) and n not in (p1, p2) else 0
        )

    def corridor_ok(n: str) -> bool:
        """Interior: at most 2 access neighbors (AN link ignored for cap)."""
        return _access_degree(n, ens_set, adj) <= 2

    paths: list[list[str]] = []
    # Direct edge counts as an empty-interior side.
    if p2 in adj.get(p1, ()):
        paths.append([p1, p2])

    for nxt in sorted(adj.get(p1, ()), key=lambda n: names.get(n, n)):
        if nxt == p2:
            continue
        if nxt not in ens_set:
            continue
        if not corridor_ok(nxt):
            continue
        path = [p1, nxt]
        prv, cur = p1, nxt
        seen = {p1, nxt}
        ok = True
        while cur != p2:
            nbs = [
                v
                for v in adj.get(cur, ())
                if v != prv and (v == p2 or v in ens_set)
            ]
            forward = [
                v
                for v in nbs
                if v == p2 or (v in ens_set and corridor_ok(v) and v not in seen)
            ]
            if p2 in nbs and p2 not in forward:
                forward.append(p2)
            if len(forward) != 1:
                ok = False
                break
            prv, cur = cur, forward[0]
            if cur in seen and cur != p2:
                ok = False
                break
            path.append(cur)
            seen.add(cur)
            if len(path) > max_len:
                ok = False
                break
        if ok and path[-1] == p2 and len(path) >= 2:
            paths.append(path)

    # Unique by interior node set (keep shortest name-stable representative).
    best: dict[frozenset[str], list[str]] = {}
    for p in paths:
        key = frozenset(p[1:-1])
        prev = best.get(key)
        if prev is None or len(p) < len(prev):
            best[key] = p
    return sorted(
        best.values(),
        key=lambda p: (len(p), names.get(p[1], p[1]) if len(p) > 1 else ""),
    )


def _is_two_portal_cycle(
    path_a: list[str],
    path_b: list[str],
    adj: dict[str, set[str]],
    ens_set: set[str],
    an_set: set[str],
) -> bool:
    """True if cycle(path_a ∪ path_b) touches the outside only at the two portals."""
    if len(path_a) < 2 or len(path_b) < 2:
        return False
    if path_a[0] != path_b[0] or path_a[-1] != path_b[-1]:
        return False
    a, b = path_a[0], path_a[-1]
    if set(path_a[1:-1]) & set(path_b[1:-1]):
        return False
    node_set = set(path_a) | set(path_b)
    if len(node_set) < 3:
        return False
    portals = []
    for n in node_set:
        # Any neighbor outside the cycle counts (AN uplink, peer AN, side chain).
        ext = [v for v in adj.get(n, ()) if v not in node_set]
        if ext:
            portals.append(n)
    return set(portals) == {a, b}


def _find_two_portal_ring_groups(
    ens: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    an_set: set[str],
) -> list[dict[str, object]]:
    """Portal pairs with ≥2 corridor paths; paths sorted shortest→longest (inner→outer).

    ANs may be portals — metro rings often hang off an AN with a direct edge
    plus a longer access corridor (the TNM/JROS/ADAK/SRIN pattern).
    """
    ens_set = set(ens)
    junc = _access_junctions(ens, adj, an_set)
    an_portals = sorted(
        [a for a in an_set if any(v in ens_set for v in adj.get(a, ()))],
        key=lambda n: names.get(n, n),
    )
    portals = sorted(set(junc) | set(an_portals), key=lambda n: names.get(n, n))
    groups: list[dict[str, object]] = []

    for i, a in enumerate(portals):
        for b in portals[i + 1 :]:
            paths = _corridor_paths_between(
                a, b, ens_set, adj, names, an_set=an_set
            )
            if len(paths) < 2:
                continue
            # Disjoint interiors, shortest first (= innermost).
            paths = sorted(
                paths,
                key=lambda p: (len(p), names.get(p[1], p[1]) if len(p) > 2 else ""),
            )
            chosen: list[list[str]] = []
            used_mid: set[str] = set()
            for p in paths:
                mid = set(p[1:-1])
                if mid & used_mid:
                    continue
                if chosen and not any(
                    _is_two_portal_cycle(p, q, adj, ens_set, an_set) for q in chosen
                ):
                    continue
                chosen.append(p)
                used_mid |= mid
            if len(chosen) < 2:
                continue
            if not any(
                _is_two_portal_cycle(chosen[0], q, adj, ens_set, an_set)
                for q in chosen[1:]
            ):
                continue
            groups.append({"portals": (a, b), "paths": chosen})

    groups.sort(
        key=lambda g: (
            -len(g["paths"]),  # type: ignore[arg-type]
            names.get(g["portals"][0], g["portals"][0]),  # type: ignore[index]
        )
    )
    kept: list[dict[str, object]] = []
    used_interior: set[str] = set()
    for g in groups:
        a, b = g["portals"]  # type: ignore[misc]
        paths = g["paths"]  # type: ignore[assignment]
        interiors = set()
        for p in paths:  # type: ignore[union-attr]
            interiors |= set(p[1:-1])
        if interiors & used_interior:
            continue
        used_interior |= interiors
        kept.append(g)
    return kept


def _place_path_on_span(
    path: list[str],
    x_left: float,
    x_right: float,
    y: float,
) -> dict[str, tuple[float, float]]:
    """Place corridor interiors evenly on a horizontal span (portals excluded)."""
    mid = path[1:-1]
    if not mid:
        return {}
    out: dict[str, tuple[float, float]] = {}
    for i, n in enumerate(mid):
        t = (i + 1) / (len(mid) + 1)
        out[n] = (x_left + t * (x_right - x_left), y)
    return out


def _ring_nodes_from_groups(groups: list[dict[str, object]]) -> set[str]:
    out: set[str] = set()
    for g in groups:
        a, b = g["portals"]  # type: ignore[misc]
        out.add(a)
        out.add(b)
        for p in g["paths"]:  # type: ignore[union-attr]
            out.update(p)
    return out


def _reflect_point_across_segment(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float]:
    """Reflect point p across the infinite line through a—b."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return p
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    qx, qy = ax + t * dx, ay + t * dy
    return (2.0 * qx - px, 2.0 * qy - py)


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """Ray-casting inclusion; boundary counts as inside."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if abs((yj - yi) * (x - xi) - (xj - xi) * (y - yi)) < 1e-6:
            # on edge segment?
            if min(xi, xj) - 1e-6 <= x <= max(xi, xj) + 1e-6 and min(
                yi, yj
            ) - 1e-6 <= y <= max(yi, yj) + 1e-6:
                return True
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def _ring_polygon(
    group: dict[str, object], pos: dict[str, tuple[float, float]]
) -> tuple[set[str], list[tuple[float, float]], list[str]]:
    """Cycle polygon for a two-portal ring group (portals + corridor bands)."""
    a, b = group["portals"]  # type: ignore[misc]
    paths: list[list[str]] = group["paths"]  # type: ignore[assignment]
    unit: set[str] = {a, b}
    for p in paths:
        unit.update(p)
    # Prefer longest path as one arc; close via remaining arcs / chord.
    arcs = []
    for p in paths:
        if p[0] == b and p[-1] == a:
            p = list(reversed(p))
        if p[0] == a and p[-1] == b:
            arcs.append(p)
    if not arcs:
        order = [n for n in unit if n in pos]
        pts = [pos[n] for n in order]
        return unit, pts, order
    arcs.sort(key=len)
    # Walk a → short … → b → reverse(long) → a
    short, longp = arcs[0], arcs[-1]
    order = list(short) + list(reversed(longp[1:-1]))
    poly = [pos[n] for n in order if n in pos]
    return unit, poly, order


def _stub_crosses_ring(
    stub: str,
    portal: str,
    pos: dict[str, tuple[float, float]],
    order: list[str],
) -> bool:
    """True if stub—portal properly intersects a ring edge (not at portal)."""
    from netx_topology_mcp.layout_metrics import segments_properly_intersect

    if stub not in pos or portal not in pos or len(order) < 2:
        return False
    p0, p1 = pos[stub], pos[portal]
    m = len(order)
    for i in range(m):
        u, v = order[i], order[(i + 1) % m]
        if portal in (u, v):
            continue
        if u not in pos or v not in pos:
            continue
        if segments_properly_intersect(p0, p1, pos[u], pos[v]):
            return True
    return False


def _ring_descriptors(
    groups: list[dict[str, object]],
    pos: dict[str, tuple[float, float]],
) -> list[tuple[str, str, set[str], list[tuple[float, float]], list[str]]]:
    """(portal_a, portal_b, unit, poly, order) for placeable rings; small first."""
    descs: list[tuple[str, str, set[str], list[tuple[float, float]], list[str]]] = []
    for g in groups:
        a, b = g["portals"]  # type: ignore[misc]
        unit, poly, order = _ring_polygon(g, pos)
        if len(poly) < 3 or a not in pos or b not in pos:
            continue
        descs.append((a, b, unit, poly, order))
    descs.sort(key=lambda t: len(t[2]))
    return descs


def _stub_clean_for_rings(
    stub: str,
    portal: str,
    pos: dict[str, tuple[float, float]],
    descs: list[tuple[str, str, set[str], list[tuple[float, float]], list[str]]],
) -> bool:
    """True if stub is outside every portal-ring and its portal edge pierces none."""
    if stub not in pos:
        return False
    sx, sy = pos[stub]
    for a, b, unit, poly, order in descs:
        if stub in unit or portal not in (a, b):
            continue
        if _point_in_poly(sx, sy, poly):
            return False
        if _stub_crosses_ring(stub, portal, pos, order):
            return False
    return True


def _total_ring_thru(
    pos: dict[str, tuple[float, float]],
    descs: list[tuple[str, str, set[str], list[tuple[float, float]], list[str]]],
    edges: list[tuple[str, str]],
) -> int:
    return sum(_foreign_edge_crossings(pos, unit, edges) for _, _, unit, _, _ in descs)


def _eject_intruders_from_rings(
    pos: dict[str, tuple[float, float]],
    groups: list[dict[str, object]],
    adj: dict[str, set[str]],
    params: LayoutParams,
    edges: list[tuple[str, str]],
    *,
    protected: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Keep minimal rings hollow: no portal-stub node/edge through any ring.

    Covers triangles (SPB–VOTI–MRBD) as well as larger trapezoid rings. A stub
    may sit *outside* the polygon yet still pierce a chord (PNBR–SPB × MRBD–VOTI).
    Candidates must stay clean for **all** portal-rings sharing the stub (so
    ejecting from SPB–TNM cannot park PNBR through the MRBD triangle).
    """
    if not groups:
        return pos
    out = dict(pos)
    prot = set(protected or ())
    pad = max(params.pitch * 0.75, 120.0)

    for _round in range(4):
        descs = _ring_descriptors(groups, out)
        if not descs:
            break
        moved_any = False
        # Portal stubs across all rings (dedupe).
        jobs: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for a, b, unit, _poly, _order in descs:
            for p in (a, b):
                for n in adj.get(p, ()):
                    if n in unit or n in prot or n not in out:
                        continue
                    key = (n, p)
                    if key in seen:
                        continue
                    seen.add(key)
                    jobs.append((n, p))
        # Prefer stubs that currently violate something; smaller rings first via descs.
        jobs.sort(
            key=lambda t: (
                0 if not _stub_clean_for_rings(t[0], t[1], out, descs) else 1,
                t[0],
                t[1],
            )
        )

        for n, p in jobs:
            if _stub_clean_for_rings(n, p, out, descs):
                continue
            px, py = out[p]
            # Aim toward outside neighbors not in any ring that uses this portal.
            ring_units = [unit for a, b, unit, _, _ in descs if p in (a, b)]
            blocked = {p} | set().union(*ring_units) if ring_units else {p}
            outs = [v for v in adj.get(n, ()) if v not in blocked and v in out]
            if outs:
                tx = sum(out[v][0] for v in outs) / len(outs)
                ty = sum(out[v][1] for v in outs) / len(outs)
            else:
                # Push away from the other portals of rings sharing p.
                others = [
                    o
                    for a, b, _, _, _ in descs
                    if p in (a, b)
                    for o in ((a if b == p else b),)
                    if o in out
                ]
                if others:
                    ox = sum(out[o][0] for o in others) / len(others)
                    oy = sum(out[o][1] for o in others) / len(others)
                    tx, ty = px + (px - ox), py + (py - oy)
                else:
                    tx, ty = px - pad, py
            dx, dy = tx - px, ty - py
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            candidates: list[tuple[float, float]] = []
            for scale in (1.0, 1.4, 1.9, 2.5, 3.2):
                candidates.append((px + ux * pad * scale, py + uy * pad * scale))
            for rad in (1.0, 1.3, 1.8, 2.4):
                for k in range(16):
                    ang = (2.0 * math.pi * k) / 16.0
                    candidates.append(
                        (px + pad * rad * math.cos(ang), py + pad * rad * math.sin(ang))
                    )

            thru0 = _total_ring_thru(out, descs, edges)
            best = None
            best_key: tuple[float, float, float] | None = None
            for cand in candidates:
                trial = dict(out)
                trial[n] = cand
                # Refresh polys for point-in-poly against moved stub only — ring
                # nodes are fixed, so existing descs polys stay valid.
                if not _stub_clean_for_rings(n, p, trial, descs):
                    continue
                thru1 = _total_ring_thru(trial, descs, edges)
                if thru1 > thru0:
                    continue
                aim_d = (cand[0] - tx) ** 2 + (cand[1] - ty) ** 2
                key = (float(thru1), aim_d, abs(cand[0] - px) + abs(cand[1] - py))
                if best_key is None or key < best_key:
                    best_key = key
                    best = cand
            if best is not None and best != out[n]:
                out[n] = best
                moved_any = True
        if not moved_any:
            break
    return out


def _foreign_edge_crossings(
    pos: dict[str, tuple[float, float]],
    unit_nodes: set[str],
    edges: list[tuple[str, str]],
) -> int:
    """Crossings of unit-internal edges by foreign edges (through the unit)."""
    if len(unit_nodes) < 2 or not edges:
        return 0
    from netx_topology_mcp.layout_metrics import segments_properly_intersect

    internal = [(a, b) for a, b in edges if a in unit_nodes and b in unit_nodes]
    foreign = [
        (a, b) for a, b in edges if a not in unit_nodes or b not in unit_nodes
    ]
    if not internal or not foreign:
        return 0
    n = 0
    for a, b in internal:
        if a not in pos or b not in pos:
            continue
        p1, p2 = pos[a], pos[b]
        for c, d in foreign:
            if c not in pos or d not in pos:
                continue
            if c in (a, b) or d in (a, b):
                continue
            if segments_properly_intersect(p1, p2, pos[c], pos[d]):
                n += 1
    return n


def _point_segment_dist(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L2 = vx * vx + vy * vy
    if L2 < 1e-12:
        return math.hypot(wx, wy)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _foreign_edges_through_nodes(
    pos: dict[str, tuple[float, float]],
    unit_nodes: set[str],
    edges: list[tuple[str, str]],
    *,
    thr: float = 28.0,
) -> int:
    """Count foreign edges that pass too close to a ring interior node (icon hit)."""
    if not unit_nodes or not edges:
        return 0
    interiors = [
        n
        for n in unit_nodes
        if n in pos
        and sum(1 for u, v in edges if n in (u, v) and (u in unit_nodes and v in unit_nodes))
        >= 2
    ]
    # Prefer explicit interiors: nodes in unit that are not only portals of a 2-set.
    # Fallback: any unit node that is an endpoint of ≥2 internal edges.
    if not interiors:
        interiors = [n for n in unit_nodes if n in pos]
    foreign = [
        (a, b)
        for a, b in edges
        if (a not in unit_nodes or b not in unit_nodes)
        and a in pos
        and b in pos
    ]
    hits = 0
    for n in interiors:
        # Skip if n is an endpoint of the foreign edge.
        for a, b in foreign:
            if n in (a, b):
                continue
            if _point_segment_dist(pos[n], pos[a], pos[b]) < thr:
                hits += 1
                break
    return hits


def _ring_side_cost(
    pos: dict[str, tuple[float, float]],
    unit_nodes: set[str],
    edges: list[tuple[str, str]],
) -> tuple[float, float]:
    """Lower is better: (through-crossings, icon-hits by foreign edges)."""
    return (
        float(_foreign_edge_crossings(pos, unit_nodes, edges)),
        float(_foreign_edges_through_nodes(pos, unit_nodes, edges)),
    )


def _place_two_portal_ring_groups(
    pos: dict[str, tuple[float, float]],
    ens: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    an_set: set[str],
    params: LayoutParams,
    edges: list[tuple[str, str]],
    *,
    blocked: set[str] | None = None,
    groups: list[dict[str, object]] | None = None,
) -> tuple[dict[str, tuple[float, float]], set[str]]:
    """Place two-portal rings as trapezoid bands (short/narrow inner → wide outer)."""
    groups = groups if groups is not None else _find_two_portal_ring_groups(
        ens, adj, names, an_set
    )
    if not groups:
        return pos, set()
    base = dict(pos)
    c0 = count_edge_crossings(base, edges) if edges else 0
    trial = dict(base)
    pinned: set[str] = set()
    blocked = set(blocked or ())
    gap = max(params.side * 0.9, 150.0)
    flare = max(params.pitch * 0.45, 70.0)

    for g in groups:
        a, b = g["portals"]  # type: ignore[misc]
        paths: list[list[str]] = g["paths"]  # type: ignore[assignment]
        if a in blocked or b in blocked:
            continue
        if a not in trial or b not in trial:
            continue
        interiors = {n for p in paths for n in p[1:-1]}
        if interiors & blocked:
            continue

        ax, ay = trial[a]
        bx, by = trial[b]
        mx, my = (ax + bx) / 2, (ay + by) / 2
        base_span = max(
            math.hypot(bx - ax, by - ay),
            params.pitch * max(3, max(len(p) for p in paths)),
        )
        x0, x1 = mx - base_span / 2, mx + base_span / 2
        trial[a] = (x0, my)
        trial[b] = (x1, my)

        unit_nodes = {a, b} | interiors
        # Trapezoid nest: path i at ±dist, span widens with i (outer wider).
        # Orient every path a→…→b so portal-adjacent nodes sit on the correct side
        # (reversed corridors are what produced the TNM/JROS × chain X-crossing).
        for i, path in enumerate(paths):
            if path[0] == b and path[-1] == a:
                path = list(reversed(path))
            elif path[0] != a or path[-1] != b:
                continue
            sign = -1.0 if i % 2 == 0 else 1.0
            dist = (i // 2 + 1) * gap
            half = base_span / 2 + i * flare
            xl, xr = mx - half, mx + half
            y = my + sign * dist
            trial.update(_place_path_on_span(path, xl, xr, y))
            trial[a] = (x0, my)
            trial[b] = (x1, my)
            pinned.update(path)

        # Prefer geometry that does not let foreign links pierce the ring unit.
        thru0 = _foreign_edge_crossings(base, unit_nodes, edges)
        thru1 = _foreign_edge_crossings(trial, unit_nodes, edges)
        if thru1 > thru0 + 2:
            for n in unit_nodes:
                if n in base:
                    trial[n] = base[n]
            pinned -= unit_nodes

    if not pinned:
        return base, set()
    c1 = count_edge_crossings(trial, edges) if edges else 0
    if c1 <= max(c0 * 1.35, c0 + 50):
        return trial, pinned
    return base, set()


def _orient_ring_sides(
    pos: dict[str, tuple[float, float]],
    groups: list[dict[str, object]],
    edges: list[tuple[str, str]],
    *,
    pinned: set[str] | None = None,
    max_interiors: int = 3,
    push: float = 0.0,
) -> dict[str, tuple[float, float]]:
    """Flip small-ring corridors across the portal chord if that clears pierces.

    Cross-petal chords (BNT–SPB through MRBD–VOTI) are only visible after AN
    petals are packed — call this on the full component, not inside one petal.
    Restrict to small rings (triangles / short arcs) so large trapezoids stay put.
    """
    if not groups or not edges:
        return pos
    out = dict(pos)
    pin = set(pinned or ())
    # Smallest first so nested outer bands see the settled inner apex.
    ordered = sorted(
        groups,
        key=lambda g: len({n for p in g["paths"] for n in p[1:-1]}),  # type: ignore[index]
    )
    for g in ordered:
        a, b = g["portals"]  # type: ignore[misc]
        paths: list[list[str]] = g["paths"]  # type: ignore[assignment]
        if a not in out or b not in out:
            continue
        interiors = {n for p in paths for n in p[1:-1] if n in out}
        if not interiors or len(interiors) > max_interiors:
            continue
        if pin and (interiors - pin):
            continue
        unit_nodes = {a, b} | interiors
        pa, pb = out[a], out[b]
        flipped = dict(out)
        for n in interiors:
            flipped[n] = _reflect_point_across_segment(out[n], pa, pb)
        k0 = _ring_side_cost(out, unit_nodes, edges)
        k1 = _ring_side_cost(flipped, unit_nodes, edges)
        c_cur = count_edge_crossings(out, edges)
        c_flip = count_edge_crossings(flipped, edges)
        take = False
        if k1 < k0 and c_flip <= c_cur + 2:
            take = True
        elif k1 == k0 and c_flip < c_cur:
            take = True
        if take:
            out = flipped
        # If foreign edges still graze the apex, push interiors further off-chord.
        if push > 0 and _ring_side_cost(out, unit_nodes, edges)[1] > 0:
            pa, pb = out[a], out[b]
            dx, dy = pb[0] - pa[0], pb[1] - pa[1]
            L = math.hypot(dx, dy) or 1.0
            # Normal pointing toward current interior centroid.
            cx = sum(out[n][0] for n in interiors) / len(interiors)
            cy = sum(out[n][1] for n in interiors) / len(interiors)
            mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
            nx, ny = -dy / L, dx / L
            if (cx - mx) * nx + (cy - my) * ny < 0:
                nx, ny = -nx, -ny
            pushed = dict(out)
            for n in interiors:
                x, y = out[n]
                pushed[n] = (x + nx * push, y + ny * push)
            pk = _ring_side_cost(pushed, unit_nodes, edges)
            pc = count_edge_crossings(pushed, edges)
            if pk < _ring_side_cost(out, unit_nodes, edges) and pc <= c_cur + 2:
                out = pushed
            elif pk[0] <= k0[0] and pk[1] < k0[1] and pc <= c_cur + 2:
                out = pushed
    return out


def _snap_small_rings(
    pos: dict[str, tuple[float, float]],
    ens: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    params: LayoutParams,
    edges: list[tuple[str, str]],
    *,
    blocked: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Snap UME-sized rings (6–16) onto local rectangle edges if crossings allow."""
    rings = _find_access_rings(ens, adj, names, min_len=6, max_len=16)
    if not rings:
        return pos
    base = dict(pos)
    c0 = count_edge_crossings(base, edges) if edges else 0
    trial = dict(base)
    used: set[str] = set(blocked or ())
    for ring in rings[:8]:
        if any(n in used for n in ring):
            continue
        order = _cycle_order(ring, adj, names)
        pts = [trial[n] for n in order if n in trial]
        if len(pts) < 4:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        w, h = _squarish_rect_dims(len(order), params)
        # Keep local scale of the cluster
        w = max(w * 0.55, max(xs) - min(xs), params.pitch * 2)
        h = max(h * 0.55, max(ys) - min(ys), params.side)
        trial.update(_place_on_rect_edge(order, cx - w / 2, cy - h / 2, w, h))
        used.update(order)
    c1 = count_edge_crossings(trial, edges) if edges else 0
    if c1 <= max(c0 * 1.25, c0 + 20):
        return trial
    return base


def _compact_petal_aspect(
    pos: dict[str, tuple[float, float]],
    *,
    max_aspect: float = 2.4,
) -> dict[str, tuple[float, float]]:
    """Soft axis compress so each AN territory is not a mega-wide strip."""
    if len(pos) < 3:
        return pos
    x0, y0, x1, y1 = bbox(pos)
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    aspect = max(w / h, h / w)
    if aspect <= max_aspect:
        return pos
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if w >= h:
        sx = (max_aspect * h) / w
        return {n: (cx + (x - cx) * sx, y) for n, (x, y) in pos.items()}
    sy = (max_aspect * w) / h
    return {n: (x, cy + (y - cy) * sy) for n, (x, y) in pos.items()}


def _contract_dangling_feeders(
    nodes: list[str],
    ens: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    attach_set: set[str],
) -> tuple[list[str], dict[str, set[str]], list[list[str]], set[str]]:
    """Contract feeder bodies onto their attach node (leaf…→attach)."""
    # Infer ANs from attach_set members that are not in ens (call site passes union).
    ens_set = set(ens)
    an_guess = {n for n in attach_set if n not in ens_set}
    chains = [
        _orient_feeder_outward(ch, attach_set)
        for ch in _extract_dangling_feeders(
            ens, adj, names, attach_set, min_len=2, an_set=an_guess
        )
    ]
    if not chains:
        node_set = set(nodes)
        return list(nodes), {n: set(adj.get(n, ())) & node_set for n in nodes}, [], set()

    feeders: list[list[str]] = []
    hidden: dict[str, str] = {}
    for order in chains:
        if len(order) < 2:
            continue
        attach = order[-1]
        feeders.append(order)
        for n in order[:-1]:
            hidden[n] = attach

    reduced = [n for n in nodes if n not in hidden]
    reduced_set = set(reduced)

    def remap(v: str) -> str | None:
        if v in hidden:
            return hidden[v]
        if v in reduced_set:
            return v
        return None

    # Edges from hidden feeder bodies attach to their attach node.
    by_attach: dict[str, list[list[str]]] = {}
    for order in feeders:
        by_attach.setdefault(order[-1], []).append(order)

    cadj: dict[str, set[str]] = {n: set() for n in reduced}
    for n in reduced:
        srcs = [n]
        for order in by_attach.get(n, ()):
            srcs.extend(order[:-1])
        for u in srcs:
            for v in adj.get(u, ()):
                rv = remap(v)
                if rv is not None and rv != n:
                    cadj[n].add(rv)
    pinned = {n for order in feeders for n in order[:-1]}
    return reduced, cadj, feeders, pinned


def _expand_feeder_units(
    pos: dict[str, tuple[float, float]],
    feeders: list[list[str]],
    avoid: dict[str, tuple[float, float]],
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    """Expand feeders as axis-aligned rays outward from attach (away from ring)."""
    if not feeders:
        return pos
    out = dict(pos)
    step = max(params.side, 150.0)
    # Lateral offset when several feeders share one attach.
    per_attach: dict[str, int] = {}
    for order in feeders:
        if len(order) < 2:
            continue
        attach = order[-1]
        if attach not in out:
            continue
        ax, ay = out[attach]
        cx, cy = avoid.get(attach, (ax, ay - step))
        dx, dy = ax - cx, ay - cy
        if abs(dx) + abs(dy) < 1e-6:
            ux, uy = 0.0, 1.0
        elif abs(dx) >= abs(dy):
            ux, uy = (1.0 if dx >= 0 else -1.0), 0.0
        else:
            ux, uy = 0.0, (1.0 if dy >= 0 else -1.0)
        # Perpendicular lane so sibling feeders do not overlap.
        lane = per_attach.get(attach, 0)
        per_attach[attach] = lane + 1
        px, py = -uy, ux
        ox = lane * step * 0.9
        n = len(order)
        for i, node in enumerate(order):
            k = n - 1 - i
            out[node] = (
                ax + k * step * ux + ox * px,
                ay + k * step * uy + ox * py,
            )
        out[attach] = (ax, ay)
    return out


def _layout_an_petals(
    nodes: list[str],
    roots: list[str],
    state: LayoutState,
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    """Rings (trapezoid) + dangling feeders (uncrossed) + layered remainder."""
    if not nodes:
        return {}
    g, layers, names = state.adj, state.layers, state.names
    ans = [n for n in roots if n in nodes]
    an = ans[0] if ans else None
    ens = [n for n in nodes if layers.get(n) == "access"]
    an_set = {n for n in nodes if layers.get(n) == "agg"} | ({an} if an else set())

    node_set = set(nodes)
    edges = [(a, b) for a, b in state.links if a in node_set and b in node_set]

    # 1) Minimal two-portal rings first (define attach targets for feeders).
    ring_groups = _find_two_portal_ring_groups(ens, g, names, an_set)
    ring_nodes = _ring_nodes_from_groups(ring_groups)
    junctions = set(_access_junctions(ens, g, an_set))
    attach_set = ring_nodes | junctions | an_set

    # 2) Dangling non-ring feeders → contract onto attach, layer backbone.
    #    Pass ANs in attach_set so AN-hanging ring legs are not mistaken for leaves.
    reduced, cadj, feeders, pinned = _contract_dangling_feeders(
        nodes, ens, g, names, attach_set | an_set
    )
    pos = _sugiyama_column(reduced, ans, state, params, adj=cadj)
    c_base = count_edge_crossings(pos, edges) if edges else 0
    if feeders:
        plain = _sugiyama_column(nodes, ans, state, params)
        c_plain = count_edge_crossings(plain, edges) if edges else 0
        if c_base > max(c_plain * 1.55, c_plain + 100):
            pos, feeders, pinned, c_base = plain, {}, set(), c_plain

    # 3) Surround remainder (keep feeder bodies for later expand).
    if an and an in pos and ens:
        trial = _surround_by_an_subtrees(pos, an, ens, g, names, pinned=pinned)
        c1 = count_edge_crossings(trial, edges) if edges else 0
        if c1 <= max(c_base * 1.2, c_base + 30):
            pos = trial

    # 4) Place rings as trapezoid bands (hollow units; reject if pierced worse).
    pos, ring_pins = _place_two_portal_ring_groups(
        pos,
        ens,
        g,
        names,
        an_set,
        params,
        edges,
        blocked=set(),
        groups=ring_groups,
    )
    pinned |= ring_pins
    # Side stubs (PNBR etc.) must not sit inside the hollow ring.
    pos = _eject_intruders_from_rings(
        pos, ring_groups, g, params, edges, protected=ring_nodes
    )

    # Avoid centroids so feeders grow outward, not through the ring.
    avoid: dict[str, tuple[float, float]] = {}
    if an and an in pos:
        for n in attach_set:
            avoid[n] = pos[an]
    for g_ring in ring_groups:
        unit = set()
        a, b = g_ring["portals"]  # type: ignore[misc]
        unit.add(a)
        unit.add(b)
        for p in g_ring["paths"]:  # type: ignore[union-attr]
            unit.update(p)
        pts = [pos[n] for n in unit if n in pos]
        if not pts:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        for n in unit:
            avoid[n] = (cx, cy)

    # 5) Expand dangling feeders as uncrossed outward rays.
    if feeders:
        pos = _expand_feeder_units(pos, feeders, avoid, params)
        pinned |= {n for order in feeders for n in order[:-1]}
    # Feeder expand can re-enter a ring — eject again.
    pos = _eject_intruders_from_rings(
        pos, ring_groups, g, params, edges, protected=ring_nodes
    )

    # 6) Other small rings on leftovers.
    free_ens = [e for e in ens if e not in pinned]
    if free_ens:
        before = dict(pos)
        snapped = _snap_small_rings(
            pos, free_ens, g, names, params, edges, blocked=pinned | ring_nodes
        )
        for n in pinned | ring_nodes:
            if n in before:
                snapped[n] = before[n]
        if an and an in before:
            snapped[an] = before[an]
        pos = snapped

    pos = _compact_petal_aspect(pos, max_aspect=4.0)

    if edges and pos:
        c0 = count_edge_crossings(pos, edges)
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        mx = (min(xs) + max(xs)) / 2
        my = (min(ys) + max(ys)) / 2
        for flipped in (
            {n: (2 * mx - x, y) for n, (x, y) in pos.items()},
            {n: (x, 2 * my - y) for n, (x, y) in pos.items()},
        ):
            c1 = count_edge_crossings(flipped, edges)
            if c1 < c0:
                pos, c0 = flipped, c1

    # Aspect compress / mirror can re-pierce hollow rings — final eject.
    # (Ring-side flip waits until petals are packed — cross-AN edges missing here.)
    if ring_groups:
        pos = _eject_intruders_from_rings(
            pos, ring_groups, g, params, edges, protected=ring_nodes
        )

    if pos:
        x0, y0, _, _ = bbox(pos)
        pos = {n: (x - x0, y - y0) for n, (x, y) in pos.items()}

    if pinned:
        bucket = state.meta.setdefault("_chain_pins", set())
        if not isinstance(bucket, set):
            bucket = set(bucket)
            state.meta["_chain_pins"] = bucket
        bucket.update(pinned)
    return pos


def _layout_column(
    nodes: list[str],
    roots: list[str],
    state: LayoutState,
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    return _layout_an_petals(nodes, roots, state, params)


def _pack_columns(
    columns: list[dict[str, tuple[float, float]]],
    params: LayoutParams,
    *,
    nrows: int,
    gap_x: float | None = None,
    gap_y: float | None = None,
) -> dict[str, tuple[float, float]]:
    cols = [c for c in columns if c]
    if not cols:
        return {}
    nrows = max(1, min(nrows, len(cols)))
    per_row = int(math.ceil(len(cols) / nrows))
    gx = params.an_gap * 1.8 if gap_x is None else gap_x
    gy = params.island_pad_y * 1.2 if gap_y is None else gap_y
    rows: list[list[int]] = []
    for r in range(nrows):
        chunk = list(range(r * per_row, min((r + 1) * per_row, len(cols))))
        if r % 2 == 1:
            chunk.reverse()
        rows.append(chunk)
    row_h = [0.0] * nrows
    col_w = [0.0] * per_row
    meta: list[tuple[int, int, int]] = []
    for r, chunk in enumerate(rows):
        for visual_k, idx in enumerate(chunk):
            x0, y0, x1, y1 = bbox(cols[idx])
            w, h = x1 - x0, y1 - y0
            row_h[r] = max(row_h[r], h)
            col_w[visual_k] = max(col_w[visual_k], w)
            meta.append((r, visual_k, idx))
    x_off = [0.0]
    for j in range(per_row):
        x_off.append(x_off[-1] + col_w[j] + (gx if j + 1 < per_row else 0.0))
    y_off = [0.0]
    for r in range(nrows):
        y_off.append(y_off[-1] + row_h[r] + (gy if r + 1 < nrows else 0.0))
    out: dict[str, tuple[float, float]] = {}
    for r, visual_k, idx in meta:
        c = cols[idx]
        x0, y0, x1, y1 = bbox(c)
        w = x1 - x0
        ox = x_off[visual_k] + max(0.0, (col_w[visual_k] - w) / 2)
        oy = y_off[r]
        for nid, (x, y) in c.items():
            out[nid] = (ox + (x - x0), oy + (y - y0))
    return out


def _best_square_pack(
    columns: list[dict[str, tuple[float, float]]],
    comp: list[str],
    state: LayoutState,
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    """Pack AN petals favoring UME-like aspect (~1.55) with soft crossing slack."""
    node_set = set(comp)
    edges = [(a, b) for a, b in state.links if a in node_set and b in node_set]
    n = len([c for c in columns if c])
    candidates = [1]
    if n >= 2:
        candidates.append(2)
    if n >= 4:
        candidates.append(3)
    target_aspect = 1.55
    scored: list[tuple[float, int, float, dict[str, tuple[float, float]]]] = []
    for nrows in candidates:
        packed = _pack_columns(columns, params, nrows=nrows)
        if not packed:
            continue
        cross = count_edge_crossings(packed, edges) if edges else 0
        x0, y0, x1, y1 = bbox(packed)
        w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        aspect = max(w / h, h / w)
        scored.append((aspect, cross, w * h, packed))
    if not scored:
        return {}
    min_cross = min(t[1] for t in scored)
    # Allow larger crossing trade to kill mega-wide strips (UME aspect ~1.6)
    slack = max(80, int(1.1 * min_cross))
    pool = [t for t in scored if t[1] <= min_cross + slack]
    if not pool:
        pool = scored

    def rank(t: tuple[float, int, float, dict]) -> tuple:
        aspect, cross, area, _ = t
        return (
            abs(math.log(aspect / target_aspect)) * 2.5
            + 0.35 * (cross / max(min_cross, 1)),
            area,
        )

    pool.sort(key=rank)
    return pool[0][3]


def _nearest_core(
    n: str, cores: list[str], adj: dict[str, set[str]], names: dict[str, str]
) -> str:
    if not cores:
        return ""
    return _nearest_an(n, cores, adj, names)


def _place_component_core_beam(
    comp: list[str],
    state: LayoutState,
    params: LayoutParams,
    *,
    ans: list[str],
    ens: list[str],
    cores: list[str],
    others: list[str],
) -> dict[str, tuple[float, float]]:
    """core_bar: pin cores on a horizontal beam; AN petals hang off (no core-in-column)."""
    g, names = state.adj, state.names
    ens_set = set(ens)
    an_set = set(ans)
    core_set = set(cores)
    # Prefer stable beam order: degree desc then name
    core_order = sorted(
        cores,
        key=lambda c: (-len(g.get(c, ())), names.get(c, c)),
    )
    gap = max(float(params.an_gap or 0) or 0.0, float(params.pitch) * 2.2, 360.0)
    petal_dy = max(float(params.side) * 3.2, gap * 1.35)

    # Multi-source ownership: BFS from ANs through access/other (cores are barriers)
    claimable = ens_set | set(others) | an_set
    owner: dict[str, str] = {}
    q: deque[str] = deque()
    for a in ans:
        owner[a] = a
        q.append(a)
    while q:
        u = q.popleft()
        for v in g.get(u, ()):
            if v in core_set or v in owner or v not in claimable:
                continue
            owner[v] = owner[u]
            q.append(v)
    for e in ens:
        if e in owner:
            continue
        owner[e] = _nearest_core(e, core_order, g, names) or (
            _nearest_an(e, ans, g, names) if ans else e
        )

    out: dict[str, tuple[float, float]] = {}
    for i, c in enumerate(core_order):
        out[c] = (i * gap, 0.0)

    # Core-owned stubs: local petal around each core (prefer upward / negative Y)
    for c in core_order:
        col = [c] + [
            n
            for n, o in owner.items()
            if o == c and n != c and n not in an_set and n not in core_set
        ]
        if len(col) <= 1:
            continue
        placed = _layout_an_petals(col, [], state, params)
        if c not in placed:
            continue
        cx0, cy0 = placed[c]
        tx, ty = out[c]
        for n, (x, y) in placed.items():
            if n == c:
                continue
            # Flip so stubs mostly sit above the beam
            dx, dy = x - cx0, y - cy0
            out[n] = (tx + dx, ty - abs(dy) - params.pitch * 0.15)

    # AN petals: layout without cores, attach under preferred core
    an_order = order_ans(ans, ens_set, g, names)
    by_core: dict[str, list[str]] = {c: [] for c in core_order}
    for a in an_order:
        nc = _nearest_core(a, core_order, g, names) or core_order[0]
        by_core.setdefault(nc, []).append(a)

    for c in core_order:
        group = by_core.get(c, [])
        cx, cy = out[c]
        n_g = len(group)
        for j, a in enumerate(group):
            col = [a]
            col.extend(
                [
                    e
                    for e in ens
                    if owner.get(e) == a
                ]
            )
            for o in others:
                if o in out or o in core_set:
                    continue
                nbs = [v for v in g.get(o, ()) if v in ens_set or v == a]
                if nbs and all(owner.get(v, v) == a or v == a for v in nbs):
                    col.append(o)
            placed = _layout_an_petals(col, [a], state, params)
            if not placed or a not in placed:
                out[a] = (
                    cx + (j - (n_g - 1) / 2) * gap * 0.55 if n_g else cx,
                    cy + petal_dy,
                )
                continue
            ax0, ay0 = placed[a]
            if n_g <= 1:
                ax = cx
            else:
                ax = cx + (j - (n_g - 1) / 2) * gap * 0.55
            ay = cy + petal_dy
            for n, (x, y) in placed.items():
                if n in core_set:
                    continue
                out[n] = (ax + (x - ax0), ay + (y - ay0))
            out[a] = (ax, ay)

    # Leftovers: near neighbor mean
    for n in comp:
        if n in out:
            continue
        nbs = [out[v] for v in g.get(n, ()) if v in out]
        if nbs:
            out[n] = (
                sum(p[0] for p in nbs) / len(nbs),
                sum(p[1] for p in nbs) / len(nbs) + params.pitch,
            )
        else:
            out[n] = (0.0, petal_dy * 2)

    return out


def _place_component(
    comp: list[str],
    state: LayoutState,
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    g, layers, names = state.adj, state.layers, state.names
    ans = sorted([n for n in comp if layers.get(n) == "agg"], key=lambda n: names[n])
    ens = [n for n in comp if layers.get(n) == "access"]
    cores = [n for n in comp if layers.get(n) == "core"]
    others = [n for n in comp if layers.get(n) == "other"]

    if len(ans) <= 1:
        out = _layout_an_petals(comp, ans, state, params)
        node_set = set(comp)
        edges = [(a, b) for a, b in state.links if a in node_set and b in node_set]
        if out and edges:
            ring_groups = _find_two_portal_ring_groups(ens, g, names, set(ans))
            if ring_groups:
                out = _orient_ring_sides(
                    out,
                    ring_groups,
                    edges,
                    pinned=_ring_nodes_from_groups(ring_groups),
                    max_interiors=1,
                    push=max(params.side * 0.25, 40.0),
                )
        return out

    # Dual+ cores → core_bar beam-first (do not stuff cores into AN columns).
    if len(cores) >= 2:
        out = _place_component_core_beam(
            comp,
            state,
            params,
            ans=ans,
            ens=ens,
            cores=cores,
            others=others,
        )
        node_set = set(comp)
        edges = [(a, b) for a, b in state.links if a in node_set and b in node_set]
        if out and edges:
            an_set = set(ans)
            ring_groups = _find_two_portal_ring_groups(ens, g, names, an_set)
            if ring_groups:
                before = dict(out)
                c_before = count_edge_crossings(out, edges)
                out = _orient_ring_sides(
                    out,
                    ring_groups,
                    edges,
                    pinned=_ring_nodes_from_groups(ring_groups),
                    max_interiors=1,
                    push=max(params.side * 0.25, 40.0),
                )
                out = _eject_intruders_from_rings(
                    out,
                    ring_groups,
                    g,
                    params,
                    edges,
                    protected=_ring_nodes_from_groups(ring_groups),
                )
                c_after = count_edge_crossings(out, edges)
                if c_after > max(c_before * 1.15, c_before + 20):
                    out = before
            # Keep cores on the beam after ring polish
            if len(cores) >= 2:
                ys = [out[c][1] for c in cores if c in out]
                if ys:
                    beam_y = sum(ys) / len(ys)
                    for c in cores:
                        if c in out:
                            out[c] = (out[c][0], beam_y)
            x0, y0, _, _ = bbox(out)
            out = {n: (x - x0, y - y0) for n, (x, y) in out.items()}
        return out

    ens_set = set(ens)
    an_order = order_ans(ans, ens_set, g, names)
    home = {e: _nearest_an(e, an_order, g, names) for e in ens}
    core_home = {c: _nearest_an(c, an_order, g, names) for c in cores}
    petals: list[dict[str, tuple[float, float]]] = []
    for a in an_order:
        col_nodes = [a]
        col_nodes.extend([e for e in ens if home.get(e) == a])
        col_nodes.extend([c for c in cores if core_home.get(c) == a])
        for o in others:
            nbs = [v for v in g.get(o, ()) if v in ens_set or v in an_order]
            if nbs and all(home.get(v, v) == a or v == a for v in nbs):
                col_nodes.append(o)
        placed = _layout_an_petals(col_nodes, [a], state, params)
        if placed:
            petals.append(placed)
    placed_ids = {n for p in petals for n in p}
    leftover = [n for n in comp if n not in placed_ids]
    if leftover:
        petals.append(_layout_an_petals(leftover, [], state, params))

    out = _best_square_pack(petals, comp, state, params)
    node_set = set(comp)
    edges = [(a, b) for a, b in state.links if a in node_set and b in node_set]
    if out and edges:
        c0 = count_edge_crossings(out, edges)
        xs = [p[0] for p in out.values()]
        mid = (min(xs) + max(xs)) / 2
        flipped = {n: (2 * mid - x, y) for n, (x, y) in out.items()}
        if count_edge_crossings(flipped, edges) < c0:
            out = flipped
        # Now cross-AN chords exist — flip triangle apexes (VOTI) off them.
        an_set = set(ans)
        ring_groups = _find_two_portal_ring_groups(ens, g, names, an_set)
        if ring_groups:
            before = dict(out)
            c_before = count_edge_crossings(out, edges)
            out = _orient_ring_sides(
                out,
                ring_groups,
                edges,
                pinned=_ring_nodes_from_groups(ring_groups),
                max_interiors=1,
                push=max(params.side * 0.25, 40.0),
            )
            out = _eject_intruders_from_rings(
                out,
                ring_groups,
                g,
                params,
                edges,
                protected=_ring_nodes_from_groups(ring_groups),
            )
            c_after = count_edge_crossings(out, edges)
            if c_after > max(c_before * 1.15, c_before + 20):
                out = before
        x0, y0, _, _ = bbox(out)
        out = {n: (x - x0, y - y0) for n, (x, y) in out.items()}
    return out


def _pack_islands(
    islands: list[dict[str, tuple[float, float]]],
    params: LayoutParams,
) -> dict[str, tuple[float, float]]:
    islands = sorted([c for c in islands if c], key=lambda p: -len(p))
    if not islands:
        return {}
    if len(islands) == 1:
        return dict(islands[0])
    n = len(islands)
    candidates = [1] + ([2] if n >= 2 else []) + ([3] if n >= 4 else [])
    target = 1.55
    best = None
    best_key = None
    for nrows in candidates:
        packed = _pack_columns(islands, params, nrows=nrows)
        if not packed:
            continue
        x0, y0, x1, y1 = bbox(packed)
        w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        aspect = max(w / h, h / w)
        key = (abs(math.log(aspect / target)), w * h)
        if best_key is None or key < best_key:
            best_key = key
            best = packed
    return best or {}


def build_sugiyama_layout(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    params = params or LayoutParams()
    st = state.copy()
    ids = list(st.names.keys())
    active = {i for i in ids if st.layers.get(i) in ("core", "agg", "access")}
    others = sorted(
        [i for i in ids if st.layers.get(i) == "other"], key=lambda n: st.names[n]
    )
    comps = connected_components(active, st.adj)
    islands = []
    for c in comps:
        placed = _place_component(c, st, params)
        if placed:
            islands.append(placed)
    pos = _pack_islands(islands, params) if islands else {}

    if others:
        if pos:
            x0, y0, x1, y1 = bbox(pos)
            fx, fy = x1 + params.island_pad_x * 0.35, y0
        else:
            fx, fy = 0.0, 0.0
        for i, n in enumerate(others):
            neigh = [v for v in st.adj.get(n, ()) if v in pos]
            if neigh:
                mx = sum(pos[v][0] for v in neigh) / len(neigh)
                my = sum(pos[v][1] for v in neigh) / len(neigh)
                pos[n] = (mx + params.pitch, my)
            else:
                pos[n] = (fx + (i % 4) * params.pitch, fy + (i // 4) * params.side)

    for n in ids:
        if n not in pos:
            pos[n] = (0.0, 0.0)

    st.positions = pos
    chain_pins = st.meta.pop("_chain_pins", set()) or set()
    if not isinstance(chain_pins, set):
        chain_pins = set(chain_pins)
    hard = {n for n in pos if st.layers.get(n) in ("agg", "core")}
    st.pinned = hard | chain_pins
    st.spine = set(chain_pins)
    st.last_moved = set(pos.keys())
    st.meta["components"] = len(comps)
    st.meta["rings_mode"] = "ume_petals"
    st.meta["chain_unit_nodes"] = len(chain_pins)
    return OpResult(
        state=st,
        moved=set(pos.keys()),
        op="build_sugiyama_layout",
        params={
            "components": len(comps),
            "pitch": params.pitch,
            "side": params.side,
            "mode": "ume_petals",
            "chain_unit_nodes": len(chain_pins),
        },
        note=f"UME petal rects comps={len(comps)} chains={len(chain_pins)}",
    )
