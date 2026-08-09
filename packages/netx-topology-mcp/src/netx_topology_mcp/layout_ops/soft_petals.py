"""Stage-2: per soft-block stub fan + spine; accept only if global crossings drop."""

from __future__ import annotations

import math
from collections import deque
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.channels import order_stubs_crossing_aware
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local
from netx_topology_mcp.layout_ops.partition import partition_soft_blocks, pick_hub_seeds
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _longest_path(
    comp: set[str], adj: dict[str, set[str]], prefer: str | None = None
) -> list[str]:
    if not comp:
        return []
    if len(comp) == 1:
        return [next(iter(comp))]

    def far(src: str) -> tuple[str, list[str]]:
        prev: dict[str, str | None] = {src: None}
        q: deque[str] = deque([src])
        last = src
        while q:
            u = q.popleft()
            last = u
            for v in adj.get(u, ()):
                if v in comp and v not in prev:
                    prev[v] = u
                    q.append(v)
        path = [last]
        while prev[path[-1]] is not None:
            path.append(prev[path[-1]])  # type: ignore[index]
        path.reverse()
        return last, path

    start = prefer if prefer in comp else next(iter(comp))
    a, _ = far(start)
    _, path = far(a)
    return path


def soft_petals_greedy(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    hub_id: str | None = None,
    step: float | None = None,
    min_block_size: int = 3,
) -> OpResult:
    """Fan stubs around each soft-block hub; keep a block only if crossings fall."""
    params = params or LayoutParams()
    step_px = float(step if step is not None else max(params.pitch * 0.92, 160.0))
    pos = dict(state.positions)
    links = list(state.links)
    adj = state.adj
    cross = count_edge_crossings(pos, links)

    blocks = partition_soft_blocks(state, mode="hub_territory")
    hubs = set(pick_hub_seeds(state))
    for n, ly in state.layers.items():
        if ly == "core":
            hubs.add(n)

    if hub_id:
        hub_id = str(hub_id).strip()
        blocks = [b for b in blocks if b.hub_id == hub_id]
        if not blocks:
            return OpResult(
                state=state,
                moved=set(),
                op="soft_petals",
                params={"hub_id": hub_id},
                note="hub_not_found",
            )

    def _commit_trial(
        trial: dict[str, tuple[float, float]],
        *,
        hub: str,
        hx: float,
        hy: float,
        best_c: int,
    ) -> tuple[dict[str, tuple[float, float]] | None, int]:
        c1 = count_edge_crossings(trial, links)
        if c1 >= best_c:
            return None, best_c
        st_try = state.copy()
        st_try.positions = trial
        stf = fix_overlaps_local(st_try, params).state
        for hid in hubs:
            if hid != hub and hid in pos:
                stf.positions[hid] = pos[hid]
        stf.positions[hub] = (hx, hy)
        trial2 = {k: (float(v[0]), float(v[1])) for k, v in stf.positions.items()}
        c2 = count_edge_crossings(trial2, links)
        if c2 < best_c:
            return trial2, c2
        return None, best_c

    accepted: list[dict[str, Any]] = []
    for b in sorted(blocks, key=lambda x: -len(x.node_ids)):
        hub = b.hub_id
        members = [n for n in b.node_ids if n in pos]
        if not hub or hub not in pos or len(members) < min_block_size:
            continue
        pinned = set(hubs)
        hx, hy = pos[hub]
        member_set = set(members)
        stubs = [
            n
            for n in adj.get(hub, ())
            if n in member_set and n not in pinned
        ]
        if len(stubs) < 1:
            continue

        owner: dict[str, str] = {}
        q: deque[str] = deque()
        for stub in stubs:
            owner[stub] = stub
            q.append(stub)
        while q:
            u = q.popleft()
            for v in adj.get(u, ()):
                if v not in member_set or v == hub or v in owner or v in pinned:
                    continue
                owner[v] = owner[u]
                q.append(v)

        def ang(s: str) -> float:
            x, y = pos[s]
            return math.atan2(y - hy, x - hx)

        best_trial = None
        best_c = cross
        kind = None

        # 1) Rigid rotate existing petal around hub (keeps local structure).
        movable = [n for n in members if n != hub and n not in pinned]
        for deg in (-90, -60, -45, -30, -15, 15, 30, 45, 60, 90):
            rad = math.radians(deg)
            ca, sa = math.cos(rad), math.sin(rad)
            trial = dict(pos)
            for n in movable:
                x, y = pos[n]
                dx, dy = x - hx, y - hy
                trial[n] = (hx + dx * ca - dy * sa, hy + dx * sa + dy * ca)
            trial[hub] = (hx, hy)
            got, best_c = _commit_trial(trial, hub=hub, hx=hx, hy=hy, best_c=best_c)
            if got is not None:
                best_trial = got
                kind = f"rotate:{deg}"

        # 2) Swap adjacent stub territories by exchanging polar angles of nodes.
        stubs_geo = sorted(stubs, key=ang)
        if len(stubs_geo) >= 2:
            for i in range(len(stubs_geo) - 1):
                a, bstub = stubs_geo[i], stubs_geo[i + 1]
                ta = {n for n, o in owner.items() if o == a} | {a}
                tb = {n for n, o in owner.items() if o == bstub} | {bstub}
                aa, ab = ang(a), ang(bstub)
                delta = ab - aa
                trial = dict(pos)
                for n in ta:
                    if n in pinned:
                        continue
                    x, y = pos[n]
                    dx, dy = x - hx, y - hy
                    r = math.hypot(dx, dy)
                    th = math.atan2(dy, dx) + delta
                    trial[n] = (hx + r * math.cos(th), hy + r * math.sin(th))
                for n in tb:
                    if n in pinned:
                        continue
                    x, y = pos[n]
                    dx, dy = x - hx, y - hy
                    r = math.hypot(dx, dy)
                    th = math.atan2(dy, dx) - delta
                    trial[n] = (hx + r * math.cos(th), hy + r * math.sin(th))
                trial[hub] = (hx, hy)
                got, best_c = _commit_trial(trial, hub=hub, hx=hx, hy=hy, best_c=best_c)
                if got is not None:
                    best_trial = got
                    kind = f"swap:{a[:6]}-{bstub[:6]}"

        # 3) Full re-fan with geo / crossing-aware orders + cyclic rotations.
        stubs_x = order_stubs_crossing_aware(hub, stubs, adj, member_set, owner)
        orders: list[list[str]] = []
        for base in (stubs_geo, stubs_x):
            for k in range(len(base)):
                orders.append(base[k:] + base[:k])
            orders.append(list(reversed(base)))

        seen_ord: set[tuple[str, ...]] = set()
        for stubs_ord in orders:
            key = tuple(stubs_ord)
            if key in seen_ord:
                continue
            seen_ord.add(key)
            angs = [ang(s) for s in stubs_ord]
            a0, a1 = min(angs) - 0.1, max(angs) + 0.1
            if a1 - a0 < 0.7:
                mid = sum(angs) / len(angs)
                a0, a1 = mid - 1.0, mid + 1.0
            # Also try a wider fan spanning more of the circle when crowded.
            spans = [(a0, a1)]
            if len(stubs_ord) >= 3:
                mid = 0.5 * (a0 + a1)
                half = max(0.9, 0.55 * (a1 - a0) + 0.4)
                spans.append((mid - half, mid + half))

            for sa0, sa1 in spans:
                trial = dict(pos)
                for i, stub in enumerate(stubs_ord):
                    mid = (
                        0.5 * (sa0 + sa1)
                        if len(stubs_ord) == 1
                        else sa0 + (sa1 - sa0) * i / (len(stubs_ord) - 1)
                    )
                    ux, uy = math.cos(mid), math.sin(mid)
                    trial[stub] = (hx + ux * step_px, hy + uy * step_px)
                    terr = {n for n, o in owner.items() if o == stub}
                    spine = _longest_path(terr, adj, prefer=stub)
                    if stub in spine:
                        si = spine.index(stub)
                        fwd, bwd = spine[si:], list(reversed(spine[: si + 1]))
                        spine = fwd if len(fwd) >= len(bwd) else bwd
                    sx, sy = trial[stub]
                    for k, n in enumerate(spine):
                        if n in pinned or n == stub:
                            continue
                        trial[n] = (
                            sx + ux * step_px * max(k, 1),
                            sy + uy * step_px * max(k, 1),
                        )
                    for n in terr:
                        if n in pinned or n == stub or n in spine:
                            continue
                        portals = [v for v in adj.get(n, ()) if v in trial]
                        if not portals:
                            continue
                        px, py = trial[portals[0]]
                        trial[n] = (px - uy * step_px * 0.7, py + ux * step_px * 0.7)

                trial[hub] = (hx, hy)
                got, best_c = _commit_trial(trial, hub=hub, hx=hx, hy=hy, best_c=best_c)
                if got is not None:
                    best_trial = got
                    kind = "refan"

        if best_trial is None:
            continue
        pos = best_trial
        cross = best_c
        accepted.append(
            {
                "hub_id": hub,
                "hub_name": state.names.get(hub, hub),
                "crossings": cross,
                "n": len(members),
                "kind": kind,
            }
        )

    if not accepted:
        return OpResult(
            state=state,
            moved=set(),
            op="soft_petals",
            params={"accepted_n": 0, "hub_id": hub_id},
            note="no_block_improved",
        )

    out = state.copy()
    out.positions = pos
    moved = {
        n
        for n, p in pos.items()
        if n in state.positions and p != state.positions[n]
    }
    out.meta = dict(out.meta or {})
    out.meta["soft_petals"] = {
        "accepted": accepted,
        "crossings_after": cross,
    }
    return OpResult(
        state=out,
        moved=moved,
        op="soft_petals",
        params={
            "accepted_n": len(accepted),
            "accepted": accepted,
            "hub_id": hub_id,
            "crossings_after": cross,
        },
        note=f"soft_petals:{len(accepted)} blocks",
    )


def soft_petals_params_from_overrides(
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not overrides:
        return out
    if overrides.get("hub_id"):
        out["hub_id"] = str(overrides["hub_id"]).strip()
    if overrides.get("step") is not None:
        try:
            out["step"] = float(overrides["step"])
        except (TypeError, ValueError):
            pass
    if overrides.get("min_block_size") is not None:
        try:
            out["min_block_size"] = int(overrides["min_block_size"])
        except (TypeError, ValueError):
            pass
    return out
