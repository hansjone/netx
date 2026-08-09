"""Dual-portal basic units: parallel lanes + straight/回 chains + tails.

A unit = two portals + ≥2 interior-disjoint corridors (+ optional deg≤2
tails), or a long chain between portals. Units may share portals.
Beautify targets zero edge crossings: multi-corridor → parallel H/V lanes;
chains (any length) → straight; tails as straight spurs. No 回字 fold.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local
from netx_topology_mcp.layout_ops.min_rings import cover_hub_paths
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.sugiyama import _find_two_portal_ring_groups


@dataclass
class DualUnit:
    portal_a: str
    portal_b: str
    paths: list[list[str]]
    tails: list[list[str]] = field(default_factory=list)
    unit_id: int = 0

    @property
    def portals(self) -> tuple[str, str]:
        return self.portal_a, self.portal_b

    def member_ids(self) -> set[str]:
        out = {self.portal_a, self.portal_b}
        for p in self.paths:
            out.update(p)
        for t in self.tails:
            out.update(t)
        return out

    def as_dict(self, names: dict[str, str] | None = None) -> dict[str, Any]:
        names = names or {}
        nest = len(self.paths)
        return {
            "unit_id": self.unit_id,
            "portal_a": self.portal_a,
            "portal_b": self.portal_b,
            "portal_a_name": names.get(self.portal_a, self.portal_a),
            "portal_b_name": names.get(self.portal_b, self.portal_b),
            "path_count": nest,
            "nest_depth": nest,
            "tail_count": len(self.tails),
            "node_count": len(self.member_ids()),
            "node_ids": sorted(self.member_ids()),
            "paths_len": [len(p) for p in self.paths],
            "tail_lens": [len(t) for t in self.tails],
        }


def _collect_tails(
    state: LayoutState,
    core: set[str],
    *,
    max_tail: int = 24,
) -> list[list[str]]:
    """deg≤2 chains hanging off unit core; stop at foreign high-degree nodes."""
    adj, names = state.adj, state.names
    claimed = set(core)
    tails: list[list[str]] = []

    seeds = []
    for n in core:
        for v in adj.get(n, ()):
            if v in claimed:
                continue
            deg = len(adj.get(v, ()))
            if deg <= 2 and state.layers.get(v) not in ("core", "agg"):
                seeds.append((n, v))

    for attach, stub in sorted(seeds, key=lambda x: names.get(x[1], x[1])):
        if stub in claimed:
            continue
        chain = [stub]
        claimed.add(stub)
        prev, cur = attach, stub
        while len(chain) < max_tail:
            nbs = [v for v in adj.get(cur, ()) if v != prev and v not in claimed]
            # Prefer continuing along deg≤2 corridor
            forward = [
                v
                for v in nbs
                if len(adj.get(v, ())) <= 2 and state.layers.get(v) not in ("core", "agg")
            ]
            if len(forward) != 1:
                break
            prev, cur = cur, forward[0]
            chain.append(cur)
            claimed.add(cur)
        tails.append(chain)
    return tails


def find_dual_portal_units(
    state: LayoutState,
    *,
    max_units: int = 120,
) -> list[DualUnit]:
    """Detect dual-portal eye units; interiors exclusive, portals may overlap."""
    adj, names, layers = state.adj, state.names, state.layers
    ens = [n for n, ly in layers.items() if ly == "access" and n in adj]
    an_set = {n for n, ly in layers.items() if ly == "agg"}
    core_set = {n for n, ly in layers.items() if ly == "core"}

    units: list[DualUnit] = []
    used_interior: set[str] = set()

    # 1) Access/AN two-portal ring groups (sugiyama metro).
    if ens:
        groups = _find_two_portal_ring_groups(ens, adj, names, an_set)
        for g in groups:
            a, b = g["portals"]  # type: ignore[misc]
            paths: list[list[str]] = list(g["paths"])  # type: ignore[arg-type]
            interior: set[str] = set()
            for p in paths:
                interior |= set(p[1:-1])
            if interior & used_interior:
                continue
            used_interior |= interior
            core = {a, b} | interior
            for p in paths:
                core.update(p)
            tails = _collect_tails(state, core)
            units.append(
                DualUnit(portal_a=a, portal_b=b, paths=paths, tails=tails)
            )
            if len(units) >= max_units:
                break

    # 2) Agg/core hub pairs with ≥2 corridor covers (fills CN—AN / CN—CN).
    hubs = sorted(
        [n for n in (an_set | core_set) if n in adj],
        key=lambda n: (-len(adj.get(n, ())), names.get(n, n)),
    )
    for i, a in enumerate(hubs):
        if len(units) >= max_units:
            break
        for b in hubs[i + 1 :]:
            if len(units) >= max_units:
                break
            # Only forbid other cores — when almost all NEs are layer=agg,
            # banning every agg hub makes cover_hub_paths return 0 corridors.
            forbid = core_set - {a, b}
            paths = cover_hub_paths(a, b, adj, names, forbid=forbid)
            if len(paths) < 2:
                continue
            interior = set()
            for p in paths:
                interior |= set(p[1:-1])
            if not interior or interior & used_interior:
                continue
            # Skip if this pair already covered as a unit
            if any(
                {u.portal_a, u.portal_b} == {a, b} for u in units
            ):
                continue
            used_interior |= interior
            core = {a, b} | interior
            for p in paths:
                core.update(p)
            tails = _collect_tails(state, core)
            # Stable left/right by name
            pa, pb = a, b
            if names.get(pa, pa) > names.get(pb, pb):
                pa, pb = pb, pa
                paths = [list(reversed(p)) for p in paths]
            units.append(
                DualUnit(portal_a=pa, portal_b=pb, paths=paths, tails=tails)
            )

    for i, u in enumerate(units):
        u.unit_id = i
    return units


def _normalize_paths(
    unit: DualUnit,
) -> list[list[str]]:
    a, b = unit.portal_a, unit.portal_b
    paths: list[list[str]] = []
    for p in unit.paths:
        pp = list(p)
        if pp and pp[0] == b and pp[-1] == a:
            pp = list(reversed(pp))
        if len(pp) >= 2 and pp[0] == a and pp[-1] == b:
            paths.append(pp)
    paths.sort(key=len)
    return paths


def classify_dual_unit(unit: DualUnit) -> str:
    """petal = multi-corridor (parallel lanes); else straight (no 回字)."""
    paths = _normalize_paths(unit)
    corridors = [p for p in paths if len(p) >= 3]
    if len(corridors) >= 2:
        return "petal"
    return "straight"


def _place_chain_straight(
    nodes: list[str],
    *,
    origin: tuple[float, float],
    direction: tuple[float, float],
    pitch: float,
    pos: dict[str, tuple[float, float]],
) -> None:
    dx, dy = direction
    nrm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / nrm, dy / nrm
    ox, oy = origin
    for i, nid in enumerate(nodes):
        if nid in pos:
            continue
        pos[nid] = (ox + ux * pitch * (i + 1), oy + uy * pitch * (i + 1))


def beautify_dual_unit_positions(
    state: LayoutState,
    unit: DualUnit,
    params: LayoutParams | None = None,
) -> dict[str, tuple[float, float]]:
    """Beautify one unit: multi-corridor→H/V lanes; chains→straight (no 回字).

    Local coords; portals on x-axis. dual_mass aligns onto world portals.
    Multi-corridor (kind=petal): parallel horizontal lanes with vertical
    stubs at portal x — no ellipse arcs.
    """
    params = params or LayoutParams()
    pitch = max(float(params.pitch), 170.0)
    ry = max(float(params.lane), float(params.side), 220.0)
    a, b = unit.portal_a, unit.portal_b
    paths = _normalize_paths(unit)
    kind = classify_dual_unit(unit)
    max_mid = max((len(p) - 2 for p in paths), default=0)
    half = max(pitch * (max_mid + 1) * 0.5, pitch * 4.0, 700.0)
    pos: dict[str, tuple[float, float]] = {a: (-half, 0.0), b: (half, 0.0)}

    if kind == "petal":
        # Parallel H/V lanes: first/last mid share portal x → V stub + H spine.
        band_i = 0
        for p in paths:
            mid = p[1:-1]
            if not mid:
                continue
            side = 1 if band_i % 2 == 0 else -1
            amp = ry * (0.85 + 0.35 * (band_i // 2))
            band_i += 1
            n_mid = len(mid)
            for k, n in enumerate(mid):
                if n in pos:
                    continue
                if n_mid == 1:
                    pos[n] = (0.0, side * amp)
                else:
                    t = k / (n_mid - 1)
                    x = -half + 2.0 * half * t
                    pos[n] = (x, side * amp)
    else:
        # Single corridor / chain body — always straight between portals.
        body: list[str] = []
        if paths:
            body = list(paths[0][1:-1])
        if not body and unit.tails:
            longest = max(unit.tails, key=len)
            body = list(longest)
        if body:
            _place_chain_straight(
                body,
                origin=(-half, 0.0),
                direction=(1.0, 0.0),
                pitch=max(pitch, (2.0 * half) / (len(body) + 1)),
                pos=pos,
            )

    # Tails: always straight H/V spurs (stack parallel if many).
    used = set(pos)
    for ti, chain in enumerate(unit.tails):
        if not chain:
            continue
        # Skip if already placed as body.
        fresh = [n for n in chain if n not in used]
        if not fresh:
            continue
        attach = None
        for n in chain:
            for v in state.adj.get(n, ()):
                if v in pos and v not in chain:
                    attach = v
                    break
            if attach is not None:
                break
        if attach is None:
            attach = a if ti % 2 == 0 else b
        ax, ay = pos[attach]
        y_off = (ti % 3 - 1) * pitch * 0.45
        if attach == a:
            origin = (ax, ay + y_off)
            direc = (-1.0, 0.0)
        elif attach == b:
            origin = (ax, ay + y_off)
            direc = (1.0, 0.0)
        else:
            origin = (ax, ay)
            direc = (0.0, 1.0 if ay >= 0 else -1.0)
        _place_chain_straight(
            fresh, origin=origin, direction=direc, pitch=pitch * 0.85, pos=pos
        )
        used |= set(pos)

    leftovers = [n for n in unit.member_ids() if n not in pos]
    top = max((xy[1] for xy in pos.values()), default=0.0) + ry
    for i, n in enumerate(sorted(leftovers, key=lambda x: state.names.get(x, x))):
        pos[n] = (-half + i * pitch, top)

    return pos


def layout_dual_unit_positions(
    state: LayoutState,
    unit: DualUnit,
    params: LayoutParams | None = None,
) -> dict[str, tuple[float, float]]:
    """Unit local layout — lanes / 回 / straight beautify (zero-cross target)."""
    return beautify_dual_unit_positions(state, unit, params)


def _uncross_unit(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    pinned: set[str],
    *,
    max_rounds: int = 80,
) -> dict[str, tuple[float, float]]:
    """Greedy: move lower-degree free endpoint vertically to kill crossings."""
    from netx_topology_mcp.layout_metrics import segments_properly_intersect

    out = dict(pos)
    deg: dict[str, int] = {}
    for u, v in links:
        deg[u] = deg.get(u, 0) + 1
        deg[v] = deg.get(v, 0) + 1

    def crossings() -> list[tuple[int, int]]:
        segs = []
        for u, v in links:
            if u in out and v in out:
                segs.append((u, v, out[u], out[v]))
        bad: list[tuple[int, int]] = []
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                a, b, pa, pb = segs[i]
                c, d, pc, pd = segs[j]
                if len({a, b, c, d}) < 4:
                    continue
                if segments_properly_intersect(pa, pb, pc, pd):
                    bad.append((i, j))
        return bad

    for _ in range(max_rounds):
        bad = crossings()
        if not bad:
            break
        segs = [(u, v) for u, v in links if u in out and v in out]
        progressed = False
        for i, j in bad[:12]:
            if i >= len(segs) or j >= len(segs):
                continue
            ends = list(segs[i]) + list(segs[j])
            free = [n for n in ends if n not in pinned]
            if not free:
                continue
            free.sort(key=lambda n: (deg.get(n, 0), n))
            n = free[0]
            x, y = out[n]
            best = None
            for dy in (
                80.0,
                -80.0,
                160.0,
                -160.0,
                240.0,
                -240.0,
                320.0,
                -320.0,
                480.0,
                -480.0,
            ):
                for dx in (0.0, 40.0, -40.0, 80.0, -80.0):
                    trial = dict(out)
                    trial[n] = (x + dx, y + dy)
                    c0 = count_edge_crossings(out, links)
                    c1 = count_edge_crossings(trial, links)
                    if c1 < c0 and (best is None or c1 < best[0]):
                        best = (c1, x + dx, y + dy)
            if best is not None:
                out[n] = (best[1], best[2])
                progressed = True
                break
        if not progressed:
            break
    return out


def layout_dual_unit(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    unit: DualUnit | None = None,
    unit_id: int | None = None,
    portal_a: str | None = None,
    portal_b: str | None = None,
) -> OpResult:
    """Layout the (single) dual-portal unit on this canvas; require crossings=0."""
    params = params or LayoutParams()
    units = find_dual_portal_units(state) if unit is None else [unit]
    if unit_id is not None:
        units = [u for u in units if u.unit_id == unit_id]
    if portal_a and portal_b:
        matched = [
            u
            for u in units
            if {u.portal_a, u.portal_b} == {portal_a, portal_b}
        ]
        if matched:
            units = matched
        else:
            # Rebuild unit for the requested portals from this subgraph.
            # Explicit portals: only forbid *other cores* (do NOT ban all agg —
            # LPG-style canvases label almost every NE as agg, which yielded 0 paths).
            forbid = {
                n
                for n, ly in state.layers.items()
                if ly == "core" and n not in (portal_a, portal_b)
            }
            paths = cover_hub_paths(
                portal_a, portal_b, state.adj, state.names, forbid=forbid
            )
            if len(paths) < 2:
                paths = cover_hub_paths(
                    portal_a, portal_b, state.adj, state.names, forbid=set()
                )
            if len(paths) >= 2:
                pa, pb = portal_a, portal_b
                if state.names.get(pa, pa) > state.names.get(pb, pb):
                    pa, pb = pb, pa
                    paths = [list(reversed(p)) for p in paths]
                core = {pa, pb}
                for p in paths:
                    core.update(p)
                units = [
                    DualUnit(
                        portal_a=pa,
                        portal_b=pb,
                        paths=paths,
                        tails=_collect_tails(state, core),
                        unit_id=0,
                    )
                ]
    if not units:
        # Whole canvas as one unit attempt: pick best hub pair cover
        hubs = [
            n
            for n, ly in state.layers.items()
            if ly in ("agg", "core") and n in state.adj
        ]
        hubs.sort(key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n)))
        if len(hubs) >= 2:
            a, b = hubs[0], hubs[1]
            forbid = {
                n
                for n, ly in state.layers.items()
                if ly == "core" and n not in (a, b)
            }
            paths = cover_hub_paths(a, b, state.adj, state.names, forbid=forbid)
            if len(paths) < 2:
                paths = cover_hub_paths(
                    a, b, state.adj, state.names, forbid=set()
                )
            if len(paths) >= 2:
                if state.names.get(a, a) > state.names.get(b, b):
                    a, b = b, a
                    paths = [list(reversed(p)) for p in paths]
                core = {a, b}
                for p in paths:
                    core.update(p)
                units = [
                    DualUnit(
                        portal_a=a,
                        portal_b=b,
                        paths=paths,
                        tails=_collect_tails(state, core),
                        unit_id=0,
                    )
                ]
    if not units:
        return OpResult(
            state=state,
            moved=set(),
            op="layout_dual_unit",
            params={},
            note="no_dual_unit",
        )

    # If multiple units detected on one canvas, layout the largest by node count
    u = max(units, key=lambda x: len(x.member_ids()))
    pos = layout_dual_unit_positions(state, u, params)
    out = state.copy()
    for n, xy in pos.items():
        if n in out.positions:
            out.positions[n] = xy
    parked = 0
    extras = [n for n in out.positions if n not in pos]
    if extras:
        base_y = max((xy[1] for xy in pos.values()), default=0.0) + max(
            params.lane, 300.0
        )
        for i, n in enumerate(sorted(extras, key=lambda x: state.names.get(x, x))):
            out.positions[n] = (
                -len(extras) * 0.5 * params.pitch + i * params.pitch,
                base_y,
            )
            parked += 1

    members = u.member_ids()
    unit_links = [e for e in out.links if e[0] in members and e[1] in members]
    # Uncross before overlap fix (overlap fix often reintroduces crossings).
    pinned = {u.portal_a, u.portal_b}
    out.positions = _uncross_unit(out.positions, unit_links, pinned)
    x_unit = count_edge_crossings(out.positions, unit_links)

    # Gentle overlap resolve only if still zero-cross; else skip.
    stf = out
    if x_unit == 0:
        cand = fix_overlaps_local(out, params).state
        cand.positions[u.portal_a] = pos[u.portal_a]
        cand.positions[u.portal_b] = pos[u.portal_b]
        x_after = count_edge_crossings(cand.positions, unit_links)
        if x_after == 0:
            stf = cand
        # else keep pre-overlap geometry

    x_unit = count_edge_crossings(stf.positions, unit_links)
    x = count_edge_crossings(stf.positions, stf.links)

    moved = {
        n
        for n, p in stf.positions.items()
        if n in state.positions and p != state.positions[n]
    }
    stf.meta = dict(stf.meta or {})
    stf.meta["layout_dual_unit"] = {
        "unit": u.as_dict(state.names),
        "unit_crossings": x_unit,
        "parked": parked,
    }
    accepted = x_unit == 0
    return OpResult(
        state=stf,
        moved=moved,
        op="layout_dual_unit",
        params={
            "unit": u.as_dict(state.names),
            "unit_crossings": x_unit,
            "global_crossings": x,
            "accepted": accepted,
            "parked": parked,
        },
        note=(
            f"layout_dual_unit:paths={len(u.paths)} x=0"
            if accepted
            else f"dual_unit_crossings={x_unit}"
        ),
    )


def dual_units_report(
    state: LayoutState,
    *,
    max_units: int = 120,
) -> dict[str, Any]:
    units = find_dual_portal_units(state, max_units=max_units)
    covered: set[str] = set()
    for u in units:
        covered |= u.member_ids()
    graph_n = len(state.positions) or len(state.names) or len(state.adj)
    return {
        "unit_count": len(units),
        "max_units": max_units,
        "covered_nodes": len(covered),
        "graph_nodes": graph_n,
        "uncovered_nodes": max(0, graph_n - len(covered)),
        "units": [u.as_dict(state.names) for u in units],
        "tip": (
            "Dual-portal eye units: ≥2 interior-disjoint corridors between "
            "portals; layout with action=layout_dual_unit (require crossings=0). "
            "Portals may be shared across units; compose merges same node ids. "
            "Leftovers (uncovered_nodes) go to misc unit canvases."
        ),
    }


def dual_unit_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not overrides:
        return out
    if overrides.get("unit_id") is not None:
        try:
            out["unit_id"] = int(overrides["unit_id"])
        except (TypeError, ValueError):
            pass
    for key in ("portal_a", "portal_b"):
        v = overrides.get(key)
        if v is not None and str(v).strip():
            out[key] = str(v).strip()
    return out
