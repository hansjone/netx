"""Polar orbit sweep: suggest top-3 single-node drags by crossing score.

Agent workflow: preview → pick rank 1..3 → apply; or round=true to auto-apply
#1 for each hot node when global crossings drop.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    crossing_participation,
    crossings_involving_node,
    node_footprint,
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


def _node_overlaps_any(
    node: str, pos: dict[str, tuple[float, float]], names: dict[str, str]
) -> bool:
    ax0, ay0, ax1, ay1 = _box(node, pos, names)
    for b, (x, y) in pos.items():
        if b == node:
            continue
        if abs(x - pos[node][0]) > 80 and abs(y - pos[node][1]) > 60:
            continue
        bx0, by0, bx1, by1 = _box(b, pos, names)
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


def _score_key(c: dict[str, Any]) -> tuple:
    return (
        int(c["crossings"]["global"]),
        int(c["crossings"]["incident"]),
        float(c.get("stretch") or 1.0),
        float(c.get("r") or 0.0),
    )


def _rerank_by_total(
    scored: list[dict[str, Any]],
    nid: str,
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
    *,
    re_rank_n: int = 20,
) -> list[dict[str, Any]]:
    """Re-rank top candidates by crossing + edge_clearance (multi-objective).

    A move may increase crossings but resolve several edge-clearance hits;
    ranking by ``crossings + edge_clearance_hits`` aligns with verdict.total.
    """
    from netx_topology_mcp.layout_metrics import compute_edge_clearance

    n = min(re_rank_n, len(scored))
    for c in scored[:n]:
        trial = dict(pos)
        trial[nid] = (float(c["x"]), float(c["y"]))
        ec = compute_edge_clearance(trial, links, names=names, top_n=1)
        c["edge_clearance_hits"] = int(ec.get("edge_clearance_hits") or 0)
    head = sorted(
        scored[:n],
        key=lambda c: (
            int(c["crossings"]["global"]) + int(c.get("edge_clearance_hits", 0)),
            int(c["crossings"]["incident"]),
            float(c.get("stretch") or 1.0),
        ),
    )
    return head + scored[n:]


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
    top_k: int = 3,
    y_min: float | None = None,
    y_max: float | None = None,
    objective: str = "crossing",
) -> dict[str, Any]:
    """Sweep polar candidates for one node; return diversified top-k.

    Default ``protect_rigid=off`` so multi-round orbit may move portals to cut
    crossings; other layout actions keep portal freeze. Opt in with portals/all.
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
    # Local untangle-style jumps stay near neighbor centroid; metro bridges
    # (max_jump≫1k) must be allowed to leave the unit blob.
    if jump > 1200:
        nbs_cap = jump * 2.5
    else:
        nbs_cap = max(_MAX_FROM_NBS, jump * 1.25)
    target_nn = float(getattr(params, "target_nn", 155.0) or 155.0)

    x0, y0 = pos[nid]
    global0 = count_edge_crossings(pos, links)
    local0 = crossings_involving_node(nid, pos, links, adj)

    # Coarse grid.
    coarse_step = max(angle_step, 24 if n_links >= 200 else angle_step)
    samples = _polar_grid(x0, y0, jump=jump, angle_step=coarse_step)
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
        for r in (180.0, 360.0, 640.0, 1200.0):
            if r > jump:
                continue
            samples.append((x0 + px * r, y0 + py * r, r, (ang + 90) % 360))
            samples.append((x0 - px * r, y0 - py * r, r, (ang + 270) % 360))
    # Dedup by rounded xy.
    seen: set[tuple[int, int]] = set()
    uniq: list[tuple[float, float, float, float]] = []
    for sx, sy, r, ang in samples:
        key = (int(round(sx)), int(round(sy)))
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
    # Multi-objective re-rank: optimize total score, not just crossings
    use_total = objective == "total" and len(scored) > 1
    base_clearance_hits = 0
    if use_total:
        from netx_topology_mcp.layout_metrics import compute_edge_clearance

        ec0 = compute_edge_clearance(pos, links, names=names, top_n=1)
        base_clearance_hits = int(ec0.get("edge_clearance_hits") or 0)
        scored = _rerank_by_total(scored, nid, pos, names, links, adj)
    # Prefer improving moves; still return best even if none improve.
    if use_total:
        base_total = int(global0) + base_clearance_hits
        improving = [
            c
            for c in scored
            if int(c["crossings"]["global"]) + int(c.get("edge_clearance_hits", 0)) < base_total
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
        "objective": objective,
        "y_band": (
            None if (y_min is None and y_max is None)
            else [y_min, y_max]
        ),
        "hint": (
            "prefer rank1 unless util/label concern; then pick 2/3. "
            "apply with params.pick=1|2|3 or updateTopologyViewPositions."
            + (" objective=total: rank by crossing+edge_clearance, may trade crossings for clearance."
               if objective == "total" else "")
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
    focus_ids: list[str] | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    objective: str = "crossing",
) -> OpResult:
    """Scan hot nodes; auto-apply each node's rank-1 if the active objective improves.

    Default ``protect_rigid=off`` (may move portals). Opt in with portals/all.
    """
    params = params or LayoutParams()
    st = state.copy()
    pos = dict(st.positions)
    names = dict(st.names)
    links = list(st.links)
    adj = {n: set(st.adj.get(n, ())) for n in pos}

    frozen = _resolve_frozen(st, protect_rigid, frozen_ids)

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
            out["pick"] = max(1, min(3, int(o["pick"])))
        except (TypeError, ValueError):
            out["pick"] = 1
    if o.get("round") is not None:
        out["round"] = str(o.get("round")).lower() not in {
            "0",
            "false",
            "no",
            "off",
            "",
        } or o.get("round") is True
    for key, cast, default in (
        ("top_n", int, 12),
        ("max_degree", int, 9),
        ("angle_step", int, None),
        ("cand_cap", int, 280),
        ("top_k", int, 3),
    ):
        if key not in o or o[key] is None:
            if default is not None:
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
    else:
        # orbit breaks rigid by default (opt in with protect_rigid=portals).
        out["protect_rigid"] = "off"
    focus = o.get("focus_ids") or o.get("focus_node_ids")
    if isinstance(focus, list):
        out["focus_ids"] = [str(x).strip() for x in focus if str(x).strip()]
    # portal freeze from polish path
    raw_p = o.get("portal_ids")
    if isinstance(raw_p, list):
        out["frozen_ids"] = {str(x) for x in raw_p if str(x)}
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
