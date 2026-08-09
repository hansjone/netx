"""Inward densify sweep: top-3 single-node pulls + corridor_cap scan.

Agent workflow: preview → pick rank 1..3 → apply; round=true auto-applies #1
when global crossings do not rise and stretch falls; phase=corridor / corridor=true
sweeps shrink_long_corridors params for util gain.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    crossings_involving_node,
)
from netx_topology_mcp.layout_ops.hotspots import overlapping_nodes
from netx_topology_mcp.layout_ops.orbit_sweep import (
    _diversify_top,
    _eval_candidate,
    _incident_stretch,
)
from netx_topology_mcp.layout_ops.rigid_units import (
    frozen_ids_for_protect,
    groups_from_compose_meta,
    shrink_long_corridors,
)
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

_MAX_PULL = 900.0
_MAX_PULL_CAP = 2200.0
_PULL_FRACS = (0.12, 0.22, 0.35, 0.5, 0.7)
_ANGLE_JITTERS = (-30.0, -15.0, 0.0, 15.0, 30.0)
_DEFAULT_CORRIDOR_CAPS = (1200.0, 1600.0, 2200.0, 3200.0)
_DEFAULT_PULLS = (0.35, 0.5, 0.65)


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
        frozen = frozen_ids_for_protect(st, protect_rigid)
    return frozen


def _groups_from_state(state: LayoutState) -> list[dict[str, Any]]:
    return groups_from_compose_meta((state.meta or {}).get("compose_views"))


def _membership_maps(
    groups: list[dict[str, Any]],
    valid: set[str],
) -> tuple[dict[str, list[str]], set[str], dict[str, tuple[float, float]]]:
    """Return node→group_keys, shared portals, group_key→exclusive centroid xy."""
    counts: dict[str, int] = {}
    g_members: dict[str, list[str]] = {}
    for g in groups:
        key = str(g.get("key") or "")
        members = [str(n) for n in (g.get("node_ids") or []) if str(n) in valid]
        if len(members) < 2:
            continue
        g_members[key] = members
        for n in members:
            counts[n] = counts.get(n, 0) + 1
    shared = {n for n, c in counts.items() if c > 1}
    # Need positions later — centroids filled by caller.
    return (
        {n: [k for k, ms in g_members.items() if n in ms] for n in counts},
        shared,
        {},
    )


def _exclusive_centroids(
    groups: list[dict[str, Any]],
    pos: dict[str, tuple[float, float]],
    shared: set[str],
) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for g in groups:
        key = str(g.get("key") or "")
        members = [str(n) for n in (g.get("node_ids") or []) if str(n) in pos]
        exclusive = [n for n in members if n not in shared]
        use = exclusive if len(exclusive) >= 1 else members
        if not use:
            continue
        cx = sum(pos[n][0] for n in use) / len(use)
        cy = sum(pos[n][1] for n in use) / len(use)
        out[key] = (cx, cy)
    return out


def _anchor_for_node(
    nid: str,
    pos: dict[str, tuple[float, float]],
    adj: dict[str, set[str]],
    *,
    node_groups: dict[str, list[str]],
    centroids: dict[str, tuple[float, float]],
    global_cx: float,
    global_cy: float,
) -> tuple[float, float, str]:
    nbs = [pos[v] for v in adj.get(nid, ()) if v in pos]
    if nbs:
        ax = sum(p[0] for p in nbs) / len(nbs)
        ay = sum(p[1] for p in nbs) / len(nbs)
        return ax, ay, "neighbors"
    for gk in node_groups.get(nid, ()):
        if gk in centroids:
            cx, cy = centroids[gk]
            return cx, cy, "unit"
    return global_cx, global_cy, "global"


def _inward_samples(
    x0: float,
    y0: float,
    ax: float,
    ay: float,
    *,
    max_pull: float,
) -> list[tuple[float, float, float, float]]:
    """Samples on ray toward anchor; never past the anchor; capped by max_pull."""
    dx, dy = ax - x0, ay - y0
    dist = math.hypot(dx, dy)
    if dist < 8.0:
        return []
    ux, uy = dx / dist, dy / dist
    px, py = -uy, ux
    out: list[tuple[float, float, float, float]] = []
    base_ang = math.degrees(math.atan2(uy, ux)) % 360.0
    for t in _PULL_FRACS:
        pull = min(dist * t, max_pull)
        if pull < 12.0:
            continue
        # Stay short of the anchor (leave 4px slack).
        pull = min(pull, dist - 4.0)
        if pull < 12.0:
            continue
        for dang in _ANGLE_JITTERS:
            if dang == 0.0:
                nx = x0 + ux * pull
                ny = y0 + uy * pull
                ang = base_ang
            else:
                rad = math.radians(base_ang + dang)
                c, s = math.cos(rad), math.sin(rad)
                nx = x0 + c * pull
                ny = y0 + s * pull
                # Reject if farther from anchor than start.
                if math.hypot(nx - ax, ny - ay) >= dist - 1e-6:
                    continue
                ang = (base_ang + dang) % 360.0
            r = math.hypot(nx - x0, ny - y0)
            if r < 12.0 or r > max_pull + 1:
                continue
            out.append((nx, ny, r, ang))
    # Mild perpendicular nudges at mid pull (still closer to anchor).
    mid = min(dist * 0.35, max_pull)
    if mid >= 20.0:
        for sign in (-1.0, 1.0):
            nx = x0 + ux * mid + px * sign * mid * 0.25
            ny = y0 + uy * mid + py * sign * mid * 0.25
            if math.hypot(nx - ax, ny - ay) >= dist - 1e-6:
                continue
            r = math.hypot(nx - x0, ny - y0)
            if 12.0 <= r <= max_pull + 1:
                ang = math.degrees(math.atan2(ny - y0, nx - x0)) % 360.0
                out.append((nx, ny, r, ang))
    return out


def _densify_score_key(c: dict[str, Any]) -> tuple:
    # Crossings first; then shorter incident stretch; prefer larger inward pull.
    return (
        int(c["crossings"]["global"]),
        int(c["crossings"]["incident"]),
        float(c.get("stretch") or 1.0),
        -float(c.get("r") or 0.0),
    )


def densify_sweep_node(
    state: LayoutState,
    node_id: str,
    *,
    params: LayoutParams | None = None,
    max_pull: float | None = None,
    nn_floor: float = 60.0,
    min_angle_sep: float = 35.0,
    cand_cap: int = 180,
    protect_rigid: bool | str = "off",
    frozen_ids: set[str] | None = None,
    top_k: int = 3,
    groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Inward pull candidates for one node; return diversified top-k.

    Default ``protect_rigid=off`` so multi-round densify may move portals;
    other layout actions keep portal freeze. Pass ``protect_rigid=portals``
    to opt back into rigid protection.
    """
    params = params or LayoutParams()
    st = state
    nid = str(node_id).strip()
    pos = dict(st.positions)
    names = dict(st.names)
    links = list(st.links)
    adj = {n: set(st.adj.get(n, ())) for n in pos}
    if nid not in pos:
        return {"ok": False, "error": "node_not_on_view", "node_id": nid}

    frozen = _resolve_frozen(st, protect_rigid, frozen_ids)
    if nid in frozen:
        return {
            "ok": False,
            "error": "frozen",
            "node_id": nid,
            "hint": "portal/rigid frozen; protect_rigid=off or pick a corridor node",
        }

    pull = float(max_pull if max_pull is not None else _MAX_PULL)
    pull = max(80.0, min(pull, _MAX_PULL_CAP))
    nbs_cap = pull * 2.5
    target_nn = float(getattr(params, "target_nn", 155.0) or 155.0)

    groups = groups if groups is not None else _groups_from_state(st)
    valid = set(pos)
    node_groups, shared, _ = _membership_maps(groups, valid)
    # With groups + protect on: shared portals stay frozen.
    if groups and nid in shared and not _protect_is_off(protect_rigid):
        return {
            "ok": False,
            "error": "shared_portal",
            "node_id": nid,
            "hint": "shared portals frozen; densify defaults protect_rigid=off",
        }

    centroids = _exclusive_centroids(groups, pos, shared)
    gcx = sum(p[0] for p in pos.values()) / max(len(pos), 1)
    gcy = sum(p[1] for p in pos.values()) / max(len(pos), 1)
    ax, ay, anchor_kind = _anchor_for_node(
        nid,
        pos,
        adj,
        node_groups=node_groups,
        centroids=centroids,
        global_cx=gcx,
        global_cy=gcy,
    )

    x0, y0 = pos[nid]
    global0 = count_edge_crossings(pos, links)
    local0 = crossings_involving_node(nid, pos, links, adj)
    stretch0 = _incident_stretch(nid, pos, adj, target_nn)

    samples = _inward_samples(x0, y0, ax, ay, max_pull=pull)
    # Also try unit centroid if neighbor anchor was used.
    if anchor_kind == "neighbors":
        for gk in node_groups.get(nid, ()):
            if gk in centroids:
                cx, cy = centroids[gk]
                samples.extend(_inward_samples(x0, y0, cx, cy, max_pull=pull))

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
        if c is None:
            continue
        # Densify: reject moves that raise global crossings.
        if int(c["delta"]["global"]) > 0:
            continue
        scored.append(c)

    scored.sort(key=_densify_score_key)
    # Prefer stretch drop among non-rising-x candidates.
    improving = [
        c
        for c in scored
        if c["delta"]["global"] <= 0 and float(c.get("stretch") or 99) < stretch0 - 1e-4
    ]
    pool = improving if improving else scored
    top = _diversify_top(pool, k=max(1, int(top_k)), min_angle_sep=min_angle_sep)

    return {
        "ok": True,
        "node_id": nid,
        "name": names.get(nid, nid),
        "x0": round(x0, 1),
        "y0": round(y0, 1),
        "degree": len(adj.get(nid, ())),
        "anchor": {"x": round(ax, 1), "y": round(ay, 1), "kind": anchor_kind},
        "stretch_before": round(stretch0, 3),
        "crossings_before": {"global": int(global0), "incident": int(local0)},
        "candidates": top,
        "sampled": len(seen),
        "improving_n": len(improving),
        "max_pull": pull,
        "nn_floor": nn_floor,
        "hint": (
            "prefer rank1 (inward, x not up, stretch down); pick 2/3 if label/util. "
            "apply with params.pick=1|2|3."
        ),
    }


def apply_densify_pick(
    state: LayoutState,
    sweep: dict[str, Any],
    *,
    pick: int = 1,
) -> OpResult:
    st = state.copy()
    nid = str(sweep.get("node_id") or "")
    cands = list(sweep.get("candidates") or [])
    if not nid or nid not in st.positions or not cands:
        return OpResult(
            state=st,
            moved=set(),
            op="densify_sweep",
            note="densify_sweep:noop",
            params={"error": "no_candidates"},
        )
    idx = max(1, min(int(pick), len(cands))) - 1
    chosen = cands[idx]
    st.positions[nid] = (float(chosen["x"]), float(chosen["y"]))
    st.last_moved = {nid}
    st.meta["densify_sweep"] = {
        "node_id": nid,
        "pick": idx + 1,
        "candidate": chosen,
        "crossings_before": sweep.get("crossings_before"),
    }
    return OpResult(
        state=st,
        moved={nid},
        op="densify_sweep",
        params={
            "node_id": nid,
            "pick": idx + 1,
            "candidate": chosen,
            "crossings_before": sweep.get("crossings_before"),
        },
        note=(
            f"densify_sweep pick={idx + 1} "
            f"g{sweep.get('crossings_before', {}).get('global')}->"
            f"{chosen['crossings']['global']}"
        ),
    )


def densify_sweep_round(
    state: LayoutState,
    *,
    params: LayoutParams | None = None,
    top_n: int = 16,
    max_degree: int = 8,
    max_pull: float | None = None,
    nn_floor: float = 60.0,
    min_angle_sep: float = 35.0,
    protect_rigid: bool | str = "off",
    frozen_ids: set[str] | None = None,
    focus_ids: list[str] | None = None,
    groups: list[dict[str, Any]] | None = None,
) -> OpResult:
    """Scan sparse/high-stretch nodes; auto-apply #1 if x not up and stretch drops.

    Default ``protect_rigid=off`` (may move portals). Opt in with portals/all.
    """
    params = params or LayoutParams()
    st = state.copy()
    pos = dict(st.positions)
    names = dict(st.names)
    links = list(st.links)
    adj = {n: set(st.adj.get(n, ())) for n in pos}
    target_nn = float(getattr(params, "target_nn", 155.0) or 155.0)

    frozen = _resolve_frozen(st, protect_rigid, frozen_ids)

    groups = groups if groups is not None else _groups_from_state(st)
    valid = set(pos)
    node_groups, shared, _ = _membership_maps(groups, valid)
    centroids = _exclusive_centroids(groups, pos, shared)
    if groups and not _protect_is_off(protect_rigid):
        frozen |= shared

    gcx = sum(p[0] for p in pos.values()) / max(len(pos), 1)
    gcy = sum(p[1] for p in pos.values()) / max(len(pos), 1)
    global0 = count_edge_crossings(pos, links)

    focus = {str(x) for x in (focus_ids or []) if str(x)}
    scored_nodes: list[tuple[float, str]] = []
    for nid in pos:
        if nid in frozen:
            continue
        deg = len(adj.get(nid, ()))
        if deg >= max_degree or deg == 0:
            continue
        if groups and nid not in node_groups and not focus:
            continue
        stretch = _incident_stretch(nid, pos, adj, target_nn)
        ax, ay, _ = _anchor_for_node(
            nid,
            pos,
            adj,
            node_groups=node_groups,
            centroids=centroids,
            global_cx=gcx,
            global_cy=gcy,
        )
        dist = math.hypot(pos[nid][0] - ax, pos[nid][1] - ay)
        if stretch < 1.15 and dist < 180 and nid not in focus:
            continue
        # Higher stretch / farther from anchor first; focus boost.
        pri = 0.0 if nid in focus else 1.0
        scored_nodes.append((-stretch * 10 - dist / 500.0 + pri * 100, nid))
    scored_nodes.sort()
    movable = [nid for _, nid in scored_nodes[: max(1, int(top_n))]]

    moved: set[str] = set()
    trace: list[dict[str, Any]] = []
    for nid in movable:
        st.positions = pos
        sweep = densify_sweep_node(
            st,
            nid,
            params=params,
            max_pull=max_pull,
            nn_floor=nn_floor,
            min_angle_sep=min_angle_sep,
            protect_rigid="off",
            frozen_ids=frozen,
            groups=groups,
        )
        if not sweep.get("ok"):
            trace.append({"node_id": nid, "skipped": sweep.get("error")})
            continue
        cands = list(sweep.get("candidates") or [])
        if not cands:
            trace.append({"node_id": nid, "skipped": "no_candidates"})
            continue
        best = cands[0]
        stretch_b = float(sweep.get("stretch_before") or 0)
        stretch_a = float(best.get("stretch") or 0)
        if int(best["delta"]["global"]) > 0:
            trace.append(
                {
                    "node_id": nid,
                    "skipped": "crossing_up",
                    "best_delta": best["delta"],
                }
            )
            continue
        if stretch_a >= stretch_b - 1e-4:
            # Allow pure global drop even if stretch flat.
            if int(best["delta"]["global"]) >= 0:
                trace.append(
                    {
                        "node_id": nid,
                        "skipped": "no_stretch_gain",
                        "stretch_before": stretch_b,
                        "stretch_after": stretch_a,
                    }
                )
                continue
        pos[nid] = (float(best["x"]), float(best["y"]))
        moved.add(nid)
        trace.append(
            {
                "node_id": nid,
                "name": names.get(nid, nid),
                "applied": True,
                "pick": 1,
                "xy": [best["x"], best["y"]],
                "delta": best["delta"],
                "stretch": {"before": stretch_b, "after": stretch_a},
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
        "nn_floor": nn_floor,
    }
    st.meta["densify_sweep"] = meta
    return OpResult(
        state=st,
        moved=moved,
        op="densify_sweep_round",
        params=meta,
        note=f"densify_sweep_round {global0}->{end_g} moved={len(moved)}/{len(movable)}",
    )


def densify_corridor_scan(
    state: LayoutState,
    *,
    groups: list[dict[str, Any]] | None = None,
    corridor_caps: list[float] | None = None,
    pulls: list[float] | None = None,
    iters: int = 6,
    x_slack: int | None = None,
) -> OpResult:
    """Sweep corridor_cap×pull; keep best util with ov=0 and tight crossing slack."""
    st0 = state.copy()
    groups = groups if groups is not None else _groups_from_state(st0)
    before = score_state(st0)
    before_util = float((before.get("summary") or {}).get("util") or 0.0)
    before_x = int((before.get("summary") or {}).get("crossings") or 0)
    before_ov = int((before.get("summary") or {}).get("overlaps") or 0)
    if before_ov == 0:
        before_ov = len(overlapping_nodes(st0))
    xs = [p[0] for p in st0.positions.values()]
    ys = [p[1] for p in st0.positions.values()]
    area0 = max(max(xs) - min(xs), 1e-6) * max(max(ys) - min(ys), 1e-6)

    caps = list(corridor_caps) if corridor_caps else list(_DEFAULT_CORRIDOR_CAPS)
    pull_list = list(pulls) if pulls else list(_DEFAULT_PULLS)
    slack = (
        max(5, int(before_x * 0.05))
        if x_slack is None
        else max(0, int(x_slack))
    )

    trials: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    for cap in caps:
        for pull in pull_list:
            op = shrink_long_corridors(
                st0,
                edge_len_cap=float(cap),
                pull=float(pull),
                iters=max(1, int(iters)),
                max_bridges=8 if groups else 12,
                min_island=2 if groups else 6,
                groups=groups or None,
                accept_crossings=True,
            )
            fin = score_state(op.state)
            util = float((fin.get("summary") or {}).get("util") or 0.0)
            x1 = int((fin.get("summary") or {}).get("crossings") or 0)
            ov = len(overlapping_nodes(op.state))
            xs1 = [p[0] for p in op.state.positions.values()]
            ys1 = [p[1] for p in op.state.positions.values()]
            area1 = max(max(xs1) - min(xs1), 1e-6) * max(max(ys1) - min(ys1), 1e-6)
            area_ratio = area0 / area1
            row = {
                "corridor_cap": float(cap),
                "pull": float(pull),
                "util": util,
                "crossings": x1,
                "overlaps": ov,
                "moved_n": len(op.moved),
                "bbox_area_ratio": round(area_ratio, 4),
                "note": op.note,
            }
            trials.append(row)
            if ov > 0:
                continue
            if x1 > before_x + slack:
                continue
            # Accept if util rises at all, or bbox shrinks ≥0.8% (metro
            # corridors often move area×1.01 before util clears 1%).
            util_up = util > before_util + 1e-6
            area_up = area_ratio >= 1.008
            if not util_up and not area_up:
                continue
            rank = (util, area_ratio, -x1)
            if best is None or rank > best["rank"]:
                best = {
                    "rank": rank,
                    "state": op.state,
                    "moved": set(op.moved),
                    "row": row,
                }

    if best is None:
        meta = {
            "start_crossings": before_x,
            "end_crossings": before_x,
            "start_util": before_util,
            "end_util": before_util,
            "reverted": True,
            "reason": "no_util_gain",
            "trials": trials,
            "x_slack": slack,
        }
        st0.meta["densify_corridor"] = meta
        return OpResult(
            state=st0,
            moved=set(),
            op="densify_corridor_scan",
            params=meta,
            note="densify_corridor_scan:reverted no_util_gain",
        )

    st = best["state"]
    fin = score_state(st)
    end_util = float((fin.get("summary") or {}).get("util") or 0.0)
    end_x = int((fin.get("summary") or {}).get("crossings") or 0)
    meta = {
        "start_crossings": before_x,
        "end_crossings": end_x,
        "start_util": before_util,
        "end_util": end_util,
        "reverted": False,
        "chosen": best["row"],
        "trials": trials,
        "x_slack": slack,
        "moved_n": len(best["moved"]),
    }
    st.meta = dict(st.meta or {})
    st.meta["densify_corridor"] = meta
    st.last_moved = best["moved"]
    return OpResult(
        state=st,
        moved=best["moved"],
        op="densify_corridor_scan",
        params=meta,
        note=(
            f"densify_corridor_scan util {before_util:.4f}->{end_util:.4f} "
            f"x {before_x}->{end_x} "
            f"cap={best['row']['corridor_cap']:.0f} pull={best['row']['pull']}"
        ),
    )


def densify_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
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
    for flag in ("round", "corridor"):
        if o.get(flag) is not None:
            out[flag] = str(o.get(flag)).lower() not in {
                "0",
                "false",
                "no",
                "off",
                "",
            } or o.get(flag) is True
    phase = str(o.get("phase") or "").strip().lower()
    if phase in {"corridor", "corridors", "shrink"}:
        out["corridor"] = True
    elif phase in {"intra", "node", "round"}:
        out["round"] = out.get("round", True)
    for key, cast, default in (
        ("top_n", int, 16),
        ("max_degree", int, 8),
        ("cand_cap", int, 180),
        ("top_k", int, 3),
        ("iters", int, 6),
    ):
        if key not in o or o[key] is None:
            out[key] = default
            continue
        try:
            out[key] = cast(o[key])
        except (TypeError, ValueError):
            out[key] = default
    if o.get("max_pull") is not None:
        try:
            out["max_pull"] = float(o["max_pull"])
        except (TypeError, ValueError):
            pass
    if o.get("nn_floor") is not None:
        try:
            out["nn_floor"] = float(o["nn_floor"])
        except (TypeError, ValueError):
            out["nn_floor"] = 60.0
    else:
        out["nn_floor"] = 60.0
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
        # densify breaks rigid by default (opt in with protect_rigid=portals).
        out["protect_rigid"] = "off"
    focus = o.get("focus_ids") or o.get("focus_node_ids")
    if isinstance(focus, list):
        out["focus_ids"] = [str(x).strip() for x in focus if str(x).strip()]
    raw_p = o.get("portal_ids")
    if isinstance(raw_p, list):
        out["frozen_ids"] = {str(x) for x in raw_p if str(x)}
    caps = o.get("corridor_caps") or o.get("corridor_cap_list")
    if isinstance(caps, list) and caps:
        try:
            out["corridor_caps"] = [float(x) for x in caps]
        except (TypeError, ValueError):
            pass
    elif o.get("corridor_cap") is not None:
        try:
            out["corridor_caps"] = [float(o["corridor_cap"])]
        except (TypeError, ValueError):
            pass
    pulls = o.get("pulls") or o.get("pull_list")
    if isinstance(pulls, list) and pulls:
        try:
            out["pulls"] = [float(x) for x in pulls]
        except (TypeError, ValueError):
            pass
    elif o.get("pull") is not None:
        try:
            out["pulls"] = [float(o["pull"])]
        except (TypeError, ValueError):
            pass
    if o.get("x_slack") is not None:
        try:
            out["x_slack"] = int(o["x_slack"])
        except (TypeError, ValueError):
            pass
    return out
