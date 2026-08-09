"""Stage-2 crossing pressure without temp scripts.

Actions used by agents via layoutTopologyView:
- press_hot_edges: rotate non-portal ends of top crossing edges about the other end
- press_crossers: move nodes that participate in crossings against those hot edges
- polish_crossings: straighten → hot_edges → crossers → untangle(portals)

Hot path uses incremental crossing deltas (O(deg·E) per trial) so KND-scale
graphs (~500 links) stay interactive under MCP stdio timeouts.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    crossings_after_node_move,
    crossings_involving_node,
    crossing_participation_full,
    node_footprint,
    segments_properly_intersect,
    top_crossing_edges,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.untangle import untangle_crossings


def park_phantom_nodes(state: LayoutState) -> set[str]:
    """Park region: markers / absurd coords so they do not blow bbox/util."""
    moved: set[str] = set()
    for nid, (x, y) in list(state.positions.items()):
        if str(nid).startswith("region:") or abs(x) > 1e5 or abs(y) > 1e5:
            if not math.isfinite(x) or not math.isfinite(y) or abs(x) > 1e5 or abs(y) > 1e5:
                state.positions[nid] = (160.0, 160.0)
                moved.add(nid)
            elif str(nid).startswith("region:"):
                # keep finite but park markers to origin corner
                if abs(x) > 1e4 or abs(y) > 1e4:
                    state.positions[nid] = (160.0, 160.0)
                    moved.add(nid)
    return moved


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
        if abs(bx - x) > 90 and abs(by - y) > 70:
            continue
        fb = node_footprint(names.get(b, ""))
        bx0, by0, bx1, by1 = bx + fb[0], by + fb[1], bx + fb[2], by + fb[3]
        if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
            return True
    return False


def _rotate_about(
    hub: tuple[float, float], leaf: tuple[float, float], ang: float
) -> tuple[float, float]:
    hx, hy = hub
    lx, ly = leaf
    dx, dy = lx - hx, ly - hy
    c, s = math.cos(ang), math.sin(ang)
    return (hx + dx * c - dy * s, hy + dx * s + dy * c)


def frozen_portals_from_state(
    state: LayoutState, portal_ids: list[str] | None = None
) -> set[str]:
    from netx_topology_mcp.layout_ops.rigid_units import (
        _hub_portals,
        frozen_ids_for_protect,
    )

    if portal_ids:
        raw = {str(x) for x in portal_ids if str(x)}
        # Explicit dual-unit portals (~20) must stay frozen; only shrink if
        # caller passed the inflated multi-membership set.
        return _hub_portals(state, raw, cap=32)
    return frozen_ids_for_protect(state, "portals")


def _large_graph_budget(n_links: int) -> dict[str, Any]:
    """Shrink search when E is large so MCP stdio does not time out.

    Target: full polish_crossings on ~500-link graphs finishes in ~15–25s.
    """
    e = max(0, int(n_links))
    if e >= 1200:
        return {
            "hot_top_n": 6,
            "hot_max_moves": 10,
            "hot_max_sweeps": 1,
            "hot_angle_step": math.pi / 6,
            "hot_radii": [0.95, 1.2],
            "cross_top_n": 4,
            "cross_max_moves": 12,
            "cross_max_sweeps": 1,
            "cross_cand_cap": 48,
            "straighten": False,
            "skip_dual_full_x": True,
            "untangle_rounds": 18,
            "untangle_moves": 2,
            "untangle_rank_cap": 36,
            "untangle_angle_step": 30,
        }
    if e >= 400:
        return {
            "hot_top_n": 8,
            "hot_max_moves": 14,
            "hot_max_sweeps": 2,
            "hot_angle_step": math.pi / 8,
            "hot_radii": [0.9, 1.15, 1.35],
            "cross_top_n": 5,
            "cross_max_moves": 16,
            "cross_max_sweeps": 2,
            "cross_cand_cap": 72,
            # straighten_channels is O(channels×modes×crossings); on 1k+ link
            # metros it can stall for minutes with no cancel checkpoints.
            "straighten": False,
            "skip_dual_full_x": e >= 800,
            "untangle_rounds": 28,
            "untangle_moves": 3,
            "untangle_rank_cap": 48,
            "untangle_angle_step": 24,
        }
    if e >= 200:
        return {
            "hot_top_n": 8,
            "hot_max_moves": 14,
            "hot_max_sweeps": 2,
            "hot_angle_step": math.pi / 9,
            "hot_radii": [0.9, 1.1, 1.25],
            "cross_top_n": 4,
            "cross_max_moves": 14,
            "cross_max_sweeps": 2,
            "cross_cand_cap": 80,
            "straighten": False,
            "skip_dual_full_x": False,
            "untangle_rounds": 24,
            "untangle_moves": 3,
            "untangle_rank_cap": 60,
            "untangle_angle_step": 20,
        }
    return {
        "hot_top_n": 10,
        "hot_max_moves": 24,
        "hot_max_sweeps": 4,
        "hot_angle_step": math.pi / 12,
        "hot_radii": [0.85, 1.0, 1.15, 1.35],
        "cross_top_n": 6,
        "cross_max_moves": 40,
        "cross_max_sweeps": 6,
        "cross_cand_cap": 220,
        "straighten": True,
        "skip_dual_full_x": False,
        "untangle_rounds": 120,
        "untangle_moves": 5,
        "untangle_rank_cap": 140,
        "untangle_angle_step": 15,
    }


def press_hot_edges(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    portal_ids: list[str] | None = None,
    top_n: int | None = None,
    max_moves: int | None = None,
    max_sweeps: int | None = None,
) -> OpResult:
    """Rotate non-portal ends of top crossing edges about the other endpoint."""
    from netx_topology_mcp.layout_jobs import raise_if_cancelled, touch_heartbeat

    del params
    st = state.copy()
    park_phantom_nodes(st)
    frozen = frozen_portals_from_state(st, portal_ids)
    pos = dict(st.positions)
    names = st.names
    links = st.links
    adj = st.adj
    budget = _large_graph_budget(len(links))
    # Cap overrides — giant metros must not explode search via params.top_n.
    top_n = min(
        int(top_n if top_n is not None else budget["hot_top_n"]),
        int(budget["hot_top_n"]) + (0 if len(links) >= 800 else 4),
    )
    max_moves = min(
        int(max_moves if max_moves is not None else budget["hot_max_moves"]),
        int(budget["hot_max_moves"]) + (0 if len(links) >= 800 else 6),
    )
    max_sweeps = min(
        int(max_sweeps if max_sweeps is not None else budget["hot_max_sweeps"]),
        int(budget["hot_max_sweeps"]),
    )
    angle_step = float(budget["hot_angle_step"])
    radii_scale = list(budget["hot_radii"])
    angles = [i * angle_step for i in range(1, max(2, int(round(2 * math.pi / angle_step))))]
    skip_dual = bool(budget.get("skip_dual_full_x"))

    touch_heartbeat()
    start = count_edge_crossings(pos, links)
    cur = start
    moved: set[str] = set()
    accepted = 0

    for _sweep in range(max(1, int(max_sweeps))):
        raise_if_cancelled()
        touch_heartbeat()
        _n, _nh, edge_hit = crossing_participation_full(pos, links)
        touch_heartbeat()
        tops = top_crossing_edges(
            pos, links, names=names, top_n=top_n, edge_participation=edge_hit
        )
        sweep_moves = 0
        for ei, edge in enumerate(tops):
            if ei % 2 == 0:
                raise_if_cancelled()
                touch_heartbeat()
            a, b = str(edge["a_node_id"]), str(edge["b_node_id"])
            if a not in pos or b not in pos:
                continue
            cand_pairs: list[tuple[str, str]] = []
            if a not in frozen:
                cand_pairs.append((a, b))
            if b not in frozen:
                cand_pairs.append((b, a))
            if not cand_pairs:
                continue
            cand_pairs.sort(
                key=lambda pair: (
                    0 if pair[1] in frozen else 1,
                    len(adj.get(pair[0], ())),
                )
            )
            leaf, hub = cand_pairs[0]
            hub_xy, leaf_xy = pos[hub], pos[leaf]
            r0 = math.hypot(leaf_xy[0] - hub_xy[0], leaf_xy[1] - hub_xy[1]) or 200.0
            # Cap orbit radius so long spokes cannot fling leaves across a giant bbox.
            # Giant metros still need a higher reel target than 900 (bridges ≫5k).
            r_cap = 2200.0 if len(links) >= 400 else 1400.0
            r_use = min(r0, r_cap)
            max_disp = 1100.0 if len(links) >= 400 else 1100.0
            # Inward reel on long metro bridges needs a larger displacement budget
            # (otherwise max_disp rejects the only moves that cut crossings).
            if r0 > max_disp * 1.5:
                max_disp = max(max_disp, min(r0 * 0.55, r0 - 0.35 * r_use))
            local_before = crossings_involving_node(leaf, pos, links, adj)
            best: tuple[int, tuple[float, float]] | None = None
            best_dual: (
                tuple[int, tuple[float, float], tuple[float, float]] | None
            ) = None

            def _try_cand(cand: tuple[float, float]) -> None:
                nonlocal best
                if math.hypot(cand[0] - leaf_xy[0], cand[1] - leaf_xy[1]) > max_disp:
                    return
                trial_pos = {**pos, leaf: cand}
                if _overlaps_any(leaf, trial_pos, names):
                    return
                c1 = crossings_after_node_move(
                    pos,
                    links,
                    adj,
                    leaf,
                    cand,
                    current_total=cur,
                    local_before=local_before,
                )
                if c1 < cur and (best is None or c1 < best[0]):
                    best = (c1, cand)

            # Reel-in along the spoke first (shorten without hunting angles).
            if r0 > r_use + 50.0:
                ux = (leaf_xy[0] - hub_xy[0]) / r0
                uy = (leaf_xy[1] - hub_xy[1]) / r0
                for rt in (
                    r_use,
                    r_use * 1.15,
                    max(220.0, r0 * 0.25),
                    max(220.0, r0 * 0.4),
                    max(220.0, r0 * 0.55),
                    max(220.0, r0 * 0.7),
                ):
                    rt = min(rt, r0 - 1.0)
                    if rt < 120.0:
                        continue
                    _try_cand((hub_xy[0] + ux * rt, hub_xy[1] + uy * rt))

            # Both ends walk toward the midpoint (metro bridges often need this).
            # Skip on giant E: each trial is a full O(E²) count and stalls MCP.
            if (not skip_dual) and r0 > 1500.0 and hub not in frozen:
                mx = 0.5 * (hub_xy[0] + leaf_xy[0])
                my = 0.5 * (hub_xy[1] + leaf_xy[1])
                ux = (leaf_xy[0] - hub_xy[0]) / r0
                uy = (leaf_xy[1] - hub_xy[1]) / r0
                px, py = -uy, ux
                dual_plans: list[tuple[tuple[float, float], tuple[float, float]]] = []
                for t in (0.15, 0.25, 0.35, 0.45, 0.55):
                    dual_plans.append(
                        (
                            (
                                leaf_xy[0] + t * (mx - leaf_xy[0]),
                                leaf_xy[1] + t * (my - leaf_xy[1]),
                            ),
                            (
                                hub_xy[0] + t * (mx - hub_xy[0]),
                                hub_xy[1] + t * (my - hub_xy[1]),
                            ),
                        )
                    )
                # Asymmetric: reel the free leaf harder than the hub.
                for tl, th in ((0.45, 0.15), (0.6, 0.2), (0.7, 0.25)):
                    dual_plans.append(
                        (
                            (
                                leaf_xy[0] + tl * (mx - leaf_xy[0]),
                                leaf_xy[1] + tl * (my - leaf_xy[1]),
                            ),
                            (
                                hub_xy[0] + th * (mx - hub_xy[0]),
                                hub_xy[1] + th * (my - hub_xy[1]),
                            ),
                        )
                    )
                for s in (-900.0, -520.0, -280.0, 280.0, 520.0, 900.0):
                    dual_plans.append(
                        (
                            (leaf_xy[0] + px * s, leaf_xy[1] + py * s),
                            (hub_xy[0] + px * s, hub_xy[1] + py * s),
                        )
                    )
                for leaf_c, hub_c in dual_plans:
                    trial = {**pos, leaf: leaf_c, hub: hub_c}
                    if _overlaps_any(leaf, trial, names) or _overlaps_any(
                        hub, trial, names
                    ):
                        continue
                    c1 = count_edge_crossings(trial, links)
                    if c1 < cur and (best_dual is None or c1 < best_dual[0]):
                        best_dual = (c1, leaf_c, hub_c)

            for ang in angles:
                for rs in radii_scale:
                    rx, ry = _rotate_about(hub_xy, leaf_xy, ang)
                    dx, dy = rx - hub_xy[0], ry - hub_xy[1]
                    L = math.hypot(dx, dy) or 1.0
                    cand = (
                        hub_xy[0] + dx / L * r_use * min(rs, 1.25),
                        hub_xy[1] + dy / L * r_use * min(rs, 1.25),
                    )
                    _try_cand(cand)
            if best is None:
                hx, hy = hub_xy
                lx, ly = leaf_xy
                dx, dy = lx - hx, ly - hy
                L = math.hypot(dx, dy) or 1.0
                px, py = -dy / L, dx / L
                for s in (-720.0, -480.0, -320.0, -200.0, -120.0, 120.0, 200.0, 320.0, 480.0, 720.0):
                    _try_cand((lx + px * s, ly + py * s))
            # Prefer dual mid-walk when it beats single-end moves.
            if best_dual is not None and (
                best is None or best_dual[0] < best[0]
            ):
                pos[leaf] = best_dual[1]
                pos[hub] = best_dual[2]
                cur = best_dual[0]
                moved.add(leaf)
                moved.add(hub)
                accepted += 1
                sweep_moves += 1
                if accepted >= max_moves:
                    break
                continue
            if best is None:
                continue
            pos[leaf] = best[1]
            cur = best[0]
            moved.add(leaf)
            accepted += 1
            sweep_moves += 1
            if accepted >= max_moves:
                break
        if sweep_moves == 0 or accepted >= max_moves:
            break

    st.positions = pos
    st.meta = dict(st.meta or {})
    st.meta["press_hot_edges"] = {
        "start_crossings": start,
        "end_crossings": cur,
        "accepted_moves": accepted,
        "frozen_n": len(frozen),
        "budget": {"top_n": top_n, "max_moves": max_moves, "max_sweeps": max_sweeps},
    }
    return OpResult(
        state=st,
        moved=moved,
        op="press_hot_edges",
        params=st.meta["press_hot_edges"],
        note=f"press_hot_edges {start}->{cur} moves={accepted}",
    )


def _edges_crossing(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    a: str,
    b: str,
) -> list[tuple[str, str]]:
    if a not in pos or b not in pos:
        return []
    p1, p2 = pos[a], pos[b]
    out: list[tuple[str, str]] = []
    for c, d in links:
        if len({a, b, c, d}) < 4 or c not in pos or d not in pos:
            continue
        if segments_properly_intersect(p1, p2, pos[c], pos[d]):
            out.append((c, d))
    return out


def _try_move_node(
    pos: dict[str, tuple[float, float]],
    names: dict[str, str],
    links: list[tuple[str, str]],
    adj: dict[str, set[str]],
    node: str,
    *,
    jump: float,
    cur: int,
    cand_cap: int = 220,
) -> tuple[int, tuple[float, float]] | None:
    x0, y0 = pos[node]
    before_local = crossings_involving_node(node, pos, links, adj)
    if before_local <= 0:
        return None
    ang_step = 30 if cand_cap <= 60 else (20 if cand_cap <= 100 else 10)
    # Keep jumps local — long radii explode util on metro canvases.
    # Large graphs still need mid-range radii to clear hot-edge crossers.
    radii = (
        (120, 220, 360, 520)
        if cand_cap <= 80
        else (80, 140, 220, 320, 450, 600, 800, 1100)
    )
    cands: list[tuple[float, float]] = []
    for ang in range(0, 360, ang_step):
        for r in radii:
            if r > jump:
                continue
            rad = math.radians(ang)
            cands.append((x0 + r * math.cos(rad), y0 + r * math.sin(rad)))
    nbs = [pos[v] for v in adj.get(node, ()) if v in pos]
    if nbs:
        cx = sum(p[0] for p in nbs) / len(nbs)
        cy = sum(p[1] for p in nbs) / len(nbs)
        dx, dy = x0 - cx, y0 - cy
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        px, py = -uy, ux
        for s in (-800.0, -500.0, -350.0, -200.0, 200.0, 350.0, 500.0, 800.0):
            cands.append((x0 + ux * s, y0 + uy * s))
            cands.append((x0 + px * s, y0 + py * s))
    if len(cands) > cand_cap:
        cands = cands[:: max(1, len(cands) // cand_cap)]
    best: tuple[int, tuple[float, float]] | None = None
    for cand in cands:
        if math.hypot(cand[0] - x0, cand[1] - y0) > jump + 1:
            continue
        trial_pos = {**pos, node: cand}
        if _overlaps_any(node, trial_pos, names):
            continue
        after_local = crossings_involving_node(node, trial_pos, links, adj)
        if after_local >= before_local:
            continue
        c1 = cur - before_local + after_local
        if c1 < cur and (best is None or c1 < best[0]):
            best = (c1, cand)
    return best


def press_crossers(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    portal_ids: list[str] | None = None,
    top_n: int | None = None,
    max_moves: int | None = None,
    max_sweeps: int | None = None,
) -> OpResult:
    """Move non-portal nodes involved in crossings against top hot edges."""
    del params
    st = state.copy()
    park_phantom_nodes(st)
    frozen = frozen_portals_from_state(st, portal_ids)
    pos = dict(st.positions)
    names = st.names
    links = st.links
    adj = st.adj
    budget = _large_graph_budget(len(links))
    top_n = int(top_n if top_n is not None else budget["cross_top_n"])
    max_moves = int(max_moves if max_moves is not None else budget["cross_max_moves"])
    max_sweeps = int(max_sweeps if max_sweeps is not None else budget["cross_max_sweeps"])
    cand_cap = int(budget["cross_cand_cap"])

    start = count_edge_crossings(pos, links)
    cur = start
    moved: set[str] = set()
    accepted = 0

    for _sweep in range(max(1, int(max_sweeps))):
        _n, _nh, edge_hit = crossing_participation_full(pos, links)
        tops = top_crossing_edges(
            pos, links, names=names, top_n=top_n, edge_participation=edge_hit
        )
        scores: dict[str, int] = {}
        for he in tops:
            a, b = str(he["a_node_id"]), str(he["b_node_id"])
            for c, d in _edges_crossing(pos, links, a, b):
                for nid in (c, d, a, b):
                    if nid in frozen or nid not in pos:
                        continue
                    deg = len(adj.get(nid, ()))
                    if deg >= 16:
                        continue
                    scores[nid] = scores.get(nid, 0) + int(he["crossing_hits"]) + max(
                        0, 10 - deg
                    )
        ranked = sorted(scores, key=lambda n: (-scores[n], len(adj.get(n, ())), n))
        node_cap = 16 if cand_cap <= 60 else (24 if cand_cap <= 100 else 40)
        improved = False
        for node in ranked[:node_cap]:
            jump = 1400.0 if len(adj.get(node, ())) <= 4 else 1000.0
            best = _try_move_node(
                pos, names, links, adj, node, jump=jump, cur=cur, cand_cap=cand_cap
            )
            if best is None:
                continue
            pos[node] = best[1]
            cur = best[0]
            moved.add(node)
            accepted += 1
            improved = True
            if accepted >= max_moves:
                break
        if not improved or accepted >= max_moves:
            break

    st.positions = pos
    st.meta = dict(st.meta or {})
    st.meta["press_crossers"] = {
        "start_crossings": start,
        "end_crossings": cur,
        "accepted_moves": accepted,
        "frozen_n": len(frozen),
        "budget": {"top_n": top_n, "max_moves": max_moves, "max_sweeps": max_sweeps},
    }
    return OpResult(
        state=st,
        moved=moved,
        op="press_crossers",
        params=st.meta["press_crossers"],
        note=f"press_crossers {start}->{cur} moves={accepted}",
    )


def polish_crossings(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    portal_ids: list[str] | None = None,
    straighten: bool | None = None,
    max_degree: int = 9,
    untangle_rounds: int | None = None,
    top_n: int | None = None,
    max_moves: int | None = None,
    max_sweeps: int | None = None,
) -> OpResult:
    """Pipeline: park phantoms → straighten → hot_edges → crossers → untangle."""
    from netx_topology_mcp.layout_jobs import raise_if_cancelled, report_progress

    params = params or LayoutParams()
    st = state.copy()
    park_phantom_nodes(st)
    budget = _large_graph_budget(len(st.links))
    # Giant graphs: never allow straighten even if caller asks (stalls for minutes).
    if len(st.links) >= 800:
        do_straighten = False
    else:
        do_straighten = (
            bool(budget["straighten"]) if straighten is None else bool(straighten)
        )
    untangle_rounds = int(
        untangle_rounds if untangle_rounds is not None else budget["untangle_rounds"]
    )
    if len(st.links) >= 800:
        untangle_rounds = min(untangle_rounds, int(budget["untangle_rounds"]))
        top_n = (
            min(int(top_n), int(budget["hot_top_n"]))
            if top_n is not None
            else top_n
        )
        max_moves = (
            min(int(max_moves), int(budget["hot_max_moves"]))
            if max_moves is not None
            else max_moves
        )
    report_progress(
        "polish_start",
        pct=42.0,
        message=f"links={len(st.links)} rounds={untangle_rounds}",
        links=len(st.links),
        untangle_rounds=untangle_rounds,
    )
    start = count_edge_crossings(st.positions, st.links)
    trace: list[dict[str, Any]] = []
    moved: set[str] = set()

    if do_straighten:
        from netx_topology_mcp.layout_ops.channels import straighten_channels_greedy

        raise_if_cancelled()
        report_progress("polish_straighten", pct=48.0, message=f"x0={start}")
        op = straighten_channels_greedy(st, params)
        st = op.state
        moved |= op.moved
        trace.append({"op": "straighten_channels", "note": op.note, **(op.params or {})})

    raise_if_cancelled()
    report_progress("polish_hot_edges", pct=55.0, message="press_hot_edges")
    op_h = press_hot_edges(
        st,
        params,
        portal_ids=portal_ids,
        top_n=top_n,
        max_moves=max_moves,
        max_sweeps=max_sweeps,
    )
    st = op_h.state
    moved |= op_h.moved
    trace.append({"op": "press_hot_edges", "note": op_h.note, **(op_h.params or {})})

    raise_if_cancelled()
    report_progress("polish_crossers", pct=62.0, message="press_crossers")
    op_c = press_crossers(
        st,
        params,
        portal_ids=portal_ids,
        top_n=top_n,
        max_moves=max_moves,
        max_sweeps=max_sweeps,
    )
    st = op_c.state
    moved |= op_c.moved
    trace.append({"op": "press_crossers", "note": op_c.note, **(op_c.params or {})})

    focus: list[str] = []
    _n, node_hit, edge_hit = crossing_participation_full(st.positions, st.links)
    tops_e = top_crossing_edges(
        st.positions, st.links, names=st.names, top_n=5, edge_participation=edge_hit
    )
    for row in tops_e:
        focus.append(str(row["a_node_id"]))
        focus.append(str(row["b_node_id"]))
    ranked_nodes = sorted(node_hit.items(), key=lambda kv: -kv[1])[:5]
    focus.extend(nid for nid, _ in ranked_nodes)
    focus = list(dict.fromkeys(focus))

    raise_if_cancelled()
    report_progress(
        "polish_untangle",
        pct=70.0,
        message=f"untangle rounds={untangle_rounds} focus={len(focus)}",
        untangle_rounds=untangle_rounds,
        focus_n=len(focus),
    )
    op_u = untangle_crossings(
        st,
        params,
        protect_rigid="portals",
        focus_ids=focus,
        max_rounds=untangle_rounds,
        max_degree=max_degree,
        target_crossings=40,
        max_jump=1000.0,
        moves_per_round=int(budget["untangle_moves"]),
        frozen_ids=frozen_portals_from_state(st, portal_ids) if portal_ids else None,
        rank_cap=int(budget["untangle_rank_cap"]),
        angle_step=int(budget["untangle_angle_step"]),
        refresh_every=3 if len(st.links) >= 200 else 1,
    )
    st = op_u.state
    moved |= op_u.moved
    trace.append({"op": "untangle", "note": op_u.note, **(op_u.params or {})})

    end = count_edge_crossings(st.positions, st.links)
    report_progress(
        "polish_done",
        pct=74.0,
        message=f"crossings {start}->{end}",
        crossings_before=start,
        crossings_after=end,
    )
    st.meta = dict(st.meta or {})
    st.meta["polish_crossings"] = {
        "start_crossings": start,
        "end_crossings": end,
        "trace": trace,
        "focus_n": len(focus),
        "budget": budget,
        "straighten": do_straighten,
        "untangle_rounds": untangle_rounds,
    }
    return OpResult(
        state=st,
        moved=moved,
        op="polish_crossings",
        params=st.meta["polish_crossings"],
        note=f"polish_crossings {start}->{end}",
    )


def press_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    o = overrides or {}
    out: dict[str, Any] = {}
    for key, cast in (
        ("top_n", int),
        ("max_moves", int),
        ("max_sweeps", int),
        ("max_degree", int),
        ("untangle_rounds", int),
    ):
        if key in o and o[key] is not None:
            try:
                out[key] = cast(o[key])
            except (TypeError, ValueError):
                pass
    if "straighten" in o:
        v = o["straighten"]
        out["straighten"] = (
            v
            if isinstance(v, bool)
            else str(v).strip().lower() in {"1", "true", "yes", "on"}
        )
    if "portal_ids" in o and isinstance(o["portal_ids"], list):
        out["portal_ids"] = [str(x) for x in o["portal_ids"] if str(x)]
    if "source_view_ids" in o and isinstance(o["source_view_ids"], list):
        out["source_view_ids"] = [str(x) for x in o["source_view_ids"] if str(x)]
    return out
