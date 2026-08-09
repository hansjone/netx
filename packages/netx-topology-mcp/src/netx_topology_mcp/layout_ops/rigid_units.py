"""Treat dual-unit / staging sub-regions as rigid bodies after compose.

Internal relative geometry of a unit must not be broken by per-node untangle.
Polish only: translate whole groups, or rotate about shared portal pivots.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.graph_util import connected_components
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

# Reject / ignore coords that would blow util (phantom region markers, bugs).
_COORD_ABS_MAX = 1.0e6


def _exclusive_unit_islands(
    groups: list[dict[str, Any]],
    valid: set[str],
    *,
    min_island: int,
) -> tuple[list[list[str]], dict[str, int], set[str]]:
    """Build rigid islands from dual-unit exclusive members (not shared portals)."""
    counts: dict[str, int] = defaultdict(int)
    parsed: list[list[str]] = []
    for g in groups:
        members = [str(n) for n in (g.get("node_ids") or []) if str(n) in valid]
        if len(members) < 2:
            continue
        for n in members:
            counts[n] += 1
        parsed.append(members)
    shared = {n for n, c in counts.items() if c > 1}
    comps: list[list[str]] = []
    node_comp: dict[str, int] = {}
    for members in parsed:
        exclusive = [n for n in members if n not in shared]
        if len(exclusive) < min_island:
            continue
        cid = len(comps)
        comps.append(exclusive)
        for n in exclusive:
            if n not in node_comp:
                node_comp[n] = cid
    return comps, node_comp, shared


def shrink_long_corridors(
    state: LayoutState,
    *,
    edge_len_cap: float = 1600.0,
    pull: float = 0.55,
    iters: int = 4,
    max_bridges: int = 12,
    min_island: int = 6,
    groups: list[dict[str, Any]] | None = None,
    accept_crossings: bool = True,
) -> OpResult:
    """Partially-rigid densify: pull islands together along long bridges.

    Island definition:
    - with ``groups`` (dual-unit membership): exclusive members per unit;
    - otherwise: short-edge connected components (``edge_len_cap``).

    Only the longest bridge **per island-pair** is used. When
    ``accept_crossings`` is set, each island translation is kept only if global
    crossings do not rise by more than a small slack.
    """
    st = state.copy()
    pos = {n: (float(p[0]), float(p[1])) for n, p in st.positions.items()}
    valid = {
        n
        for n, (x, y) in pos.items()
        if abs(x) <= _COORD_ABS_MAX
        and abs(y) <= _COORD_ABS_MAX
        and math.isfinite(x)
        and math.isfinite(y)
    }
    if len(valid) < 2 or not st.links:
        return OpResult(
            state=st, moved=set(), op="shrink_long_corridors", note="noop"
        )

    cap = max(400.0, float(edge_len_cap))
    pull = max(0.1, min(float(pull), 0.9))
    iters = max(1, int(iters))
    max_bridges = max(2, min(int(max_bridges), 24))
    min_island = max(2, int(min_island))
    use_units = bool(groups)

    xs0 = [pos[n][0] for n in valid]
    ys0 = [pos[n][1] for n in valid]
    area0 = max(max(xs0) - min(xs0), 1e-6) * max(max(ys0) - min(ys0), 1e-6)

    moved: set[str] = set()
    bridges_used = 0
    bridges_rejected = 0
    island_n = 0
    mode = "unit_exclusive" if use_units else "short_edge_cc"

    x0 = count_edge_crossings(pos, st.links) if accept_crossings else 0

    for _ in range(iters):
        comps: list[list[str]]
        node_comp: dict[str, int]
        if use_units:
            comps, node_comp, _shared = _exclusive_unit_islands(
                groups or [], valid, min_island=min_island
            )
            island_n = len(comps)
            if island_n < 2:
                break
            sizes = [len(c) for c in comps]
            bridges: list[tuple[float, str, str]] = []
            for a, b in st.links:
                if a not in valid or b not in valid:
                    continue
                ca, cb = node_comp.get(a), node_comp.get(b)
                # Portal/shared ends: map to nearest exclusive island via membership.
                if ca is None or cb is None or ca == cb:
                    continue
                ax, ay = pos[a]
                bx, by = pos[b]
                L = math.hypot(bx - ax, by - ay)
                if L > cap:
                    bridges.append((L, a, b))
            # Also pull by exclusive-centroid separation when no direct exclusive edge.
            for i in range(len(comps)):
                if not comps[i]:
                    continue
                cxi = sum(pos[n][0] for n in comps[i]) / len(comps[i])
                cyi = sum(pos[n][1] for n in comps[i]) / len(comps[i])
                for j in range(i + 1, len(comps)):
                    if not comps[j]:
                        continue
                    cxj = sum(pos[n][0] for n in comps[j]) / len(comps[j])
                    cyj = sum(pos[n][1] for n in comps[j]) / len(comps[j])
                    L = math.hypot(cxj - cxi, cyj - cyi)
                    if L <= cap * 1.25:
                        continue
                    # Synthetic bridge endpoints = closest pair of exclusives.
                    best = None
                    best_d = -1.0
                    # Cap pair scan for large islands.
                    ai = comps[i][:48]
                    bj = comps[j][:48]
                    for a in ai:
                        ax, ay = pos[a]
                        for b in bj:
                            d = math.hypot(pos[b][0] - ax, pos[b][1] - ay)
                            if d > best_d:
                                best_d = d
                                best = (d, a, b)
                    if best is not None and best[0] > cap:
                        bridges.append(best)
        else:
            short_adj: dict[str, set[str]] = defaultdict(set)
            bridges = []
            for a, b in st.links:
                if a not in valid or b not in valid:
                    continue
                ax, ay = pos[a]
                bx, by = pos[b]
                L = math.hypot(bx - ax, by - ay)
                if L <= cap:
                    short_adj[a].add(b)
                    short_adj[b].add(a)
                else:
                    bridges.append((L, a, b))
            if not bridges:
                break
            comps = connected_components(valid, short_adj)
            island_n = len(comps)
            sizes = [len(c) for c in comps]
            node_comp = {}
            for i, comp in enumerate(comps):
                for n in comp:
                    node_comp[n] = i

        if not bridges:
            break
        # Longest bridge per unordered island pair; skip tiny islands.
        best_pair: dict[tuple[int, int], tuple[float, str, str, int, int]] = {}
        for L, a, b in bridges:
            ca, cb = node_comp.get(a), node_comp.get(b)
            if ca is None or cb is None or ca == cb:
                continue
            if sizes[ca] < min_island or sizes[cb] < min_island:
                continue
            key = (ca, cb) if ca < cb else (cb, ca)
            prev = best_pair.get(key)
            if prev is None or L > prev[0]:
                best_pair[key] = (L, a, b, ca, cb)
        cross = sorted(best_pair.values(), reverse=True)[:max_bridges]
        if not cross:
            break

        # Apply one pair at a time when accepting crossings; else batch.
        if not accept_crossings:
            # batch path uses all pairs once
            disp: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
            for L, a, b, ca, cb in cross:
                ax, ay = pos[a]
                bx, by = pos[b]
                ux, uy = (bx - ax) / L, (by - ay) / L
                move = 0.5 * pull * (L - cap)
                if move < 1.0:
                    continue
                disp[ca][0] += ux * move
                disp[ca][1] += uy * move
                disp[ca][2] += 1.0
                disp[cb][0] -= ux * move
                disp[cb][1] -= uy * move
                disp[cb][2] += 1.0
                bridges_used += 1
            if not disp:
                break
            progress = False
            for cid, (sx, sy, c) in disp.items():
                if c <= 0:
                    continue
                dx, dy = sx / c, sy / c
                if abs(dx) < 0.5 and abs(dy) < 0.5:
                    continue
                progress = True
                for n in comps[cid]:
                    x, y = pos[n]
                    pos[n] = (x + dx, y + dy)
                    moved.add(n)
            if not progress:
                break
            continue

        progress = False
        for L, a, b, ca, cb in cross:
            ax, ay = pos[a]
            bx, by = pos[b]
            ux, uy = (bx - ax) / L, (by - ay) / L
            move = 0.5 * pull * (L - cap)
            if move < 1.0:
                continue
            dx_a, dy_a = ux * move, uy * move
            dx_b, dy_b = -ux * move, -uy * move
            snap_a = {n: pos[n] for n in comps[ca]}
            snap_b = {n: pos[n] for n in comps[cb]}
            for n in comps[ca]:
                x, y = pos[n]
                pos[n] = (x + dx_a, y + dy_a)
            for n in comps[cb]:
                x, y = pos[n]
                pos[n] = (x + dx_b, y + dy_b)
            if accept_crossings:
                x1 = count_edge_crossings(pos, st.links)
                # Metro dual-units often need a bit of slack; polish afterwards.
                slack = max(20, int(x0 * 0.12))
                if x1 > x0 + slack:
                    for n, p in snap_a.items():
                        pos[n] = p
                    for n, p in snap_b.items():
                        pos[n] = p
                    bridges_rejected += 1
                    continue
                x0 = x1
            for n in comps[ca]:
                moved.add(n)
            for n in comps[cb]:
                moved.add(n)
            bridges_used += 1
            progress = True
        if not progress:
            break

    st.positions = pos
    st.last_moved = moved
    xs1 = [pos[n][0] for n in valid]
    ys1 = [pos[n][1] for n in valid]
    area1 = max(max(xs1) - min(xs1), 1e-6) * max(max(ys1) - min(ys1), 1e-6)
    area_ratio = area0 / area1
    return OpResult(
        state=st,
        moved=moved,
        op="shrink_long_corridors",
        params={
            "edge_len_cap": round(cap, 1),
            "pull": round(pull, 3),
            "iters": iters,
            "bridges_applied": bridges_used,
            "bridges_rejected": bridges_rejected,
            "islands": island_n,
            "min_island": min_island,
            "island_mode": mode,
            "moved_n": len(moved),
            "bbox_area_ratio": round(area_ratio, 3),
        },
        note=(
            f"shrink_corridors[{mode}] cap={cap:.0f} islands={island_n} "
            f"bridges={bridges_used} rej={bridges_rejected} area×{area_ratio:.2f}"
        ),
    )


def groups_from_compose_meta(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Read mass_groups (preferred) or rigid_groups written by compose."""
    if not meta:
        return []
    raw = meta.get("mass_groups") or meta.get("rigid_groups") or []
    out: list[dict[str, Any]] = []
    for g in raw:
        if not isinstance(g, dict):
            continue
        nodes = [str(x) for x in (g.get("node_ids") or []) if str(x)]
        if len(nodes) < 2:
            continue
        pivots = [str(x) for x in (g.get("pivots") or []) if str(x)]
        out.append(
            {
                "key": str(g.get("key") or ""),
                "node_ids": nodes,
                "pivots": pivots,
                "cores": [str(x) for x in (g.get("cores") or pivots) if str(x)],
                "soft": bool(g.get("soft", bool(meta.get("soft")))),
            }
        )
    return out


def _coord_ok(pos: dict[str, tuple[float, float]]) -> bool:
    for x, y in pos.values():
        if abs(x) > _COORD_ABS_MAX or abs(y) > _COORD_ABS_MAX:
            return False
        if not math.isfinite(x) or not math.isfinite(y):
            return False
    return True


def _apply_rigid(
    pos: dict[str, tuple[float, float]],
    members: list[str],
    *,
    pivot: tuple[float, float] | None,
    dx: float,
    dy: float,
    angle: float,
) -> dict[str, tuple[float, float]]:
    """Rotate members about pivot (or centroid), then translate."""
    pts = [pos[n] for n in members if n in pos]
    if not pts:
        return pos
    if pivot is None:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
    else:
        cx, cy = pivot
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    out = dict(pos)
    for n in members:
        if n not in out:
            continue
        x, y = out[n]
        rx = (x - cx) * cos_a - (y - cy) * sin_a
        ry = (x - cx) * sin_a + (y - cy) * cos_a
        out[n] = (cx + rx + dx, cy + ry + dy)
    return out


def _reflect_about_axis(
    pos: dict[str, tuple[float, float]],
    members: list[str],
    p0: tuple[float, float],
    p1: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    """Reflect members across the line through p0→p1 (2-portal flip)."""
    ax, ay = p0
    bx, by = p1
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return dict(pos)
    out = dict(pos)
    for n in members:
        if n not in out:
            continue
        x, y = out[n]
        # projection of (x-a) onto axis
        t = ((x - ax) * dx + (y - ay) * dy) / L2
        px, py = ax + t * dx, ay + t * dy
        out[n] = (2 * px - x, 2 * py - y)
    return out


def _bbox(pos: dict[str, tuple[float, float]], members: list[str]) -> tuple[float, float, float, float] | None:
    pts = [pos[n] for n in members if n in pos]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _bboxes_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float = 80.0,
) -> bool:
    return not (
        a[2] + pad < b[0]
        or b[2] + pad < a[0]
        or a[3] + pad < b[1]
        or b[3] + pad < a[1]
    )


def _exclusive_members(g: dict[str, Any]) -> list[str]:
    piv = set(g.get("pivots") or [])
    return [n for n in (g.get("node_ids") or []) if n not in piv]


def _group_bbox_overlap_count(
    pos: dict[str, tuple[float, float]],
    groups: list[dict[str, Any]],
    *,
    pad: float = 40.0,
) -> int:
    """How many exclusive-bbox pairs from different groups collide."""
    boxes: list[tuple[float, float, float, float]] = []
    for g in groups:
        excl = [n for n in _exclusive_members(g) if n in pos]
        bb = _bbox(pos, excl)
        if bb:
            boxes.append(bb)
    n = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _bboxes_overlap(boxes[i], boxes[j], pad=pad):
                n += 1
    return n


def _cost(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    groups: list[dict[str, Any]],
) -> tuple[int, int]:
    """Lexicographic: crossings first, then exclusive bbox overlaps."""
    if not _coord_ok(pos):
        return (10**9, 10**9)
    return (
        count_edge_crossings(pos, links),
        _group_bbox_overlap_count(pos, groups),
    )


def rigid_fan_out_portals(
    pos: dict[str, tuple[float, float]],
    groups: list[dict[str, Any]],
    links: list[tuple[str, str]],
    *,
    crossing_budget: int = 40,
) -> tuple[dict[str, tuple[float, float]], int]:
    """Space single-pivot units around each shared portal by discrete angles.

    When several eyes glue on the same portal they stack; assign angular
    sectors so exclusive hulls fan out. Never moves the portal itself.

    Optimizes exclusive-bbox overlaps first; may spend up to ``crossing_budget``
    extra crossings vs the pre-fan baseline (otherwise stacked eyes never move).
    """
    out = dict(pos)
    accepted = 0
    baseline_x = count_edge_crossings(out, links) if _coord_ok(out) else 10**9
    by_pivot: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in groups:
        pivots = [p for p in (g.get("pivots") or []) if p in out]
        if len(pivots) == 1:
            by_pivot[pivots[0]].append(g)

    # Absolute target headings for the unit centroid about the portal.
    target_angles = [i * (math.pi / 6) for i in range(12)]

    for pivot_id, gs in by_pivot.items():
        if len(gs) < 2:
            continue
        # Largest first stays; rotate smaller ones into free sectors.
        ordered = sorted(gs, key=lambda g: -len(g.get("node_ids") or []))
        reserved: list[float] = []
        # Record current heading of the keeper.
        keep = ordered[0]
        keep_excl = [n for n in _exclusive_members(keep) if n in out]
        if keep_excl and pivot_id in out:
            cx = sum(out[n][0] for n in keep_excl) / len(keep_excl)
            cy = sum(out[n][1] for n in keep_excl) / len(keep_excl)
            px, py = out[pivot_id]
            reserved.append(math.atan2(cy - py, cx - px))

        for g in ordered[1:]:
            members = [n for n in g["node_ids"] if n in out]
            excl = [n for n in _exclusive_members(g) if n in out]
            if len(members) < 2 or not excl or pivot_id not in out:
                continue
            pivot_xy = out[pivot_id]
            cx = sum(out[n][0] for n in excl) / len(excl)
            cy = sum(out[n][1] for n in excl) / len(excl)
            cur_heading = math.atan2(cy - pivot_xy[1], cx - pivot_xy[0])
            cur_ov = _group_bbox_overlap_count(out, groups)
            cur_x = count_edge_crossings(out, links)
            best: tuple[tuple[int, int, float], dict[str, tuple[float, float]], float] | None = (
                None
            )
            for tgt in target_angles:
                # Prefer sectors away from already-reserved headings.
                if any(_angle_delta(tgt, r) < (math.pi / 12) for r in reserved):
                    continue
                delta = tgt - cur_heading
                trial = _apply_rigid(
                    out, members, pivot=pivot_xy, dx=0.0, dy=0.0, angle=delta
                )
                trial[pivot_id] = out[pivot_id]
                if not _coord_ok(trial):
                    continue
                x1 = count_edge_crossings(trial, links)
                if x1 > baseline_x + crossing_budget and x1 > cur_x:
                    continue
                ov1 = _group_bbox_overlap_count(trial, groups)
                # Prefer lower exclusive-bbox overlaps; then crossings; then
                # separation from reserved headings.
                sep = min((_angle_delta(tgt, r) for r in reserved), default=math.pi)
                key = (ov1, x1, -sep)
                if ov1 < cur_ov or (ov1 == cur_ov and x1 < cur_x):
                    if best is None or key < best[0]:
                        best = (key, trial, tgt)
            if best is None:
                continue
            out = best[1]
            reserved.append(best[2])
            accepted += 1
    return out, accepted


def _angle_delta(a: float, b: float) -> float:
    d = abs(a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


def rigid_separate_groups(
    pos: dict[str, tuple[float, float]],
    groups: list[dict[str, Any]],
    *,
    pad: float = 120.0,
    max_iters: int = 12,
) -> dict[str, tuple[float, float]]:
    """Push free rigid groups (no shared pivots) apart by whole-group translate.

    Groups anchored on shared portals are left to rotate-about-pivot in
    ``rigid_untangle_groups`` — never translate exclusive nodes without the portal.
    """
    out = dict(pos)
    free = [g for g in groups if not (g.get("pivots") or [])]
    for _ in range(max_iters):
        moved = False
        boxes: list[tuple[dict[str, Any], tuple[float, float, float, float]]] = []
        for g in free:
            members = [n for n in g.get("node_ids") or [] if n in out]
            bb = _bbox(out, members)
            if bb:
                boxes.append((g, bb))
        # Separate free groups from each other only (not the full anchored hull —
        # that hull often swallows free slots and shove-walks them forever).
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                gi, bi = boxes[i]
                gj, bj = boxes[j]
                if not _bboxes_overlap(bi, bj, pad=pad):
                    continue
                area_i = (bi[2] - bi[0]) * (bi[3] - bi[1])
                area_j = (bj[2] - bj[0]) * (bj[3] - bj[1])
                g_move, b_move, b_other = (gi, bi, bj) if area_i <= area_j else (gj, bj, bi)
                cix = (b_move[0] + b_move[2]) / 2
                ciy = (b_move[1] + b_move[3]) / 2
                cjx = (b_other[0] + b_other[2]) / 2
                cjy = (b_other[1] + b_other[3]) / 2
                dx, dy = cix - cjx, ciy - cjy
                if abs(dx) + abs(dy) < 1e-6:
                    dx = 1.0
                overlap_x = min(b_move[2], b_other[2]) - max(b_move[0], b_other[0]) + pad
                overlap_y = min(b_move[3], b_other[3]) - max(b_move[1], b_other[1]) + pad
                if overlap_x <= 0 and overlap_y <= 0:
                    continue
                if overlap_x < overlap_y:
                    push = ((overlap_x if dx >= 0 else -overlap_x), 0.0)
                else:
                    push = (0.0, (overlap_y if dy >= 0 else -overlap_y))
                cap = 600.0
                push = (max(-cap, min(cap, push[0])), max(-cap, min(cap, push[1])))
                trial = dict(out)
                for n in g_move.get("node_ids") or []:
                    if n in trial:
                        x, y = trial[n]
                        trial[n] = (x + push[0], y + push[1])
                if not _coord_ok(trial):
                    continue
                out = trial
                moved = True
        if not moved:
            break
    return out


def rigid_untangle_groups(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    groups: list[dict[str, Any]] | None = None,
    max_rounds: int = 24,
    step: float = 280.0,
) -> OpResult:
    """Reduce crossings by rigid moves of whole sub-region groups only."""
    del params
    st = state.copy()
    meta = dict(st.meta or {})
    if groups is None:
        groups = groups_from_compose_meta(meta.get("compose_views"))
    if not groups:
        return OpResult(
            state=state,
            moved=set(),
            op="rigid_untangle",
            params={"groups": 0},
            note="no_rigid_groups",
        )

    # Drop absurd coords (phantom region nodes) from the working set.
    pos = {
        nid: xy
        for nid, xy in st.positions.items()
        if abs(xy[0]) <= _COORD_ABS_MAX
        and abs(xy[1]) <= _COORD_ABS_MAX
        and math.isfinite(xy[0])
        and math.isfinite(xy[1])
    }
    # Keep phantoms parked but do not let them drive cost.
    for nid, xy in st.positions.items():
        if nid not in pos:
            pos[nid] = (0.0, 0.0)

    links = list(st.links)
    cur_cost = _cost(pos, links, groups)
    start = cur_cost[0]
    moved_nodes: set[str] = set()
    accepted_moves = 0

    # 1) Fan-out stacked eyes about shared portals (overlap-aware).
    pos, fan_n = rigid_fan_out_portals(pos, groups, links)
    accepted_moves += fan_n
    if fan_n:
        moved_nodes.update(
            n
            for g in groups
            for n in (g.get("node_ids") or [])
            if n in pos
        )
    cur_cost = _cost(pos, links, groups)

    # Prefer moving freer groups first (fewer pivots / smaller).
    ordered = sorted(
        groups,
        key=lambda g: (len(g.get("pivots") or []), len(g.get("node_ids") or [])),
    )

    angles = (
        0.0,
        math.pi / 6,
        -math.pi / 6,
        math.pi / 4,
        -math.pi / 4,
        math.pi / 3,
        -math.pi / 3,
        math.pi / 2,
        -math.pi / 2,
        2 * math.pi / 3,
        -2 * math.pi / 3,
        3 * math.pi / 4,
        -3 * math.pi / 4,
        math.pi,
    )
    translations = [
        (0.0, 0.0),
        (step, 0.0),
        (-step, 0.0),
        (0.0, step),
        (0.0, -step),
        (step, step),
        (-step, step),
        (step, -step),
        (-step, -step),
        (step * 1.6, 0.0),
        (-step * 1.6, 0.0),
        (0.0, step * 1.6),
        (0.0, -step * 1.6),
    ]

    for _ in range(max(1, int(max_rounds))):
        improved = False
        for g in ordered:
            members = [n for n in g["node_ids"] if n in pos]
            if len(members) < 2:
                continue
            pivots = [p for p in (g.get("pivots") or []) if p in pos]
            if len(pivots) >= 2:
                # Fully locked except reflection about portal axis.
                cand_moves: list[tuple[float, float, float] | str] = ["flip"]
            elif len(pivots) == 1:
                pivot_xy = pos[pivots[0]]
                cand_moves = [
                    (0.0, 0.0, ang) for ang in angles if abs(ang) > 1e-12
                ]
            else:
                pivot_xy = None
                cand_moves = [
                    (dx, dy, ang)
                    for dx, dy in translations
                    for ang in ((0.0,) if (dx, dy) != (0.0, 0.0) else angles)
                ]

            best: tuple[tuple[int, int], dict[str, tuple[float, float]]] | None = None
            for move in cand_moves:
                if move == "flip":
                    p0, p1 = pos[pivots[0]], pos[pivots[1]]
                    trial = _reflect_about_axis(pos, members, p0, p1)
                    for p in pivots:
                        trial[p] = pos[p]
                else:
                    dx, dy, ang = move
                    if dx == 0 and dy == 0 and abs(ang) < 1e-12:
                        continue
                    trial = _apply_rigid(
                        pos,
                        members,
                        pivot=pivot_xy if len(pivots) == 1 else None,
                        dx=dx,
                        dy=dy,
                        angle=ang,
                    )
                    if pivots:
                        for p in pivots:
                            trial[p] = pos[p]
                c1 = _cost(trial, links, groups)
                if c1 < cur_cost and (best is None or c1 < best[0]):
                    best = (c1, trial)
            if best is None:
                continue
            pos = best[1]
            cur_cost = best[0]
            moved_nodes.update(members)
            accepted_moves += 1
            improved = True
        if not improved:
            break

    # Whole-group bbox separation (still rigid; no internal reshaping).
    before_sep = cur_cost
    sep = rigid_separate_groups(pos, groups, pad=140.0)
    sep_cost = _cost(sep, links, groups)
    if sep_cost <= before_sep:
        pos = sep
        cur_cost = sep_cost

    st.positions = pos
    st.meta = meta
    st.meta["rigid_untangle"] = {
        "start_crossings": start,
        "end_crossings": cur_cost[0],
        "end_group_bbox_overlaps": cur_cost[1],
        "groups": len(groups),
        "accepted_moves": accepted_moves,
        "fan_out_moves": fan_n,
        "moved_n": len(moved_nodes),
    }
    return OpResult(
        state=st,
        moved=moved_nodes,
        op="rigid_untangle",
        params={
            "groups": len(groups),
            "max_rounds": max_rounds,
            "step": step,
            "start_crossings": start,
            "end_crossings": cur_cost[0],
            "end_group_bbox_overlaps": cur_cost[1],
            "accepted_moves": accepted_moves,
            "fan_out_moves": fan_n,
        },
        note=(
            f"rigid_untangle {start}->{cur_cost[0]} "
            f"bbox_ov={cur_cost[1]} moves={accepted_moves}"
        ),
    )


def densify_rigid_groups(
    state: LayoutState,
    groups: list[dict[str, Any]],
    *,
    scale: float = 0.55,
    accept_crossings: bool = True,
    x_slack: int | None = None,
) -> OpResult:
    """Translate each staging unit toward the global centroid (rigid).

    Dual-unit membership heavily overlaps on portals — averaging a translation
    over *all* members cancels densify / spikes crossings. Instead:
    - move each group's **exclusive** nodes (membership==1) toward centroid;
    - **freeze shared portals** (glue stays put; spokes shorten from islands);
    - apply **one group at a time**; when ``accept_crossings``, reject a move
      that raises global crossings beyond a small slack.
    """
    st = state.copy()
    pos0 = dict(st.positions)
    valid = {
        n
        for n, (x, y) in pos0.items()
        if abs(x) <= _COORD_ABS_MAX
        and abs(y) <= _COORD_ABS_MAX
        and math.isfinite(x)
        and math.isfinite(y)
    }
    if len(valid) < 2 or not groups:
        return OpResult(
            state=st, moved=set(), op="densify_rigid_groups", note="noop"
        )
    s = max(0.2, min(float(scale), 0.95))
    gcx = sum(pos0[n][0] for n in valid) / len(valid)
    gcy = sum(pos0[n][1] for n in valid) / len(valid)

    xs = [pos0[n][0] for n in valid]
    ys = [pos0[n][1] for n in valid]
    bw0 = max(max(xs) - min(xs), 1e-6)
    bh0 = max(max(ys) - min(ys), 1e-6)

    counts: dict[str, int] = defaultdict(int)
    parsed: list[tuple[str, list[str], set[str]]] = []
    for g in groups:
        members = [
            str(n)
            for n in (g.get("node_ids") or [])
            if str(n) in valid
        ]
        if len(members) < 2:
            continue
        pivots = {
            str(p)
            for p in (g.get("pivots") or [])
            if str(p) in valid
        }
        for n in members:
            counts[n] += 1
        parsed.append((str(g.get("key") or ""), members, pivots))

    shared = {n for n, c in counts.items() if c > 1}
    pos = dict(pos0)
    moved: set[str] = set()
    used_groups = 0
    accepted = 0
    rejected = 0
    exclusive_moved = 0
    x0 = (
        count_edge_crossings(pos0, st.links) if accept_crossings else 0
    )
    slack = (
        max(20, int(x0 * 0.12))
        if x_slack is None
        else max(0, int(x_slack))
    )

    # Farther islands first — they dominate bbox.
    candidates: list[tuple[float, str, list[str], float, float]] = []
    for key, members, pivots in parsed:
        exclusive = [n for n in members if n not in shared]
        if len(exclusive) < 2:
            exclusive = [n for n in members if n not in pivots and n not in shared]
        if len(exclusive) < 2:
            continue
        cx = sum(pos0[n][0] for n in exclusive) / len(exclusive)
        cy = sum(pos0[n][1] for n in exclusive) / len(exclusive)
        dist = math.hypot(cx - gcx, cy - gcy)
        candidates.append((dist, key, exclusive, cx, cy))
    candidates.sort(reverse=True)

    for _dist, _key, exclusive, cx, cy in candidates:
        ncx = gcx + (cx - gcx) * s
        ncy = gcy + (cy - gcy) * s
        dx, dy = ncx - cx, ncy - cy
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            continue
        used_groups += 1
        trial = dict(pos)
        for n in exclusive:
            x0n, y0n = pos[n]
            trial[n] = (x0n + dx, y0n + dy)
        if accept_crossings:
            x1 = count_edge_crossings(trial, st.links)
            if x1 > x0 + slack:
                rejected += 1
                continue
            x0 = x1
        for n in exclusive:
            pos[n] = trial[n]
            moved.add(n)
            exclusive_moved += 1
        accepted += 1

    st.positions = pos
    st.last_moved = moved
    xs1 = [pos[n][0] for n in valid]
    ys1 = [pos[n][1] for n in valid]
    bw1 = max(max(xs1) - min(xs1), 1e-6)
    bh1 = max(max(ys1) - min(ys1), 1e-6)
    area_ratio = (bw0 * bh0) / (bw1 * bh1)
    return OpResult(
        state=st,
        moved=moved,
        op="densify_rigid_groups",
        params={
            "scale": round(s, 4),
            "groups": used_groups,
            "accepted": accepted,
            "rejected": rejected,
            "moved_n": len(moved),
            "exclusive_moved": exclusive_moved,
            "shared_n": len(shared),
            "portals_frozen": True,
            "bbox_area_ratio": round(area_ratio, 3),
            "x_slack": slack if accept_crossings else None,
        },
        note=(
            f"densify_rigid s={s:.2f} groups={used_groups} "
            f"ok={accepted}/rej={rejected} excl={exclusive_moved} "
            f"area×{area_ratio:.2f}"
        ),
    )


def rigid_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    out: dict[str, Any] = {}
    if o.get("max_rounds") is not None:
        try:
            out["max_rounds"] = int(o["max_rounds"])
        except (TypeError, ValueError):
            pass
    if o.get("step") is not None:
        try:
            out["step"] = float(o["step"])
        except (TypeError, ValueError):
            pass
    groups = o.get("rigid_groups") or o.get("_rigid_groups")
    if isinstance(groups, list):
        out["groups"] = groups
    return out


def exclusive_members_frozen(state: LayoutState) -> set[str]:
    """All nodes in compose rigid groups (legacy full freeze)."""
    return frozen_ids_for_protect(state, "all")


def _hub_portals(state: LayoutState, candidates: set[str], *, cap: int = 28) -> set[str]:
    """Shrink an inflated pivot set to true dual-unit hubs (high degree).

    Overlapping dual-units mark whole shared corridors as pivots; freezing
    those stalls soft polish. Keep high-degree hubs first.
    """
    if len(candidates) <= cap:
        return set(candidates)
    scored = sorted(
        ((len(state.adj.get(n, ())), n) for n in candidates),
        reverse=True,
    )
    hubs = {n for deg, n in scored if deg >= 5}
    if 2 <= len(hubs) <= cap * 2:
        return hubs
    return {n for _deg, n in scored[:cap]}


def frozen_ids_for_protect(
    state: LayoutState,
    mode: str | bool | None = "portals",
) -> set[str]:
    """Which compose-unit nodes to freeze during per-node untangle.

    Modes:
    - ``False`` / ``off``: freeze nothing
    - ``portals`` / ``skeleton`` / ``True`` (default): freeze dual-unit portals
      (high-degree shared hubs). Corridors/tails may move.
    - ``all``: freeze every rigid-group member (pure rigid; crossings stall)
    """
    if mode is False:
        return set()
    if isinstance(mode, str):
        key = mode.strip().lower()
    elif mode is True:
        key = "portals"
    else:
        key = "portals"
    if key in {"0", "false", "no", "off", "none"}:
        return set()

    groups = groups_from_compose_meta((state.meta or {}).get("compose_views"))
    if not groups:
        return set()

    if key in {"all", "full", "rigid"}:
        frozen: set[str] = set()
        for g in groups:
            frozen.update(g.get("node_ids") or [])
        return frozen

    # portals / skeleton / true: start from compose pivots, then shrink if the
    # dual-unit overlap inflated the set with corridor nodes.
    frozen: set[str] = set()
    for g in groups:
        frozen.update(str(x) for x in (g.get("pivots") or []) if str(x))
    if not frozen:
        counts: dict[str, int] = {}
        for g in groups:
            for nid in g.get("node_ids") or []:
                counts[str(nid)] = counts.get(str(nid), 0) + 1
        frozen = {n for n, c in counts.items() if c > 1}
    return _hub_portals(state, frozen)


def groups_from_membership(
    membership: list[tuple[str, list[str]]],
) -> list[dict[str, Any]]:
    """Build rigid groups from (key, node_ids) membership lists.

    Pivots = nodes that appear in more than one group (shared portals).
    """
    counts: dict[str, int] = {}
    for _key, ids in membership:
        for nid in ids:
            counts[nid] = counts.get(nid, 0) + 1
    out: list[dict[str, Any]] = []
    for key, ids in membership:
        uniq = sorted({str(x) for x in ids if str(x)})
        if len(uniq) < 2:
            continue
        pivots = [n for n in uniq if counts.get(n, 0) > 1]
        out.append({"key": str(key), "node_ids": uniq, "pivots": pivots})
    return out
