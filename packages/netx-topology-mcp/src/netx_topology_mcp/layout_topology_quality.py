"""Mid-tier topology geometry QA: chain cohesion + min-ring integrity.

- chain（直链成一体）: deg≤2 corridors should lay nearly collinear as one unit.
- rings（最小环不被穿）: short chordless cycles should not be crossed by foreign edges.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

from netx_topology_mcp.layout_metrics import segments_properly_intersect

# Turn from 180° beyond this → kink (degrees).
CHAIN_KINK_DEG = 40.0
# Min nodes on a corridor to score as a chain (hub + ≥2 hops, or ≥3 deg≤2).
CHAIN_MIN_NODES = 3
# Chordless cycles longer than this are ignored (metro min-rings are short).
RING_MAX_LEN = 8


def _adj(links: list[tuple[str, str]]) -> dict[str, set[str]]:
    g: dict[str, set[str]] = defaultdict(set)
    for a, b in links:
        if a == b:
            continue
        g[a].add(b)
        g[b].add(a)
    return g


def _edge_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _walk_path(start: str, nodes: set[str], adj: dict[str, set[str]]) -> list[str]:
    ordered = [start]
    seen = {start}
    prev: str | None = None
    cur = start
    while True:
        nbs = [v for v in adj.get(cur, ()) if v in nodes and v not in seen]
        if not nbs:
            break
        # Prefer continuing along the unique unused neighbor.
        nxt = nbs[0] if len(nbs) == 1 else sorted(nbs)[0]
        if prev in nbs and len(nbs) > 1:
            nbs = [v for v in nbs if v != prev]
            nxt = nbs[0]
        prev, cur = cur, nxt
        ordered.append(cur)
        seen.add(cur)
    return ordered


def extract_chain_paths(adj: dict[str, set[str]]) -> list[list[str]]:
    """Maximal deg≤2 corridors, optionally extended by one hub endpoint each side."""
    if not adj:
        return []
    deg = {u: len(vs) for u, vs in adj.items()}
    low = {u for u, d in deg.items() if 0 < d <= 2}
    if not low:
        return []

    seen: set[str] = set()
    chains: list[list[str]] = []
    for seed in sorted(low):
        if seed in seen:
            continue
        q = deque([seed])
        seen.add(seed)
        comp: set[str] = set()
        while q:
            u = q.popleft()
            comp.add(u)
            for v in adj.get(u, ()):
                if v in low and v not in seen:
                    seen.add(v)
                    q.append(v)

        # Cycle of only low-deg nodes → not a chain corridor.
        sub_deg = {u: sum(1 for v in adj.get(u, ()) if v in comp) for u in comp}
        ends = [u for u in comp if sub_deg[u] <= 1]
        if len(ends) == 0 and len(comp) >= 3:
            continue

        start = sorted(ends)[0] if ends else sorted(comp)[0]
        ordered = _walk_path(start, comp, adj)
        if len(ordered) < len(comp):
            # branched low-deg blob: take longest path approximation via ends
            best = ordered
            for e in ends:
                p = _walk_path(e, comp, adj)
                if len(p) > len(best):
                    best = p
            ordered = best

        # Extend with unique high-deg portals (hub ends).
        def _portal(end: str) -> str | None:
            outs = [v for v in adj.get(end, ()) if v not in comp]
            return outs[0] if len(outs) == 1 else None

        if ordered:
            left = _portal(ordered[0])
            right = _portal(ordered[-1])
            if left:
                ordered = [left] + ordered
            if right and right != left:
                ordered = ordered + [right]

        if len(ordered) >= CHAIN_MIN_NODES:
            chains.append(ordered)
    return chains


def _chain_straightness(path: list[str], pos: dict[str, tuple[float, float]]) -> float:
    pts = [pos[n] for n in path if n in pos]
    if len(pts) < 2:
        return 1.0
    chord = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
    plen = 0.0
    for i in range(len(pts) - 1):
        plen += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
    if plen < 1e-9:
        return 1.0
    return max(0.0, min(1.0, chord / plen))


def _chain_kinks(path: list[str], pos: dict[str, tuple[float, float]]) -> int:
    pts = [pos[n] for n in path if n in pos]
    kinks = 0
    for i in range(1, len(pts) - 1):
        ax, ay = pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]
        bx, by = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        la, lb = math.hypot(ax, ay), math.hypot(bx, by)
        if la < 1e-9 or lb < 1e-9:
            continue
        cos = max(-1.0, min(1.0, (ax * bx + ay * by) / (la * lb)))
        turn = abs(math.degrees(math.acos(cos)) - 0.0)  # 0 = straight continuation
        # acos of dot gives angle between directions; 0° = collinear same way.
        # kink if direction changes more than CHAIN_KINK_DEG from straight (0°).
        if turn > CHAIN_KINK_DEG:
            kinks += 1
    return kinks


def compute_chain_cohesion(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
) -> dict[str, Any]:
    """直链成一体：corridor straightness / kink rate → score∈[0,1]."""
    adj = _adj(links)
    chains = extract_chain_paths(adj)
    if not chains:
        return {
            "chain_count": 0,
            "chain_nodes": 0,
            "straightness_p50": None,
            "kink_count": 0,
            "kink_frac": 0.0,
            "score": 1.0,
            "tip": "无 deg≤2 走廊可评；不扣分。",
        }

    straight: list[float] = []
    weights: list[float] = []
    kink_total = 0
    kink_slots = 0
    node_union: set[str] = set()
    for path in chains:
        node_union.update(path)
        s = _chain_straightness(path, pos)
        straight.append(s)
        weights.append(max(1, len(path) - 1))
        k = _chain_kinks(path, pos)
        kink_total += k
        kink_slots += max(0, len([n for n in path if n in pos]) - 2)

    # weighted mean straightness
    wsum = sum(weights) or 1.0
    mean_s = sum(s * w for s, w in zip(straight, weights)) / wsum
    sorted_s = sorted(straight)
    p50 = sorted_s[len(sorted_s) // 2]
    kink_frac = (kink_total / kink_slots) if kink_slots else 0.0
    # Mid-tier score: mostly straightness, penalize kinks.
    score = max(0.0, min(1.0, 0.75 * mean_s + 0.25 * (1.0 - kink_frac)))

    return {
        "chain_count": len(chains),
        "chain_nodes": len(node_union),
        "straightness_p50": round(p50, 4),
        "straightness_mean": round(mean_s, 4),
        "kink_count": kink_total,
        "kink_frac": round(kink_frac, 4),
        "score": round(score, 4),
        "tip": (
            "deg≤2 走廊应近似共线成一体（chord/path≈1、少折角）；"
            f"折角阈值 {CHAIN_KINK_DEG:.0f}°。"
        ),
    }


def _is_chordless(cycle: list[str], adj: dict[str, set[str]]) -> bool:
    n = len(cycle)
    idx = {u: i for i, u in enumerate(cycle)}
    for i, u in enumerate(cycle):
        for v in adj.get(u, ()):
            j = idx.get(v)
            if j is None:
                continue
            dist = min((j - i) % n, (i - j) % n)
            if dist > 1:
                return False
    return True


def find_short_chordless_cycles(
    adj: dict[str, set[str]],
    *,
    max_len: int = RING_MAX_LEN,
    max_cycles: int = 120,
) -> list[list[str]]:
    """Enumerate chordless cycles of length 3..max_len (canonicalized).

    Caps at ``max_cycles`` so giant star canvases stay interactive.
    """
    if not adj:
        return []
    cycles: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    nodes = sorted(adj.keys())
    for start in nodes:
        if len(cycles) >= max_cycles:
            break
        # path grows; first neighbor ordered to cut duplicates
        for first in sorted(adj.get(start, ())):
            if first <= start:
                continue
            if len(cycles) >= max_cycles:
                break
            stack: list[tuple[str, list[str]]] = [(first, [start, first])]
            while stack:
                if len(cycles) >= max_cycles:
                    break
                u, path = stack.pop()
                if len(path) > max_len:
                    continue
                for v in adj.get(u, ()):
                    if v == start and len(path) >= 3:
                        key = frozenset(path)
                        if key in seen:
                            continue
                        if _is_chordless(path, adj):
                            seen.add(key)
                            cycles.append(list(path))
                            if len(cycles) >= max_cycles:
                                break
                        continue
                    if v in path or v < start:
                        continue
                    if len(path) + 1 > max_len:
                        continue
                    stack.append((v, path + [v]))
    return cycles


def compute_ring_integrity(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
) -> dict[str, Any]:
    """最小环不被穿：foreign edges crossing a short cycle's boundary."""
    adj = _adj(links)
    cycles = find_short_chordless_cycles(adj, max_len=RING_MAX_LEN)
    if not cycles:
        return {
            "ring_count": 0,
            "rings_pierced": 0,
            "pierce_crossings": 0,
            "score": 1.0,
            "tip": "无长度≤8 的弦无关短环；不扣分。",
        }

    pierced = 0
    pierce_crossings = 0
    for cyc in cycles:
        cyc_edges = {
            _edge_key(cyc[i], cyc[(i + 1) % len(cyc)]) for i in range(len(cyc))
        }
        hit = False
        for a, b in links:
            ek = _edge_key(a, b)
            if ek in cyc_edges:
                continue
            if a not in pos or b not in pos:
                continue
            pa, pb = pos[a], pos[b]
            for u, v in cyc_edges:
                if u not in pos or v not in pos:
                    continue
                if segments_properly_intersect(pa, pb, pos[u], pos[v]):
                    pierce_crossings += 1
                    hit = True
                    break
            # continue scanning to count pierce_crossings
        if hit:
            pierced += 1

    n = len(cycles)
    # Soften: many pierce events on one ring still one pierced ring;
    # also decay by pierce density.
    pierce_frac = pierced / n
    dens = pierce_crossings / max(1, n)
    score = max(0.0, min(1.0, 1.0 - 0.7 * pierce_frac - 0.3 * min(1.0, dens / 3.0)))

    return {
        "ring_count": n,
        "rings_pierced": pierced,
        "pierce_crossings": pierce_crossings,
        "score": round(score, 4),
        "tip": (
            f"弦无关短环（3–{RING_MAX_LEN}）边界不应被环外边穿越；"
            "rings_pierced / pierce_crossings 越低越好。"
        ),
    }


def compute_topology_quality(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
) -> dict[str, Any]:
    chain = compute_chain_cohesion(pos, links)
    rings = compute_ring_integrity(pos, links)
    return {"chains": chain, "rings": rings}
