"""Uniform bbox shrink toward eye portals — eye-safe compactness.

Hard gates (must all pass to accept a scale):
- global crossings must not rise
- footprint overlaps must stay zero
- frozen portal_ids stay put

Optional soft: prefer scales that do not worsen edge_clearance hits.
"""

from __future__ import annotations

from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    compute_edge_clearance,
)
from netx_topology_mcp.layout_ops.graph_util import bbox
from netx_topology_mcp.layout_ops.orbit_sweep import _has_any_footprint_overlap
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _anchor(
    pos: dict[str, tuple[float, float]],
    frozen: set[str],
) -> tuple[float, float]:
    pts = [pos[n] for n in frozen if n in pos]
    if len(pts) >= 1:
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )
    if not pos:
        return (0.0, 0.0)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)


def _area(pos: dict[str, tuple[float, float]]) -> float:
    if len(pos) < 2:
        return 1.0
    x0, y0, x1, y1 = bbox(pos)
    return max((x1 - x0) * (y1 - y0), 1.0)


def _clr_hits(pos: dict[str, tuple[float, float]], links, names) -> int:
    ec = compute_edge_clearance(pos, links, names=names, top_n=1)
    if ec.get("edge_clearance_skipped"):
        return 10**9
    return int(ec.get("edge_clearance_hits") or 0)


def compact_bbox(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    frozen_ids: set[str] | None = None,
    portal_ids: list[str] | None = None,
    min_scale: float = 0.72,
    step: float = 0.03,
    max_clearance_slack: int = 80,
    outlier_only: bool = True,
) -> OpResult:
    """Probe shrink toward portals; keep best gated layout.

    ``outlier_only``: only move nodes farther than ~median radius from the
    portal anchor — avoids crushing the already-dense eye core into overlaps.
    """
    import math

    del params
    st = state.copy()
    pos0 = dict(st.positions)
    names = st.names
    links = list(st.links)
    frozen: set[str] = set(frozen_ids or ())
    for p in portal_ids or []:
        if p:
            frozen.add(str(p))
    if not pos0:
        return OpResult(
            state=st,
            moved=set(),
            op="compact_bbox",
            note="compact_bbox:empty",
            params={"error": "empty"},
        )

    g0 = count_edge_crossings(pos0, links)
    if _has_any_footprint_overlap(pos0, names):
        return OpResult(
            state=st,
            moved=set(),
            op="compact_bbox",
            note="compact_bbox:overlaps_before",
            params={
                "error": "overlaps_before",
                "hint": "Refuse shrink while footprints already overlap.",
            },
        )
    clr0 = _clr_hits(pos0, links, names)
    area0 = _area(pos0)
    cx, cy = _anchor(pos0, frozen)

    dists = {
        n: math.hypot(x - cx, y - cy)
        for n, (x, y) in pos0.items()
        if n not in frozen
    }
    mobile: set[str]
    if outlier_only and dists:
        # Farthest-K only: percentile bands often crush mid-ring nodes into overlaps.
        k = max(16, min(40, len(dists) // 6))
        mobile = {
            n
            for n, _ in sorted(dists.items(), key=lambda kv: kv[1], reverse=True)[:k]
        }
    else:
        mobile = set(dists.keys())

    best_pos = pos0
    best_meta: dict[str, Any] = {
        "scale": 1.0,
        "crossings": g0,
        "clearance_hits": clr0,
        "area": round(area0, 1),
        "accepted": False,
    }
    best_key = (g0, area0, clr0)

    scales: list[float] = []
    s = 1.0 - float(step)
    lo = max(0.5, float(min_scale))
    while s >= lo - 1e-9:
        scales.append(round(s, 4))
        s -= float(step)

    for scale in scales:
        trial: dict[str, tuple[float, float]] = {}
        for nid, (x, y) in pos0.items():
            if nid in frozen or nid not in mobile:
                trial[nid] = (x, y)
            else:
                trial[nid] = (
                    cx + (x - cx) * scale,
                    cy + (y - cy) * scale,
                )
        if _has_any_footprint_overlap(trial, names):
            continue
        g1 = count_edge_crossings(trial, links)
        if g1 > g0:
            continue
        clr1 = _clr_hits(trial, links, names)
        if clr1 > clr0 + max(0, int(max_clearance_slack)):
            continue
        area1 = _area(trial)
        key = (g1, area1, clr1)
        if key < best_key:
            best_key = key
            best_pos = trial
            best_meta = {
                "scale": scale,
                "crossings": g1,
                "clearance_hits": clr1,
                "area": round(area1, 1),
                "accepted": True,
            }

    moved = {
        n
        for n, xy in best_pos.items()
        if n in pos0
        and (
            abs(xy[0] - pos0[n][0]) > 0.05
            or abs(xy[1] - pos0[n][1]) > 0.05
        )
    }
    st.positions = best_pos
    st.last_moved = moved
    meta = {
        "mode": "compact_bbox",
        "anchor": [round(cx, 1), round(cy, 1)],
        "frozen_n": len(frozen),
        "mobile_n": len(mobile),
        "outlier_only": bool(outlier_only),
        "start_crossings": g0,
        "start_clearance_hits": clr0,
        "start_area": round(area0, 1),
        "end_crossings": int(best_meta["crossings"]),
        "end_clearance_hits": int(best_meta["clearance_hits"]),
        "end_area": best_meta["area"],
        "scale": best_meta["scale"],
        "moved_n": len(moved),
        "accepted": bool(best_meta["accepted"]),
    }
    st.meta["compact_bbox"] = meta
    return OpResult(
        state=st,
        moved=moved,
        op="compact_bbox",
        params=meta,
        note=(
            f"compact_bbox scale={meta['scale']} "
            f"Δx={meta['end_crossings'] - g0} "
            f"clr={clr0}→{meta['end_clearance_hits']} "
            f"area={meta['start_area']}→{meta['end_area']}"
        ),
    )


def compact_bbox_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    portals = o.get("portal_ids") or o.get("portals") or []
    if isinstance(portals, str):
        portals = [portals]
    frozen = o.get("frozen_ids") or []
    if isinstance(frozen, str):
        frozen = [frozen]
    outlier = o.get("outlier_only")
    if outlier is None:
        outlier_only = True
    else:
        outlier_only = str(outlier).strip().lower() not in {"0", "false", "no", "off"}
    return {
        "portal_ids": [str(x) for x in portals if str(x).strip()],
        "frozen_ids": {str(x) for x in frozen if str(x).strip()},
        "min_scale": float(o.get("min_scale") or 0.72),
        "step": float(o.get("step") or 0.03),
        "max_clearance_slack": int(o.get("max_clearance_slack") or 80),
        "outlier_only": outlier_only,
    }
