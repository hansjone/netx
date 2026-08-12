"""Polar orbit sweep: suggest top-3 single-node drags by crossing score.

Agent workflow:
- preview → pick rank 1..3 → apply
- round=true: one batch, auto-apply #1 per hot node
- until_limit=true: loop single-point sweeps until stall (eye polish)
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    crossing_participation,
    crossing_participation_full,
    crossings_involving_node,
    node_footprint,
    top_crossing_edges,
    top_crossing_nodes,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

_MAX_JUMP = 900.0
# Long fabric bridges on giant metros often exceed 5k; cap must leave room for
# max_jump≈8k–12k orbit rounds (still clamped per-call via params).
_MAX_JUMP_CAP = 12000.0
_MAX_FROM_NBS = 1100.0


def _protect_is_off(protect_rigid: bool | str) -> bool:
    return protect_rigid in (False, "false", "off", "none", "0")


_LAYER_ALIASES: dict[str, str] = {
    "external": "external",
    "ext": "external",
    "0": "external",
    "core": "core",
    "cn": "core",
    "1": "core",
    "agg": "agg",
    "an": "agg",
    "aggregation": "agg",
    "aggregate": "agg",
    "2": "agg",
    "access": "access",
    "en": "access",
    "edge": "access",
    "cpe": "access",
    "3": "access",
    "other": "other",
}


def _coerce_str_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if isinstance(raw, (list, tuple, set)):
        out: list[str] = []
        for x in raw:
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    s = str(raw).strip()
    return [s] if s else []


def _normalize_layer_token(tok: str) -> str | None:
    t = str(tok or "").strip().lower()
    if not t:
        return None
    if t in _LAYER_ALIASES:
        return _LAYER_ALIASES[t]
    # numeric-looking → major band
    try:
        lv = float(t)
    except ValueError:
        return t if t in {"external", "core", "agg", "access", "other"} else None
    maj = int(math.floor(lv))
    if maj <= 0:
        return "external"
    if maj == 1:
        return "core"
    if maj == 2:
        return "agg"
    return "access"


def _major_from_level(lv: float) -> int:
    maj = int(math.floor(float(lv)))
    if maj < 0:
        return 0
    return maj


def ids_matching_freeze_layers_levels(
    state: LayoutState,
    freeze_layers: list[str] | None = None,
    freeze_levels: list[Any] | None = None,
) -> set[str]:
    """Resolve freeze_layers / freeze_levels → fabric_node_id set.

    - ``freeze_layers``: ``core|agg|access|external`` (aliases CN/AN/EN/1/2/3 OK)
    - ``freeze_levels``: fabric numeric levels. Integer-ish (1 / 2) freezes that
      major band; fractional (1.1) matches exact ``state.levels`` when present,
      else falls back to major band via ``state.layers``.
    """
    want_layers: set[str] = set()
    for tok in _coerce_str_list(freeze_layers):
        layer = _normalize_layer_token(tok)
        if layer:
            want_layers.add(layer)

    want_majors: set[int] = set()
    want_exact: set[float] = set()
    for tok in _coerce_str_list(freeze_levels):
        try:
            lv = float(tok)
        except (TypeError, ValueError):
            layer = _normalize_layer_token(tok)
            if layer:
                want_layers.add(layer)
            continue
        if not math.isfinite(lv):
            continue
        # Exact sub-level when not an integer (1.1); majors when 1 / 2.0
        if abs(lv - round(lv)) < 1e-9:
            want_majors.add(int(round(lv)))
            layer = _normalize_layer_token(str(int(round(lv))))
            if layer:
                want_layers.add(layer)
        else:
            want_exact.add(float(lv))

    if not want_layers and not want_majors and not want_exact:
        return set()

    out: set[str] = set()
    levels = getattr(state, "levels", None) or {}
    layers = getattr(state, "layers", None) or {}

    if want_exact and levels:
        for nid, lv in levels.items():
            if float(lv) in want_exact or any(
                abs(float(lv) - e) < 1e-6 for e in want_exact
            ):
                out.add(str(nid))

    if want_majors and levels:
        for nid, lv in levels.items():
            if _major_from_level(lv) in want_majors:
                out.add(str(nid))

    if want_layers and layers:
        for nid, layer in layers.items():
            if str(layer) in want_layers:
                out.add(str(nid))

    # No numeric levels on state → majors already folded into want_layers above.
    return out


def _merge_explicit_freeze(
    state: LayoutState,
    *,
    protect_rigid: bool | str,
    frozen_ids: set[str] | None,
    freeze_layers: list[str] | None = None,
    freeze_levels: list[Any] | None = None,
    always_honor_explicit: bool = True,
) -> set[str]:
    """Combine protect_rigid portals with explicit id/layer/level freezes."""
    frozen = _resolve_frozen(state, protect_rigid, frozen_ids)
    layer_ids = ids_matching_freeze_layers_levels(
        state, freeze_layers, freeze_levels
    )
    if always_honor_explicit or not _protect_is_off(protect_rigid):
        if frozen_ids:
            frozen |= {str(x) for x in frozen_ids if str(x)}
        frozen |= layer_ids
    elif layer_ids:
        # Explicit layer/level freeze always means user intent.
        frozen |= layer_ids
        if frozen_ids:
            frozen |= {str(x) for x in frozen_ids if str(x)}
    return frozen


def _resolve_frozen(
    st: LayoutState,
    protect_rigid: bool | str,
    frozen_ids: set[str] | None,
) -> set[str]:
    """Portal/rigid freeze only when protect is on; off ignores portal_ids inject."""
    if _protect_is_off(protect_rigid):
        return set()
    frozen: set[str] = set(frozen_ids or ())
    if not frozen:
        from netx_topology_mcp.layout_ops.rigid_units import frozen_ids_for_protect

        frozen = frozen_ids_for_protect(st, protect_rigid)
    return frozen


def _box(nid: str, pos: dict[str, tuple[float, float]], names: dict[str, str]):
    x, y = pos[nid]
    minx, miny, maxx, maxy = node_footprint(names.get(nid, ""))
    return (x + minx, y + miny, x + maxx, y + maxy)


def _centers_may_overlap(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    fa: tuple[float, float, float, float],
    fb: tuple[float, float, float, float],
    *,
    pad: float = 12.0,
) -> bool:
    """Coarse AABB reach check — label width can exceed 80px."""
    ra_x = max(abs(fa[0]), abs(fa[2])) + pad
    ra_y = max(abs(fa[1]), abs(fa[3])) + pad
    rb_x = max(abs(fb[0]), abs(fb[2])) + pad
    rb_y = max(abs(fb[1]), abs(fb[3])) + pad
    return abs(ax - bx) <= ra_x + rb_x and abs(ay - by) <= ra_y + rb_y


def _node_overlaps_any(
    node: str, pos: dict[str, tuple[float, float]], names: dict[str, str]
) -> bool:
    ax0, ay0, ax1, ay1 = _box(node, pos, names)
    ax, ay = pos[node]
    fa = node_footprint(names.get(node, ""))
    for b, (x, y) in pos.items():
        if b == node:
            continue
        fb = node_footprint(names.get(b, ""))
        if not _centers_may_overlap(ax, ay, x, y, fa, fb):
            continue
        bx0, by0, bx1, by1 = _box(b, pos, names)
        if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
            return True
    return False


def _moved_invades_outsiders(
    members: set[str],
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
) -> bool:
    """True if any moved node footprint overlaps a non-member (invasion)."""
    for n in members:
        if n not in pos:
            continue
        ax0, ay0, ax1, ay1 = _box(n, pos, names)
        nx, ny = pos[n]
        fa = node_footprint(names.get(n, ""))
        for b, (x, y) in pos.items():
            if b in members:
                continue
            fb = node_footprint(names.get(b, ""))
            if not _centers_may_overlap(nx, ny, x, y, fa, fb):
                continue
            bx0, by0, bx1, by1 = _box(b, pos, names)
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                return True
    return False


def _has_any_footprint_overlap(
    pos: dict[str, tuple[float, float]], names: dict[str, str]
) -> bool:
    """Full pairwise footprint gate — matches apply overlaps_remain."""
    ids = list(pos.keys())
    boxes: list[tuple[str, tuple[float, float, float, float]]] = []
    for n in ids:
        boxes.append((n, _box(n, pos, names)))
    for i, (_a, ab) in enumerate(boxes):
        ax0, ay0, ax1, ay1 = ab
        for _b, bb in boxes[i + 1 :]:
            bx0, by0, bx1, by1 = bb
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                return True
    return False


def _nn_ok(
    node: str,
    pos: dict[str, tuple[float, float]],
    nn_floor: float,
) -> bool:
    if nn_floor <= 0:
        return True
    x0, y0 = pos[node]
    floor2 = nn_floor * nn_floor
    for b, (x, y) in pos.items():
        if b == node:
            continue
        dx, dy = x - x0, y - y0
        if dx * dx + dy * dy < floor2:
            return False
    return True


def _incident_stretch(
    node: str,
    pos: dict[str, tuple[float, float]],
    adj: dict[str, set[str]],
    target_nn: float,
) -> float:
    nbs = [v for v in adj.get(node, ()) if v in pos]
    if not nbs:
        return 1.0
    tn = max(40.0, float(target_nn))
    x0, y0 = pos[node]
    lengths = [math.hypot(pos[v][0] - x0, pos[v][1] - y0) for v in nbs]
    mean_l = sum(lengths) / len(lengths)
    return mean_l / tn


def _angle_diff_deg(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return d if d <= 180.0 else 360.0 - d


def _radii_for_jump(jump: float) -> list[float]:
    radii = [80.0, 120.0, 180.0, 260.0, 360.0, 480.0]
    if jump > 520:
        radii = radii + [640.0, 800.0]
    if jump > 1200:
        radii = radii + [1200.0, 1600.0, 2200.0, min(jump, 3200.0)]
    if jump > 3200:
        radii = radii + [min(jump * 0.7, 5000.0), min(jump * 0.9, jump)]
    return [r for r in radii if r <= jump + 1]


def _polar_grid(
    x0: float,
    y0: float,
    *,
    jump: float,
    angle_step: int,
    radii: list[float] | None = None,
) -> list[tuple[float, float, float, float]]:
    """Return (x, y, r, angle_deg) samples on polar rings about (x0,y0)."""
    step = max(10, int(angle_step))
    rs = radii if radii is not None else _radii_for_jump(jump)
    out: list[tuple[float, float, float, float]] = []
    for ang in range(0, 360, step):
        rad = math.radians(ang)
        c, s = math.cos(rad), math.sin(rad)
        for r in rs:
            if r > jump + 1:
                continue
            out.append((x0 + r * c, y0 + r * s, float(r), float(ang)))
    return out


def _neighbor_guides(
    pos: dict[str, tuple[float, float]],
    node: str,
    adj: dict[str, set[str]],
    jump: float,
) -> list[tuple[float, float, float, float]]:
    x, y = pos[node]
    nbs = [pos[v] for v in adj.get(node, ()) if v in pos]
    if not nbs:
        return []
    cx = sum(p[0] for p in nbs) / len(nbs)
    cy = sum(p[1] for p in nbs) / len(nbs)
    dx, dy = x - cx, y - cy
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    out: list[tuple[float, float, float, float]] = []
    for s in (-360.0, -220.0, -120.0, 120.0, 220.0, 360.0):
        if abs(s) > jump:
            continue
        for nx, ny in (
            (x + ux * s, y + uy * s),
            (cx + ux * abs(s), cy + uy * abs(s)),
        ):
            r = math.hypot(nx - x, ny - y)
            if r > jump or r < 1.0:
                continue
            ang = math.degrees(math.atan2(ny - y, nx - x)) % 360.0
            out.append((nx, ny, r, ang))
    for s in (-240.0, -160.0, -80.0, 80.0, 160.0, 240.0):
        if abs(s) > jump:
            continue
        nx, ny = x + px * s, y + py * s
        r = abs(s)
        ang = math.degrees(math.atan2(ny - y, nx - x)) % 360.0
        out.append((nx, ny, r, ang))
    return out


def _far_field_guides(
    pos: dict[str, tuple[float, float]],
    node: str,
    adj: dict[str, set[str]],
    jump: float,
) -> list[tuple[float, float, float, float]]:
    """Samples that leave the local blob — bbox rim + radial escape.

    Single-node polar rings around the current xy often miss global untangles
    (long chord edges). Push candidates toward the hull exterior and along the
    vector from the graph / neighbor centroid through the node.
    """
    if node not in pos or jump < 200:
        return []
    x, y = pos[node]
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    if not xs:
        return []
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    gcx = 0.5 * (xmin + xmax)
    gcy = 0.5 * (ymin + ymax)
    out: list[tuple[float, float, float, float]] = []

    def _push(nx: float, ny: float) -> None:
        r = math.hypot(nx - x, ny - y)
        if r < 40.0 or r > jump + 1.0:
            return
        ang = math.degrees(math.atan2(ny - y, nx - x)) % 360.0
        out.append((nx, ny, r, ang))

    # Radial escape from graph center through node.
    dx, dy = x - gcx, y - gcy
    gl = math.hypot(dx, dy) or 1.0
    gx, gy = dx / gl, dy / gl
    for dist in (0.45 * jump, 0.7 * jump, 0.92 * jump):
        _push(x + gx * dist, y + gy * dist)
        _push(gcx + gx * (gl + dist), gcy + gy * (gl + dist))

    # Neighbor-centroid radial (often differs from graph center on eyes).
    nbs = [pos[v] for v in adj.get(node, ()) if v in pos]
    if nbs:
        ncx = sum(p[0] for p in nbs) / len(nbs)
        ncy = sum(p[1] for p in nbs) / len(nbs)
        ndx, ndy = x - ncx, y - ncy
        nl = math.hypot(ndx, ndy) or 1.0
        nxu, nyu = ndx / nl, ndy / nl
        for dist in (0.5 * jump, 0.85 * jump):
            _push(x + nxu * dist, y + nyu * dist)
            # Reflect across neighbor centroid (flip to clear side).
            _push(ncx - nxu * dist, ncy - nyu * dist)

    # Bbox rim / corners — good for long chords that pierce the drawing.
    pad = min(jump, max(600.0, 0.35 * jump))
    rim = [
        (xmin - pad, y),
        (xmax + pad, y),
        (x, ymin - pad),
        (x, ymax + pad),
        (xmin - pad, ymin - pad),
        (xmax + pad, ymin - pad),
        (xmin - pad, ymax + pad),
        (xmax + pad, ymax + pad),
        (xmin - pad, gcy),
        (xmax + pad, gcy),
        (gcx, ymin - pad),
        (gcx, ymax + pad),
    ]
    for nx, ny in rim:
        _push(nx, ny)

    # Small rings around each neighbor (park beside spoke tip).
    for nb in list(adj.get(node, ()))[:8]:
        if nb not in pos:
            continue
        bx, by = pos[nb]
        for ang in (0, 45, 90, 135, 180, 225, 270, 315):
            rad = math.radians(ang)
            for rr in (220.0, 480.0, 900.0):
                if rr > jump:
                    continue
                _push(bx + rr * math.cos(rad), by + rr * math.sin(rad))
    return out


def _score_key(c: dict[str, Any]) -> tuple:
    return (
        int(c["crossings"]["global"]),
        int(c["crossings"]["incident"]),
        float(c.get("stretch") or 1.0),
        float(c.get("r") or 0.0),
    )


# Verdict weights for the components a single-node orbit move mainly affects.
_W_CROSS = 0.18
_W_CLR = 0.08
_W_AXIS = 0.06


def _crossing_part_score(crossings: int, *, n_links: int, n_nodes: int) -> float:
    """Match layout_stats crossing sub-score in [0,1] from raw crossing count."""
    cpl = float(crossings) / max(int(n_links), 1)
    if n_nodes <= 50:
        cpl_ok, cpl_bad = 0.05, 0.20
    elif n_nodes <= 200:
        cpl_ok, cpl_bad = 0.10, 0.25
    else:
        cpl_ok, cpl_bad = 0.16, 0.30
    if cpl <= cpl_ok:
        return 1.0
    if cpl >= cpl_bad:
        return 0.0
    return 1.0 - (cpl - cpl_ok) / max(cpl_bad - cpl_ok, 1e-9)


def _incident_axis_frac(
    nid: str,
    pos: dict[str, tuple[float, float]],
    adj: dict[str, set[str]],
    *,
    tol_px: float = 8.0,
) -> float:
    """Fraction of incident edges that are near-horizontal or near-vertical."""
    nbs = [v for v in adj.get(nid, ()) if v in pos and v != nid]
    if not nbs or nid not in pos:
        return 0.0
    ax, ay = pos[nid]
    good = 0
    for v in nbs:
        bx, by = pos[v]
        if abs(ax - bx) <= tol_px or abs(ay - by) <= tol_px:
            good += 1
    return good / float(len(nbs))


def _local_clearance_hits(
    nid: str,
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
    *,
    thr: float = 40.0,
    endpoint_t: float = 0.05,
) -> int:
    """Fast hit count affected by moving ``nid`` (O(E + N·deg), not O(N·E))."""
    from netx_topology_mcp.layout_metrics import point_segment_dist

    if nid not in pos:
        return 0
    thr_f = float(thr)
    et = float(endpoint_t)
    hits = 0
    p = pos[nid]
    # nid sitting on non-incident edges
    for a, b in links:
        if nid in (a, b) or a not in pos or b not in pos:
            continue
        d, t = point_segment_dist(p, pos[a], pos[b])
        if t <= et or t >= 1.0 - et:
            continue
        if d < thr_f:
            hits += 1
    # other nodes sitting on edges incident to nid
    incident = {nid, *adj.get(nid, ())}
    inc_edges = [(nid, v) for v in adj.get(nid, ()) if v in pos]
    for other, (ox, oy) in pos.items():
        if other in incident:
            continue
        for a, b in inc_edges:
            d, t = point_segment_dist((ox, oy), pos[a], pos[b])
            if t <= et or t >= 1.0 - et:
                continue
            if d < thr_f:
                hits += 1
    return hits


def _local_clearance_score(hits: int, *, n_nodes: int) -> float:
    """Map local hit count to a soft [0,1] score (higher=better)."""
    # Cap relative to graph size so one node doesn't dominate.
    hit_frac = float(hits) / max(float(n_nodes) * 0.05, 1.0)
    if hit_frac <= 0.0:
        return 1.0
    if hit_frac >= 1.0:
        return 0.0
    return 1.0 - hit_frac


def _verdict_partial(
    crossings: int,
    clearance_score: float,
    *,
    n_links: int,
    n_nodes: int,
    axis_frac: float = 0.0,
) -> float:
    """Weighted crossing+clearance(+axis) slice of verdict (higher is better)."""
    return (
        _W_CROSS * _crossing_part_score(crossings, n_links=n_links, n_nodes=n_nodes)
        + _W_CLR * max(0.0, min(1.0, float(clearance_score)))
        + _W_AXIS * max(0.0, min(1.0, float(axis_frac)))
    )


def _rerank_by_total(
    scored: list[dict[str, Any]],
    nid: str,
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
    links: list[tuple[str, str]],
    *,
    global0: int,
    re_rank_n: int = 20,
    adj: dict[str, set[str]] | None = None,
) -> tuple[list[dict[str, Any]], bool, float]:
    """Re-rank top candidates by crossing+local-clearance+axis (fast).

    Uses local clearance around ``nid`` so until_limit stays interactive on
    mid-size eyes. Returns ``(ranked, ok, base_partial)``.
    """
    del names  # names reserved for future label-aware clearance
    n_nodes = len(pos)
    n_links = len(links)
    adj_m = adj or {}
    base_hits = _local_clearance_hits(nid, pos, links, adj_m)
    base_clr = _local_clearance_score(base_hits, n_nodes=n_nodes)
    base_axis = _incident_axis_frac(nid, pos, adj_m)
    base_partial = _verdict_partial(
        int(global0),
        base_clr,
        n_links=n_links,
        n_nodes=n_nodes,
        axis_frac=base_axis,
    )

    n = min(re_rank_n, len(scored))
    for c in scored[:n]:
        trial = dict(pos)
        trial[nid] = (float(c["x"]), float(c["y"]))
        hits = _local_clearance_hits(nid, trial, links, adj_m)
        clr_s = _local_clearance_score(hits, n_nodes=n_nodes)
        axis_s = _incident_axis_frac(nid, trial, adj_m)
        c["edge_clearance_hits"] = int(hits)
        c["edge_clearance_score"] = clr_s
        c["axis_frac"] = round(axis_s, 4)
        c["verdict_partial"] = _verdict_partial(
            int(c["crossings"]["global"]),
            clr_s,
            n_links=n_links,
            n_nodes=n_nodes,
            axis_frac=axis_s,
        )
    head = sorted(
        scored[:n],
        key=lambda c: (
            -float(c.get("verdict_partial") or 0.0),
            int(c.get("edge_clearance_hits") or 0),
            int(c["crossings"]["incident"]),
            float(c.get("stretch") or 1.0),
            float(c.get("r") or 0.0),
        ),
    )
    return head + scored[n:], True, base_partial


def _diversify_top(
    ranked: list[dict[str, Any]],
    *,
    k: int = 3,
    min_angle_sep: float = 35.0,
) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for c in ranked:
        ok = True
        for p in picked:
            if _angle_diff_deg(float(c["angle_deg"]), float(p["angle_deg"])) < min_angle_sep:
                r0 = max(float(p.get("r") or 1.0), 1.0)
                r1 = max(float(c.get("r") or 1.0), 1.0)
                ratio = max(r0, r1) / min(r0, r1)
                if ratio < 1.3:
                    ok = False
                    break
        if ok:
            picked.append(c)
        if len(picked) >= k:
            break
    # Fill if diversity filtered too hard.
    if len(picked) < k:
        ids = {id(p) for p in picked}
        for c in ranked:
            if id(c) in ids:
                continue
            picked.append(c)
            if len(picked) >= k:
                break
    for i, c in enumerate(picked):
        c["rank"] = i + 1
    return picked


def _eval_candidate(
    node: str,
    cand_xy: tuple[float, float],
    r: float,
    angle_deg: float,
    *,
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
    global0: int,
    local0: int,
    target_nn: float,
    nn_floor: float,
    nbs_cap: float,
) -> dict[str, Any] | None:
    x0, y0 = pos[node]
    nx, ny = cand_xy
    if math.hypot(nx - x0, ny - y0) > r + 1e-6 and r > 0:
        # keep r as reported displacement
        pass
    nbs = [pos[v] for v in adj.get(node, ()) if v in pos]
    if nbs:
        mx = sum(p[0] for p in nbs) / len(nbs)
        my = sum(p[1] for p in nbs) / len(nbs)
        if math.hypot(nx - mx, ny - my) > nbs_cap:
            return None
    trial = dict(pos)
    trial[node] = (nx, ny)
    if _node_overlaps_any(node, trial, names):
        return None
    nn_ok = _nn_ok(node, trial, nn_floor)
    if not nn_ok:
        return None
    local1 = crossings_involving_node(node, trial, links, adj)
    g1 = int(global0) - int(local0) + int(local1)
    stretch = _incident_stretch(node, trial, adj, target_nn)
    disp = math.hypot(nx - x0, ny - y0)
    return {
        "x": round(nx, 1),
        "y": round(ny, 1),
        "r": round(disp, 1),
        "angle_deg": round(angle_deg % 360.0, 1),
        "crossings": {"global": g1, "incident": int(local1)},
        "delta": {
            "global": g1 - int(global0),
            "incident": int(local1) - int(local0),
        },
        "ov": False,
        "nn_ok": True,
        "stretch": round(stretch, 3),
    }


def orbit_sweep_node(
    state: LayoutState,
    node_id: str,
    *,
    params: LayoutParams | None = None,
    max_jump: float | None = None,
    angle_step: int | None = None,
    nn_floor: float = 36.0,
    min_angle_sep: float = 35.0,
    cand_cap: int = 280,
    protect_rigid: bool | str = "off",
    frozen_ids: set[str] | None = None,
    freeze_layers: list[str] | None = None,
    freeze_levels: list[Any] | None = None,
    top_k: int = 3,
    y_min: float | None = None,
    y_max: float | None = None,
    objective: str = "crossing",
) -> dict[str, Any]:
    """Sweep polar candidates for one node; return diversified top-k.

    Default ``protect_rigid=off`` so multi-round orbit may move portals to cut
    crossings; other layout actions keep portal freeze. Opt in with portals/all.
    ``freeze_layers`` / ``freeze_levels`` always freeze matched nodes.
    """
    params = params or LayoutParams()
    st = state
    nid = str(node_id).strip()
    pos = dict(st.positions)
    names = dict(st.names)
    links = list(st.links)
    adj = {n: set(st.adj.get(n, ())) for n in pos}
    if nid not in pos:
        return {
            "ok": False,
            "error": "node_not_on_view",
            "node_id": nid,
        }

    frozen = _resolve_frozen(st, protect_rigid, frozen_ids)
    frozen |= ids_matching_freeze_layers_levels(st, freeze_layers, freeze_levels)
    if nid in frozen:
        return {
            "ok": False,
            "error": "frozen",
            "node_id": nid,
            "hint": "portal/rigid frozen; protect_rigid=off or pick a corridor node",
        }

    n_links = len(links)
    jump = float(max_jump if max_jump is not None else _MAX_JUMP)
    jump = max(200.0, min(jump, _MAX_JUMP_CAP))
    if angle_step is None:
        angle_step = 24 if n_links >= 400 else (18 if n_links >= 200 else 15)
    angle_step = max(10, int(angle_step))
    # Local untangle stays near neighbor centroid; metro bridges / far-field
    # escapes (max_jump≫1k) must leave the unit blob — nbs_cap ≈ jump*3.5.
    if jump > 1200:
        nbs_cap = max(jump * 3.5, 6000.0)
    else:
        nbs_cap = max(_MAX_FROM_NBS, jump * 1.25)
    # High-incident hotspots: allow even farther from neighbor centroid.
    target_nn = float(getattr(params, "target_nn", 155.0) or 155.0)

    x0, y0 = pos[nid]
    global0 = count_edge_crossings(pos, links)
    local0 = crossings_involving_node(nid, pos, links, adj)
    if local0 >= 8 and jump > 1200:
        nbs_cap = max(nbs_cap, jump * 4.5)

    # Prefer far-field / neighbor guides before dense polar rings so cand_cap
    # does not truncate the escapes that actually cut long-chord crossings.
    coarse_step = max(angle_step, 24 if n_links >= 200 else angle_step)
    samples: list[tuple[float, float, float, float]] = []
    samples.extend(_far_field_guides(pos, nid, adj, jump))
    samples.extend(_neighbor_guides(pos, nid, adj, jump))
    # Explicit samples toward each neighbor (incl. long bridges).
    for nb in adj.get(nid, ()):
        if nb not in pos:
            continue
        bx, by = pos[nb]
        dx, dy = bx - x0, by - y0
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        ang = math.degrees(math.atan2(uy, ux)) % 360.0
        for frac in (0.15, 0.35, 0.55, 0.75):
            r = min(jump, L * frac)
            if r < 40:
                continue
            samples.append((x0 + ux * r, y0 + uy * r, r, ang))
        # Perpendicular escapes at mid-chord fractions.
        px, py = -uy, ux
        for r in (180.0, 360.0, 640.0, 1200.0, min(jump, 2200.0)):
            if r > jump:
                continue
            samples.append((x0 + px * r, y0 + py * r, r, (ang + 90) % 360))
            samples.append((x0 - px * r, y0 - py * r, r, (ang + 270) % 360))
    samples.extend(_polar_grid(x0, y0, jump=jump, angle_step=coarse_step))
    # Dedup by rounded xy.
    seen: set[tuple[int, int]] = set()
    uniq: list[tuple[float, float, float, float]] = []
    for sx, sy, r, ang in samples:
        key = (int(round(sx / 8.0) * 8), int(round(sy / 8.0) * 8))
        if key in seen:
            continue
        seen.add(key)
        uniq.append((sx, sy, r, ang))
        if len(uniq) >= cand_cap:
            break

    # Layered y constraint: skip candidates outside [y_min, y_max]
    if y_min is not None or y_max is not None:
        uniq = [
            (sx, sy, r, ang)
            for sx, sy, r, ang in uniq
            if (y_min is None or sy >= y_min)
            and (y_max is None or sy <= y_max)
        ]

    scored: list[dict[str, Any]] = []
    for sx, sy, r, ang in uniq:
        c = _eval_candidate(
            nid,
            (sx, sy),
            r,
            ang,
            pos=pos,
            names=names,
            links=links,
            adj=adj,
            global0=global0,
            local0=local0,
            target_nn=target_nn,
            nn_floor=nn_floor,
            nbs_cap=nbs_cap,
        )
        if c is not None:
            scored.append(c)

    scored.sort(key=_score_key)
    # Refine around top-8 coarse winners.
    refine_budget = max(0, cand_cap - len(uniq))
    fine: list[tuple[float, float, float, float]] = []
    half = max(5.0, coarse_step / 2.0)
    radii = _radii_for_jump(jump)
    for base in scored[:8]:
        ang0 = float(base["angle_deg"])
        r0 = float(base["r"])
        # nearest radius indices
        near_r = sorted(radii, key=lambda rr: abs(rr - r0))[:3]
        for dang in (-half, 0.0, half):
            ang = (ang0 + dang) % 360.0
            rad = math.radians(ang)
            c_, s_ = math.cos(rad), math.sin(rad)
            for rr in near_r:
                if rr > jump + 1:
                    continue
                fine.append((x0 + rr * c_, y0 + rr * s_, rr, ang))
                if len(fine) >= refine_budget:
                    break
            if len(fine) >= refine_budget:
                break
        if len(fine) >= refine_budget:
            break

    for sx, sy, r, ang in fine:
        if (y_min is not None and sy < y_min) or (y_max is not None and sy > y_max):
            continue
        key = (int(round(sx)), int(round(sy)))
        if key in seen:
            continue
        seen.add(key)
        c = _eval_candidate(
            nid,
            (sx, sy),
            r,
            ang,
            pos=pos,
            names=names,
            links=links,
            adj=adj,
            global0=global0,
            local0=local0,
            target_nn=target_nn,
            nn_floor=nn_floor,
            nbs_cap=nbs_cap,
        )
        if c is not None:
            scored.append(c)

    scored.sort(key=_score_key)
    # Multi-objective re-rank: weighted crossing+clearance slice of verdict.total
    use_total = objective == "total" and len(scored) > 1
    base_partial = 0.0
    if use_total:
        scored, clr_ok, base_partial = _rerank_by_total(
            scored, nid, pos, names, links, global0=int(global0), adj=adj
        )
        if not clr_ok:
            # Large-graph clearance skip → fall back to crossing-only ranking.
            use_total = False
        else:
            for c in scored:
                c.setdefault("verdict_partial", float(base_partial))
                c.setdefault("edge_clearance_score", 0.0)
    # Prefer improving moves; still return best even if none improve.
    if use_total:
        improving = [
            c
            for c in scored
            if float(c.get("verdict_partial") or 0.0) > base_partial
        ]
    else:
        improving = [c for c in scored if c["delta"]["global"] < 0]
    pool = improving if improving else scored
    top = _diversify_top(pool, k=max(1, int(top_k)), min_angle_sep=min_angle_sep)

    return {
        "ok": True,
        "node_id": nid,
        "name": names.get(nid, nid),
        "x0": round(x0, 1),
        "y0": round(y0, 1),
        "degree": len(adj.get(nid, ())),
        "crossings_before": {"global": int(global0), "incident": int(local0)},
        "candidates": top,
        "sampled": len(seen),
        "improving_n": len(improving),
        "max_jump": jump,
        "angle_step": angle_step,
        "objective": "total" if use_total else (
            "crossing" if objective != "total" else "crossing_fallback"
        ),
        "y_band": (
            None if (y_min is None and y_max is None)
            else [y_min, y_max]
        ),
        "hint": (
            "prefer rank1 unless util/label concern; then pick 2/3. "
            "apply with params.pick=1|2|3 or updateTopologyViewPositions."
            + (
                " objective=total: rank by weighted crossing+edge_clearance (verdict slice)."
                if use_total
                else (
                    " objective=total skipped clearance (graph too large); ranked by crossings."
                    if objective == "total"
                    else ""
                )
            )
        ),
    }


def apply_orbit_pick(
    state: LayoutState,
    sweep: dict[str, Any],
    *,
    pick: int = 1,
) -> OpResult:
    """Move node to chosen candidate (1-based rank)."""
    st = state.copy()
    nid = str(sweep.get("node_id") or "")
    cands = list(sweep.get("candidates") or [])
    if not nid or nid not in st.positions or not cands:
        return OpResult(
            state=st,
            moved=set(),
            op="orbit_sweep",
            note="orbit_sweep:noop",
            params={"error": "no_candidates"},
        )
    idx = max(1, min(int(pick), len(cands))) - 1
    chosen = cands[idx]
    st.positions[nid] = (float(chosen["x"]), float(chosen["y"]))
    st.last_moved = {nid}
    st.meta["orbit_sweep"] = {
        "node_id": nid,
        "pick": idx + 1,
        "candidate": chosen,
        "crossings_before": sweep.get("crossings_before"),
    }
    return OpResult(
        state=st,
        moved={nid},
        op="orbit_sweep",
        params={
            "node_id": nid,
            "pick": idx + 1,
            "candidate": chosen,
            "crossings_before": sweep.get("crossings_before"),
        },
        note=(
            f"orbit_sweep pick={idx + 1} "
            f"g{sweep.get('crossings_before', {}).get('global')}->"
            f"{chosen['crossings']['global']}"
        ),
    )


def _truthy_flag(v: Any) -> bool:
    if v is True:
        return True
    if v is False or v is None:
        return False
    return str(v).strip().lower() not in {"0", "false", "no", "off", ""}


def _select_stretch_pick(
    sweep: dict[str, Any],
    *,
    max_stretch: float,
    min_delta: int,
    objective: str = "crossing",
) -> tuple[int, dict[str, Any]] | None:
    """Pick best improving candidate with stretch gate (1-based pick index).

    ``objective=total``: accept clearance/partial gains when crossings do not
    rise (flat Δg=0 allowed). Crossing-only mode still requires Δg≤−min_delta.
    """
    if int(sweep.get("improving_n") or 0) <= 0:
        return None
    use_total = str(objective).lower() in {"total", "score", "multi"}
    stretch_cap = float(max_stretch)

    if use_total:
        ranked_t: list[tuple[float, int, int, float, int, dict[str, Any]]] = []
        for i, c in enumerate(sweep.get("candidates") or [], start=1):
            if not isinstance(c, dict) or bool(c.get("ov")):
                continue
            try:
                d = int(((c.get("delta") or {}).get("global") or 0))
            except (TypeError, ValueError):
                continue
            if d > 0:
                continue  # hard: never raise crossings
            partial = float(c.get("verdict_partial") or 0.0)
            clr_hits = int(c.get("edge_clearance_hits") or 10**9)
            st = float(c.get("stretch") or 0.0)
            ranked_t.append((-partial, clr_hits, d, st, i, c))
        if not ranked_t:
            return None
        ranked_t.sort()
        for _neg_p, _hits, d, st, i, c in ranked_t:
            if st <= stretch_cap or (d < 0 and abs(d) >= 3):
                return i, c
        _neg_p, _hits, d, st, i, c = ranked_t[0]
        if st <= stretch_cap * 1.75:
            return i, c
        if d < 0 and abs(d) >= 5:
            return i, c
        if d == 0 and st <= stretch_cap:
            return i, c
        return None

    ranked: list[tuple[int, float, int, dict[str, Any]]] = []
    for i, c in enumerate(sweep.get("candidates") or [], start=1):
        if not isinstance(c, dict):
            continue
        if bool(c.get("ov")):
            continue
        try:
            d = int(((c.get("delta") or {}).get("global") or 0))
        except (TypeError, ValueError):
            continue
        if d >= 0 or abs(d) < max(1, int(min_delta)):
            continue
        st = float(c.get("stretch") or 0.0)
        ranked.append((d, st, i, c))
    if not ranked:
        return None
    ranked.sort(key=lambda t: (t[0], t[1]))
    for d, st, i, c in ranked:
        if st <= stretch_cap or abs(d) >= 3:
            return i, c
    d, st, i, c = ranked[0]
    if st <= stretch_cap * 1.75:
        return i, c
    if abs(d) >= 5:
        return i, c
    return None


def _until_limit_queue(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    frozen: set[str],
    max_degree: int,
    top_edges_n: int = 16,
    prefer_low_degree: bool = True,
    hard_degree_cap: int | None = None,
    include_clearance: bool = False,
) -> list[str]:
    """Hotspot queue: crossing nodes + top-edge endpoints (+ clearance hits).

    ``max_degree`` soft-prefers corridor nodes; ``hard_degree_cap`` (default
    max(max_degree, 16)) still admits high-deg hubs that own most hits.
    """
    _n, node_hit, edge_hit = crossing_participation_full(pos, links)
    hard = int(hard_degree_cap) if hard_degree_cap is not None else max(int(max_degree), 16)
    soft = max(1, int(max_degree))
    # hits, deg, edge_boost, clr_boost
    scored: dict[str, tuple[int, int, int, int]] = {}
    for nid, hits in (node_hit or {}).items():
        if nid in frozen or nid not in pos:
            continue
        deg = len(adj.get(nid, ()))
        if deg > hard or hits <= 0:
            continue
        scored[nid] = (int(hits), deg, 0, 0)
    for row in top_crossing_edges(
        pos,
        links,
        names=names,
        top_n=top_edges_n,
        edge_participation=edge_hit,
    ):
        hits = int(row.get("crossing_hits") or 0)
        for key in ("a_node_id", "b_node_id"):
            nid = str(row.get(key) or "")
            if not nid or nid in frozen or nid not in pos:
                continue
            deg = len(adj.get(nid, ()))
            if deg > hard:
                continue
            prev = scored.get(nid)
            base_hits = max(hits, prev[0] if prev else 0)
            scored[nid] = (base_hits, deg, 1, prev[3] if prev else 0)
    if include_clearance:
        from netx_topology_mcp.layout_metrics import compute_edge_clearance

        ec = compute_edge_clearance(pos, links, names=names, top_n=24)
        if not ec.get("edge_clearance_skipped"):
            for row in ec.get("top_edge_hits") or []:
                nid = str(row.get("fabric_node_id") or "")
                if not nid or nid in frozen or nid not in pos:
                    continue
                deg = len(adj.get(nid, ()))
                if deg > hard:
                    continue
                prev = scored.get(nid)
                if prev:
                    scored[nid] = (prev[0], prev[1], prev[2], 1)
                else:
                    # Pure clearance obstacle — give a synthetic hit so it queues.
                    scored[nid] = (1, deg, 0, 1)
    if not scored:
        return []
    items = list(scored.items())
    if prefer_low_degree:
        items.sort(
            key=lambda kv: (
                kv[1][3],  # clearance obstacles first when include_clearance
                kv[1][2],
                kv[1][0],
                1 if kv[1][1] <= soft else 0,
                -kv[1][1],
            ),
            reverse=True,
        )
    else:
        items.sort(
            key=lambda kv: (kv[1][3], kv[1][0], kv[1][2], -kv[1][1]),
            reverse=True,
        )
    return [nid for nid, _ in items]


def orbit_sweep_until_limit(
    state: LayoutState,
    *,
    params: LayoutParams | None = None,
    max_degree: int = 14,
    max_jump: float | None = None,
    angle_step: int | None = None,
    nn_floor: float = 36.0,
    min_angle_sep: float = 35.0,
    protect_rigid: bool | str = "portals",
    frozen_ids: set[str] | None = None,
    freeze_layers: list[str] | None = None,
    freeze_levels: list[Any] | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    objective: str = "crossing",
    max_moves: int = 40,
    stall_limit: int = 12,
    max_stretch: float = 32.0,
    min_delta: int = 1,
    scan_cap: int = 32,
    top_k: int = 8,
    prefer_low_degree: bool = True,
    cand_cap: int = 360,
    bundle: bool = True,
    bundle_max: int = 10,
) -> OpResult:
    """Loop single-node orbit picks until no improving hotspot (eye polish).

    Unlike ``round`` (one batch, always pick#1), this re-ranks after each apply,
    gates stretch, and defaults ``protect_rigid=portals`` so CN portals stay put.

    When single-point stalls, ``bundle=True`` (default) tries rigid
    chain / ring+chain contract→orbit→expand moves.

    Freeze = protect portals ∪ ``portal_ids``/``frozen_ids`` ∪ ``freeze_layers`` ∪
    ``freeze_levels``.
    """
    params = params or LayoutParams()
    st = state.copy()
    pos = dict(st.positions)
    names = dict(st.names)
    links = list(st.links)
    adj = {n: set(st.adj.get(n, ())) for n in pos}
    frozen = _merge_explicit_freeze(
        st,
        protect_rigid=protect_rigid,
        frozen_ids=frozen_ids,
        freeze_layers=freeze_layers,
        freeze_levels=freeze_levels,
        always_honor_explicit=True,
    )

    jump = float(max_jump if max_jump is not None else 4000.0)
    jump = max(120.0, min(jump, _MAX_JUMP_CAP))
    obj = "total" if str(objective).lower() in {"total", "score", "multi"} else "crossing"
    global0 = count_edge_crossings(pos, links)
    use_bundle = bool(bundle)

    moves: list[dict[str, Any]] = []
    tried_fail: set[str] = set()
    stall = 0
    stop_reason = "max_moves"
    moved: set[str] = set()
    rounds = 0
    bundle_moves = 0
    bundle_exhausted = False
    max_moves_i = max(1, int(max_moves))
    stall_lim = max(1, int(stall_limit))
    scan_n = max(4, int(scan_cap))
    cand_n = max(120, int(cand_cap))

    while len(moves) < max_moves_i and stall < stall_lim:
        rounds += 1
        try:
            from netx_topology_mcp.layout_jobs import raise_if_cancelled, touch_heartbeat

            touch_heartbeat()
            raise_if_cancelled()
        except ImportError:
            pass
        queue = [
            nid
            for nid in _until_limit_queue(
                pos,
                links,
                adj,
                names,
                frozen=frozen,
                max_degree=max(1, int(max_degree)),
                prefer_low_degree=prefer_low_degree,
                include_clearance=(obj == "total"),
            )
            if nid not in tried_fail
        ]
        applied = False
        for nid in queue[:scan_n]:
            st.positions = pos
            sweep = orbit_sweep_node(
                st,
                nid,
                params=params,
                max_jump=jump,
                angle_step=angle_step,
                nn_floor=nn_floor,
                min_angle_sep=min_angle_sep,
                cand_cap=cand_n,
                protect_rigid="off",
                frozen_ids=frozen,
                top_k=max(3, int(top_k)),
                y_min=y_min,
                y_max=y_max,
                objective=obj,
            )
            if not sweep.get("ok"):
                tried_fail.add(nid)
                continue
            deg_n = len(adj.get(nid, ()))
            # Soft max_degree: still allow high-deg hubs that made the queue
            # via hard_degree_cap; only skip absurd stars.
            if deg_n > max(int(max_degree), 16):
                tried_fail.add(nid)
                continue
            picked = _select_stretch_pick(
                sweep,
                max_stretch=float(max_stretch),
                min_delta=int(min_delta),
                objective=obj,
            )
            if not picked:
                tried_fail.add(nid)
                continue
            pick_i, cand = picked
            prev_xy = pos[nid]
            pos[nid] = (float(cand["x"]), float(cand["y"]))
            if _node_overlaps_any(nid, pos, names):
                pos[nid] = prev_xy
                tried_fail.add(nid)
                continue
            moved.add(nid)
            g_after = int((cand.get("crossings") or {}).get("global") or 0)
            delta_g = int((cand.get("delta") or {}).get("global") or 0)
            moves.append(
                {
                    "node_id": nid,
                    "name": names.get(nid, nid),
                    "degree": deg_n,
                    "pick": pick_i,
                    "xy": [round(float(cand["x"]), 1), round(float(cand["y"]), 1)],
                    "delta": delta_g,
                    "stretch": float(cand.get("stretch") or 0.0),
                    "crossings": g_after,
                    "mode": "point",
                }
            )
            tried_fail.clear()
            stall = 0
            applied = True
            bundle_exhausted = False
            break

        if not applied and use_bundle and not bundle_exhausted:
            from netx_topology_mcp.layout_ops.bundle_orbit import (
                bundle_orbit_until_progress,
            )

            try:
                from netx_topology_mcp.layout_jobs import raise_if_cancelled, touch_heartbeat

                touch_heartbeat()
                raise_if_cancelled()
            except ImportError:
                pass
            st.positions = pos
            bop = bundle_orbit_until_progress(
                st,
                frozen_ids=frozen,
                max_jump=max(jump, 5000.0),
                max_bundles=max(4, min(12, int(bundle_max))),
                min_delta=int(min_delta),
                cand_cap=min(cand_n, 64),
                angle_step=max(angle_step or 20, 24),
                nn_floor=nn_floor,
                params=params,
            )
            if bop.moved:
                trial = dict(bop.state.positions)
                mem = set(bop.moved)
                # Expand must not invade or leave any footprint pairs (apply gate).
                if _has_any_footprint_overlap(trial, names):
                    bundle_exhausted = True
                else:
                    pos = trial
                    moved |= mem
                    bmeta = bop.params or {}
                    moves.append(
                        {
                            "node_id": bmeta.get("tip_id"),
                            "name": names.get(str(bmeta.get("tip_id") or ""), ""),
                            "kind": bmeta.get("kind"),
                            "bundle": bmeta.get("bundle"),
                            "member_n": bmeta.get("member_n"),
                            "delta": int(bmeta.get("delta") or 0),
                            "crossings": int(
                                bmeta.get("end_crossings")
                                or bmeta.get("crossings")
                                or 0
                            ),
                            "expand_scale": bmeta.get("expand_scale"),
                            "mode": "bundle",
                        }
                    )
                    bundle_moves += 1
                    bundle_exhausted = False
                    tried_fail.clear()
                    stall = 0
                    applied = True
            else:
                bundle_exhausted = True

        if not applied:
            if not queue and not use_bundle:
                stop_reason = "no_candidates"
                break
            stall += 1
            if queue:
                tried_fail.add(queue[0])
            if stall >= stall_lim:
                stop_reason = "stall"
                break

    if len(moves) >= max_moves_i:
        stop_reason = "max_moves"
    elif stop_reason == "max_moves" and stall >= stall_lim:
        stop_reason = "stall"
    if not moves and stop_reason == "max_moves":
        stop_reason = "no_candidates"

    st.positions = pos
    st.last_moved = moved
    end_g = count_edge_crossings(pos, links)
    meta = {
        "mode": "until_limit",
        "start_crossings": global0,
        "end_crossings": end_g,
        "delta_crossings": int(end_g) - int(global0),
        "moved_n": len(moved),
        "moves_n": len(moves),
        "moves": moves,
        "bundle_moves": bundle_moves,
        "bundle": use_bundle,
        "rounds": rounds,
        "stall": stall,
        "stop_reason": stop_reason,
        "max_degree": int(max_degree),
        "max_jump": jump,
        "max_stretch": float(max_stretch),
        "min_delta": int(min_delta),
        "objective": obj,
        "protect_rigid": (
            "off"
            if _protect_is_off(protect_rigid)
            else str(protect_rigid)
        ),
        "frozen_n": len(frozen),
        "freeze_layers": list(freeze_layers or []),
        "freeze_levels": list(freeze_levels or []),
    }
    st.meta["orbit_sweep"] = meta
    return OpResult(
        state=st,
        moved=moved,
        op="orbit_sweep_until_limit",
        params=meta,
        note=(
            f"orbit_sweep_until_limit {global0}->{end_g} "
            f"moves={len(moves)} bundle={bundle_moves} "
            f"stop={stop_reason} frozen={len(frozen)}"
        ),
    )


def orbit_sweep_round(
    state: LayoutState,
    *,
    params: LayoutParams | None = None,
    top_n: int = 12,
    max_degree: int = 9,
    max_jump: float | None = None,
    angle_step: int | None = None,
    nn_floor: float = 36.0,
    min_angle_sep: float = 35.0,
    protect_rigid: bool | str = "off",
    frozen_ids: set[str] | None = None,
    freeze_layers: list[str] | None = None,
    freeze_levels: list[Any] | None = None,
    focus_ids: list[str] | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    objective: str = "crossing",
) -> OpResult:
    """Scan hot nodes; auto-apply each node's rank-1 if the active objective improves.

    Default ``protect_rigid=off`` (may move portals). Opt in with portals/all.
    Explicit ``portal_ids`` / ``freeze_layers`` / ``freeze_levels`` are always honored.
    """
    params = params or LayoutParams()
    st = state.copy()
    pos = dict(st.positions)
    names = dict(st.names)
    links = list(st.links)
    adj = {n: set(st.adj.get(n, ())) for n in pos}

    frozen = _merge_explicit_freeze(
        st,
        protect_rigid=protect_rigid,
        frozen_ids=frozen_ids,
        freeze_layers=freeze_layers,
        freeze_levels=freeze_levels,
        always_honor_explicit=True,
    )

    global0 = count_edge_crossings(pos, links)
    hit = crossing_participation(pos, links)[1]
    focus = {str(x) for x in (focus_ids or []) if str(x)}
    if not focus:
        focus = {
            str(r["fabric_node_id"])
            for r in top_crossing_nodes(
                pos, links, names=names, adj=adj, top_n=5, participation=hit
            )
        }
    # Expand hub focus to low-deg neighbors.
    movable: list[str] = []
    seen_m: set[str] = set()
    prefer: list[str] = []
    for nid in focus:
        if nid in hit and len(adj.get(nid, ())) < max_degree and nid not in frozen:
            prefer.append(nid)
        for nb in adj.get(nid, ()):
            if nb in hit and len(adj.get(nb, ())) < max_degree and nb not in frozen:
                prefer.append(nb)
    ranked = sorted(
        hit.keys(),
        key=lambda n: (
            0 if n in prefer or n in focus else 1,
            -hit[n] / max(len(adj.get(n, ())), 1),
            len(adj.get(n, ())),
            -hit[n],
        ),
    )
    for nid in ranked:
        if nid in frozen or nid in seen_m:
            continue
        if len(adj.get(nid, ())) >= max_degree:
            continue
        if hit.get(nid, 0) <= 0:
            continue
        movable.append(nid)
        seen_m.add(nid)
        if len(movable) >= max(1, int(top_n)):
            break

    cur_g = global0
    moved: set[str] = set()
    trace: list[dict[str, Any]] = []
    for nid in movable:
        # Refresh state positions into a temp LayoutState for sweep.
        st.positions = pos
        sweep = orbit_sweep_node(
            st,
            nid,
            params=params,
            max_jump=max_jump,
            angle_step=angle_step,
            nn_floor=nn_floor,
            min_angle_sep=min_angle_sep,
            protect_rigid="off",  # already applied frozen set
            frozen_ids=frozen,
            y_min=y_min,
            y_max=y_max,
            objective=objective,
        )
        if not sweep.get("ok"):
            trace.append({"node_id": nid, "skipped": sweep.get("error")})
            continue
        cands = list(sweep.get("candidates") or [])
        if not cands:
            trace.append({"node_id": nid, "skipped": "no_candidates"})
            continue
        best = cands[0]
        # Skip when the active objective has no improving candidates.
        if int(sweep.get("improving_n") or 0) <= 0:
            trace.append(
                {
                    "node_id": nid,
                    "skipped": "no_global_gain",
                    "best_delta": best["delta"],
                    "objective": objective,
                }
            )
            continue
        # Apply #1
        pos[nid] = (float(best["x"]), float(best["y"]))
        moved.add(nid)
        cur_g = int(best["crossings"]["global"])
        # Refresh hit lightly for ranking continuity.
        local = crossings_involving_node(nid, pos, links, adj)
        if local > 0:
            hit[nid] = local
        else:
            hit.pop(nid, None)
        trace.append(
            {
                "node_id": nid,
                "name": names.get(nid, nid),
                "applied": True,
                "pick": 1,
                "xy": [best["x"], best["y"]],
                "delta": best["delta"],
                "crossings": best["crossings"],
            }
        )

    st.positions = pos
    st.last_moved = moved
    end_g = count_edge_crossings(pos, links)
    meta = {
        "start_crossings": global0,
        "end_crossings": end_g,
        "moved_n": len(moved),
        "scanned_n": len(movable),
        "trace": trace,
        "top_n": top_n,
        "max_degree": max_degree,
    }
    st.meta["orbit_sweep"] = meta
    return OpResult(
        state=st,
        moved=moved,
        op="orbit_sweep_round",
        params=meta,
        note=f"orbit_sweep_round {global0}->{end_g} moved={len(moved)}/{len(movable)}",
    )


def orbit_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    out: dict[str, Any] = {}
    if o.get("node_id") is not None:
        out["node_id"] = str(o.get("node_id") or "").strip()
    elif o.get("fabric_node_id") is not None:
        out["node_id"] = str(o.get("fabric_node_id") or "").strip()
    if o.get("pick") is not None:
        try:
            out["pick"] = max(1, min(12, int(o["pick"])))
        except (TypeError, ValueError):
            out["pick"] = 1
    if o.get("round") is not None:
        out["round"] = _truthy_flag(o.get("round"))
    until = o.get("until_limit")
    if until is None:
        until = o.get("until_stall")
    if until is not None:
        out["until_limit"] = _truthy_flag(until)
    for key, cast, default in (
        ("top_n", int, 12),
        ("max_degree", int, 14),
        ("angle_step", int, None),
        ("cand_cap", int, 360),
        ("top_k", int, 3),
        ("max_moves", int, 40),
        ("stall_limit", int, 12),
        ("min_delta", int, 1),
        ("scan_cap", int, 32),
    ):
        if key not in o or o[key] is None:
            if default is not None and (
                key in ("top_n", "max_degree", "cand_cap", "top_k")
                or out.get("until_limit")
            ):
                # until_limit-only knobs only default when flag on
                if key in (
                    "max_moves",
                    "stall_limit",
                    "min_delta",
                    "scan_cap",
                ) and not out.get("until_limit"):
                    continue
                if key == "max_degree" and out.get("until_limit"):
                    out[key] = 14
                    continue
                if key == "cand_cap" and out.get("until_limit"):
                    out[key] = 360
                    continue
                if key == "top_k" and out.get("until_limit"):
                    out[key] = 8
                    continue
                out[key] = default
            continue
        try:
            out[key] = cast(o[key])
        except (TypeError, ValueError):
            if default is not None:
                out[key] = default
    if o.get("max_jump") is not None:
        try:
            out["max_jump"] = float(o["max_jump"])
        except (TypeError, ValueError):
            pass
    elif out.get("until_limit"):
        out["max_jump"] = 4000.0
    if o.get("max_stretch") is not None:
        try:
            out["max_stretch"] = float(o["max_stretch"])
        except (TypeError, ValueError):
            pass
    elif out.get("until_limit"):
        out["max_stretch"] = 32.0
    if o.get("nn_floor") is not None:
        try:
            out["nn_floor"] = float(o["nn_floor"])
        except (TypeError, ValueError):
            out["nn_floor"] = 36.0
    else:
        out["nn_floor"] = 36.0
    if o.get("min_angle_sep") is not None:
        try:
            out["min_angle_sep"] = float(o["min_angle_sep"])
        except (TypeError, ValueError):
            out["min_angle_sep"] = 35.0
    else:
        out["min_angle_sep"] = 35.0
    if "prefer_low_degree" in o:
        out["prefer_low_degree"] = _truthy_flag(o.get("prefer_low_degree"))
    elif out.get("until_limit"):
        out["prefer_low_degree"] = True
    if "bundle" in o:
        out["bundle"] = _truthy_flag(o.get("bundle"))
    elif "bundle_orbit" in o:
        out["bundle"] = _truthy_flag(o.get("bundle_orbit"))
    elif out.get("until_limit"):
        out["bundle"] = True
    if o.get("bundle_max") is not None:
        try:
            out["bundle_max"] = max(1, min(40, int(o["bundle_max"])))
        except (TypeError, ValueError):
            out["bundle_max"] = 10
    elif out.get("until_limit"):
        out["bundle_max"] = 10
    if "protect_rigid" in o:
        v = o["protect_rigid"]
        if isinstance(v, bool):
            out["protect_rigid"] = "portals" if v else "off"
        else:
            key = str(v).strip().lower()
            if key in {"0", "false", "no", "off", "none"}:
                out["protect_rigid"] = "off"
            elif key in {"1", "true", "yes", "on", "portals", "skeleton"}:
                out["protect_rigid"] = "portals"
            elif key in {"all", "full", "rigid"}:
                out["protect_rigid"] = "all"
            else:
                out["protect_rigid"] = key
    elif out.get("until_limit"):
        # Eye polish: freeze portals by default (unlike single-node / round).
        out["protect_rigid"] = "portals"
    else:
        # orbit breaks rigid by default (opt in with protect_rigid=portals).
        out["protect_rigid"] = "off"
    focus = o.get("focus_ids") or o.get("focus_node_ids")
    if isinstance(focus, list):
        out["focus_ids"] = [str(x).strip() for x in focus if str(x).strip()]
    # portal freeze from polish path / until_limit
    raw_p = o.get("portal_ids") or o.get("frozen_ids")
    if isinstance(raw_p, list):
        out["frozen_ids"] = {str(x) for x in raw_p if str(x)}
    elif isinstance(raw_p, set):
        out["frozen_ids"] = {str(x) for x in raw_p if str(x)}
    # freeze by layout layer and/or fabric numeric level
    flayers = o.get("freeze_layers")
    if flayers is None:
        flayers = o.get("frozen_layers")
    if flayers is not None:
        out["freeze_layers"] = _coerce_str_list(flayers)
    flevels = o.get("freeze_levels")
    if flevels is None:
        flevels = o.get("frozen_levels")
    if flevels is not None:
        out["freeze_levels"] = _coerce_str_list(flevels)
    # Layered y constraint (y_min/y_max): keep node within its layer band
    for yk in ("y_min", "y_max"):
        if o.get(yk) is not None:
            try:
                out[yk] = float(o[yk])
            except (TypeError, ValueError):
                pass
    # Multi-objective ranking: "crossing" (default) or "total"
    obj = str(o.get("objective") or "crossing").strip().lower()
    out["objective"] = "total" if obj in ("total", "score", "multi") else "crossing"
    # until_limit defaults: larger jump + more picks in the pool
    if out.get("until_limit"):
        if "max_jump" not in out:
            out["max_jump"] = 4000.0
        if "max_degree" not in o or o.get("max_degree") is None:
            out["max_degree"] = 14
        if "top_k" not in o or o.get("top_k") is None:
            out["top_k"] = 8
        if "cand_cap" not in o or o.get("cand_cap") is None:
            out["cand_cap"] = 360
        if "max_stretch" not in out:
            out["max_stretch"] = 32.0
        if "scan_cap" not in o or o.get("scan_cap") is None:
            out["scan_cap"] = 32
    return out


def orbit_lite_suggest(
    node: str,
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
    names: dict[str, str],
    *,
    max_jump: float = 360.0,
    angle_step: int = 45,
    top_k: int = 3,
    target_nn: float = 155.0,
) -> list[dict[str, Any]]:
    """Lightweight polar suggest for analyze sight (fewer samples)."""
    if node not in pos:
        return []
    # Build a tiny state-like eval without LayoutState.
    x0, y0 = pos[node]
    global0 = count_edge_crossings(pos, links)
    local0 = crossings_involving_node(node, pos, links, adj)
    jump = max(120.0, min(float(max_jump), 800.0))
    samples = _polar_grid(
        x0,
        y0,
        jump=jump,
        angle_step=angle_step,
        radii=[120.0, 200.0, 320.0],
    )
    samples.extend(_neighbor_guides(pos, node, adj, jump)[:12])
    scored: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for sx, sy, r, ang in samples:
        key = (int(round(sx)), int(round(sy)))
        if key in seen:
            continue
        seen.add(key)
        c = _eval_candidate(
            node,
            (sx, sy),
            r,
            ang,
            pos=pos,
            names=names,
            links=links,
            adj=adj,
            global0=global0,
            local0=local0,
            target_nn=target_nn,
            nn_floor=36.0,
            nbs_cap=jump * 2.5 if jump > 1200 else max(_MAX_FROM_NBS, jump * 1.25),
        )
        if c is not None:
            scored.append(c)
    scored.sort(key=_score_key)
    improving = [c for c in scored if c["delta"]["global"] < 0]
    pool = improving if improving else scored
    top = _diversify_top(pool, k=top_k, min_angle_sep=35.0)
    out: list[dict[str, Any]] = []
    for c in top:
        out.append(
            {
                "kind": f"orbit_r{int(c['r'])}_a{int(c['angle_deg'])}",
                "x": c["x"],
                "y": c["y"],
                "r": c["r"],
                "angle_deg": c["angle_deg"],
                "delta_crossings_est": c["delta"]["global"],
                "delta_incident": c["delta"]["incident"],
                "global_after": c["crossings"]["global"],
            }
        )
    return out
