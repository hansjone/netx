"""Fold a deg≤2 tentacle into the emptiest cheap sector around a hub.

Agent workflow (stage 2): nearest-ring angular sweep → push low-degree blockers
radially out → place the whole chain on an arc in that sector.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _deg(st: LayoutState) -> dict[str, int]:
    return {nid: len(st.adj.get(nid, ())) for nid in st.positions}


def _chain_from(st: LayoutState, hub: str, stub: str, deg: dict[str, int]) -> list[str]:
    path = [stub]
    prev, cur = hub, stub
    while True:
        nxt = [x for x in st.adj.get(cur, ()) if x != prev and deg.get(x, 0) <= 2]
        if len(nxt) != 1:
            break
        path.append(nxt[0])
        prev, cur = cur, nxt[0]
    return path


def _pick_hub(st: LayoutState, deg: dict[str, int], hub_id: str | None) -> str:
    if hub_id and hub_id in st.positions:
        return hub_id
    # Prefer highest degree; break ties by name for stability.
    ranked = sorted(
        st.positions.keys(),
        key=lambda n: (-deg.get(n, 0), st.names.get(n, ""), n),
    )
    if not ranked:
        raise ValueError("fold_chain:empty_graph")
    return ranked[0]


def _first_hop_toward(
    st: LayoutState, hub: str, target: str
) -> str | None:
    """BFS: first neighbor of hub on a path to target."""
    if target in st.adj.get(hub, ()):
        return target
    from collections import deque

    q = deque([hub])
    prev: dict[str, str | None] = {hub: None}
    while q:
        u = q.popleft()
        for v in st.adj.get(u, ()):
            if v in prev:
                continue
            prev[v] = u
            if v == target:
                # Walk back to hop after hub.
                cur = target
                while prev[cur] is not None and prev[cur] != hub:
                    cur = prev[cur]
                return cur
            q.append(v)
    return None


def _pick_stub(
    st: LayoutState,
    hub: str,
    deg: dict[str, int],
    stub_id: str | None,
    *,
    min_len: int,
) -> tuple[str, list[str]]:
    if stub_id and stub_id in st.positions:
        hop = _first_hop_toward(st, hub, stub_id)
        if hop is None:
            raise ValueError(f"fold_chain:stub_unreachable:{stub_id}")
        chain = _chain_from(st, hub, hop, deg)
        return hop, chain

    best: list[str] = []
    best_stub = ""
    for nb in sorted(st.adj.get(hub, ()), key=lambda n: (st.names.get(n, ""), n)):
        if deg.get(nb, 0) > 2:
            continue
        chain = _chain_from(st, hub, nb, deg)
        if len(chain) > len(best):
            best = chain
            best_stub = nb
    if len(best) < min_len:
        raise ValueError(
            f"fold_chain:no_tentacle_from_hub:{hub}:need>={min_len},got={len(best)}"
        )
    return best_stub, best


def _ang_delta(a: float, b: float) -> float:
    return abs(((a - b + 180.0) % 360.0) - 180.0)


def fold_chain_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Pull fold_chain knobs from layout params overrides."""
    o = overrides or {}
    out: dict[str, Any] = {}

    def _f(key: str) -> float | None:
        if key not in o or o[key] is None:
            return None
        try:
            return float(o[key])
        except (TypeError, ValueError):
            return None

    def _i(key: str) -> int | None:
        if key not in o or o[key] is None:
            return None
        try:
            return int(o[key])
        except (TypeError, ValueError):
            return None

    for key in ("hub_id", "stub_id", "chain_start"):
        if key in o and o[key] is not None and str(o[key]).strip():
            out[key] = str(o[key]).strip()
    for key in ("prefer_mid_deg", "r_arc", "chord", "push_r", "min_gap_deg"):
        v = _f(key)
        if v is not None:
            out[key] = v
    for key in ("ring_k", "min_chain_len", "protect_deg_ge"):
        v = _i(key)
        if v is not None:
            out[key] = v
    if "clear_only" in o and o["clear_only"] is not None:
        out["clear_only"] = str(o["clear_only"]).strip().lower() in {
            "1",
            "true",
            "yes",
        } or o["clear_only"] is True
    if isinstance(o.get("protected_ids"), list):
        out["protected_ids"] = [str(x).strip() for x in o["protected_ids"] if str(x).strip()]
    return out


def fold_chain_into_sector(
    st: LayoutState,
    params: LayoutParams,
    *,
    hub_id: str | None = None,
    stub_id: str | None = None,
    chain_start: str | None = None,
    prefer_mid_deg: float | None = None,
    r_arc: float | None = None,
    chord: float | None = None,
    push_r: float | None = None,
    ring_k: int = 14,
    min_gap_deg: float = 26.0,
    min_chain_len: int = 3,
    clear_only: bool = False,
    protected_ids: list[str] | None = None,
    protect_deg_ge: int = 4,
) -> OpResult:
    """Sweep nearest ring, clear cheapest large gap, fold deg≤2 chain onto arc."""
    st = st.copy()
    deg = _deg(st)
    hub = _pick_hub(st, deg, hub_id)
    stub = stub_id or chain_start
    stub, chain = _pick_stub(st, hub, deg, stub, min_len=int(min_chain_len))
    chain_set = set(chain)
    hx, hy = st.positions[hub]

    protected = {hub, *chain_set}
    for pid in protected_ids or []:
        if str(pid).strip():
            protected.add(str(pid).strip())
    # Auto-protect high-degree nodes near hub
    for nid, d in deg.items():
        if d >= int(protect_deg_ge):
            protected.add(nid)

    others: list[tuple[float, float, str]] = []
    for nid, (x, y) in st.positions.items():
        if nid == hub or nid in chain_set:
            continue
        r = math.hypot(x - hx, y - hy)
        ang = math.degrees(math.atan2(y - hy, x - hx)) % 360.0
        others.append((r, ang, nid))
    others.sort()
    k = max(4, min(int(ring_k), len(others)))
    ring = sorted(others[:k], key=lambda t: t[1]) if others else []

    gaps: list[tuple[float, float, float, float, str, str]] = []
    if len(ring) >= 2:
        for i in range(len(ring)):
            a0 = ring[i][1]
            a1 = ring[(i + 1) % len(ring)][1]
            gap = (a1 - a0) % 360.0
            mid = (a0 + gap / 2.0) % 360.0
            lid, rid = ring[i][2], ring[(i + 1) % len(ring)][2]
            cost = 0.0
            for bid in (lid, rid):
                if bid in protected or deg.get(bid, 0) >= int(protect_deg_ge):
                    cost += 4.0
                elif bid in st.adj.get(hub, ()):
                    cost += 1.0
            gaps.append((cost, -gap, mid, gap, lid, rid))
        gaps.sort()
    else:
        # Empty / sparse: open southish default sector.
        mid = float(prefer_mid_deg) if prefer_mid_deg is not None else 90.0
        gaps = [(0.0, -60.0, mid % 360.0, 60.0, "", "")]

    def mid_penalty(mid: float) -> float:
        if prefer_mid_deg is None:
            return 0.0
        return _ang_delta(mid, float(prefer_mid_deg)) / 30.0

    cand = [g for g in gaps if g[3] >= float(min_gap_deg)] or gaps[:3]
    cand.sort(key=lambda g: (g[0], mid_penalty(g[2]), -g[3]))
    cost, _, sector_mid, best_gap, lid, rid = cand[0]
    sector_half = max(min(best_gap / 2.0 - 2.0, 38.0), 28.0)

    target_nn = float(params.target_nn or 155.0)
    r_push = float(push_r) if push_r is not None else max(480.0, target_nn * 3.0)
    arc_r = float(r_arc) if r_arc is not None else max(280.0, target_nn * 2.0)
    chord_len = float(chord) if chord is not None else max(170.0, target_nn * 1.15)

    moved: set[str] = set()
    pushed: list[str] = []

    def in_sector(ang: float) -> bool:
        return _ang_delta(ang, sector_mid) <= sector_half

    for r, ang, nid in others:
        if not in_sector(ang) or nid in protected or deg.get(nid, 0) >= 3:
            continue
        if r >= r_push:
            continue
        nr = max(r_push, r + 200.0)
        rad = math.radians(ang)
        st.positions[nid] = (hx + nr * math.cos(rad), hy + nr * math.sin(rad))
        moved.add(nid)
        pushed.append(nid)

    folded: list[str] = []
    if not clear_only:
        n = len(chain)
        d_ang = math.degrees(2 * math.asin(min(0.95, chord_len / (2.0 * arc_r))))
        span = d_ang * (n - 1)
        a0 = sector_mid - span / 2.0
        for i, nid in enumerate(chain):
            ang = math.radians((a0 + i * d_ang) % 360.0)
            st.positions[nid] = (hx + arc_r * math.cos(ang), hy + arc_r * math.sin(ang))
            moved.add(nid)
            folded.append(nid)

    st.last_moved = set(moved)
    st.meta["fold_chain"] = {
        "hub_id": hub,
        "stub_id": stub,
        "chain": chain,
        "sector_mid": round(sector_mid, 2),
        "sector_half": round(sector_half, 2),
        "gap_deg": round(best_gap, 2),
        "gap_cost": cost,
        "gap_bounds": [lid, rid],
        "prefer_mid_deg": prefer_mid_deg,
        "r_arc": arc_r,
        "chord": chord_len,
        "push_r": r_push,
        "pushed": pushed,
        "folded": folded,
        "clear_only": bool(clear_only),
        "ring_k": k,
    }
    note = (
        f"fold_chain hub={hub[:8]} chain={len(chain)} "
        f"mid={sector_mid:.0f} gap={best_gap:.0f} pushed={len(pushed)}"
    )
    return OpResult(
        state=st,
        moved=moved,
        op="fold_chain_into_sector",
        params={
            "hub_id": hub,
            "stub_id": stub,
            "sector_mid": sector_mid,
            "gap_deg": best_gap,
            "clear_only": bool(clear_only),
        },
        note=note,
    )
