"""Atomic transforms: pin select, regional scale, pack util, resolve overlaps."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from netx_topology_mcp.layout_metrics import (
    ICON_SIZE,
    REC_CENTER_DX,
    REC_CENTER_DY,
    node_footprint,
)
from netx_topology_mcp.layout_ops.graph_util import bbox
from netx_topology_mcp.layout_ops.scope import active_nodes, movable_nodes
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def select_pins(
    state: LayoutState,
    *,
    mode: str = "spine",
    complement_of_last: bool = False,
) -> OpResult:
    """Set pinned set by rule. Does not move positions."""
    st = state.copy()
    pos = st.positions
    if mode == "spine":
        pin = {
            n
            for n in pos
            if st.layers.get(n) in ("agg", "core") or n in st.spine
        }
    elif mode == "agg":
        pin = {n for n in pos if st.layers.get(n) in ("agg", "core")}
    elif mode == "high_degree":
        deg = sorted(pos.keys(), key=lambda n: (-len(st.adj.get(n, ())), st.names.get(n, n)))
        k = max(3, len(pos) // 10)
        pin = set(deg[:k])
    elif mode == "bbox_quartile":
        if not pos:
            pin = set()
        else:
            x0, y0, x1, y1 = bbox(pos)
            mid_x = (x0 + x1) / 2
            # pin left half (already "stable" after lateral stretch)
            pin = {n for n, (x, _) in pos.items() if x <= mid_x}
    elif mode == "prev_moved":
        pin = set(st.last_moved)
    else:
        pin = set(st.pinned)

    if complement_of_last and st.last_moved:
        # pin everyone except last_moved (stabilize what we just placed)
        pin = set(pos.keys()) - set(st.last_moved)

    st.pinned = pin
    return OpResult(
        state=st,
        moved=set(),
        op="select_pins",
        params={"mode": mode, "complement_of_last": complement_of_last, "pinned_n": len(pin)},
        note=f"pins={len(pin)} mode={mode}",
    )


def scale_region(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    sx: float | None = None,
    sy: float | None = None,
    factor: float | None = None,
    node_ids: set[str] | None = None,
    only_unpinned: bool = True,
    anchor: str = "centroid",
) -> OpResult:
    """Scale a subset of nodes about an anchor. Pinned stay fixed when only_unpinned."""
    params = params or LayoutParams()
    st = state.copy()
    pos = dict(st.positions)
    if factor is not None:
        sx = sy = float(factor)
    sx = 1.0 if sx is None else float(sx)
    sy = 1.0 if sy is None else float(sy)
    if abs(sx - 1.0) < 1e-12 and abs(sy - 1.0) < 1e-12:
        return OpResult(state=st, moved=set(), op="scale_region", note="noop")

    if node_ids is not None:
        targets = {n for n in node_ids if n in pos}
    else:
        targets = active_nodes(st)
    if only_unpinned:
        targets = {n for n in targets if n not in st.pinned}
        # hard anchors never move
        targets = {n for n in targets if st.layers.get(n) not in ("agg", "core")}
    if not targets:
        return OpResult(state=st, moved=set(), op="scale_region", note="no targets")

    if anchor == "origin":
        ax, ay = 0.0, 0.0
    else:
        ax = sum(pos[n][0] for n in targets) / len(targets)
        ay = sum(pos[n][1] for n in targets) / len(targets)

    moved: set[str] = set()
    for n in targets:
        x, y = pos[n]
        pos[n] = (ax + (x - ax) * sx, ay + (y - ay) * sy)
        moved.add(n)
    st.positions = pos
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="scale_region",
        params={"sx": sx, "sy": sy, "moved_n": len(moved), "only_unpinned": only_unpinned},
        note=f"scaled {len(moved)} nodes sx={sx} sy={sy}",
    )


def scale_edge_axes(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """Anisotropic stretch so median |dx|/|dy| of edges approach pitch/side."""
    params = params or LayoutParams()
    st = state.copy()
    pos = st.positions
    dxs = [
        abs(pos[a][0] - pos[b][0])
        for a, b in st.links
        if a in pos and b in pos and abs(pos[a][0] - pos[b][0]) > 1e-6
    ]
    dys = [
        abs(pos[a][1] - pos[b][1])
        for a, b in st.links
        if a in pos and b in pos and abs(pos[a][1] - pos[b][1]) > 1e-6
    ]

    def _med(vals: list[float]) -> float:
        vals = sorted(vals)
        return vals[len(vals) // 2] if vals else 0.0

    med_dx, med_dy = _med(dxs), _med(dys)
    sx = sy = 1.0
    if 1e-6 < med_dx < params.pitch:
        sx = min(params.pitch / med_dx, 1.8)
    if 1e-6 < med_dy < params.side:
        sy = min(params.side / med_dy, 1.8)
    out = scale_region(
        st,
        params,
        sx=sx,
        sy=sy,
        node_ids=active_nodes(st),
        only_unpinned=False,
        anchor="centroid",
    )
    out.op = "scale_edge_axes"
    out.params = {**out.params, "med_dx": round(med_dx, 2), "med_dy": round(med_dy, 2)}
    out.note = f"edge-axis stretch sx={sx:.3f} sy={sy:.3f}"
    return out


def compress_long_edges(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    edge_len_cap: float | None = None,
    pull: float = 0.45,
    iters: int = 4,
    frozen: set[str] | None = None,
) -> OpResult:
    """Shorten edges longer than ``edge_len_cap`` by walking ends toward mid.

    This is the densify that works when nn is already sweet but bbox is huge
    from metro bridges (uniform pack cannot raise util without crushing nn).
    Frozen nodes (e.g. a small portal set) stay put; the other end takes the
    full pull.
    """
    params = params or LayoutParams()
    st = state.copy()
    pos = {n: (float(p[0]), float(p[1])) for n, p in st.positions.items()}
    freeze = set(frozen or ())
    cap = float(edge_len_cap) if edge_len_cap is not None else max(
        700.0, float(params.target_nn) * 4.5
    )
    pull = max(0.05, min(float(pull), 0.9))
    iters = max(1, int(iters))
    moved: set[str] = set()
    shortened = 0

    def _util() -> float:
        ids = list(active_nodes(st))
        if len(ids) < 2:
            return 0.0
        sub = {n: pos[n] for n in ids if n in pos}
        x0, y0, x1, y1 = bbox(sub)
        return len(ids) * REC_CENTER_DX * REC_CENTER_DY / max((x1 - x0) * (y1 - y0), 1e-6)

    util_before = _util()
    for _ in range(iters):
        progress = 0
        for a, b in list(st.links):
            if a not in pos or b not in pos:
                continue
            ax, ay = pos[a]
            bx, by = pos[b]
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy)
            if L <= cap + 1e-6:
                continue
            mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
            # Desired length after this step.
            target = L - pull * (L - cap)
            target = max(cap, target)
            if target >= L - 1e-6:
                continue
            half = 0.5 * target
            ux, uy = dx / L, dy / L
            new_a = (mx - ux * half, my - uy * half)
            new_b = (mx + ux * half, my + uy * half)
            a_fr, b_fr = a in freeze, b in freeze
            if a_fr and b_fr:
                continue
            trial = dict(pos)
            if a_fr and not b_fr:
                # Keep a; place b on the ray at distance target.
                trial[b] = (ax + ux * target, ay + uy * target)
            elif b_fr and not a_fr:
                trial[a] = (bx - ux * target, by - uy * target)
            else:
                trial[a], trial[b] = new_a, new_b
            movers = {n for n in (a, b) if trial[n] != pos[n]}
            # Reject if a mover lands too close to another center (avoid ov).
            min_gap = max(80.0, float(params.min_center_gap) * 0.55)
            too_close = False
            for n in movers:
                nx, ny = trial[n]
                for m, (mx2, my2) in trial.items():
                    if m == n:
                        continue
                    if math.hypot(nx - mx2, ny - my2) < min_gap:
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                continue
            for n in movers:
                pos[n] = trial[n]
                moved.add(n)
            shortened += 1
            progress += 1
        if progress == 0:
            break

    st.positions = pos
    st.last_moved = moved
    util_after = _util()
    return OpResult(
        state=st,
        moved=moved,
        op="compress_long_edges",
        params={
            "edge_len_cap": round(cap, 1),
            "pull": round(pull, 3),
            "iters": iters,
            "shortened": shortened,
            "moved_n": len(moved),
            "util_before": round(util_before, 4),
            "util_after": round(util_after, 4),
            "frozen_n": len(freeze),
        },
        note=(
            f"compress_long cap={cap:.0f} shortened={shortened} "
            f"util {util_before:.4f}→{util_after:.4f}"
        ),
    )


def pack_toward_portals(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    portal_ids: list[str] | set[str] | None = None,
    scale: float | None = None,
) -> OpResult:
    """Pull non-portal nodes toward nearest portal (shorten long metro spokes).

    Uniform bbox pack cannot raise util when nn is already in the sweet band —
    empty area lives in long corridors. This compresses leaves toward portals
    while leaving portal coordinates fixed (semi-rigid dual-unit style).
    """
    params = params or LayoutParams()
    st = state.copy()
    portals = {str(p) for p in (portal_ids or []) if str(p) and str(p) in st.positions}
    if len(portals) < 1:
        return OpResult(
            state=st, moved=set(), op="pack_toward_portals", note="no portals"
        )
    # Mild default: 0.82 keeps local clusters readable; caller may tighten.
    s = 0.82 if scale is None else float(scale)
    s = max(0.35, min(s, 0.98))
    targets = [n for n in active_nodes(st) if n not in portals]
    if not targets:
        return OpResult(
            state=st, moved=set(), op="pack_toward_portals", note="no leaves"
        )
    pos = dict(st.positions)
    moved: set[str] = set()
    for n in targets:
        x, y = pos[n]
        best_p = None
        best_d = None
        for p in portals:
            px, py = pos[p]
            d = math.hypot(x - px, y - py)
            if best_d is None or d < best_d:
                best_d = d
                best_p = p
        if best_p is None or best_d is None or best_d < 1e-6:
            continue
        px, py = pos[best_p]
        pos[n] = (px + (x - px) * s, py + (y - py) * s)
        moved.add(n)
    st.positions = pos
    st.last_moved = moved
    ids = list(active_nodes(st))
    sub = {n: pos[n] for n in ids}
    x0, y0, x1, y1 = bbox(sub)
    util = len(ids) * REC_CENTER_DX * REC_CENTER_DY / max((x1 - x0) * (y1 - y0), 1e-6)
    return OpResult(
        state=st,
        moved=moved,
        op="pack_toward_portals",
        params={
            "scale": round(s, 4),
            "portal_n": len(portals),
            "moved_n": len(moved),
            "util_after": round(util, 4),
            "nn_p50_after": round(_nn_p50(pos, ids), 2),
        },
        note=f"portal-pack s={s:.2f} moved={len(moved)} util→{util:.4f}",
    )


def _nn_p50(pos: dict[str, tuple[float, float]], ids: list[str]) -> float:
    nns: list[float] = []
    for a in ids:
        ax, ay = pos[a]
        best = min(
            (math.hypot(ax - pos[b][0], ay - pos[b][1]) for b in ids if b != a),
            default=None,
        )
        if best is not None:
            nns.append(best)
    if not nns:
        return 0.0
    nns.sort()
    return float(nns[len(nns) // 2])


def pack_utilization(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """Shrink active scope toward centroid until util≈target (multi-iter).

    Each iter clamps scale to [pack_min_scale, 1] and also refuses to push
    nn_p50 below ``pack_nn_floor`` (default 140) — otherwise a sparse-but-
    well-spaced canvas gets crushed and fix_overlaps blows crossings.
    Use with resolve_overlaps afterwards when pack still leaves footprint hits.
    """
    params = params or LayoutParams()
    st = state.copy()
    targets = list(active_nodes(st))
    if len(targets) < 2:
        return OpResult(state=st, moved=set(), op="pack_utilization", note="too few")

    pos = dict(st.positions)
    moved: set[str] = set()
    util_before = None
    scales: list[float] = []
    iters = max(1, int(params.pack_iters))
    # Allow aggressive pack when caller sets pack_min_scale < 0.2 (MCP tune).
    floor = max(0.05, min(float(params.pack_min_scale), 0.95))
    nn_floor = max(40.0, float(getattr(params, "pack_nn_floor", 140.0) or 140.0))
    nn_capped = False

    for _ in range(iters):
        sub = {n: pos[n] for n in targets}
        x0, y0, x1, y1 = bbox(sub)
        bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        util = len(targets) * REC_CENTER_DX * REC_CENTER_DY / (bw * bh)
        if util_before is None:
            util_before = util
        if util >= params.target_util:
            break
        nn = _nn_p50(pos, targets)
        if nn <= nn_floor + 1e-6:
            # Already at/below floor — do not shrink further.
            nn_capped = True
            break
        s = math.sqrt(util / max(params.target_util, 1e-6))
        # Hard cap: never project nn_p50 below floor (similarity shrink).
        s_nn = nn_floor / nn
        if s < s_nn:
            nn_capped = True
            s = s_nn
        s = max(floor, min(s, 1.0))
        if s >= 0.999:
            break
        # Guard: refuse a step that would still undershoot the floor.
        if nn * s < nn_floor - 1e-3:
            nn_capped = True
            break
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        snap = {n: pos[n] for n in targets}
        for n in targets:
            x, y = pos[n]
            pos[n] = (cx + (x - cx) * s, cy + (y - cy) * s)
            moved.add(n)
        nn_step = _nn_p50(pos, targets)
        if nn_step + 1e-6 < nn_floor:
            # Revert step — nn metric can drop faster than uniform scale predicts.
            for n, p in snap.items():
                pos[n] = p
            nn_capped = True
            break
        scales.append(round(s, 4))
        if nn_capped:
            break

    st.positions = pos
    st.last_moved = moved
    sub = {n: pos[n] for n in targets}
    x0, y0, x1, y1 = bbox(sub)
    util_after = len(targets) * REC_CENTER_DX * REC_CENTER_DY / max((x1 - x0) * (y1 - y0), 1e-6)
    nn_after = _nn_p50(pos, targets)
    return OpResult(
        state=st,
        moved=moved,
        op="pack_utilization",
        params={
            "scales": scales,
            "util_before": round(float(util_before or 0.0), 4),
            "util_after": round(util_after, 4),
            "target_util": params.target_util,
            "nn_p50_after": round(nn_after, 2),
            "pack_nn_floor": nn_floor,
            "nn_capped": nn_capped,
            "scope_n": len(targets),
        },
        note=(
            (
                f"pack iters={len(scales)} util {util_before:.4f}→{util_after:.4f}"
                + (" (nn floor)" if nn_capped else "")
            )
            if scales
            else (
                f"nn floor blocks pack (nn≈{nn_after:.1f})"
                if nn_capped
                else f"util already ok ({util_before:.4f})"
            )
        ),
    )


def _footprints(
    pos: dict[str, tuple[float, float]], names: dict[str, str]
) -> dict[str, tuple[float, float, float, float]]:
    out = {}
    for n, (x, y) in pos.items():
        fx0, fy0, fx1, fy1 = node_footprint(names.get(n, n))
        out[n] = (x + fx0, y + fy0, x + fx1, y + fy1)
    return out


def resolve_overlaps(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    mode: str = "lateral",
) -> OpResult:
    """Push movable nodes to clear footprint overlaps.

    mode=lateral (default): project pushes onto ±X (corridor sides) to preserve
    Tutte chain crossings. mode=free: isotropic push (can raise crossings a lot).
    Hard-pinned = agg/core.
    """
    params = params or LayoutParams()
    st = state.copy()
    pos = {n: [float(p[0]), float(p[1])] for n, p in st.positions.items()}
    ids = list(pos)
    # Move only scoped non-agg/core; outsiders are frozen obstacles.
    can_move = movable_nodes(st, respect_pins=False)
    # Spine = accepted pure-chain units; keep them atomic during unstick.
    hard = {n for n in ids if st.layers.get(n) in ("agg", "core")} | set(st.spine)
    pinned_soft = set(st.pinned) - hard
    moved: set[str] = set()

    for _ in range(params.overlap_iters):
        fps = _footprints({n: (pos[n][0], pos[n][1]) for n in ids}, st.names)
        cell = max(REC_CENTER_DX, 80.0)
        buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
        for n in ids:
            ax0, ay0, ax1, ay1 = fps[n]
            cx = int(((ax0 + ax1) / 2) // cell)
            cy = int(((ay0 + ay1) / 2) // cell)
            buckets[(cx, cy)].append(n)

        hits = 0
        for n in ids:
            if n not in can_move:
                continue
            # Prefer moving non-spine; spine only if still overlapping
            ax0, ay0, ax1, ay1 = fps[n]
            cx = int(((ax0 + ax1) / 2) // cell)
            cy = int(((ay0 + ay1) / 2) // cell)
            fx = fy = 0.0
            local_hits = 0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for m in buckets[(cx + dx, cy + dy)]:
                        if m == n:
                            continue
                        bx0, by0, bx1, by1 = fps[m]
                        if not (ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0):
                            continue
                        local_hits += 1
                        mx = (ax0 + ax1) / 2 - (bx0 + bx1) / 2
                        my = (ay0 + ay1) / 2 - (by0 + by1) / 2
                        if abs(mx) + abs(my) < 1e-9:
                            mx = 1.0 if (hash(n) & 1) else -1.0
                            my = 0.0
                        ox = min(ax1, bx1) - max(ax0, bx0)
                        oy = min(ay1, by1) - max(ay0, by0)
                        push = 0.5 * max(ox, oy, ICON_SIZE)
                        if m in hard:
                            push *= 2.0
                        L = math.hypot(mx, my) or 1.0
                        ux, uy = mx / L, my / L
                        if mode == "lateral":
                            # keep chain Y; separate in X (+ small Y for label)
                            fx += math.copysign(push, ux if abs(ux) > 1e-9 else 1.0)
                            fy += 0.25 * push if uy >= 0 else -0.15 * push
                        else:
                            fx += ux * push
                            fy += uy * push
            if local_hits == 0:
                continue
            if n in pinned_soft and mode == "lateral" and local_hits < 2:
                # spine lightly held unless multi-overlap
                continue
            hits += local_hits
            L = math.hypot(fx, fy)
            if L > params.overlap_step:
                fx *= params.overlap_step / L
                fy *= params.overlap_step / L
            if L > 1e-9:
                pos[n][0] += fx
                pos[n][1] += fy
                moved.add(n)
        if hits == 0:
            break

    st.positions = {n: (pos[n][0], pos[n][1]) for n in ids}
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="resolve_overlaps",
        params={"iters": params.overlap_iters, "moved_n": len(moved), "mode": mode},
        note=f"unstick mode={mode} moved={len(moved)} scope={len(can_move)}",
    )


def explode_clusters(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    thr: float | None = None,
    gap: float | None = None,
    axis: str = "along",
) -> OpResult:
    """Spread near-coincident clusters (generic atom).

    Union nodes with center dist ≤ thr; lay cluster on a short line.
    axis=along: parallel to average neighbor edge (usually fewer new crossings).
    axis=perp: side-lane / normal direction.
    Only moves nodes in current scope (non agg/core).
    """
    params = params or LayoutParams()
    thr = params.cluster_thr if thr is None else float(thr)
    gap = params.cluster_gap if gap is None else float(gap)
    st = state.copy()
    pos = dict(st.positions)
    ids = list(pos)
    if len(ids) < 2:
        return OpResult(state=st, moved=set(), op="explode_clusters", note="too few")

    can_move = movable_nodes(st, respect_pins=False)
    hard = {n for n in ids if st.layers.get(n) in ("agg", "core")}
    parent = {n: n for n in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def uni(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    cell = max(thr, 1.0)
    buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    for n, (x, y) in pos.items():
        buckets[(int(x // cell), int(y // cell))].append(n)
    for n, (x, y) in pos.items():
        cx, cy = int(x // cell), int(y // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in buckets[(cx + dx, cy + dy)]:
                    if m <= n:
                        continue
                    if math.hypot(x - pos[m][0], y - pos[m][1]) <= thr:
                        uni(n, m)

    groups: dict[str, list[str]] = defaultdict(list)
    for n in ids:
        groups[find(n)].append(n)

    moved: set[str] = set()
    for grp in groups.values():
        if len(grp) < 2:
            continue
        grp = sorted(grp, key=lambda n: st.names.get(n, n))
        vx = vy = 0.0
        for n in grp:
            for m in st.adj.get(n, ()):
                if m not in pos:
                    continue
                dx = pos[m][0] - pos[n][0]
                dy = pos[m][1] - pos[n][1]
                if abs(dx) + abs(dy) < 1e-9:
                    continue
                if dx < 0:
                    dx, dy = -dx, -dy
                vx += dx
                vy += dy
        L = math.hypot(vx, vy)
        if L < 1e-9:
            ax, ay = 1.0, 0.0
        else:
            ax, ay = vx / L, vy / L
        if axis == "perp":
            tx, ty = -ay, ax
        else:
            tx, ty = ax, ay
        cx = sum(pos[n][0] for n in grp) / len(grp)
        cy = sum(pos[n][1] for n in grp) / len(grp)
        # Only explode clusters that touch the active scope; only move can_move.
        movable = [n for n in grp if n in can_move]
        if not movable:
            continue
        for i, n in enumerate(movable):
            off = (i - (len(movable) - 1) / 2) * gap
            pos[n] = (cx + tx * off, cy + ty * off)
            moved.add(n)

    st.positions = pos
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="explode_clusters",
        params={
            "thr": thr,
            "gap": gap,
            "axis": axis,
            "moved_n": len(moved),
            "scope_n": len(can_move),
        },
        note=f"exploded axis={axis} moved={len(moved)} scope={len(can_move)}",
    )


def enforce_min_gap(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    min_gap: float | None = None,
    iters: int | None = None,
) -> OpResult:
    """Push centers apart until euclidean nearest-neighbor ≥ min_gap (unpinned)."""
    params = params or LayoutParams()
    min_gap = params.min_center_gap if min_gap is None else float(min_gap)
    iters = params.overlap_iters if iters is None else int(iters)
    st = state.copy()
    pos = {n: [float(p[0]), float(p[1])] for n, p in st.positions.items()}
    ids = list(pos)
    can_move = movable_nodes(st, respect_pins=False)
    hard = {n for n in ids if st.layers.get(n) in ("agg", "core")}
    moved: set[str] = set()
    cell = max(min_gap, 1.0)
    step = params.overlap_step

    for _ in range(iters):
        buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
        for n in ids:
            buckets[(int(pos[n][0] // cell), int(pos[n][1] // cell))].append(n)
        hits = 0
        for n in ids:
            if n not in can_move:
                continue
            cx, cy = int(pos[n][0] // cell), int(pos[n][1] // cell)
            fx = fy = 0.0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for m in buckets[(cx + dx, cy + dy)]:
                        if m == n:
                            continue
                        vx = pos[n][0] - pos[m][0]
                        vy = pos[n][1] - pos[m][1]
                        d = math.hypot(vx, vy)
                        if d >= min_gap:
                            continue
                        hits += 1
                        if d < 1e-6:
                            ang = (hash(n) ^ hash(m)) % 360
                            rad = math.radians(float(ang))
                            vx, vy = math.cos(rad), math.sin(rad)
                            d = 1e-6
                        else:
                            vx /= d
                            vy /= d
                        push = (min_gap - d) * (1.0 if m in hard else 0.5)
                        fx += vx * push
                        fy += vy * push
            L = math.hypot(fx, fy)
            if L > step:
                fx *= step / L
                fy *= step / L
            if L > 1e-9:
                pos[n][0] += fx
                pos[n][1] += fy
                moved.add(n)
        if hits == 0:
            break

    st.positions = {n: (pos[n][0], pos[n][1]) for n in ids}
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="enforce_min_gap",
        params={"min_gap": min_gap, "moved_n": len(moved)},
        note=f"min_gap={min_gap} moved={len(moved)}",
    )


def soft_nn_scale(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """Capped isotropic scale by nn_p50 toward target_nn (active scope)."""
    params = params or LayoutParams()
    st = state.copy()
    ids = list(active_nodes(st))
    pos = st.positions
    if len(ids) < 2:
        return OpResult(state=st, moved=set(), op="soft_nn_scale", note="too few")
    nns = []
    for a in ids:
        ax, ay = pos[a]
        best = min(
            (math.hypot(ax - pos[b][0], ay - pos[b][1]) for b in ids if b != a),
            default=None,
        )
        if best is not None:
            nns.append(best)
    nns.sort()
    nn = nns[len(nns) // 2] if nns else 0.0
    if nn <= 1e-9 or nn >= params.target_nn:
        return OpResult(
            state=st,
            moved=set(),
            op="soft_nn_scale",
            params={"nn_p50": round(nn, 2), "scope_n": len(ids)},
            note="no scale",
        )
    s = min(params.target_nn / nn, params.scale_cap)
    out = scale_region(
        st, params, factor=s, node_ids=set(ids), only_unpinned=False, anchor="centroid"
    )
    out.op = "soft_nn_scale"
    out.params = {
        **out.params,
        "nn_p50_before": round(nn, 2),
        "scale": round(s, 3),
        "scope_n": len(ids),
    }
    out.note = f"nn soft scale s={s:.3f} scope={len(ids)}"
    return out


def normalize_origin(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """Translate so min corner is at margin."""
    params = params or LayoutParams()
    st = state.copy()
    if not st.positions:
        return OpResult(state=st, moved=set(), op="normalize_origin", note="empty")
    x0, y0, _, _ = bbox(st.positions)
    m = params.margin
    pos = {
        n: (x - x0 + m, y - y0 + m) for n, (x, y) in st.positions.items()
    }
    st.positions = pos
    st.last_moved = set(pos.keys())
    return OpResult(
        state=st,
        moved=st.last_moved,
        op="normalize_origin",
        params={"margin": m},
        note="origin+margin",
    )


OPS: dict[str, Any] = {
    "select_pins": select_pins,
    "scale_region": scale_region,
    "scale_edge_axes": scale_edge_axes,
    "pack_utilization": pack_utilization,
    "resolve_overlaps": resolve_overlaps,
    "explode_clusters": explode_clusters,
    "enforce_min_gap": enforce_min_gap,
    "soft_nn_scale": soft_nn_scale,
    "normalize_origin": normalize_origin,
}
