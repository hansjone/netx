"""Rigid-body densify: drag a dual-unit by an external corridor (bridge orbit).

For each staging rigid group, pick an external bridge tip as pivot and sweep the
exclusive members on concentric circles (angle × radius_scale). Prefer inward
radii to raise util / shrink bbox while keeping overlaps=0 and crossings within
``x_slack``.

This reuses the polar spirit of ``compose_orbit`` attach, but runs *after*
compose as a densify/util pass (portals stay frozen when shared).
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.compose_orbit import crossings_touching
from netx_topology_mcp.layout_ops.hotspots import overlapping_nodes
from netx_topology_mcp.layout_ops.rigid_units import groups_from_compose_meta
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_ops.state import LayoutState, OpResult

_COORD_ABS_MAX = 1.0e6
_DEFAULT_ANGLE_STEP = 30
# Bias inward: pull units toward external tips to densify.
_DEFAULT_RADII = (0.55, 0.7, 0.85, 1.0, 1.15, 1.35)
# Large canvases: coarser polar grid (full E² count is too expensive).
_LARGE_N = 600
_LARGE_ANGLE_STEP = 45
_LARGE_RADII = (0.55, 0.75, 0.9, 1.1)
_MIN_BRIDGE = 180.0
_FOOTPRINT_MIN = 90.0


def _apply_polar(
    base: dict[str, tuple[float, float]],
    members: list[str],
    pivot: tuple[float, float],
    *,
    angle: float,
    radius_scale: float,
) -> dict[str, tuple[float, float]]:
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    cx, cy = pivot
    out = dict(base)
    for n in members:
        if n not in out:
            continue
        x, y = out[n]
        dx, dy = x - cx, y - cy
        rx = (dx * cos_a - dy * sin_a) * radius_scale
        ry = (dx * sin_a + dy * cos_a) * radius_scale
        out[n] = (cx + rx, cy + ry)
    return out


def _bbox_area(pos: dict[str, tuple[float, float]], ids: set[str] | None = None) -> float:
    pts = [
        pos[n]
        for n in (ids or pos.keys())
        if n in pos
        and abs(pos[n][0]) <= _COORD_ABS_MAX
        and abs(pos[n][1]) <= _COORD_ABS_MAX
    ]
    if len(pts) < 2:
        return 1.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(max(xs) - min(xs), 1e-6) * max(max(ys) - min(ys), 1e-6)


def _footprint_hits(
    pos: dict[str, tuple[float, float]],
    moved: set[str],
    frozen: set[str],
    *,
    min_dist: float = _FOOTPRINT_MIN,
) -> int:
    """Cheap center collisions between moved nodes and the rest."""
    hits = 0
    md2 = min_dist * min_dist
    others = [
        (pos[n][0], pos[n][1])
        for n in pos
        if n not in moved and n not in frozen
        and abs(pos[n][0]) <= _COORD_ABS_MAX
    ]
    for n in moved:
        if n not in pos:
            continue
        x, y = pos[n]
        for ox, oy in others:
            dx, dy = x - ox, y - oy
            if dx * dx + dy * dy < md2:
                hits += 1
                break
    return hits


def _parse_groups(
    groups: list[dict[str, Any]],
    valid: set[str],
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    parsed_raw: list[tuple[str, list[str], list[str]]] = []
    for g in groups:
        key = str(g.get("key") or "")
        members = [str(n) for n in (g.get("node_ids") or []) if str(n) in valid]
        if len(members) < 2:
            continue
        pivots = [str(p) for p in (g.get("pivots") or []) if str(p) in valid]
        for n in members:
            counts[n] = counts.get(n, 0) + 1
        parsed_raw.append((key, members, pivots))
    shared = {n for n, c in counts.items() if c > 1}
    out: list[dict[str, Any]] = []
    for key, members, pivots in parsed_raw:
        piv = pivots or [n for n in members if n in shared]
        exclusive = [n for n in members if n not in shared]
        out.append(
            {
                "key": key,
                "members": members,
                "pivots": piv,
                "exclusive": exclusive,
                "shared": [n for n in members if n in shared],
            }
        )
    return out


def _external_bridges(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    members: set[str],
    exclusive: set[str],
) -> list[tuple[float, str, str]]:
    """Return (length, inner_id, outer_id) sorted longest-first."""
    bridges: list[tuple[float, str, str]] = []
    for a, b in links:
        if a not in pos or b not in pos:
            continue
        a_in, b_in = a in members, b in members
        if a_in == b_in:
            continue
        inner, outer = (a, b) if a_in else (b, a)
        # Prefer tips that are exclusive (true corridor leaf), not shared portals.
        if exclusive and inner not in exclusive and inner not in members:
            continue
        L = math.hypot(pos[inner][0] - pos[outer][0], pos[inner][1] - pos[outer][1])
        if L < _MIN_BRIDGE:
            continue
        bridges.append((L, inner, outer))
    bridges.sort(reverse=True)
    return bridges


def _movers_for_group(g: dict[str, Any]) -> list[str]:
    """Nodes that move under bridge orbit (exclusive; whole group if free)."""
    if g["exclusive"]:
        return list(g["exclusive"])
    # No shared membership → whole body can orbit.
    return list(g["members"])


def rigid_orbit_candidates_for_group(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    group: dict[str, Any],
    *,
    angle_step: int = _DEFAULT_ANGLE_STEP,
    radii: tuple[float, ...] = _DEFAULT_RADII,
    bridges_per_group: int = 4,
    cand_cap: int = 120,
    x0: int | None = None,
    area0: float | None = None,
    x_slack: int = 40,
) -> list[dict[str, Any]]:
    """Ranked candidates for one rigid group (best first).

    Ranking uses *partial* crossings (edges touching movers) — O(focus×E) —
    not full global E². Callers verify top picks with ``count_edge_crossings``.
    """
    del x0  # reserved; global verify happens in round
    members = set(group["members"])
    exclusive = set(group["exclusive"])
    movers = _movers_for_group(group)
    if len(movers) < 2:
        return []
    frozen_portals = set(group["shared"] or group["pivots"])
    bridges = _external_bridges(pos, links, members, exclusive)[
        : max(1, bridges_per_group)
    ]
    if not bridges:
        return []

    area_base = _bbox_area(pos) if area0 is None else float(area0)
    step = max(15, min(60, int(angle_step)))
    rads = tuple(float(r) for r in radii) or _DEFAULT_RADII
    touch_slack = max(4, int(x_slack))

    cands: list[dict[str, Any]] = []
    for L, inner, outer in bridges:
        if outer not in pos or inner not in pos:
            continue
        pivot = pos[outer]
        focus0 = set(movers) | {inner, outer}
        x_touch0 = crossings_touching(pos, links, focus0)
        for deg in range(0, 360, step):
            ang = math.radians(deg)
            for rs in rads:
                if deg == 0 and abs(rs - 1.0) < 1e-9:
                    continue
                trial = _apply_polar(pos, movers, pivot, angle=ang, radius_scale=rs)
                for p in frozen_portals:
                    if p in pos:
                        trial[p] = pos[p]
                trial[outer] = pos[outer]
                ov_cheap = _footprint_hits(trial, set(movers), frozen_portals)
                if ov_cheap > 0:
                    continue
                x_touch = crossings_touching(trial, links, focus0)
                if x_touch > x_touch0 + touch_slack:
                    continue
                area1 = _bbox_area(trial)
                area_ratio = area_base / max(area1, 1e-6)
                # Prefer denser bbox, then fewer partial crossings.
                rank = (area_ratio, -(x_touch - x_touch0), -x_touch, -L)
                cands.append(
                    {
                        "group_key": group["key"],
                        "inner": inner,
                        "outer": outer,
                        "bridge_len": round(L, 1),
                        "angle_deg": float(deg),
                        "radius_scale": float(rs),
                        "crossings_touch": x_touch,
                        "crossings_touch0": x_touch0,
                        "area_ratio": round(area_ratio, 4),
                        "delta_touch": x_touch - x_touch0,
                        "movers_n": len(movers),
                        "rank": rank,
                        "positions": {n: trial[n] for n in movers if n in trial},
                    }
                )
                if len(cands) >= cand_cap * 2:
                    break
            if len(cands) >= cand_cap * 2:
                break
        if len(cands) >= cand_cap * 2:
            break

    cands.sort(key=lambda r: r["rank"], reverse=True)
    useful = [
        c
        for c in cands
        if c["area_ratio"] >= 1.004 or c["delta_touch"] < 0
    ]
    out = useful[:cand_cap] if useful else cands[: min(3, len(cands))]
    for i, c in enumerate(out, start=1):
        c["rank_i"] = i
        c.pop("rank", None)
    return out


def rigid_orbit_round(
    state: LayoutState,
    *,
    groups: list[dict[str, Any]] | None = None,
    top_n: int = 24,
    bridges_per_group: int = 3,
    angle_step: int = _DEFAULT_ANGLE_STEP,
    radii: tuple[float, ...] | list[float] | None = None,
    cand_cap: int = 80,
    x_slack: int | None = None,
    max_accepts: int = 16,
) -> OpResult:
    """Greedy bridge-orbit densify over top rigid groups."""
    from netx_topology_mcp.layout_jobs import (
        raise_if_cancelled,
        report_progress,
        touch_heartbeat,
    )

    st = state.copy()
    groups = groups if groups is not None else groups_from_compose_meta(
        (st.meta or {}).get("compose_views")
    )
    valid = {
        n
        for n, (x, y) in st.positions.items()
        if abs(x) <= _COORD_ABS_MAX
        and abs(y) <= _COORD_ABS_MAX
        and math.isfinite(x)
        and math.isfinite(y)
    }
    parsed = _parse_groups(groups or [], valid)
    if not parsed:
        return OpResult(
            state=st,
            moved=set(),
            op="rigid_orbit",
            params={"groups": 0},
            note="rigid_orbit:no_groups",
        )

    large = len(st.positions) >= _LARGE_N or len(st.links) >= _LARGE_N
    before = score_state(st, fast=True)
    before_util = float((before.get("summary") or {}).get("util") or 0.0)
    before_x = int((before.get("summary") or {}).get("crossings") or 0)
    before_ov = len(overlapping_nodes(st))
    area0 = _bbox_area(st.positions)
    slack = (
        max(8, int(before_x * 0.08))
        if x_slack is None
        else max(0, int(x_slack))
    )
    # Auto-coarsen polar grid on large canvases unless caller overrode radii.
    use_angle = int(angle_step)
    if radii is None and large:
        rads = _LARGE_RADII
        use_angle = max(use_angle, _LARGE_ANGLE_STEP)
        bridges_per_group = min(int(bridges_per_group), 2)
        cand_cap = min(int(cand_cap), 48)
        top_n = min(int(top_n), 20)
        max_accepts = min(int(max_accepts), 10)
    else:
        rads = tuple(float(r) for r in (radii or _DEFAULT_RADII))

    # Order groups by longest external bridge (sparsity / stretch drivers).
    scored_g: list[tuple[float, dict[str, Any]]] = []
    for g in parsed:
        br = _external_bridges(
            st.positions, st.links, set(g["members"]), set(g["exclusive"])
        )
        if not br:
            continue
        scored_g.append((br[0][0], g))
    scored_g.sort(key=lambda t: t[0], reverse=True)
    ordered = [g for _L, g in scored_g[: max(1, int(top_n))]]

    pos = {n: (float(p[0]), float(p[1])) for n, p in st.positions.items()}
    links = list(st.links)
    cur_x = before_x
    cur_area = area0
    moved: set[str] = set()
    trace: list[dict[str, Any]] = []
    accepts = 0
    verify_k = 3 if large else 5

    report_progress(
        "rigid_orbit",
        pct=48.0,
        message=f"scan {len(ordered)} groups large={large}",
        groups=len(ordered),
    )

    for gi, g in enumerate(ordered):
        if accepts >= max(1, int(max_accepts)):
            break
        raise_if_cancelled()
        touch_heartbeat()
        if gi == 0 or gi % 2 == 0 or accepts > 0:
            pct = 48.0 + 25.0 * (gi / max(len(ordered), 1))
            report_progress(
                "rigid_orbit",
                pct=min(72.0, pct),
                message=f"group {gi + 1}/{len(ordered)} accepts={accepts}",
                group_key=g["key"],
                accepts=accepts,
            )
        cands = rigid_orbit_candidates_for_group(
            pos,
            links,
            g,
            angle_step=use_angle,
            radii=rads,
            bridges_per_group=bridges_per_group,
            cand_cap=cand_cap,
            x0=cur_x,
            area0=cur_area,
            x_slack=slack,
        )
        if not cands:
            trace.append({"group_key": g["key"], "skipped": "no_candidates"})
            continue
        applied = False
        for cand in cands[:verify_k]:
            trial = dict(pos)
            movers_ids = set(cand.get("positions") or {})
            for n, xy in (cand.get("positions") or {}).items():
                trial[n] = xy
            frozen = set(g.get("shared") or g.get("pivots") or [])
            if _footprint_hits(trial, movers_ids, frozen) > 0:
                continue
            raise_if_cancelled()
            touch_heartbeat()
            x1 = count_edge_crossings(trial, links)
            if x1 > cur_x + slack:
                continue
            area1 = _bbox_area(trial)
            area_ratio = cur_area / max(area1, 1e-6)
            util_proxy_up = area_ratio >= 1.004
            x_down = x1 < cur_x
            if not util_proxy_up and not x_down:
                continue
            # Accept (footprint-ok). Residual AABB → ensure_zero_overlap in layout_tool.
            pos = trial
            cur_x = x1
            cur_area = area1
            moved.update(movers_ids)
            accepts += 1
            applied = True
            trace.append(
                {
                    "group_key": g["key"],
                    "applied": True,
                    "inner": cand.get("inner"),
                    "outer": cand.get("outer"),
                    "angle_deg": cand.get("angle_deg"),
                    "radius_scale": cand.get("radius_scale"),
                    "bridge_len": cand.get("bridge_len"),
                    "crossings": x1,
                    "area_ratio": round(area_ratio, 4),
                    "movers_n": len(movers_ids),
                }
            )
            break
        if not applied:
            best = cands[0]
            trace.append(
                {
                    "group_key": g["key"],
                    "skipped": "no_accept",
                    "best_area_ratio": best.get("area_ratio"),
                    "best_delta_touch": best.get("delta_touch"),
                }
            )

    st.positions = pos
    fin = score_state(st, fast=True)
    end_util = float((fin.get("summary") or {}).get("util") or 0.0)
    end_x = int((fin.get("summary") or {}).get("crossings") or 0)
    end_ov = len(overlapping_nodes(st))
    # Residual overlaps: keep accepted moves; layout_tool ensure_zero_overlap repairs.
    # Only hard-revert when we never accepted and somehow got worse.
    if end_ov > before_ov and accepts == 0:
        return OpResult(
            state=state.copy(),
            moved=set(),
            op="rigid_orbit",
            params={
                "reverted": True,
                "reason": "overlaps",
                "start_util": before_util,
                "start_crossings": before_x,
                "trace": trace,
            },
            note="rigid_orbit:reverted overlaps",
        )
    util_up = end_util > before_util + 1e-6
    area_up = (area0 / max(_bbox_area(st.positions), 1e-6)) >= 1.004
    x_ok = end_x <= before_x + slack
    # Crossing-only gains count (util may stay flat when bbox is hull-dominated).
    if not ((util_up or area_up or (accepts > 0 and end_x < before_x)) and x_ok) and accepts == 0:
        meta = {
            "reverted": True,
            "reason": "no_gain",
            "start_util": before_util,
            "end_util": before_util,
            "start_crossings": before_x,
            "end_crossings": before_x,
            "x_slack": slack,
            "scanned_groups": len(ordered),
            "trace": trace[:40],
        }
        st0 = state.copy()
        st0.meta = dict(st0.meta or {})
        st0.meta["rigid_orbit"] = meta
        return OpResult(
            state=st0,
            moved=set(),
            op="rigid_orbit",
            params=meta,
            note="rigid_orbit:reverted no_gain",
        )

    meta = {
        "reverted": False,
        "start_util": before_util,
        "end_util": end_util,
        "start_crossings": before_x,
        "end_crossings": end_x,
        "accepted": accepts,
        "moved_n": len(moved),
        "x_slack": slack,
        "scanned_groups": len(ordered),
        "bbox_area_ratio": round(area0 / max(_bbox_area(st.positions), 1e-6), 4),
        "trace": trace[:60],
    }
    st.meta = dict(st.meta or {})
    st.meta["rigid_orbit"] = meta
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="rigid_orbit",
        params=meta,
        note=(
            f"rigid_orbit util {before_util:.4f}->{end_util:.4f} "
            f"x {before_x}->{end_x} accepted={accepts}/{len(ordered)}"
        ),
    )


def rigid_orbit_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    out: dict[str, Any] = {}
    if o.get("top_n") is not None:
        try:
            out["top_n"] = max(1, min(80, int(o["top_n"])))
        except (TypeError, ValueError):
            pass
    if o.get("bridges_per_group") is not None:
        try:
            out["bridges_per_group"] = max(1, min(12, int(o["bridges_per_group"])))
        except (TypeError, ValueError):
            pass
    if o.get("angle_step") is not None:
        try:
            out["angle_step"] = max(10, min(90, int(o["angle_step"])))
        except (TypeError, ValueError):
            pass
    if o.get("cand_cap") is not None:
        try:
            out["cand_cap"] = max(20, min(400, int(o["cand_cap"])))
        except (TypeError, ValueError):
            pass
    if o.get("max_accepts") is not None:
        try:
            out["max_accepts"] = max(1, min(64, int(o["max_accepts"])))
        except (TypeError, ValueError):
            pass
    if o.get("x_slack") is not None:
        try:
            out["x_slack"] = max(0, int(o["x_slack"]))
        except (TypeError, ValueError):
            pass
    if isinstance(o.get("radii"), (list, tuple)) and o["radii"]:
        try:
            out["radii"] = tuple(float(r) for r in o["radii"])
        except (TypeError, ValueError):
            pass
    groups = o.get("rigid_groups") or o.get("_rigid_groups")
    if isinstance(groups, list):
        out["groups"] = groups
    # round defaults true; allow explicit false for preview-only catalog
    if "round" in o:
        v = o.get("round")
        out["round"] = v in (True, 1, "1", "true", "yes", "on") or v is True
    else:
        out["round"] = True
    if o.get("group_key") is not None:
        out["group_key"] = str(o.get("group_key") or "").strip()
    return out
