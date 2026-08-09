"""Top-down hierarchy layout: contract → order sectors → expand.

At each hub level we only score crossings on the *contracted* graph
(parent + one representative per child territory). Internal edges of
children are ignored until that child is expanded. Accept/reject is
driven by contracted crossings only — global leaf crossings may rise
temporarily; that is intentional (fix lower levels later).

Expand is a *rigid* polar remapping of each stub territory onto the new
sector angle so local corridor geometry survives the reorder.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings, segments_properly_intersect
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local
from netx_topology_mcp.layout_ops.partition import pick_hub_seeds
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _ang(pos: dict[str, tuple[float, float]], hub: str, nid: str) -> float:
    hx, hy = pos[hub]
    x, y = pos[nid]
    return math.atan2(y - hy, x - hx)


def _stub_territories(
    hub: str,
    members: set[str],
    adj: dict[str, set[str]],
    pinned: set[str],
) -> tuple[list[str], dict[str, str]]:
    stubs = [
        n
        for n in adj.get(hub, ())
        if n in members and n not in pinned
    ]
    owner: dict[str, str] = {}
    q: deque[str] = deque()
    for stub in stubs:
        owner[stub] = stub
        q.append(stub)
    while q:
        u = q.popleft()
        for v in adj.get(u, ()):
            if v not in members or v == hub or v in owner or v in pinned:
                continue
            owner[v] = owner[u]
            q.append(v)
    return stubs, owner


def _owner_hub_map(
    adj: dict[str, set[str]],
    hubs: set[str],
) -> dict[str, str]:
    """BFS Voronoi: every node maps to nearest hub (ties: first reached)."""
    owner: dict[str, str] = {h: h for h in hubs}
    q: deque[str] = deque(hubs)
    while q:
        u = q.popleft()
        ou = owner[u]
        for v in adj.get(u, ()):
            if v in owner:
                continue
            owner[v] = ou
            q.append(v)
    return owner


def _contracted_links(
    hub: str,
    stubs: list[str],
    owner: dict[str, str],
    adj: dict[str, set[str]],
    pinned: set[str],
    *,
    hub_of: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Edges among {hub} ∪ stubs after contracting each stub territory.

    Foreign endpoints outside this hub's territories collapse to their
    owning hub (Voronoi), so cross-block edges are not dropped from the
    contracted objective.
    """
    terr: dict[str, set[str]] = {s: {s} for s in stubs}
    for n, o in owner.items():
        terr.setdefault(o, set()).add(n)
    node_to_rep: dict[str, str] = {hub: hub}
    for s, nodes in terr.items():
        for n in nodes:
            node_to_rep[n] = s

    links: set[tuple[str, str]] = set()
    for s in stubs:
        links.add(tuple(sorted((hub, s))))
    for s, nodes in terr.items():
        for n in nodes:
            for v in adj.get(n, ()):
                if v == hub:
                    continue
                if v in node_to_rep:
                    r = node_to_rep[v]
                    if r != s:
                        links.add(tuple(sorted((s, r))))
                elif v in pinned:
                    links.add(tuple(sorted((s, v))))
                elif hub_of is not None and v in hub_of:
                    fh = hub_of[v]
                    if fh != hub:
                        links.add(tuple(sorted((s, fh))))
    return sorted(links)


def _contracted_crossings(
    hub: str,
    stubs_ord: list[str],
    links: list[tuple[str, str]],
    *,
    radius: float,
    hub_xy: tuple[float, float],
    foreign_pos: dict[str, tuple[float, float]] | None = None,
    a0: float | None = None,
    a1: float | None = None,
) -> int:
    """Place stub reps on an arc; count crossings of contracted links only."""
    hx, hy = hub_xy
    n = len(stubs_ord)
    if n == 0:
        return 0
    if a0 is None or a1 is None:
        a0, a1 = -math.pi * 0.85, math.pi * 0.85
    pos: dict[str, tuple[float, float]] = {hub: (hx, hy)}
    if foreign_pos:
        pos.update(foreign_pos)
    for i, s in enumerate(stubs_ord):
        mid = 0.5 * (a0 + a1) if n == 1 else a0 + (a1 - a0) * i / (n - 1)
        pos[s] = (hx + math.cos(mid) * radius, hy + math.sin(mid) * radius)
    segs = [(a, b) for a, b in links if a in pos and b in pos]
    c = 0
    for i, (a, b) in enumerate(segs):
        p1, p2 = pos[a], pos[b]
        for u, v in segs[i + 1 :]:
            if len({a, b, u, v}) < 4:
                continue
            if segments_properly_intersect(p1, p2, pos[u], pos[v]):
                c += 1
    return c


def _score_order(
    hub: str,
    stubs_ord: list[str],
    links: list[tuple[str, str]],
    *,
    radius: float,
    hub_xy: tuple[float, float],
    foreign_pos: dict[str, tuple[float, float]],
    a0: float,
    a1: float,
) -> int:
    return _contracted_crossings(
        hub,
        stubs_ord,
        links,
        radius=radius,
        hub_xy=hub_xy,
        foreign_pos=foreign_pos,
        a0=a0,
        a1=a1,
    )


def _candidate_orders(stubs: list[str], pos: dict[str, tuple[float, float]], hub: str) -> list[list[str]]:
    if not stubs:
        return []
    geo = sorted(stubs, key=lambda s: _ang(pos, hub, s))
    orders: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def _add(ord_: list[str]) -> None:
        key = tuple(ord_)
        if key not in seen:
            seen.add(key)
            orders.append(ord_)

    for base in (geo, list(reversed(geo))):
        for k in range(len(base)):
            _add(base[k:] + base[:k])
    # Adjacent swaps from geo.
    for i in range(len(geo) - 1):
        trial = list(geo)
        trial[i], trial[i + 1] = trial[i + 1], trial[i]
        _add(trial)
    return orders


def _best_order_2opt(
    hub: str,
    stubs: list[str],
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    *,
    radius: float,
    hub_xy: tuple[float, float],
    foreign_pos: dict[str, tuple[float, float]],
    a0: float,
    a1: float,
) -> tuple[list[str], int]:
    """Greedy 2-opt / insert bakeoff seeded from candidate rotations."""
    seeds = _candidate_orders(stubs, pos, hub)
    best_ord = sorted(stubs, key=lambda s: _ang(pos, hub, s))
    best_cc = _score_order(
        hub, best_ord, links, radius=radius, hub_xy=hub_xy, foreign_pos=foreign_pos, a0=a0, a1=a1
    )
    for seed in seeds:
        cur = list(seed)
        cur_cc = _score_order(
            hub, cur, links, radius=radius, hub_xy=hub_xy, foreign_pos=foreign_pos, a0=a0, a1=a1
        )
        improved = True
        rounds = 0
        while improved and rounds < 12:
            improved = False
            rounds += 1
            n = len(cur)
            for i in range(n):
                for j in range(i + 2, n + (0 if i == 0 else 1)):
                    # reverse segment [i:j]
                    jj = j if j <= n else n
                    if jj - i < 2:
                        continue
                    trial = cur[:i] + list(reversed(cur[i:jj])) + cur[jj:]
                    if len(trial) != n:
                        continue
                    cc = _score_order(
                        hub,
                        trial,
                        links,
                        radius=radius,
                        hub_xy=hub_xy,
                        foreign_pos=foreign_pos,
                        a0=a0,
                        a1=a1,
                    )
                    if cc < cur_cc:
                        cur, cur_cc = trial, cc
                        improved = True
                        break
                if improved:
                    break
        if cur_cc < best_cc:
            best_cc = cur_cc
            best_ord = cur
    return best_ord, best_cc


def _contracted_crossings_at_pos(
    hub: str,
    stubs: list[str],
    links: list[tuple[str, str]],
    pos: dict[str, tuple[float, float]],
    foreign_pos: dict[str, tuple[float, float]],
) -> int:
    """Contracted crossings using *actual* stub coordinates as representatives."""
    cpos: dict[str, tuple[float, float]] = {hub: pos[hub]}
    cpos.update(foreign_pos)
    for s in stubs:
        if s in pos:
            cpos[s] = pos[s]
    segs = [(a, b) for a, b in links if a in cpos and b in cpos]
    c = 0
    for i, (a, b) in enumerate(segs):
        p1, p2 = cpos[a], cpos[b]
        for u, v in segs[i + 1 :]:
            if len({a, b, u, v}) < 4:
                continue
            if segments_properly_intersect(p1, p2, cpos[u], cpos[v]):
                c += 1
    return c


def _swap_territories(
    hub: str,
    a: str,
    b: str,
    owner: dict[str, str],
    pinned: set[str],
    pos: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Exchange polar angles of two stub territories around hub (rigid)."""
    hx, hy = pos[hub]
    aa, ab = _ang(pos, hub, a), _ang(pos, hub, b)
    delta = ab - aa
    trial = dict(pos)
    ta = {n for n, o in owner.items() if o == a} | {a}
    tb = {n for n, o in owner.items() if o == b} | {b}
    for n in ta:
        if n not in pos or (n in pinned and n != a):
            continue
        x, y = pos[n]
        dx, dy = x - hx, y - hy
        r = math.hypot(dx, dy)
        th = math.atan2(dy, dx) + delta
        trial[n] = (hx + r * math.cos(th), hy + r * math.sin(th))
    for n in tb:
        if n not in pos or (n in pinned and n != b):
            continue
        x, y = pos[n]
        dx, dy = x - hx, y - hy
        r = math.hypot(dx, dy)
        th = math.atan2(dy, dx) - delta
        trial[n] = (hx + r * math.cos(th), hy + r * math.sin(th))
    trial[hub] = (hx, hy)
    return trial


def _realize_order_by_swaps(
    hub: str,
    stubs: list[str],
    owner: dict[str, str],
    pinned: set[str],
    pos: dict[str, tuple[float, float]],
    clinks: list[tuple[str, str]],
    foreign: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    *,
    target_ord: list[str] | None = None,
    allow_global_rise: bool = False,
) -> tuple[dict[str, tuple[float, float]], int, int]:
    """Greedy adjacent territory swaps; primary score = contracted@pos.

    Search is driven by contracted crossings (lower-level edges ignored in
    the objective). On an already-laid canvas (stage-2 polish) we also refuse
    swaps that raise *global* crossings — otherwise leaf noise swamps the
    map before lower levels can be re-expanded. Set allow_global_rise for
    true build-from-skeleton top-down.
    """
    trial = dict(pos)
    before = _contracted_crossings_at_pos(hub, stubs, clinks, trial, foreign)
    g_base = count_edge_crossings(trial, links)
    target_idx = {s: i for i, s in enumerate(target_ord)} if target_ord else {}

    for _ in range(max(2 * len(stubs), 8)):
        geo = sorted(stubs, key=lambda s: _ang(trial, hub, s))
        pair_idxs = list(range(len(geo) - 1))
        if target_idx:
            inv = [
                i
                for i in pair_idxs
                if target_idx.get(geo[i], 0) > target_idx.get(geo[i + 1], 0)
            ]
            pair_idxs = inv + [i for i in pair_idxs if i not in inv]
        progressed = False
        c0 = _contracted_crossings_at_pos(hub, stubs, clinks, trial, foreign)
        g0 = count_edge_crossings(trial, links)
        # Pick the adjacent swap with best contracted drop; tie-break by global.
        best_cand = None
        best_key = None
        for i in pair_idxs:
            cand = _swap_territories(hub, geo[i], geo[i + 1], owner, pinned, trial)
            for h in pinned:
                if h in pos:
                    cand[h] = pos[h]
            cand[hub] = pos[hub]
            c1 = _contracted_crossings_at_pos(hub, stubs, clinks, cand, foreign)
            if c1 >= c0:
                continue
            g1 = count_edge_crossings(cand, links)
            if not allow_global_rise and g1 > g0:
                continue
            key = (c0 - c1, g0 - g1)
            if best_key is None or key > best_key:
                best_key = key
                best_cand = cand
        if best_cand is not None:
            trial = best_cand
            progressed = True
        if not progressed:
            break
    after = _contracted_crossings_at_pos(hub, stubs, clinks, trial, foreign)
    del g_base
    return trial, before, after


def hierarchy_sectors_greedy(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    step: float | None = None,
    max_hubs: int = 20,
    allow_global_rise: bool = False,
) -> OpResult:
    """Top-down: contract → order by contracted crossings → rigid expand.

    Role beam offsets (pin_beam) belong *after* this pass, not inside it.
    Default stage-2 polish keeps global from rising; pass allow_global_rise
    for skeleton-time top-down where leaf crossings are deferred.
    """
    params = params or LayoutParams()
    step_px = float(step if step is not None else max(params.pitch * 0.95, 170.0))
    pos = dict(state.positions)
    links = list(state.links)
    adj = state.adj
    global0 = count_edge_crossings(pos, links)

    hubs = pick_hub_seeds(state, max_hubs=max_hubs)
    cores = [h for h in hubs if state.layers.get(h) == "core"]
    aggs = [h for h in hubs if state.layers.get(h) == "agg"]
    # Top-down: cores first, then aggs, then remaining high-degree hubs.
    order_hubs = list(cores) + [h for h in aggs if h not in cores]
    for h in hubs:
        if h not in order_hubs:
            order_hubs.append(h)

    pinned = set(hubs)
    hub_of = _owner_hub_map(adj, pinned)
    level_notes: list[dict[str, Any]] = []
    any_level = False
    contracted_gain = 0

    for hub in order_hubs:
        if hub not in pos:
            continue
        members = {hub}
        q: deque[str] = deque([hub])
        while q:
            u = q.popleft()
            for v in adj.get(u, ()):
                if v in members:
                    continue
                if v in pinned and v != hub:
                    continue
                members.add(v)
                q.append(v)
        for v in adj.get(hub, ()):
            if v not in pinned:
                members.add(v)

        stubs, owner = _stub_territories(hub, members, adj, pinned)
        if len(stubs) < 2:
            continue

        clinks = _contracted_links(
            hub, stubs, owner, adj, pinned, hub_of=hub_of
        )
        foreign = {
            n: pos[n]
            for n in pinned
            if n != hub and n in pos and any(n in e for e in clinks)
        }
        angs = [_ang(pos, hub, s) for s in stubs]
        a0, a1 = min(angs) - 0.15, max(angs) + 0.15
        if a1 - a0 < 0.8:
            mid = sum(angs) / len(angs)
            a0, a1 = mid - 1.1, mid + 1.1

        geo = sorted(stubs, key=lambda s: _ang(pos, hub, s))
        base_cc = _score_order(
            hub,
            geo,
            clinks,
            radius=step_px,
            hub_xy=pos[hub],
            foreign_pos=foreign,
            a0=a0,
            a1=a1,
        )
        best_ord, best_cc = _best_order_2opt(
            hub,
            stubs,
            pos,
            clinks,
            radius=step_px,
            hub_xy=pos[hub],
            foreign_pos=foreign,
            a0=a0,
            a1=a1,
        )
        # Abstract arc bakeoff only proposes a target cyclic order.
        if best_cc >= base_cc:
            continue

        # Realize via adjacent territory swaps; accept on contracted@pos only.
        trial, c_before, c_after = _realize_order_by_swaps(
            hub,
            stubs,
            owner,
            pinned,
            pos,
            clinks,
            foreign,
            links,
            target_ord=best_ord,
            allow_global_rise=allow_global_rise,
        )
        if c_after >= c_before:
            continue
        for h in pinned:
            if h in pos:
                trial[h] = pos[h]
        trial[hub] = pos[hub]
        # No per-level overlap fix — it fights sector moves and is slow on
        # large canvases. Zero-overlap is ensured by the layout_tool wrapper.
        g1 = count_edge_crossings(trial, links)
        pos = trial
        any_level = True
        contracted_gain += c_before - c_after
        level_notes.append(
            {
                "hub_id": hub,
                "hub_name": state.names.get(hub, hub),
                "stubs": len(stubs),
                "contracted_before": c_before,
                "contracted_after": c_after,
                "contracted_arc_before": base_cc,
                "contracted_arc_after": best_cc,
                "global_after": g1,
            }
        )

    if not any_level:
        return OpResult(
            state=state,
            moved=set(),
            op="hierarchy_sectors",
            params={"accepted_n": 0, "contracted_gain": 0},
            note="no_level_improved",
        )

    # One end-of-pass overlap polish (hubs stay pinned).
    st_end = state.copy()
    st_end.positions = pos
    st_end = fix_overlaps_local(st_end, params).state
    for h in pinned:
        if h in pos:
            st_end.positions[h] = pos[h]
    pos = {k: (float(v[0]), float(v[1])) for k, v in st_end.positions.items()}

    out = state.copy()
    out.positions = pos
    moved = {
        n for n, p in pos.items() if n in state.positions and p != state.positions[n]
    }
    g_end = count_edge_crossings(pos, links)
    out.meta = dict(out.meta or {})
    out.meta["hierarchy_sectors"] = {
        "levels": level_notes,
        "crossings_before": global0,
        "crossings_after": g_end,
        "contracted_gain": contracted_gain,
    }
    return OpResult(
        state=out,
        moved=moved,
        op="hierarchy_sectors",
        params={
            "accepted_n": len(level_notes),
            "levels": level_notes[:12],
            "crossings_before": global0,
            "crossings_after": g_end,
            "contracted_gain": contracted_gain,
        },
        note=(
            f"hierarchy_sectors:{len(level_notes)} levels "
            f"contractedΔ={contracted_gain} x:{global0}->{g_end}"
        ),
    )


def hierarchy_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not overrides:
        return out
    if overrides.get("step") is not None:
        try:
            out["step"] = float(overrides["step"])
        except (TypeError, ValueError):
            pass
    if overrides.get("max_hubs") is not None:
        try:
            out["max_hubs"] = int(overrides["max_hubs"])
        except (TypeError, ValueError):
            pass
    if "allow_global_rise" in overrides:
        out["allow_global_rise"] = bool(overrides.get("allow_global_rise"))
    return out
