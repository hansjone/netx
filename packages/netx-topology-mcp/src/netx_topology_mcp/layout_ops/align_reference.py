"""Align a canvas to a reference layout (e.g. hand golden / UME).

Uses shared ``fabric_node_id``s. Preferred mode ``similarity``: map reference
portal chord → target portal chord (scale+rotate+translate), then place every
shared node from the transformed reference. Target-only leftovers stay put
(or park toward nearest aligned neighbour).

This is the escape hatch when gated local polish stalls: reuse known-good
geometry instead of more until_limit.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.graph_util import bbox
from netx_topology_mcp.layout_ops.orbit_sweep import _has_any_footprint_overlap
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _similarity_from_portals(
    ref: dict[str, tuple[float, float]],
    tgt_portals: dict[str, tuple[float, float]],
    portal_ids: list[str],
) -> tuple[float, float, float, float, float, float, float] | None:
    """Return (cos, sin, scale, tx, ty, rx0, ry0) mapping ref → target via two portals.

    x' = scale * R * (x - r0) + t0
    """
    if len(portal_ids) < 2:
        return None
    a, b = portal_ids[0], portal_ids[1]
    if a not in ref or b not in ref or a not in tgt_portals or b not in tgt_portals:
        return None
    r0 = ref[a]
    r1 = ref[b]
    t0 = tgt_portals[a]
    t1 = tgt_portals[b]
    rdx, rdy = r1[0] - r0[0], r1[1] - r0[1]
    tdx, tdy = t1[0] - t0[0], t1[1] - t0[1]
    rlen = math.hypot(rdx, rdy)
    tlen = math.hypot(tdx, tdy)
    if rlen < 1e-6 or tlen < 1e-6:
        return None
    scale = tlen / rlen
    ang = math.atan2(tdy, tdx) - math.atan2(rdy, rdx)
    return (math.cos(ang), math.sin(ang), scale, t0[0], t0[1], r0[0], r0[1])


def _apply_sim(
    xy: tuple[float, float],
    sim: tuple[float, float, float, float, float, float, float],
) -> tuple[float, float]:
    cos_a, sin_a, scale, tx, ty, rx0, ry0 = sim
    dx, dy = xy[0] - rx0, xy[1] - ry0
    return (
        tx + scale * (dx * cos_a - dy * sin_a),
        ty + scale * (dx * sin_a + dy * cos_a),
    )


def _procrustes_similarity(
    src: dict[str, tuple[float, float]],
    dst: dict[str, tuple[float, float]],
    ids: list[str],
) -> tuple[float, float, float, float, float, float, float] | None:
    """Umeyama-like 2D similarity from matched point pairs (ids in both)."""
    pts = [i for i in ids if i in src and i in dst]
    if len(pts) < 2:
        return None
    sx = sum(src[i][0] for i in pts) / len(pts)
    sy = sum(src[i][1] for i in pts) / len(pts)
    dx = sum(dst[i][0] for i in pts) / len(pts)
    dy = sum(dst[i][1] for i in pts) / len(pts)
    var_s = 0.0
    cross = 0.0  # complex: sum conj(s)*d
    # Using: scale*R maps (s-mean_s) → (d-mean_d)
    sum_xx = sum_yy = sum_xy = sum_yx = 0.0
    for i in pts:
        sx0, sy0 = src[i][0] - sx, src[i][1] - sy
        dx0, dy0 = dst[i][0] - dx, dst[i][1] - dy
        var_s += sx0 * sx0 + sy0 * sy0
        sum_xx += sx0 * dx0
        sum_yy += sy0 * dy0
        sum_xy += sx0 * dy0
        sum_yx += sy0 * dx0
    if var_s < 1e-8:
        return None
    # R = [[c,-s],[s,c]]; from SVD of covariance — 2D closed form:
    # mu = atan2(sum_xy - sum_yx, sum_xx + sum_yy) ... use complex
    re = sum_xx + sum_yy
    im = sum_xy - sum_yx
    ang = math.atan2(im, re)
    cos_a, sin_a = math.cos(ang), math.sin(ang)
    # scale = trace(R^T Cov) / var_s
    scale = (re * cos_a + im * sin_a) / var_s
    if scale < 1e-6:
        scale = 1.0
    # x' = scale R (x - mean_s) + mean_d
    return (cos_a, sin_a, scale, dx, dy, sx, sy)


def align_to_reference(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    reference: dict[str, tuple[float, float]],
    portal_ids: list[str] | None = None,
    mode: str = "similarity",
    park_missing: bool = True,
    freeze_portals: bool = True,
) -> OpResult:
    """Rewrite ``state.positions`` from ``reference`` geometry.

    ``mode``:
      - ``similarity``: portal (or Procrustes) similarity; keep target portals
        fixed when ``freeze_portals`` and portals provided.
      - ``adopt``: copy reference coords for shared ids (normalize origin ≥40).
    """
    del params
    st = state.copy()
    pos = dict(st.positions)
    ref = {k: v for k, v in reference.items() if k in pos or True}
    portals = [str(p) for p in (portal_ids or []) if str(p).strip()]
    shared = sorted(set(pos) & set(ref))
    if len(shared) < 2:
        return OpResult(
            state=st,
            moved=set(),
            op="align_reference",
            note="align_reference:too_few_shared",
            params={"error": "too_few_shared", "shared_n": len(shared)},
        )

    mode_k = str(mode or "similarity").strip().lower() or "similarity"
    g0 = count_edge_crossings(pos, st.links)
    area0 = 0.0
    if len(pos) >= 2:
        x0, y0, x1, y1 = bbox(pos)
        area0 = max((x1 - x0) * (y1 - y0), 1.0)

    new_pos = dict(pos)
    moved: set[str] = set()
    sim = None
    meta_mode = mode_k

    if mode_k in {"adopt", "copy", "absolute"}:
        xs = [ref[i][0] for i in shared]
        ys = [ref[i][1] for i in shared]
        ox, oy = min(xs), min(ys)
        pad = 40.0
        for nid in shared:
            new_pos[nid] = (ref[nid][0] - ox + pad, ref[nid][1] - oy + pad)
            moved.add(nid)
        meta_mode = "adopt"
    else:
        # similarity
        if len(portals) >= 2 and all(p in ref and p in pos for p in portals[:2]):
            sim = _similarity_from_portals(ref, pos, portals[:2])
        if sim is None:
            # Procrustes on hubs: prefer portals if present else all shared
            pivot = [p for p in portals if p in shared] if portals else shared
            if len(pivot) < 2:
                pivot = shared
            sim = _procrustes_similarity(ref, pos, pivot[: min(32, len(pivot))])
        if sim is None:
            return OpResult(
                state=st,
                moved=set(),
                op="align_reference",
                note="align_reference:no_transform",
                params={"error": "no_transform", "shared_n": len(shared)},
            )
        for nid in shared:
            if freeze_portals and nid in portals[:2]:
                continue  # keep target portals pinned
            new_pos[nid] = _apply_sim(ref[nid], sim)
            moved.add(nid)
        meta_mode = "similarity"

    # Target-only: park near nearest aligned neighbour
    only_tgt = [n for n in pos if n not in ref]
    parked = 0
    if park_missing and only_tgt and shared:
        for nid in only_tgt:
            # nearest shared by old distance
            nb = min(
                shared,
                key=lambda s: _dist(pos[nid], pos[s]),
            )
            old_dx = pos[nid][0] - pos[nb][0]
            old_dy = pos[nid][1] - pos[nb][1]
            # shrink orphan offset toward new nb (keeps relative stub)
            new_pos[nid] = (new_pos[nb][0] + old_dx * 0.85, new_pos[nb][1] + old_dy * 0.85)
            moved.add(nid)
            parked += 1

    # Optional flip across portal chord if it lowers crossings
    if (
        meta_mode == "similarity"
        and len(portals) >= 2
        and portals[0] in new_pos
        and portals[1] in new_pos
    ):
        pa, pb = new_pos[portals[0]], new_pos[portals[1]]
        flipped = dict(new_pos)
        ax, ay = pa
        bx, by = pb
        dx, dy = bx - ax, by - ay
        llen2 = dx * dx + dy * dy
        if llen2 > 1e-8:
            for nid, (x, y) in new_pos.items():
                if nid in portals[:2]:
                    continue
                # reflect point across line AB
                t = ((x - ax) * dx + (y - ay) * dy) / llen2
                projx, projy = ax + t * dx, ay + t * dy
                flipped[nid] = (2 * projx - x, 2 * projy - y)
            g_a = count_edge_crossings(new_pos, st.links)
            g_b = count_edge_crossings(flipped, st.links)
            if g_b < g_a:
                new_pos = flipped
                meta_mode = "similarity_flipped"

    st.positions = new_pos
    st.last_moved = moved
    g1 = count_edge_crossings(new_pos, st.links)
    ov = _has_any_footprint_overlap(new_pos, st.names)
    x0, y0, x1, y1 = bbox(new_pos) if len(new_pos) >= 2 else (0, 0, 1, 1)
    area1 = max((x1 - x0) * (y1 - y0), 1.0)
    meta: dict[str, Any] = {
        "mode": meta_mode,
        "shared_n": len(shared),
        "moved_n": len(moved),
        "parked_n": parked,
        "target_only_n": len(only_tgt),
        "ref_only_n": len(set(ref) - set(pos)),
        "portal_ids": portals[:4],
        "freeze_portals": bool(freeze_portals),
        "start_crossings": g0,
        "end_crossings": g1,
        "overlaps": bool(ov),
        "start_area": round(area0, 1),
        "end_area": round(area1, 1),
        "scale": round(float(sim[2]), 4) if sim else None,
    }
    st.meta["align_reference"] = meta
    return OpResult(
        state=st,
        moved=moved,
        op="align_reference",
        params=meta,
        note=(
            f"align_reference mode={meta_mode} shared={len(shared)} "
            f"moved={len(moved)} x={g0}→{g1} area={meta['start_area']}→{meta['end_area']}"
        ),
    )


def align_reference_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    portals = o.get("portal_ids") or o.get("portals") or []
    if isinstance(portals, str):
        portals = [portals]
    return {
        "portal_ids": [str(x) for x in portals if str(x).strip()],
        "mode": str(o.get("mode") or o.get("align_mode") or "similarity").strip().lower(),
        "park_missing": bool(o.get("park_missing", True)),
        "freeze_portals": bool(o.get("freeze_portals", True)),
        "reference_view_id": str(
            o.get("reference_view_id") or o.get("ref_view_id") or ""
        ).strip(),
    }
