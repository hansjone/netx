"""Greedy local untangle: move low-degree nodes to cut crossings (keep ov=0)."""

from __future__ import annotations

import math
import random
from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    crossing_participation,
    crossings_involving_node,
    node_footprint,
    top_crossing_nodes,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


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


def _participation(
    pos: dict[str, tuple[float, float]], links: list[tuple[str, str]]
) -> dict[str, int]:
    _n, hit = crossing_participation(pos, links)
    return hit


# Max jump from current position — prevents radial *scale* candidates from
# flinging nodes across a giant star and exploding util/bbox.
# Default raised: BTM-scale stars need ~900px jumps to escape petal local minima.
_MAX_JUMP = 900.0
_MAX_FROM_NBS = 1100.0
# Sparse metro compose can have spokes of several thousand px; a hard 1200
# cap makes untangle a no-op on the worst bridges.
_MAX_JUMP_CAP = 4800.0


def _candidates(
    pos: dict[str, tuple[float, float]],
    node: str,
    adj: dict[str, set[str]],
    rng: random.Random,
    *,
    max_jump: float = _MAX_JUMP,
    angle_step: int = 15,
    random_n: int = 12,
) -> list[tuple[float, float]]:
    x, y = pos[node]
    jump = max(200.0, min(float(max_jump), _MAX_JUMP_CAP))
    nbs_cap = max(_MAX_FROM_NBS, jump * 1.25)
    step = max(10, int(angle_step))
    cands: list[tuple[float, float]] = []
    radii = [80, 120, 180, 260, 360, 480]
    if jump > 520:
        radii = radii + [640, 800]
    if jump > 1200:
        radii = radii + [1200, 1600, 2200, min(jump, 3200)]
    if step >= 25:
        radii = [r for r in radii if r >= 120]
    for ang in range(0, 360, step):
        for r in radii:
            if r > jump + 1:
                continue
            rad = math.radians(ang)
            cands.append((x + r * math.cos(rad), y + r * math.sin(rad)))
    nbs = [pos[v] for v in adj.get(node, ()) if v in pos]
    if nbs:
        cx = sum(p[0] for p in nbs) / len(nbs)
        cy = sum(p[1] for p in nbs) / len(nbs)
        dx, dy = x - cx, y - cy
        L = math.hypot(dx, dy) or 1.0
        # Unit steps along/away from neighbor centroid — NOT unbounded *s* of L.
        ux, uy = dx / L, dy / L
        for s in (-360.0, -220.0, -120.0, 120.0, 220.0, 360.0):
            if abs(s) <= jump:
                cands.append((x + ux * s, y + uy * s))
                cands.append((cx + ux * abs(s), cy + uy * abs(s)))
        px, py = -uy, ux
        for s in (-240, -160, -80, 80, 160, 240):
            if abs(s) <= jump:
                cands.append((x + px * s, y + py * s))
    span = min(350.0, jump * 0.7)
    for _ in range(max(0, int(random_n))):
        cands.append((x + rng.uniform(-span, span), y + rng.uniform(-span, span)))
    # Drop absurd jumps (legacy radial scale could send nodes 10k+ px away).
    out: list[tuple[float, float]] = []
    for cx_, cy_ in cands:
        if math.hypot(cx_ - x, cy_ - y) > jump:
            continue
        if nbs:
            mx = sum(p[0] for p in nbs) / len(nbs)
            my = sum(p[1] for p in nbs) / len(nbs)
            if math.hypot(cx_ - mx, cy_ - my) > nbs_cap:
                continue
        out.append((cx_, cy_))
    return out


def untangle_crossings(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    max_rounds: int = 200,
    max_degree: int = 7,
    target_crossings: int = 60,
    seed: int = 7,
    protect_rings: bool = False,
    protect_rigid: bool | str = "portals",
    moves_per_round: int = 3,
    max_jump: float | None = None,
    frozen_ids: set[str] | None = None,
    focus_ids: list[str] | None = None,
    rank_cap: int | None = None,
    angle_step: int | None = None,
    refresh_every: int | None = None,
) -> OpResult:
    """Move low-degree nodes greedily to reduce global edge crossings.

    ``protect_rigid`` (default ``portals``): freeze shared dual-unit pivots
    only — corridors/tails may still move. Use ``all`` to freeze every
    compose-group member, or ``false`` to freeze nothing.
    """
    params = params or LayoutParams()
    st = state.copy()
    pos = dict(st.positions)
    names = dict(st.names)
    links = list(st.links)
    adj = {n: set(st.adj.get(n, ())) for n in pos}
    rng = random.Random(seed)
    jump = float(max_jump if max_jump is not None else _MAX_JUMP)
    jump = max(200.0, min(jump, _MAX_JUMP_CAP))
    n_links = len(links)
    # Auto-throttle on large E so MCP stdio stays interactive.
    if rank_cap is None:
        rank_cap = 36 if n_links >= 400 else (60 if n_links >= 200 else 140)
    if angle_step is None:
        angle_step = 30 if n_links >= 400 else (20 if n_links >= 200 else 15)
    if refresh_every is None:
        refresh_every = 3 if n_links >= 200 else 1
    rank_cap = max(8, int(rank_cap))
    angle_step = max(10, int(angle_step))
    refresh_every = max(1, int(refresh_every))
    random_n = 4 if n_links >= 400 else (8 if n_links >= 200 else 12)

    cur_c = count_edge_crossings(pos, links)
    start_c = cur_c
    moved: set[str] = set()
    frozen: set[str] = set(frozen_ids or ())
    if not frozen and protect_rigid not in (False, "false", "off", "none", "0"):
        from netx_topology_mcp.layout_ops.rigid_units import frozen_ids_for_protect

        frozen = frozen_ids_for_protect(st, protect_rigid)
    faces = None
    pierce0 = 0
    if protect_rings:
        from netx_topology_mcp.layout_ops.ring_faces import (
            count_ring_pierces,
            extract_ring_faces,
        )

        faces = extract_ring_faces(st)
        pierce0 = int(count_ring_pierces(pos, links, faces).get("pierce_crossings") or 0)

    focus: set[str] = {str(x) for x in (focus_ids or []) if str(x)}
    hit: dict[str, int] = {}
    from netx_topology_mcp.layout_jobs import raise_if_cancelled, report_progress

    rounds_total = max(1, int(max_rounds))
    for _round in range(rounds_total):
        if _round % max(1, refresh_every) == 0:
            raise_if_cancelled()
            pct = 70.0 + 4.0 * (_round / rounds_total)
            report_progress(
                "untangle",
                pct=min(74.0, pct),
                message=f"round {_round + 1}/{rounds_total} x={cur_c}",
                step=_round + 1,
                total_steps=rounds_total,
                crossings=cur_c,
            )
        if _round % refresh_every == 0 or not hit:
            hit = _participation(pos, links)
        if not hit:
            break
        # Prefer analyze.top_crossing_nodes / explicit focus_ids first.
        if not focus:
            focus = {
                str(r["fabric_node_id"])
                for r in top_crossing_nodes(
                    pos, links, names=names, adj=adj, top_n=5, participation=hit
                )
            }
        # High-degree hubs in top_nodes cannot move under max_degree — pull in
        # their low-degree neighbors that still participate in crossings.
        movable_focus: set[str] = set()
        for nid in focus:
            if len(adj.get(nid, ())) < max_degree and nid in hit:
                movable_focus.add(nid)
            for nb in adj.get(nid, ()):
                if nb in hit and len(adj.get(nb, ())) < max_degree:
                    movable_focus.add(nb)
        prefer = movable_focus or focus
        ranked = sorted(
            hit.keys(),
            key=lambda n: (
                0 if n in prefer else 1,
                -hit[n] / max(len(adj.get(n, ())), 1),
                len(adj.get(n, ())),
                -hit[n],
            ),
        )
        improved = False
        moves_left = max(1, int(moves_per_round))
        for node in ranked[:rank_cap]:
            if moves_left <= 0:
                break
            if node in frozen:
                continue
            deg = len(adj.get(node, ()))
            if deg >= max_degree:
                continue
            before = crossings_involving_node(node, pos, links, adj)
            if before <= 0:
                continue
            best: tuple[int, tuple[float, float]] | None = None
            x0, y0 = pos[node]
            for cand in _candidates(
                pos,
                node,
                adj,
                rng,
                max_jump=jump,
                angle_step=angle_step,
                random_n=random_n,
            ):
                if math.hypot(cand[0] - x0, cand[1] - y0) > jump:
                    continue
                trial = dict(pos)
                trial[node] = cand
                if _node_overlaps_any(node, trial, names):
                    continue
                after = crossings_involving_node(node, trial, links, adj)
                if after >= before:
                    continue
                g2 = cur_c - before + after
                if g2 >= cur_c:
                    continue
                if faces is not None:
                    from netx_topology_mcp.layout_ops.ring_faces import count_ring_pierces

                    pierce1 = int(
                        count_ring_pierces(trial, links, faces).get("pierce_crossings")
                        or 0
                    )
                    if pierce1 > pierce0:
                        continue
                if best is None or g2 < best[0]:
                    best = (g2, cand)
            if best is None:
                continue
            after_local = best[0] - cur_c + before
            pos[node] = best[1]
            moved.add(node)
            cur_c = best[0]
            # Stale hit is ok between refreshes; keep local estimate for ranking.
            if after_local > 0:
                hit[node] = after_local
            else:
                hit.pop(node, None)
            if faces is not None:
                from netx_topology_mcp.layout_ops.ring_faces import count_ring_pierces

                pierce0 = int(
                    count_ring_pierces(pos, links, faces).get("pierce_crossings") or 0
                )
            improved = True
            moves_left -= 1
        if not improved:
            break
        if cur_c <= target_crossings:
            break

    protect_mode = (
        protect_rigid
        if isinstance(protect_rigid, str)
        else ("portals" if protect_rigid else "off")
    )
    st.positions = pos
    # Reuse last hit for meta (avoid an extra O(E²) pass on large graphs).
    if not hit:
        hit = _participation(pos, links)
    st.meta["untangle"] = {
        "start_crossings": start_c,
        "end_crossings": cur_c,
        "moved_n": len(moved),
        "max_degree": max_degree,
        "target_crossings": target_crossings,
        "max_jump": jump,
        "protect_rings": bool(protect_rings),
        "protect_rigid": protect_mode,
        "frozen_n": len(frozen),
        "moves_per_round": int(moves_per_round),
        "focus_n": len(focus),
        "rank_cap": rank_cap,
        "angle_step": angle_step,
        "refresh_every": refresh_every,
        "top_crossing_nodes": top_crossing_nodes(
            pos, links, names=names, adj=adj, top_n=5, participation=hit
        ),
    }
    return OpResult(
        state=st,
        moved=moved,
        op="untangle_crossings",
        params={
            "max_rounds": max_rounds,
            "max_degree": max_degree,
            "target_crossings": target_crossings,
            "seed": seed,
            "protect_rings": bool(protect_rings),
            "protect_rigid": protect_mode,
            "frozen_n": len(frozen),
            "moves_per_round": int(moves_per_round),
            "max_jump": jump,
            "focus_n": len(focus),
        },
        note=f"untangle {start_c}->{cur_c} moved={len(moved)} frozen={len(frozen)}",
    )


def untangle_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Pull untangle knobs from layout params overrides (optional)."""
    o = overrides or {}
    out: dict[str, Any] = {}
    for key, cast, default in (
        ("max_rounds", int, 200),
        ("max_degree", int, 7),
        ("target_crossings", int, 60),
        ("seed", int, 7),
        ("moves_per_round", int, 3),
    ):
        if key not in o or o[key] is None:
            out[key] = default
            continue
        try:
            out[key] = cast(o[key])
        except (TypeError, ValueError):
            out[key] = default
    if "protect_rings" in o:
        out["protect_rings"] = bool(o["protect_rings"])
    if "protect_rigid" in o:
        v = o["protect_rigid"]
        if isinstance(v, bool):
            # true → portals (semi-rigid); false → off
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
    if o.get("max_jump") is not None:
        try:
            out["max_jump"] = float(o["max_jump"])
        except (TypeError, ValueError):
            pass
    focus = o.get("focus_ids") or o.get("focus_node_ids")
    if isinstance(focus, list):
        out["focus_ids"] = [str(x).strip() for x in focus if str(x).strip()]
    elif isinstance(focus, str) and focus.strip():
        out["focus_ids"] = [s.strip() for s in focus.split(",") if s.strip()]
    return out
