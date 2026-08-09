"""Eject nodes that sit on / too close to non-incident edges.

Default: orthogonal eject. With ``preserve_axis`` + grid pitch/side, only accept
moves that keep incident H/V edges and snap to the metro grid — safe after
``ortho_metro`` (the old 60px nudge was breaking right angles).
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    EDGE_CLEARANCE_THR,
    REC_CENTER_DX,
    REC_CENTER_DY,
    compute_edge_clearance,
    count_edge_crossings,
    node_footprint,
    point_segment_dist,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

_AXIS_TOL = 8.0


def clear_edge_params_from_overrides(params: dict[str, Any] | None) -> dict[str, Any]:
    p = params or {}
    out: dict[str, Any] = {
        "top_n": int(p.get("top_n") or 12),
        "thr": float(p.get("thr") or EDGE_CLEARANCE_THR),
        "margin": float(p.get("margin") or 20.0),
        "max_moves": int(p.get("max_moves") or 24),
        "preserve_axis": bool(p.get("preserve_axis", False)),
        "rounds": int(p.get("rounds") or 1),
    }
    if p.get("pitch") is not None:
        out["pitch"] = float(p["pitch"])
    if p.get("side") is not None:
        out["side"] = float(p["side"])
    return out


def _axis_ok(a: tuple[float, float], b: tuple[float, float], tol: float = _AXIS_TOL) -> bool:
    return abs(a[0] - b[0]) <= tol or abs(a[1] - b[1]) <= tol


def _overlaps_any(
    node: str,
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
) -> bool:
    x, y = pos[node]
    fa = node_footprint(names.get(node, ""))
    ax0, ay0, ax1, ay1 = x + fa[0], y + fa[1], x + fa[2], y + fa[3]
    for b, (bx, by) in pos.items():
        if b == node:
            continue
        if abs(bx - x) > 120 and abs(by - y) > 100:
            continue
        fb = node_footprint(names.get(b, ""))
        bx0, by0, bx1, by1 = bx + fb[0], by + fb[1], bx + fb[2], by + fb[3]
        if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
            return True
    return False


def _incident_axis_score(
    nid: str,
    pos: dict[str, tuple[float, float]],
    adj: dict[str, set[str]],
) -> int:
    s = 0
    for nb in adj.get(nid) or ():
        if nb in pos and _axis_ok(pos[nid], pos[nb]):
            s += 1
    return s


def _global_axis_score(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
) -> int:
    return sum(
        1
        for a, b in links
        if a in pos and b in pos and _axis_ok(pos[a], pos[b])
    )


def _on_open_segment(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    *,
    thr: float,
) -> bool:
    d, t = point_segment_dist(p, a, b)
    return d < thr and 0.05 < t < 0.95


def _candidates_orthogonal(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    *,
    target: float,
) -> list[tuple[float, float]]:
    """Axis-aligned eject candidates at distance ``target`` from segment AB."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    cands: list[tuple[float, float]] = []
    if L < 1e-9:
        cands.extend(
            [
                (px + target, py),
                (px - target, py),
                (px, py + target),
                (px, py - target),
            ]
        )
        return cands

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (L * L)))
    fx, fy = ax + t * dx, ay + t * dy

    cands.extend(
        [
            (fx, fy + target),
            (fx, fy - target),
            (fx + target, fy),
            (fx - target, fy),
        ]
    )
    ux, uy = -dy / L, dx / L
    cands.append((fx + ux * target, fy + uy * target))
    cands.append((fx - ux * target, fy - uy * target))

    if abs(dx) >= abs(dy):
        cands.insert(0, (px, fy + target if py >= fy else fy - target))
        cands.insert(1, (px, fy - target if py >= fy else fy + target))
    else:
        cands.insert(0, (fx + target if px >= fx else fx - target, py))
        cands.insert(1, (fx - target if px >= fx else fx + target, py))

    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float]] = []
    for xy in cands:
        key = (round(xy[0], 2), round(xy[1], 2))
        if key in seen:
            continue
        seen.add(key)
        out.append((float(xy[0]), float(xy[1])))
    return out


def _candidates_grid_ortho(
    p: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    *,
    pitch: float,
    side: float,
    max_steps: int = 8,
    neighbor_xy: list[tuple[float, float]] | None = None,
) -> list[tuple[float, float]]:
    """Eject perpendicular to H/V trunk by whole grid steps (preserve metro)."""
    ax, ay = a
    bx, by = b
    px, py = p
    horiz = abs(ay - by) <= _AXIS_TOL
    vert = abs(ax - bx) <= _AXIS_TOL
    cands: list[tuple[float, float]] = []
    # Prefer landing that still shares H/V with a neighbor (keep incident ortho).
    for nxy in neighbor_xy or ():
        nx, ny = nxy
        if horiz:
            for sign in (1, -1):
                cands.append((nx, py + sign * side))  # V to neighbor, off row
                cands.append((nx, py + sign * 2 * side))
        elif vert:
            for sign in (1, -1):
                cands.append((px + sign * pitch, ny))  # H to neighbor, off col
                cands.append((px + sign * 2 * pitch, ny))
    if horiz:
        for s in range(1, max_steps + 1):
            for sign in (1, -1):
                cands.append((px, py + sign * s * side))
                cands.append((px + sign * s * pitch, py + sign * side))
                cands.append((px + sign * s * pitch, py - sign * side))
    elif vert:
        for s in range(1, max_steps + 1):
            for sign in (1, -1):
                cands.append((px + sign * s * pitch, py))
                cands.append((px + sign * pitch, py + sign * s * side))
                cands.append((px - sign * pitch, py + sign * s * side))
    else:
        return _candidates_orthogonal(p, a, b, target=max(side, pitch) * 0.5)

    if horiz:
        cands = [(px, py + side), (px, py - side)] + cands
    elif vert:
        cands = [(px + pitch, py), (px - pitch, py)] + cands

    seen: set[tuple[float, float]] = set()
    out: list[tuple[float, float]] = []
    for xy in cands:
        key = (round(xy[0], 1), round(xy[1], 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(xy)
    return out


def clear_edge_hits(
    state: LayoutState,
    params: LayoutParams,
    *,
    top_n: int = 12,
    thr: float = EDGE_CLEARANCE_THR,
    margin: float = 20.0,
    max_moves: int = 24,
    preserve_axis: bool = False,
    pitch: float | None = None,
    side: float | None = None,
    rounds: int = 1,
) -> OpResult:
    """Move nodes off non-incident edges; gate on crossings + overlaps.

    ``preserve_axis=True``: snap to pitch/side grid, keep incident H/V count,
    multi-round until no progress — use after ``ortho_metro``.
    """
    del params
    st = state.copy()
    pos = dict(st.positions)
    names = st.names
    links = list(st.links)
    adj = st.adj
    pitch_f = float(pitch if pitch is not None else REC_CENTER_DX)
    side_f = float(side if side is not None else REC_CENTER_DY)
    rounds_n = max(1, int(rounds))
    if preserve_axis:
        rounds_n = max(rounds_n, 6)
        top_n = max(top_n, 40)
        max_moves = max(max_moves, 80)

    clr0 = compute_edge_clearance(pos, links, names=names, thr=thr, top_n=max(top_n, 5))
    hits_before = int(clr0.get("edge_clearance_hits") or 0)
    if clr0.get("edge_clearance_skipped"):
        return OpResult(
            state=st,
            moved=set(),
            op="clear_edge_hits",
            note="clear_edge_hits:skipped_large",
            params={"hits_before": hits_before, "skipped": True},
        )
    if hits_before <= 0:
        return OpResult(
            state=st,
            moved=set(),
            op="clear_edge_hits",
            note="clear_edge_hits:noop",
            params={"hits_before": 0, "hits_after": 0, "moved_n": 0},
        )

    moved: set[str] = set()
    accepted: list[dict[str, Any]] = []
    target = float(thr) + float(margin)
    x0 = count_edge_crossings(pos, links)
    ax0 = _global_axis_score(pos, links)
    # Clearance matters, but keep metro readable — modest crossing slack only.
    x_slack = 4 if preserve_axis else 0
    deg = {n: len(adj.get(n) or ()) for n in pos}

    def _move_budget(nid: str) -> float:
        d = deg.get(nid, 0)
        if d >= 5:
            return max(pitch_f, side_f) * 1.5
        if d >= 3:
            return max(pitch_f, side_f) * 3.0
        return max(pitch_f, side_f) * 8.0

    def _within_budget(nid: str, xy: tuple[float, float], origin: tuple[float, float]) -> bool:
        return math.hypot(xy[0] - origin[0], xy[1] - origin[1]) <= _move_budget(nid) + 1e-6

    for _rnd in range(rounds_n):
        full = compute_edge_clearance(pos, links, names=names, thr=thr, top_n=top_n)
        ordered = list(full.get("hit_nodes") or full.get("top_edge_hits") or [])
        if not ordered:
            break
        # Prefer ejecting low-degree obstacles first (keep core hubs stable).
        ordered.sort(
            key=lambda h: (
                float(h.get("dist") or 0.0),
                deg.get(str(h.get("fabric_node_id")), 99),
                str(h.get("fabric_node_id")),
            )
        )
        progress = False
        moves_this = 0
        for h in ordered:
            if moves_this >= max_moves:
                break
            nid = str(h["fabric_node_id"])
            if nid not in pos:
                continue
            a = str(h["a_node_id"])
            b = str(h["b_node_id"])
            if a not in pos or b not in pos:
                continue
            p0 = pos[nid]
            if not _on_open_segment(p0, pos[a], pos[b], thr=thr) and float(h.get("dist") or 99) >= thr:
                continue
            # Very high-degree hub on a chord: break the chord instead of ejecting hub.
            if deg.get(nid, 0) >= 5:
                continue
            if preserve_axis:
                nbr_xy = [
                    pos[nb]
                    for nb in (adj.get(nid) or ())
                    if nb in pos and nb not in (a, b)
                ]
                cands = _candidates_grid_ortho(
                    p0,
                    pos[a],
                    pos[b],
                    pitch=pitch_f,
                    side=side_f,
                    neighbor_xy=nbr_xy,
                )
            else:
                cands = _candidates_orthogonal(p0, pos[a], pos[b], target=target)

            inc0 = _incident_axis_score(nid, pos, adj)
            for xy in cands:
                if not _within_budget(nid, xy, p0):
                    continue
                trial = dict(pos)
                trial[nid] = xy
                if _overlaps_any(nid, trial, names):
                    continue
                x1 = count_edge_crossings(trial, links)
                if x1 > x0 + x_slack:
                    continue
                d1, t1 = point_segment_dist(xy, pos[a], pos[b])
                if 0.05 < t1 < 0.95 and d1 < thr:
                    continue
                # Must leave ALL non-incident open segments (not just a-b).
                still_hit = False
                for ea, eb in links:
                    if nid in (ea, eb) or ea not in trial or eb not in trial:
                        continue
                    if _on_open_segment(xy, trial[ea], trial[eb], thr=thr):
                        still_hit = True
                        break
                if still_hit:
                    continue
                if preserve_axis:
                    inc1 = _incident_axis_score(nid, trial, adj)
                    if inc1 < inc0:
                        continue
                    ax1 = _global_axis_score(trial, links)
                    if ax1 < ax0 - 2:  # allow small axis trade for clearance
                        continue
                pos[nid] = xy
                moved.add(nid)
                moves_this += 1
                progress = True
                x0 = x1
                if preserve_axis:
                    ax0 = _global_axis_score(pos, links)
                accepted.append(
                    {
                        "fabric_node_id": nid,
                        "from": [round(p0[0], 1), round(p0[1], 1)],
                        "to": [round(xy[0], 1), round(xy[1], 1)],
                        "edge": [a, b],
                        "dist_before": round(float(h["dist"]), 2),
                        "dist_after": round(d1, 2),
                    }
                )
                break
        if not progress and preserve_axis:
            # Phase 2: translate the whole H/V trunk off the obstacle row/col
            # (keeps the edge axis-aligned; frees every node that sat on it).
            for h in ordered:
                if moves_this >= max_moves:
                    break
                nid = str(h["fabric_node_id"])
                a = str(h["a_node_id"])
                b = str(h["b_node_id"])
                if nid not in pos or a not in pos or b not in pos:
                    continue
                if not _on_open_segment(pos[nid], pos[a], pos[b], thr=thr):
                    continue
                ax, ay = pos[a]
                bx, by = pos[b]
                horiz = abs(ay - by) <= _AXIS_TOL
                vert = abs(ax - bx) <= _AXIS_TOL
                if not horiz and not vert:
                    continue
                # Prefer moving the lower-degree endpoint pair as a rigid H/V bar
                max_trunk_steps = 2 if max(deg.get(a, 0), deg.get(b, 0)) >= 4 else 4
                for s in range(1, max_trunk_steps + 1):
                    deltas = (
                        [(0.0, s * side_f), (0.0, -s * side_f)]
                        if horiz
                        else [(s * pitch_f, 0.0), (-s * pitch_f, 0.0)]
                    )
                    for dx, dy in deltas:
                        trial = dict(pos)
                        trial[a] = (ax + dx, ay + dy)
                        trial[b] = (bx + dx, by + dy)
                        if _overlaps_any(a, trial, names) or _overlaps_any(b, trial, names):
                            continue
                        if _global_axis_score(trial, links) < ax0 - 2:
                            continue
                        x1 = count_edge_crossings(trial, links)
                        if x1 > x0 + x_slack:
                            continue
                        d1, t1 = point_segment_dist(pos[nid], trial[a], trial[b])
                        # obstacle stays put; edge moved away
                        if 0.05 < t1 < 0.95 and d1 < thr:
                            continue
                        # also: no OTHER node should sit on the moved trunk
                        blocked = False
                        for n2, p2 in trial.items():
                            if n2 in (a, b):
                                continue
                            dd, tt = point_segment_dist(p2, trial[a], trial[b])
                            if dd < thr and 0.05 < tt < 0.95:
                                blocked = True
                                break
                        if blocked:
                            continue
                        pos[a], pos[b] = trial[a], trial[b]
                        moved.add(a)
                        moved.add(b)
                        moves_this += 2
                        progress = True
                        x0 = x1
                        ax0 = _global_axis_score(pos, links)
                        accepted.append(
                            {
                                "fabric_node_id": a,
                                "with": b,
                                "from": [round(ax, 1), round(ay, 1)],
                                "to": [round(pos[a][0], 1), round(pos[a][1], 1)],
                                "edge": [a, b],
                                "mode": "translate_trunk",
                                "cleared": nid,
                            }
                        )
                        break
                    if progress:
                        break
                if progress:
                    break
        if not progress and preserve_axis:
            # Phase 3: break the occluding H/V chord (prefer low-deg endpoint).
            # Prefer this over ejecting hubs — layout sense > pure clearance force.
            for h in ordered:
                if moves_this >= max_moves:
                    break
                nid = str(h["fabric_node_id"])
                a = str(h["a_node_id"])
                b = str(h["b_node_id"])
                if nid not in pos or a not in pos or b not in pos:
                    continue
                if not _on_open_segment(pos[nid], pos[a], pos[b], thr=thr):
                    continue
                ax, ay = pos[a]
                bx, by = pos[b]
                horiz = abs(ay - by) <= _AXIS_TOL
                vert = abs(ax - bx) <= _AXIS_TOL
                if not horiz and not vert:
                    continue
                ends = sorted([a, b], key=lambda n: (deg.get(n, 0), n))
                cleared_local = False
                for end in ends:
                    ox, oy = pos[end]
                    # Hubs: only 1 grid step off the shared track (keep layout sense).
                    max_step = 1 if deg.get(end, 0) >= 5 else (2 if deg.get(end, 0) >= 3 else 4)
                    for step in range(1, max_step + 1):
                        step_deltas = (
                            [(0.0, step * side_f), (0.0, -step * side_f)]
                            if horiz
                            else [(step * pitch_f, 0.0), (-step * pitch_f, 0.0)]
                        )
                        for dx, dy in step_deltas:
                            trial = dict(pos)
                            trial[end] = (ox + dx, oy + dy)
                            if _overlaps_any(end, trial, names):
                                continue
                            x1 = count_edge_crossings(trial, links)
                            if x1 > x0 + x_slack + 6:
                                continue
                            if _on_open_segment(pos[nid], trial[a], trial[b], thr=thr):
                                continue
                            blocked = False
                            for n2, p2 in trial.items():
                                if n2 in (a, b):
                                    continue
                                if _on_open_segment(p2, trial[a], trial[b], thr=thr):
                                    blocked = True
                                    break
                            if blocked:
                                continue
                            pos[end] = trial[end]
                            moved.add(end)
                            moves_this += 1
                            progress = True
                            cleared_local = True
                            x0 = x1
                            ax0 = _global_axis_score(pos, links)
                            accepted.append(
                                {
                                    "fabric_node_id": end,
                                    "from": [round(ox, 1), round(oy, 1)],
                                    "to": [
                                        round(pos[end][0], 1),
                                        round(pos[end][1], 1),
                                    ],
                                    "edge": [a, b],
                                    "mode": "break_chord",
                                    "cleared": nid,
                                }
                            )
                            break
                        if cleared_local:
                            break
                    if cleared_local:
                        break
                if progress:
                    break
        if not progress and preserve_axis:
            # Phase 4: local force-eject low-deg obstacles only.
            for h in ordered:
                if moves_this >= max_moves:
                    break
                nid = str(h["fabric_node_id"])
                a = str(h["a_node_id"])
                b = str(h["b_node_id"])
                if nid not in pos or a not in pos or b not in pos:
                    continue
                if deg.get(nid, 0) >= 4:
                    continue
                if not _on_open_segment(pos[nid], pos[a], pos[b], thr=thr):
                    continue
                p0 = pos[nid]
                nbr_xy = [pos[nb] for nb in (adj.get(nid) or ()) if nb in pos]
                cands = _candidates_grid_ortho(
                    p0,
                    pos[a],
                    pos[b],
                    pitch=pitch_f,
                    side=side_f,
                    neighbor_xy=nbr_xy,
                    max_steps=6,
                )
                for xy in cands:
                    if not _within_budget(nid, xy, p0):
                        continue
                    trial = dict(pos)
                    trial[nid] = xy
                    if _overlaps_any(nid, trial, names):
                        continue
                    x1 = count_edge_crossings(trial, links)
                    if x1 > x0 + x_slack + 4:
                        continue
                    if _on_open_segment(xy, trial[a], trial[b], thr=thr):
                        continue
                    still_hit = False
                    for ea, eb in links:
                        if nid in (ea, eb) or ea not in trial or eb not in trial:
                            continue
                        if _on_open_segment(xy, trial[ea], trial[eb], thr=thr):
                            still_hit = True
                            break
                    if still_hit:
                        continue
                    pos[nid] = xy
                    moved.add(nid)
                    moves_this += 1
                    progress = True
                    x0 = x1
                    ax0 = _global_axis_score(pos, links)
                    accepted.append(
                        {
                            "fabric_node_id": nid,
                            "from": [round(p0[0], 1), round(p0[1], 1)],
                            "to": [round(xy[0], 1), round(xy[1], 1)],
                            "edge": [a, b],
                            "mode": "force_eject",
                            "dist_before": round(float(h["dist"]), 2),
                        }
                    )
                    break
                if progress:
                    break
        if not progress:
            break

    st.positions = pos
    clr1 = compute_edge_clearance(pos, links, names=names, thr=thr)
    hits_after = int(clr1.get("edge_clearance_hits") or 0)
    st.meta = dict(st.meta or {})
    st.meta["clear_edge_hits"] = {
        "hits_before": hits_before,
        "hits_after": hits_after,
        "moved_n": len(moved),
        "accepted": accepted[:40],
        "thr": thr,
        "margin": margin,
        "preserve_axis": preserve_axis,
    }
    return OpResult(
        state=st,
        moved=moved,
        op="clear_edge_hits",
        note=f"clear_edge_hits {hits_before}->{hits_after} moved={len(moved)}"
        + (" axis" if preserve_axis else ""),
        params={
            "hits_before": hits_before,
            "hits_after": hits_after,
            "moved_n": len(moved),
            "accepted": accepted[:40],
            "thr": thr,
            "margin": margin,
            "top_n": top_n,
            "preserve_axis": preserve_axis,
        },
    )
