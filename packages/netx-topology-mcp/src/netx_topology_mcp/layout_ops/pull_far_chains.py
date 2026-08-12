"""Pull far deg≤2 chains / isolates toward the eye portal anchor.

Unlike uniform ``compact_bbox``, only moves far corridors (and deg=0 orphans).
Hard gates: crossings not rise, overlaps stay 0, portals frozen.
Scale is toward the portal mid-point (not the chain hub) so outer hubs
still shrink the canvas bbox.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings, compute_edge_clearance
from netx_topology_mcp.layout_ops.graph_util import bbox
from netx_topology_mcp.layout_ops.orbit_sweep import _has_any_footprint_overlap
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_topology_quality import extract_chain_paths


def _anchor(pos: dict[str, tuple[float, float]], frozen: set[str]) -> tuple[float, float]:
    pts = [pos[n] for n in frozen if n in pos]
    if pts:
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )
    xs = [p[0] for p in pos.values()] or [0.0]
    ys = [p[1] for p in pos.values()] or [0.0]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _area(pos: dict[str, tuple[float, float]]) -> float:
    if len(pos) < 2:
        return 1.0
    x0, y0, x1, y1 = bbox(pos)
    return max((x1 - x0) * (y1 - y0), 1.0)


def _clr_hits(pos, links, names) -> int:
    ec = compute_edge_clearance(pos, links, names=names, top_n=1)
    if ec.get("edge_clearance_skipped"):
        return 10**9
    return int(ec.get("edge_clearance_hits") or 0)


def _try_scale(
    pos: dict[str, tuple[float, float]],
    mobile: list[str],
    *,
    cx: float,
    cy: float,
    scale: float,
    links,
    names,
    g0: int,
    clr0: int,
    max_clearance_slack: int,
) -> tuple[dict[str, tuple[float, float]], int, int, float] | None:
    trial = dict(pos)
    for n in mobile:
        x, y = pos[n]
        trial[n] = (cx + (x - cx) * scale, cy + (y - cy) * scale)
    if _has_any_footprint_overlap(trial, names):
        return None
    g1 = count_edge_crossings(trial, links)
    if g1 > g0:
        return None
    clr1 = _clr_hits(trial, links, names)
    if clr1 > clr0 + max(0, int(max_clearance_slack)):
        return None
    return trial, g1, clr1, _area(trial)


def pull_far_chains(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    frozen_ids: set[str] | None = None,
    portal_ids: list[str] | None = None,
    max_chains: int = 16,
    min_tip_radius: float = 1800.0,
    scales: tuple[float, ...] = (0.92, 0.88, 0.84, 0.80, 0.75),
    max_clearance_slack: int = 40,
    pull_isolates: bool = True,
) -> OpResult:
    """Shorten farthest corridors / orphans toward the portal mid-point."""
    del params
    st = state.copy()
    pos = dict(st.positions)
    names = st.names
    links = list(st.links)
    adj = st.adj
    frozen: set[str] = set(frozen_ids or ())
    for p in portal_ids or []:
        if p:
            frozen.add(str(p))

    g0 = count_edge_crossings(pos, links)
    if _has_any_footprint_overlap(pos, names):
        return OpResult(
            state=st,
            moved=set(),
            op="pull_far_chains",
            note="pull_far_chains:overlaps_before",
            params={"error": "overlaps_before"},
        )
    clr0 = _clr_hits(pos, links, names)
    area0 = _area(pos)
    cx, cy = _anchor(pos, frozen)

    chains = extract_chain_paths(adj)
    scored: list[tuple[float, list[str], str]] = []
    for path in chains:
        path = [n for n in path if n in pos]
        if len(path) < 2:
            continue
        d0 = math.hypot(pos[path[0]][0] - cx, pos[path[0]][1] - cy)
        d1 = math.hypot(pos[path[-1]][0] - cx, pos[path[-1]][1] - cy)
        tip = path[0] if d0 >= d1 else path[-1]
        hub = path[-1] if tip == path[0] else path[0]
        if tip in frozen:
            continue
        tip_r = max(d0, d1)
        if tip_r < float(min_tip_radius):
            continue
        scored.append((tip_r, path, hub))
    scored.sort(key=lambda t: t[0], reverse=True)

    moved: set[str] = set()
    accepted: list[dict[str, Any]] = []
    used: set[str] = set(frozen)

    for tip_r, path, hub in scored[: max(1, int(max_chains))]:
        # Scale corridor toward portal mid-point (shrinks bbox even if hub is outer).
        mobile = [n for n in path if n not in used and n in pos and n not in frozen]
        if len(mobile) < 1:
            continue
        best_local = None
        best_key = None
        for scale in scales:
            got = _try_scale(
                pos,
                mobile,
                cx=cx,
                cy=cy,
                scale=scale,
                links=links,
                names=names,
                g0=g0,
                clr0=clr0,
                max_clearance_slack=max_clearance_slack,
            )
            if got is None:
                continue
            trial, g1, clr1, area1 = got
            key = (g1, area1, clr1)
            if best_key is None or key < best_key:
                best_key = key
                best_local = (trial, scale, g1, clr1, area1)
        if best_local is None:
            continue
        trial, scale, g1, clr1, area1 = best_local
        tip = (
            path[0]
            if math.hypot(pos[path[0]][0] - cx, pos[path[0]][1] - cy)
            >= math.hypot(pos[path[-1]][0] - cx, pos[path[-1]][1] - cy)
            else path[-1]
        )
        tip_r1 = math.hypot(trial[tip][0] - cx, trial[tip][1] - cy)
        if tip_r1 >= tip_r - 1.0 and area1 >= area0 * 0.999:
            continue
        pos = trial
        g0 = g1
        clr0 = clr1
        for n in mobile:
            moved.add(n)
            used.add(n)
        accepted.append(
            {
                "hub": hub,
                "tip": tip,
                "member_n": len(mobile),
                "scale": scale,
                "tip_r0": round(tip_r, 1),
                "tip_r1": round(tip_r1, 1),
            }
        )

    isolates_n = 0
    if pull_isolates:
        orphans = [
            n
            for n, nbs in adj.items()
            if n in pos
            and n not in used
            and n not in frozen
            and len(nbs) == 0
            and math.hypot(pos[n][0] - cx, pos[n][1] - cy) >= float(min_tip_radius) * 0.6
        ]
        orphans.sort(
            key=lambda n: math.hypot(pos[n][0] - cx, pos[n][1] - cy),
            reverse=True,
        )
        for n in orphans[: max(4, int(max_chains) // 2)]:
            best_local = None
            best_key = None
            for scale in scales:
                got = _try_scale(
                    pos,
                    [n],
                    cx=cx,
                    cy=cy,
                    scale=scale,
                    links=links,
                    names=names,
                    g0=g0,
                    clr0=clr0,
                    max_clearance_slack=max_clearance_slack,
                )
                if got is None:
                    continue
                trial, g1, clr1, area1 = got
                key = (g1, area1, clr1)
                if best_key is None or key < best_key:
                    best_key = key
                    best_local = (trial, scale, g1, clr1)
            if best_local is None:
                continue
            trial, scale, g1, clr1 = best_local
            pos = trial
            g0 = g1
            clr0 = clr1
            moved.add(n)
            used.add(n)
            isolates_n += 1
            accepted.append(
                {
                    "hub": None,
                    "tip": n,
                    "member_n": 1,
                    "scale": scale,
                    "isolate": True,
                }
            )

    leaves = [
        n
        for n, nbs in adj.items()
        if n in pos
        and n not in used
        and n not in frozen
        and len(nbs) == 1
        and math.hypot(pos[n][0] - cx, pos[n][1] - cy) >= float(min_tip_radius)
    ]
    leaves.sort(
        key=lambda n: math.hypot(pos[n][0] - cx, pos[n][1] - cy),
        reverse=True,
    )
    for n in leaves[: max(4, int(max_chains) // 2)]:
        best_local = None
        best_key = None
        for scale in scales:
            got = _try_scale(
                pos,
                [n],
                cx=cx,
                cy=cy,
                scale=scale,
                links=links,
                names=names,
                g0=g0,
                clr0=clr0,
                max_clearance_slack=max_clearance_slack,
            )
            if got is None:
                continue
            trial, g1, clr1, area1 = got
            key = (g1, area1, clr1)
            if best_key is None or key < best_key:
                best_key = key
                best_local = (trial, scale, g1, clr1)
        if best_local is None:
            continue
        trial, scale, g1, clr1 = best_local
        pos = trial
        g0 = g1
        clr0 = clr1
        moved.add(n)
        used.add(n)
        accepted.append(
            {
                "hub": next(iter(adj.get(n) or ()), None),
                "tip": n,
                "member_n": 1,
                "scale": scale,
                "leaf": True,
            }
        )

    st.positions = pos
    st.last_moved = moved
    g_end = count_edge_crossings(pos, links)
    clr_end = _clr_hits(pos, links, names)
    meta = {
        "mode": "pull_far_chains",
        "chains_tried": min(len(scored), max(1, int(max_chains))),
        "chains_accepted": len(accepted),
        "accepted_chains": accepted[:20],
        "moved_n": len(moved),
        "isolates_pulled": isolates_n,
        "start_area": round(area0, 1),
        "end_area": round(_area(pos), 1),
        "start_crossings": count_edge_crossings(dict(state.positions), list(state.links)),
        "end_crossings": g_end,
        "start_clearance_hits": _clr_hits(dict(state.positions), list(state.links), names),
        "end_clearance_hits": clr_end,
    }
    st.meta["pull_far_chains"] = meta
    return OpResult(
        state=st,
        moved=moved,
        op="pull_far_chains",
        params=meta,
        note=(
            f"pull_far_chains n={len(accepted)} moved={len(moved)} "
            f"area={meta['start_area']}→{meta['end_area']} "
            f"x={meta['start_crossings']}→{meta['end_crossings']}"
        ),
    )


def pull_far_chains_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    portals = o.get("portal_ids") or o.get("portals") or []
    if isinstance(portals, str):
        portals = [portals]
    frozen = o.get("frozen_ids") or []
    if isinstance(frozen, str):
        frozen = [frozen]
    scales = o.get("scales")
    if isinstance(scales, (list, tuple)) and scales:
        sc = tuple(float(x) for x in scales)
    else:
        sc = (0.92, 0.88, 0.84, 0.80, 0.75)
    return {
        "portal_ids": [str(x) for x in portals if str(x).strip()],
        "frozen_ids": {str(x) for x in frozen if str(x).strip()},
        "max_chains": int(o.get("max_chains") or 16),
        "min_tip_radius": float(o.get("min_tip_radius") or 1800.0),
        "scales": sc,
        "max_clearance_slack": int(o.get("max_clearance_slack") or 40),
        "pull_isolates": bool(o.get("pull_isolates", True)),
    }
