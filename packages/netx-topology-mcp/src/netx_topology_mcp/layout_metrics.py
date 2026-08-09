"""Canvas layout metrics: edge crossings, spacing, label/icon overlap.

Coordinates are API icon-center (x, y), matching TopologyPage neApiPosition.
Visual constants mirror web/src (TOPO_ICON=25, caption under glyph).
"""

from __future__ import annotations

import math
from typing import Any

# --- canvas visual budget (zoom=1, full LOD) ---
ICON_SIZE = 25.0
CAPTION_GAP = 1.0
CAPTION_NAME_H = 10.0  # ~8px font * 1.15 line-height
CAPTION_META_H = 8.0
CHAR_W = 4.8  # empirical for 8px UI font, Latin/digit NE names
# Comfortable center-to-center (infinite canvas — prefer airy over dense)
MIN_CENTER_DX = 160.0
MIN_CENTER_DY = 100.0
REC_CENTER_DX = 200.0
REC_CENTER_DY = 170.0
MIN_CENTER_DIST = 150.0  # euclidean floor between icon centers
# Node-center to non-incident edge segment (icon + pad); below → edge_clearance hit.
EDGE_CLEARANCE_THR = 40.0
EDGE_CLEARANCE_ENDPOINT_T = 0.05  # t near 0/1 ⇒ nn-like, skip (avoid double-count)
EDGE_CLEARANCE_SKIP_NE = 200_000  # n*e above this: skip full scan
# Edge axis: H/V metro look; within tol of axis counts as orthogonal.
EDGE_AXIS_TOL_DEG = 8.0
EDGE_AXIS_TOL_PX = 4.0
EDGE_AXIS_CREDIT_H = 1.0
EDGE_AXIS_CREDIT_V = 0.75  # horizontal preferred over vertical


def estimate_label_width(name: str) -> float:
    n = max(1, len((name or "").strip()))
    return max(40.0, n * CHAR_W)


def node_footprint(
    name: str, *, with_meta: bool = False
) -> tuple[float, float, float, float]:
    """AABB relative to icon center: (min_x, min_y, max_x, max_y)."""
    half_icon = ICON_SIZE / 2.0
    lw = estimate_label_width(name) / 2.0
    half_w = max(half_icon, lw)
    top = -half_icon
    bottom = half_icon + CAPTION_GAP + CAPTION_NAME_H
    if with_meta:
        bottom += CAPTION_META_H
    return (-half_w, top, half_w, bottom)


def _orient(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def segments_properly_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """True iff open segments ab and cd cross (shared endpoint ⇒ False)."""
    if a == c or a == d or b == c or b == d:
        return False
    o1, o2 = _orient(a, b, c), _orient(a, b, d)
    o3, o4 = _orient(c, d, a), _orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def point_segment_dist(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    """Distance from point ``p`` to segment ``ab`` and clamped projection ``t``∈[0,1]."""
    ax, ay = a
    bx, by = b
    px, py = p
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    L2 = vx * vx + vy * vy
    if L2 < 1e-12:
        return math.hypot(wx, wy), 0.0
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy)), t


def compute_edge_clearance(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    *,
    names: dict[str, str] | None = None,
    thr: float = EDGE_CLEARANCE_THR,
    endpoint_t: float = EDGE_CLEARANCE_ENDPOINT_T,
    top_n: int = 5,
) -> dict[str, Any]:
    """Score nodes sitting too close to non-incident edge segments.

    A hit = node N within ``thr`` of segment AB where N∉{A,B} and the
    projection is not near an endpoint (endpoint nearness ≈ neighbour spacing).
    """
    ids = [n for n in pos if n in pos]
    n_nodes = len(ids)
    n_links = len(links)
    tip_ok = (
        "非关联边不得擦过网元图标；d≥thr（默认 40）。"
        "端点近距豁免（与 nn 分工）。阶段2：clear_edge_hits。"
    )
    if n_nodes == 0 or n_links == 0:
        return {
            "edge_clearance_hits": 0,
            "nodes_hit": 0,
            "min_clearance_p50": None,
            "edge_clearance_score": 1.0,
            "top_edge_hits": [],
            "edge_clearance_tip": tip_ok,
            "edge_clearance_skipped": False,
        }
    if n_nodes * n_links > EDGE_CLEARANCE_SKIP_NE:
        return {
            "edge_clearance_hits": None,
            "nodes_hit": None,
            "min_clearance_p50": None,
            "edge_clearance_score": 1.0,
            "top_edge_hits": [],
            "edge_clearance_tip": (
                f"skipped: n*e={n_nodes * n_links}>{EDGE_CLEARANCE_SKIP_NE}"
            ),
            "edge_clearance_skipped": True,
        }

    name_map = names or {}
    hits: list[dict[str, Any]] = []
    clearances: list[float] = []
    nodes_with_hit: set[str] = set()
    thr_f = float(thr)
    et = float(endpoint_t)

    for nid in ids:
        p = pos[nid]
        best_d = float("inf")
        for a, b in links:
            if nid in (a, b) or a not in pos or b not in pos:
                continue
            d, t = point_segment_dist(p, pos[a], pos[b])
            if t <= et or t >= 1.0 - et:
                continue
            if d < best_d:
                best_d = d
            if d < thr_f:
                hits.append(
                    {
                        "fabric_node_id": nid,
                        "name": name_map.get(nid, nid),
                        "a_node_id": a,
                        "b_node_id": b,
                        "a_name": name_map.get(a, a),
                        "b_name": name_map.get(b, b),
                        "dist": round(d, 2),
                        "t": round(t, 4),
                    }
                )
                nodes_with_hit.add(nid)
        if math.isfinite(best_d):
            clearances.append(best_d)

    hits.sort(key=lambda h: float(h["dist"]))
    # Worst edge per node (sorted by dist)
    per_node: list[dict[str, Any]] = []
    seen_n: set[str] = set()
    for h in hits:
        nid = str(h["fabric_node_id"])
        if nid in seen_n:
            continue
        seen_n.add(nid)
        per_node.append(h)

    hits_n = len(hits)
    # Score by unique nodes hit / n (plan: ~0.05 warn, 0.2 → 0)
    hit_frac = len(nodes_with_hit) / max(n_nodes, 1)
    if hit_frac <= 0.0:
        score = 1.0
    elif hit_frac >= 0.2:
        score = 0.0
    else:
        score = 1.0 - hit_frac / 0.2

    def _pct(vals: list[float], p: float) -> float | None:
        if not vals:
            return None
        vs = sorted(vals)
        idx = min(len(vs) - 1, max(0, int(round((len(vs) - 1) * p))))
        return round(vs[idx], 1)

    return {
        "edge_clearance_hits": hits_n,
        "nodes_hit": len(nodes_with_hit),
        "min_clearance_p50": _pct(clearances, 0.5),
        "edge_clearance_score": round(score, 4),
        "top_edge_hits": per_node[:top_n],
        "hit_nodes": per_node,
        "edge_clearance_tip": tip_ok,
        "edge_clearance_skipped": False,
        "edge_clearance_thr": thr_f,
    }


def compute_edge_axis(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    *,
    names: dict[str, str] | None = None,
    tol_deg: float = EDGE_AXIS_TOL_DEG,
    tol_px: float = EDGE_AXIS_TOL_PX,
    top_n: int = 5,
) -> dict[str, Any]:
    """Score edges for axis-alignment; prefer horizontal over vertical.

    Classification (first match):
    - horizontal: |dy|≤tol_px or angle-to-H ≤ tol_deg
    - vertical:   |dx|≤tol_px or angle-to-V ≤ tol_deg
    - diagonal:   else

    Per-edge credit: H=1.0, V=0.75, diagonal=0. Score = mean credit.
    """
    name_map = names or {}
    tip = (
        "边宜水平/垂直（地铁风）；水平优先于垂直。"
        f"容差≈{tol_deg:g}°或{tol_px:g}px。斜边拉低总分。"
    )
    if not links:
        return {
            "edge_axis_score": 1.0,
            "axis_frac": 1.0,
            "horiz_frac": 1.0,
            "vert_frac": 0.0,
            "diag_frac": 0.0,
            "horiz_n": 0,
            "vert_n": 0,
            "diag_n": 0,
            "top_skew_edges": [],
            "edge_axis_tip": tip,
            "edge_axis_tol_deg": float(tol_deg),
        }

    tol_rad = math.radians(max(0.1, float(tol_deg)))
    tol_p = max(0.0, float(tol_px))
    h_n = v_n = d_n = 0
    credits: list[float] = []
    skew: list[dict[str, Any]] = []

    for a, b in links:
        if a not in pos or b not in pos:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            # Degenerate: count as aligned (no geometry to fix)
            h_n += 1
            credits.append(EDGE_AXIS_CREDIT_H)
            continue
        adx, ady = abs(dx), abs(dy)
        # Angle from nearest axis in [0, 45°]
        ang_h = math.atan2(ady, adx)  # 0=H, π/2=V
        ang_v = abs(math.pi / 2 - ang_h)
        near_h = ady <= tol_p or ang_h <= tol_rad
        near_v = adx <= tol_p or ang_v <= tol_rad
        if near_h and (not near_v or ang_h <= ang_v):
            kind = "H"
            h_n += 1
            credits.append(EDGE_AXIS_CREDIT_H)
            skew_deg = math.degrees(ang_h)
        elif near_v:
            kind = "V"
            v_n += 1
            credits.append(EDGE_AXIS_CREDIT_V)
            skew_deg = math.degrees(ang_v)
        else:
            kind = "D"
            d_n += 1
            credits.append(0.0)
            skew_deg = math.degrees(min(ang_h, ang_v))
            skew.append(
                {
                    "a_node_id": a,
                    "b_node_id": b,
                    "a_name": name_map.get(a, a),
                    "b_name": name_map.get(b, b),
                    "label": f"{name_map.get(a, a)}–{name_map.get(b, b)}",
                    "angle_from_axis_deg": round(skew_deg, 1),
                    "len": round(length, 1),
                    "kind": kind,
                }
            )

    n = max(len(credits), 1)
    score = sum(credits) / n
    axis_n = h_n + v_n
    total_n = h_n + v_n + d_n
    skew.sort(key=lambda r: -float(r["angle_from_axis_deg"]))
    return {
        "edge_axis_score": round(score, 4),
        "axis_frac": round(axis_n / max(total_n, 1), 4),
        "horiz_frac": round(h_n / max(total_n, 1), 4),
        "vert_frac": round(v_n / max(total_n, 1), 4),
        "diag_frac": round(d_n / max(total_n, 1), 4),
        "horiz_n": h_n,
        "vert_n": v_n,
        "diag_n": d_n,
        "top_skew_edges": skew[:top_n],
        "edge_axis_tip": tip,
        "edge_axis_tol_deg": float(tol_deg),
        "edge_axis_tol_px": float(tol_px),
    }


def collapse_links(edges: list[dict[str, Any]]) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        a = str(e.get("a_node_id") or e.get("a") or "").strip()
        b = str(e.get("b_node_id") or e.get("b") or "").strip()
        if not a or not b or a == b:
            continue
        key = (a, b) if a < b else (b, a)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _seg_bbox(
    p: tuple[float, float], q: tuple[float, float]
) -> tuple[float, float, float, float]:
    return (
        min(p[0], q[0]),
        min(p[1], q[1]),
        max(p[0], q[0]),
        max(p[1], q[1]),
    )


def _bbox_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def count_edge_crossings(
    pos: dict[str, tuple[float, float]], links: list[tuple[str, str]]
) -> int:
    """Full O(E²) crossing count with bbox prune (skips obvious non-hits)."""
    segs: list[
        tuple[tuple[float, float], tuple[float, float], tuple[float, float, float, float]]
    ] = []
    for u, v in links:
        if u in pos and v in pos:
            p, q = pos[u], pos[v]
            segs.append((p, q, _seg_bbox(p, q)))
    n = 0
    for i in range(len(segs)):
        p1, p2, b1 = segs[i]
        for j in range(i + 1, len(segs)):
            p3, p4, b2 = segs[j]
            if not _bbox_overlap(b1, b2):
                continue
            if segments_properly_intersect(p1, p2, p3, p4):
                n += 1
    return n


def crossings_involving_node(
    node: str,
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
) -> int:
    """Crossings on edges incident to ``node`` vs the rest of the graph.

    Used for O(deg·E) trial scoring when one node moves.
    """
    nbs = adj.get(node, ())
    if not nbs or node not in pos:
        return 0
    incident = [(node, v) if node < v else (v, node) for v in nbs if v in pos]
    if not incident:
        return 0
    inc_set = set(incident)
    n = 0
    p0 = pos[node]
    for u, v in incident:
        other = v if u == node else u
        p1 = pos[other]
        bb = _seg_bbox(p0, p1)
        for a, b in links:
            if (a, b) in inc_set or node in (a, b) or other in (a, b):
                continue
            if a not in pos or b not in pos:
                continue
            pa, pb = pos[a], pos[b]
            if not _bbox_overlap(bb, _seg_bbox(pa, pb)):
                continue
            if segments_properly_intersect(p0, p1, pa, pb):
                n += 1
    return n


def crossings_after_node_move(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
    node: str,
    new_xy: tuple[float, float],
    *,
    current_total: int,
    local_before: int | None = None,
) -> int:
    """Global crossing count after moving ``node`` — incremental, exact."""
    if node not in pos:
        return int(current_total)
    before = (
        int(local_before)
        if local_before is not None
        else crossings_involving_node(node, pos, links, adj)
    )
    trial = dict(pos)
    trial[node] = new_xy
    after = crossings_involving_node(node, trial, links, adj)
    return int(current_total) - before + after


def crossing_participation(
    pos: dict[str, tuple[float, float]], links: list[tuple[str, str]]
) -> tuple[int, dict[str, int]]:
    """Count crossings + per-node hit counts (endpoint of a crossing edge)."""
    n, node_hit, _edge_hit = crossing_participation_full(pos, links)
    return n, node_hit


def crossing_participation_full(
    pos: dict[str, tuple[float, float]], links: list[tuple[str, str]]
) -> tuple[int, dict[str, int], dict[tuple[str, str], int]]:
    """Count crossings + per-node and per-edge hit counts.

    Edge keys are undirected ``(min_id, max_id)``. An edge's hit count is how
    many proper crossings that segment participates in.
    """
    node_hit: dict[str, int] = {}
    edge_hit: dict[tuple[str, str], int] = {}
    n = 0
    for i, (a, b) in enumerate(links):
        if a not in pos or b not in pos:
            continue
        p1, p2 = pos[a], pos[b]
        bb1 = _seg_bbox(p1, p2)
        e1 = (a, b) if a < b else (b, a)
        for c, d in links[i + 1 :]:
            if len({a, b, c, d}) < 4 or c not in pos or d not in pos:
                continue
            p3, p4 = pos[c], pos[d]
            if not _bbox_overlap(bb1, _seg_bbox(p3, p4)):
                continue
            if segments_properly_intersect(p1, p2, p3, p4):
                n += 1
                e2 = (c, d) if c < d else (d, c)
                for nid in (a, b, c, d):
                    node_hit[nid] = node_hit.get(nid, 0) + 1
                edge_hit[e1] = edge_hit.get(e1, 0) + 1
                edge_hit[e2] = edge_hit.get(e2, 0) + 1
    return n, node_hit, edge_hit


def top_crossing_nodes(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    *,
    names: dict[str, str] | None = None,
    adj: dict[str, set[str]] | None = None,
    top_n: int = 5,
    participation: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Top-N nodes by crossing participation — primary surgical untangle targets."""
    hit = participation
    if hit is None:
        _cross, hit = crossing_participation(pos, links)
    if not hit:
        return []
    names = names or {}
    ranked = sorted(
        hit.items(),
        key=lambda kv: (-kv[1], names.get(kv[0], kv[0]), kv[0]),
    )
    out: list[dict[str, Any]] = []
    for nid, hits in ranked[: max(0, int(top_n))]:
        if adj is not None:
            deg = len(adj.get(nid, ()))
        else:
            deg = sum(1 for u, v in links if nid in (u, v) and u in pos and v in pos)
        xy = pos.get(nid, (0.0, 0.0))
        out.append(
            {
                "fabric_node_id": nid,
                "name": names.get(nid, nid),
                "crossing_hits": int(hits),
                "degree": int(deg),
                "x": round(float(xy[0]), 1),
                "y": round(float(xy[1]), 1),
            }
        )
    return out


def top_crossing_edges(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    *,
    names: dict[str, str] | None = None,
    top_n: int = 5,
    edge_participation: dict[tuple[str, str], int] | None = None,
) -> list[dict[str, Any]]:
    """Top-N undirected edges by number of crossings they participate in."""
    edge_hit = edge_participation
    if edge_hit is None:
        _n, _nodes, edge_hit = crossing_participation_full(pos, links)
    if not edge_hit:
        return []
    names = names or {}
    ranked = sorted(
        edge_hit.items(),
        key=lambda kv: (
            -kv[1],
            names.get(kv[0][0], kv[0][0]),
            names.get(kv[0][1], kv[0][1]),
            kv[0],
        ),
    )
    out: list[dict[str, Any]] = []
    for (a, b), hits in ranked[: max(0, int(top_n))]:
        ax, ay = pos.get(a, (0.0, 0.0))
        bx, by = pos.get(b, (0.0, 0.0))
        a_name = names.get(a, a)
        b_name = names.get(b, b)
        out.append(
            {
                "a_node_id": a,
                "b_node_id": b,
                "a_name": a_name,
                "b_name": b_name,
                "label": f"{a_name}<->{b_name}",
                "crossing_hits": int(hits),
                "ax": round(float(ax), 1),
                "ay": round(float(ay), 1),
                "bx": round(float(bx), 1),
                "by": round(float(by), 1),
                "mid_x": round((float(ax) + float(bx)) / 2.0, 1),
                "mid_y": round((float(ay) + float(by)) / 2.0, 1),
            }
        )
    return out


def _aabb_overlap(
    ax0: float, ay0: float, ax1: float, ay1: float, bx0: float, by0: float, bx1: float, by1: float
) -> bool:
    return ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0


def analyze_positions(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    with_meta: bool = False,
) -> dict[str, Any]:
    """Compute crossings + spacing/overlap stats for a view graph."""
    pos: dict[str, tuple[float, float]] = {}
    names: dict[str, str] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        fid = str(n.get("fabric_node_id") or n.get("id") or "").strip()
        if not fid:
            continue
        try:
            x = float(n.get("x") if n.get("x") is not None else 0.0)
            y = float(n.get("y") if n.get("y") is not None else 0.0)
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        pos[fid] = (x, y)
        names[fid] = str(n.get("name") or n.get("label") or fid)

    links = collapse_links(edges)
    crossings, hit, edge_hit = crossing_participation_full(pos, links)
    n_nodes = len(pos)
    n_links = len(links)
    adj: dict[str, set[str]] = {nid: set() for nid in pos}
    for u, v in links:
        if u in adj and v in adj:
            adj[u].add(v)
            adj[v].add(u)
    top_x = top_crossing_nodes(
        pos, links, names=names, adj=adj, top_n=5, participation=hit
    )
    top_e = top_crossing_edges(
        pos, links, names=names, top_n=5, edge_participation=edge_hit
    )

    # nearest-neighbor distances
    ids = list(pos.keys())
    nn: list[float] = []
    close_pairs = 0
    for i, a in enumerate(ids):
        ax, ay = pos[a]
        best = None
        for b in ids:
            if a == b:
                continue
            bx, by = pos[b]
            d = math.hypot(ax - bx, ay - by)
            if best is None or d < best:
                best = d
            if d < MIN_CENTER_DIST:
                close_pairs += 1
        if best is not None:
            nn.append(best)
    # close_pairs counted twice
    close_pairs //= 2

    overlap_pairs = 0
    label_overlap_pairs = 0
    for i, a in enumerate(ids):
        ax, ay = pos[a]
        fa = node_footprint(names[a], with_meta=with_meta)
        for b in ids[i + 1 :]:
            bx, by = pos[b]
            fb = node_footprint(names[b], with_meta=with_meta)
            if _aabb_overlap(
                ax + fa[0],
                ay + fa[1],
                ax + fa[2],
                ay + fa[3],
                bx + fb[0],
                by + fb[1],
                bx + fb[2],
                by + fb[3],
            ):
                overlap_pairs += 1
                # stricter: only caption bands (below icon)
                a_cap = (ax + fa[0], ay + ICON_SIZE / 2 + CAPTION_GAP, ax + fa[2], ay + fa[3])
                b_cap = (bx + fb[0], by + ICON_SIZE / 2 + CAPTION_GAP, bx + fb[2], by + fb[3])
                if _aabb_overlap(*a_cap, *b_cap):
                    label_overlap_pairs += 1

    xs = [p[0] for p in pos.values()] or [0.0]
    ys = [p[1] for p in pos.values()] or [0.0]
    nn_sorted = sorted(nn)
    def pct(p: float) -> float | None:
        if not nn_sorted:
            return None
        idx = min(len(nn_sorted) - 1, max(0, int(round((len(nn_sorted) - 1) * p))))
        return round(nn_sorted[idx], 1)

    crossings_per_link = round(crossings / n_links, 4) if n_links else 0.0
    crossings_per_node = round(crossings / n_nodes, 4) if n_nodes else 0.0
    bw = max(xs) - min(xs)
    bh = max(ys) - min(ys)
    area = max(bw * bh, 1.0)
    # Ideal tile per NE ≈ recommended center pitch; util ∈ (0, ~1+].
    space_utilization = round(n_nodes * REC_CENTER_DX * REC_CENTER_DY / area, 4)

    return {
        "node_count": n_nodes,
        "link_count": n_links,
        "edge_crossings": crossings,
        "crossings_per_link": crossings_per_link,
        "crossings_per_node": crossings_per_node,
        "top_crossing_nodes": top_x,
        "top_crossing_edges": top_e,
        "bbox": [round(bw, 1), round(bh, 1)],
        "nn_min": pct(0.0),
        "nn_p50": pct(0.5),
        "nn_p10": pct(0.1),
        "pairs_closer_than_min_dist": close_pairs,
        "footprint_overlap_pairs": overlap_pairs,
        "label_overlap_pairs": label_overlap_pairs,
        "space_utilization": space_utilization,
        "spacing_guide": {
            "icon_px": ICON_SIZE,
            "min_center_dx": MIN_CENTER_DX,
            "min_center_dy": MIN_CENTER_DY,
            "recommended_center_dx": REC_CENTER_DX,
            "recommended_center_dy": REC_CENTER_DY,
            "min_center_dist": MIN_CENTER_DIST,
            "note": (
                "API x/y = icon center. Caption sits under icon (8px, nowrap). "
                "Layout should keep centers ≥ recommended dx/dy so icons+names do not collide. "
                "space_utilization = n*rec_dx*rec_dy / bbox_area (higher is denser; avoid empty zoom)."
            ),
        },
        "crossing_definition": (
            "Undirected NE-NE links (port edges collapsed). "
            "A crossing = two link segments properly intersect in the plane; "
            "shared endpoints do not count."
        ),
    }


def grade_layout(
    metrics: dict[str, Any],
    *,
    ume_reference: bool = False,
) -> dict[str, Any]:
    """Pass/warn/fail vs spacing + crossing density.

    Default: hard zero footprint/label overlap. Set ume_reference=True to tolerate
    tiny residue when scoring engineer UME canvases.
    """
    n = int(metrics.get("node_count") or 0)
    cross = int(metrics.get("edge_crossings") or 0)
    cpl = float(metrics.get("crossings_per_link") or 0.0)
    overlaps = int(metrics.get("footprint_overlap_pairs") or 0)
    label_overlaps = int(metrics.get("label_overlap_pairs") or 0)
    nn_p50 = metrics.get("nn_p50")
    util = metrics.get("space_utilization")
    issues: list[str] = []
    # Crossing budgets: absolute + crossings_per_link (UME KRO 201-500 p50≈0.16, p90≈0.16)
    if n <= 50:
        cross_warn, cross_fail = 5, 20
        cpl_warn, cpl_fail = 0.05, 0.20
    elif n <= 200:
        cross_warn, cross_fail = 20, 80
        cpl_warn, cpl_fail = 0.10, 0.25
    elif n <= 500:
        cross_warn, cross_fail = 80, 200
        cpl_warn, cpl_fail = 0.16, 0.30
    else:
        cross_warn, cross_fail = 150, 400
        cpl_warn, cpl_fail = 0.18, 0.35
    if cross >= cross_fail or cpl >= cpl_fail:
        cross_grade = "fail"
        issues.append(f"crossings={cross}/cpl={cpl} (fail {cross_fail}/{cpl_fail})")
    elif cross >= cross_warn or cpl >= cpl_warn:
        cross_grade = "warn"
        issues.append(f"crossings={cross}/cpl={cpl} (warn {cross_warn}/{cpl_warn})")
    else:
        cross_grade = "ok"

    if ume_reference:
        overlap_warn = max(3, n // 100)
        overlap_fail = max(10, n // 40)
        hard_overlap = overlaps > overlap_fail
        soft_overlap = overlaps > overlap_warn
    else:
        # Absolute: icons/names must not overlap or block each other.
        hard_overlap = overlaps > 0 or label_overlaps > 0
        soft_overlap = False
        if overlaps > 0:
            issues.append(f"footprint_overlaps={overlaps}>0")
        if label_overlaps > 0:
            issues.append(f"label_overlaps={label_overlaps}>0")

    util_warn = 0.08
    util_fail = 0.03
    util_f = float(util) if util is not None else None
    util_bad = util_f is not None and util_f < util_fail
    util_soft = util_f is not None and util_f < util_warn

    if hard_overlap or util_bad or (nn_p50 is not None and float(nn_p50) < 80):
        space_grade = "fail"
        if util_bad:
            issues.append(f"space_utilization={util_f}<{util_fail}")
        if nn_p50 is not None and float(nn_p50) < 80:
            issues.append(f"nn_p50={nn_p50}<80")
    elif soft_overlap or util_soft or (nn_p50 is not None and float(nn_p50) < MIN_CENTER_DIST):
        space_grade = "warn"
        if soft_overlap and ume_reference:
            issues.append(f"footprint_overlaps={overlaps}")
        if util_soft:
            issues.append(f"space_utilization={util_f}<{util_warn}")
        if nn_p50 is not None and float(nn_p50) < MIN_CENTER_DIST:
            issues.append(f"nn_p50={nn_p50}<{MIN_CENTER_DIST}")
    else:
        space_grade = "ok"

    order = {"ok": 0, "warn": 1, "fail": 2}
    overall = max([cross_grade, space_grade], key=lambda g: order[g])
    return {
        "overall": overall,
        "crossing_grade": cross_grade,
        "spacing_grade": space_grade,
        "issues": issues,
        "budgets": {
            "cross_warn": cross_warn,
            "cross_fail": cross_fail,
            "min_center_dist": MIN_CENTER_DIST,
            "recommended_dx": REC_CENTER_DX,
            "recommended_dy": REC_CENTER_DY,
            "overlap_hard_zero": not ume_reference,
            "util_warn": util_warn,
            "util_fail": util_fail,
        },
        "hint": (
            "Fewer edge_crossings is better. Prefer crossings_per_link as size-normalized score. "
            "Hard spacing: footprint_overlap_pairs=0 and label_overlap_pairs=0; "
            "raise space_utilization without stacking icons."
        ),
    }
