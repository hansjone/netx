"""Dual-portal basic units: ellipse petal arcs + straight chains + tails.

A unit = two portals + ≥2 interior-disjoint corridors (+ optional deg≤2
tails), or a long chain between portals. Units may share portals.
Detection walks eyes **top→down** (core–core → core–agg → agg–agg) and
greedily maximizes membership.

**Objective**: minimize crossings while keeping spread; **keep the eye
interior hollow** (portals only on the chord; AN/corridors on arcs; avoid
mid stacking / overlaps). Residual mesh chords OK; do not polish-fix the eye.
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


def _unit_from_hub_paths(
    state: LayoutState,
    a: str,
    b: str,
    paths: list[list[str]],
) -> DualUnit:
    names = state.names
    pa, pb = a, b
    out_paths = [list(p) for p in paths]
    if names.get(pa, pa) > names.get(pb, pb):
        pa, pb = pb, pa
        out_paths = [list(reversed(p)) for p in out_paths]
    core = {pa, pb}
    for p in out_paths:
        core.update(p)
    return DualUnit(
        portal_a=pa,
        portal_b=pb,
        paths=out_paths,
        tails=_collect_tails(state, core),
    )


def _interior_of(unit: DualUnit) -> set[str]:
    interior: set[str] = set()
    for p in unit.paths:
        interior |= set(p[1:-1])
    return interior


def _greedy_take_by_size(
    candidates: list[DualUnit],
    *,
    used_interior: set[str],
    max_units: int,
    already: list[DualUnit],
) -> None:
    """Claim largest-membership units first (CN eyes should swallow most NEs)."""
    ranked = sorted(candidates, key=lambda u: (-len(u.member_ids()), u.portal_a, u.portal_b))
    for u in ranked:
        if len(already) >= max_units:
            return
        interior = _interior_of(u)
        if not interior or interior & used_interior:
            continue
        if any({x.portal_a, x.portal_b} == {u.portal_a, u.portal_b} for x in already):
            continue
        used_interior |= interior
        already.append(u)


def _portal_eye_tier(layer: str | None) -> int:
    """Lower = higher in fabric (eyes walk top→down)."""
    ly = (layer or "").strip().lower()
    if ly == "core":
        return 0
    if ly == "agg":
        return 1
    if ly == "access":
        return 2
    return 3


def _eye_portal_key(
    portal_a: str,
    portal_b: str,
    layers: dict[str, str],
) -> tuple[int, int]:
    """Sorted portal tiers: (0,0)=core–core, (0,1)=core–agg, (1,1)=agg–agg…"""
    t = sorted(
        (
            _portal_eye_tier(layers.get(portal_a)),
            _portal_eye_tier(layers.get(portal_b)),
        )
    )
    return (t[0], t[1])


def _hub_pair_candidates(
    state: LayoutState,
    hubs_a: list[str],
    hubs_b: list[str],
    *,
    core_set: set[str],
    allow_same: bool,
) -> list[DualUnit]:
    """Build dual units for hub pairs; when allow_same, iterate i<j on hubs_a."""
    adj, names = state.adj, state.names
    out: list[DualUnit] = []
    if allow_same:
        for i, a in enumerate(hubs_a):
            for b in hubs_a[i + 1 :]:
                forbid = core_set - {a, b}
                paths = cover_hub_paths(a, b, adj, names, forbid=forbid)
                if len(paths) < 2:
                    paths = cover_hub_paths(a, b, adj, names, forbid=set())
                if len(paths) < 2:
                    continue
                out.append(_unit_from_hub_paths(state, a, b, paths))
        return out
    seen: set[frozenset[str]] = set()
    for a in hubs_a:
        for b in hubs_b:
            if a == b:
                continue
            key = frozenset((a, b))
            if key in seen:
                continue
            seen.add(key)
            forbid = core_set - {a, b}
            paths = cover_hub_paths(a, b, adj, names, forbid=forbid)
            if len(paths) < 2:
                paths = cover_hub_paths(a, b, adj, names, forbid=set())
            if len(paths) < 2:
                continue
            out.append(_unit_from_hub_paths(state, a, b, paths))
    return out


def find_dual_portal_units(
    state: LayoutState,
    *,
    max_units: int = 120,
) -> list[DualUnit]:
    """Detect dual-portal eye units; interiors exclusive, portals may overlap.

    Eyes walk **top→down**: core–core → core–agg → agg–agg, each tier
    greedily maximizing membership; access rings fill leftovers.
    Crossings are not a detection gate.
    """
    adj, names, layers = state.adj, state.names, state.layers
    ens = [n for n, ly in layers.items() if ly == "access" and n in adj]
    an_set = {n for n, ly in layers.items() if ly == "agg"}
    core_set = {n for n, ly in layers.items() if ly == "core"}

    units: list[DualUnit] = []
    used_interior: set[str] = set()

    cores = sorted(
        [n for n in core_set if n in adj],
        key=lambda n: (-len(adj.get(n, ())), names.get(n, n)),
    )
    aggs = sorted(
        [n for n in an_set if n in adj],
        key=lambda n: (-len(adj.get(n, ())), names.get(n, n)),
    )

    # 1) Core–core
    _greedy_take_by_size(
        _hub_pair_candidates(
            state, cores, cores, core_set=core_set, allow_same=True
        ),
        used_interior=used_interior,
        max_units=max_units,
        already=units,
    )
    # 2) Core–agg
    if len(units) < max_units and cores and aggs:
        _greedy_take_by_size(
            _hub_pair_candidates(
                state, cores, aggs, core_set=core_set, allow_same=False
            ),
            used_interior=used_interior,
            max_units=max_units,
            already=units,
        )
    # 3) Agg–agg
    if len(units) < max_units and len(aggs) >= 2:
        _greedy_take_by_size(
            _hub_pair_candidates(
                state, aggs, aggs, core_set=core_set, allow_same=True
            ),
            used_interior=used_interior,
            max_units=max_units,
            already=units,
        )

    # 4) Access/AN metro rings (bottom leftovers).
    if ens and len(units) < max_units:
        groups = _find_two_portal_ring_groups(ens, adj, names, an_set)
        an_cands: list[DualUnit] = []
        for g in groups:
            a, b = g["portals"]  # type: ignore[misc]
            paths = list(g["paths"])  # type: ignore[arg-type]
            if any({u.portal_a, u.portal_b} == {a, b} for u in units):
                continue
            an_cands.append(_unit_from_hub_paths(state, a, b, paths))
        _greedy_take_by_size(
            an_cands, used_interior=used_interior, max_units=max_units, already=units
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


def _path_first_hop(path: list[str]) -> str:
    return path[1] if len(path) > 2 else ""


def _path_last_hop(path: list[str]) -> str:
    return path[-2] if len(path) > 2 else ""


def _order_paths_for_nest(
    paths: list[list[str]],
    names: dict[str, str],
) -> list[list[str]]:
    """Order corridors to cut spine crossings while nesting short→inner.

    Barycenter-align first/last hops across portals, then split ±y so
    same-side bands stay nested by length (distribution kept).
    """
    if len(paths) <= 1:
        return list(paths)

    def hop_key(nid: str) -> str:
        return names.get(nid, nid)

    order = sorted(
        paths,
        key=lambda p: (
            hop_key(_path_first_hop(p)),
            len(p),
            hop_key(_path_last_hop(p)),
        ),
    )
    for _ in range(5):
        idx = {id(p): i for i, p in enumerate(order)}
        a_pos: dict[str, list[float]] = {}
        b_pos: dict[str, list[float]] = {}
        for p in order:
            a_pos.setdefault(_path_first_hop(p), []).append(float(idx[id(p)]))
            b_pos.setdefault(_path_last_hop(p), []).append(float(idx[id(p)]))
        a_rank = {s: sum(vs) / len(vs) for s, vs in a_pos.items()}
        b_rank = {s: sum(vs) / len(vs) for s, vs in b_pos.items()}
        order = sorted(
            order,
            key=lambda p: (
                0.55 * a_rank.get(_path_first_hop(p), 0.0)
                + 0.45 * b_rank.get(_path_last_hop(p), 0.0),
                len(p),
                hop_key(_path_first_hop(p)),
            ),
        )

    upper: list[list[str]] = []
    lower: list[list[str]] = []
    for i, p in enumerate(order):
        (upper if i % 2 == 0 else lower).append(p)
    upper.sort(key=len)
    lower.sort(key=len)
    out: list[list[str]] = []
    for i in range(max(len(upper), len(lower))):
        if i < len(upper):
            out.append(upper[i])
        if i < len(lower):
            out.append(lower[i])
    return out


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


def _place_petal_bands(
    paths: list[list[str]],
    *,
    a: str,
    b: str,
    rx: float,
    ry_step: float,
    chord_gap: float = 160.0,
) -> dict[str, tuple[float, float]]:
    """Nested half-ellipse bands; chord = portals only (hollow eye interior).

    Short corridors inner, longer outer. Shared nodes claimed by first path.
    Soft apex offset so single-mid corridors do not stack on the mid vertical.
    ``chord_gap`` kept for call-site compat (unused).
    """
    del chord_gap
    pos: dict[str, tuple[float, float]] = {a: (-rx, 0.0), b: (rx, 0.0)}

    band_i = 0
    for p in paths:
        mid = p[1:-1]
        if not mid:
            continue
        side = 1 if band_i % 2 == 0 else -1
        nest = band_i // 2
        ry = ry_step * (nest + 1)
        band_i += 1
        m = len(p)

        # One mid (CN–AN–CN): park on left/right lobe — keep eye interior open.
        if len(mid) == 1:
            n = mid[0]
            if n not in pos:
                lobe = 1.0 if nest % 2 == 0 else -1.0
                ang = side * (math.pi / 2.0 + lobe * 0.45)
                pos[n] = (rx * math.cos(ang), ry * math.sin(ang))
            continue

        for j, n in enumerate(p):
            if n in (a, b):
                continue
            if n in pos:
                continue
            t = j / (m - 1) if m > 1 else 0.5
            ang = math.pi * (1.0 - t)
            if side < 0:
                ang = -ang
            x = rx * math.cos(ang)
            y = ry * math.sin(ang)
            # Soft hollow: do not sit on the exact mid vertical (visual spine).
            if abs(x) < rx * 0.08:
                x = math.copysign(rx * 0.08, x if abs(x) > 1e-9 else float(side))
                # Keep roughly on the ellipse by scaling y down slightly.
                y = y * 0.98
            pos[n] = (x, y)
    return pos


def _unit_member_links(
    paths: list[list[str]],
    a: str,
    b: str,
    state: LayoutState,
) -> list[tuple[str, str]]:
    members = {a, b}
    for p in paths:
        members.update(p)
    return [e for e in state.links if e[0] in members and e[1] in members]


def _best_path_order_for_crossings(
    paths: list[list[str]],
    *,
    a: str,
    b: str,
    rx: float,
    ry_step: float,
    state: LayoutState,
    names: dict[str, str],
    chord_gap: float = 160.0,
) -> list[list[str]]:
    """Pick nest order: barycenter seed + adjacent same-side swaps to cut x."""
    base = _order_paths_for_nest(paths, names)
    if len(base) <= 2:
        return base

    links = _unit_member_links(base, a, b, state)

    def score(order: list[list[str]]) -> int:
        pos = _place_petal_bands(
            order, a=a, b=b, rx=rx, ry_step=ry_step, chord_gap=chord_gap
        )
        return count_edge_crossings(pos, links)

    best = list(base)
    best_x = score(best)
    # Adjacent swaps within the interleaved list (preserves ±y nesting pattern).
    improved = True
    rounds = 0
    while improved and rounds < 24:
        improved = False
        rounds += 1
        for i in range(len(best) - 1):
            trial = list(best)
            trial[i], trial[i + 1] = trial[i + 1], trial[i]
            x = score(trial)
            if x < best_x:
                best, best_x = trial, x
                improved = True
                break
    return best


def beautify_dual_unit_positions(
    state: LayoutState,
    unit: DualUnit,
    params: LayoutParams | None = None,
) -> dict[str, tuple[float, float]]:
    """Beautify one unit: petal→nested ellipse arcs; chains→outward fans.

    Goal: fewer crossings, readable spread, **hollow eye interior**, low
    overlap. Portals on x-axis only; corridors on ± ellipse bands; **long
    tails park outside the eye** (do not pierce nested rings).
    """
    params = params or LayoutParams()
    pitch = max(float(params.pitch), 170.0)
    a, b = unit.portal_a, unit.portal_b
    paths = _normalize_paths(unit)
    kind = classify_dual_unit(unit)

    if kind == "petal":
        max_mid = max((len(p) - 2 for p in paths), default=0)
        rx = max(
            float(params.an_gap) * 2.5,
            pitch * max(7.0, float(max_mid) + 2.0),
            1100.0,
        )
        ry_step = max(float(params.side) * 1.8, float(params.lane) * 1.15, pitch * 1.15, 320.0)
        ordered = _best_path_order_for_crossings(
            paths,
            a=a,
            b=b,
            rx=rx,
            ry_step=ry_step,
            state=state,
            names=state.names,
            chord_gap=max(pitch * 0.85, 150.0),
        )
        pos = _place_petal_bands(
            ordered,
            a=a,
            b=b,
            rx=rx,
            ry_step=ry_step,
            chord_gap=max(pitch * 0.85, 150.0),
        )
        half = rx
        band_n = sum(1 for p in ordered if p[1:-1])
        ry_park = ry_step * max(1, (band_n + 1) // 2 + 1)
    else:
        max_mid = max((len(p) - 2 for p in paths), default=0)
        half = max(pitch * (max_mid + 1) * 0.5, pitch * 4.0, 700.0)
        pos = {a: (-half, 0.0), b: (half, 0.0)}
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
        ry_park = max(float(params.lane), float(params.side), 220.0)

    # Eye envelope from corridor/portal placement (tails must stay outside).
    eye_rx = max((abs(xy[0]) for xy in pos.values()), default=half)
    eye_ry = max((abs(xy[1]) for xy in pos.values()), default=ry_park)
    eye_rx = max(eye_rx, half)
    eye_ry = max(eye_ry, ry_park * 0.5, pitch * 2.0)

    def _outside_start(
        ax: float, ay: float, *, fan_i: int
    ) -> tuple[float, float, float, float]:
        """Return (x0,y0, ux,uy) for the first tail node — fully outside the eye."""
        # Near mid vertical / apex: park on outer top/bottom shelf, grow sideways.
        if abs(ax) < eye_rx * 0.35:
            side_y = 1.0 if ay >= 0 else -1.0
            if abs(ay) < 1e-6:
                side_y = 1.0 if fan_i % 2 == 0 else -1.0
            y0 = side_y * (eye_ry + pitch * 1.25)
            dir_x = 1.0 if ax >= 0 else -1.0
            if abs(ax) < 1e-6:
                dir_x = 1.0 if fan_i % 2 == 0 else -1.0
            # Stagger parallel shelves for many apex tails.
            y0 += side_y * ((fan_i // 2) * pitch * 0.55)
            x0 = ax + dir_x * pitch * 0.6
            return x0, y0, dir_x, 0.0

        # Otherwise: jump outside along outward ray, then continue outward.
        ang = math.atan2(ay, ax)
        lat = ((fan_i + 1) // 2) * (1 if fan_i % 2 else -1)
        ang += lat * 0.18
        ux, uy = math.cos(ang), math.sin(ang)
        # Ellipse-ish clearance along this ray.
        ca, sa = abs(ux), abs(uy)
        r_hit = eye_rx * eye_ry / max(1e-6, math.hypot(eye_ry * ca, eye_rx * sa))
        r0 = max(math.hypot(ax, ay), r_hit) + pitch * 1.1
        return ux * r0, uy * r0, ux, uy

    # Tails: long chains park fully outside the eye (do not pierce rings).
    used = set(pos)
    attach_fan: dict[str, int] = {}
    # Longer first so they claim outer shelves.
    tail_order = sorted(
        enumerate(unit.tails),
        key=lambda it: (-len(it[1] or []), it[0]),
    )
    for ti, chain in tail_order:
        if not chain:
            continue
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
        fan_i = attach_fan.get(attach, 0)
        attach_fan[attach] = fan_i + 1

        long_chain = len(fresh) >= 3
        if attach == a:
            stub_ux, stub_uy = -1.0, 0.35 if fan_i % 2 == 0 else -0.35
        elif attach == b:
            stub_ux, stub_uy = 1.0, 0.35 if fan_i % 2 == 0 else -0.35
        else:
            stub_ux, stub_uy = ax, ay
            if abs(stub_ux) + abs(stub_uy) < 1e-6:
                stub_ux, stub_uy = (0.0, 1.0 if ti % 2 == 0 else -1.0)

        if long_chain or kind == "petal":
            # Petal eye: keep all tail nodes outside envelope (even short ones
            # if they would otherwise climb through bands).
            x0, y0, ux, uy = _outside_start(ax, ay, fan_i=fan_i)
            step = pitch * (1.0 if long_chain else 0.9)
            px, py = -uy, ux
            for i, n in enumerate(fresh):
                if n in pos:
                    continue
                x = x0 + ux * step * i
                y = y0 + uy * step * i
                for _ in range(12):
                    # Must stay outside eye box and not stack.
                    outside = abs(x) >= eye_rx * 0.92 or abs(y) >= eye_ry * 0.92
                    hit = any(
                        abs(x - ox) < pitch * 0.4 and abs(y - oy) < pitch * 0.4
                        for ox, oy in pos.values()
                    )
                    if outside and not hit:
                        break
                    if not outside:
                        # Push further out along ray / shelf.
                        x += ux * pitch * 0.5
                        y += uy * pitch * 0.5
                        if abs(ux) < 0.2 and abs(uy) > 0.8:
                            # shelf mode: grow sideways
                            x += (1.0 if ax >= 0 else -1.0) * pitch * 0.5
                    else:
                        x += px * pitch * 0.45
                        y += py * pitch * 0.45
                pos[n] = (x, y)
        else:
            bn = math.hypot(stub_ux, stub_uy) or 1.0
            ux, uy = stub_ux / bn, stub_uy / bn
            px, py = -uy, ux
            lat = ((fan_i + 1) // 2) * (1 if fan_i % 2 else -1)
            ang = lat * 0.22
            ux2 = ux * math.cos(ang) + px * math.sin(ang)
            uy2 = uy * math.cos(ang) + py * math.sin(ang)
            nrm = math.hypot(ux2, uy2) or 1.0
            ux2, uy2 = ux2 / nrm, uy2 / nrm
            step = pitch * 0.95
            for i, n in enumerate(fresh):
                if n in pos:
                    continue
                x = ax + ux2 * step * (i + 1)
                y = ay + uy2 * step * (i + 1)
                for _ in range(10):
                    hit = any(
                        abs(x - ox) < pitch * 0.4 and abs(y - oy) < pitch * 0.4
                        for ox, oy in pos.values()
                    )
                    if not hit:
                        break
                    x += px * pitch * 0.45
                    y += py * pitch * 0.45
                pos[n] = (x, y)
        used |= set(pos)

    leftovers = [n for n in unit.member_ids() if n not in pos]
    # Outer ellipse parking — keep off portal chord / hollow mid.
    n_left = len(leftovers)
    if n_left:
        sorted_left = sorted(leftovers, key=lambda x: state.names.get(x, x))
        for i, n in enumerate(sorted_left):
            t = (i + 0.5) / n_left
            ang = math.pi * (0.12 + 0.76 * t)
            if i % 2:
                ang = -ang
            pos[n] = (
                eye_rx * 1.05 * math.cos(ang),
                max(eye_ry, ry_park) * 1.1 * math.sin(ang),
            )

    return pos


def layout_dual_unit_positions(
    state: LayoutState,
    unit: DualUnit,
    params: LayoutParams | None = None,
) -> dict[str, tuple[float, float]]:
    """Unit local layout — ellipse petal / straight beautify."""
    return beautify_dual_unit_positions(state, unit, params)


def _uncross_unit(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    pinned: set[str],
    *,
    max_rounds: int = 80,
    preserve_side: bool = True,
) -> dict[str, tuple[float, float]]:
    """Greedy: nudge free endpoints to kill crossings without flipping eye sides."""
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
                aa, bb, pa, pb = segs[i]
                c, d, pc, pd = segs[j]
                if len({aa, bb, c, d}) < 4:
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
                if preserve_side and abs(y) > 1e-6 and (y + dy) * y < 0:
                    continue  # do not flip across the portal chord
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
    require_zero_cross: bool = False,
) -> OpResult:
    """Layout the dual-portal unit; default accepts residual crossings.

    Placement objective: **minimize unit crossings while keeping spread**
    (nested ellipse bands). Selection still prefers top eyes + max
    membership. `require_zero_cross=True` restores the old hard gate.
    """
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
                units = [_unit_from_hub_paths(state, portal_a, portal_b, paths)]
                units[0].unit_id = 0
    if not units:
        # Whole canvas as one unit: prefer core–core, else densest hub pair by cover.
        core_hubs = [
            n
            for n, ly in state.layers.items()
            if ly == "core" and n in state.adj
        ]
        hubs = [
            n
            for n, ly in state.layers.items()
            if ly in ("agg", "core") and n in state.adj
        ]
        hubs.sort(key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n)))
        core_hubs.sort(key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n)))
        pair_order: list[tuple[str, str]] = []
        for i, a in enumerate(core_hubs):
            for b in core_hubs[i + 1 :]:
                pair_order.append((a, b))
        if len(hubs) >= 2 and not pair_order:
            pair_order.append((hubs[0], hubs[1]))
        best: DualUnit | None = None
        for a, b in pair_order:
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
            if len(paths) < 2:
                continue
            cand = _unit_from_hub_paths(state, a, b, paths)
            cand.unit_id = 0
            if best is None or len(cand.member_ids()) > len(best.member_ids()):
                best = cand
        if best is not None:
            units = [best]
    if not units:
        return OpResult(
            state=state,
            moved=set(),
            op="layout_dual_unit",
            params={},
            note="no_dual_unit",
        )

    def _pick_key(x: DualUnit) -> tuple:
        # Top-down eye (core→agg→access), then max membership.
        return (
            _eye_portal_key(x.portal_a, x.portal_b, state.layers),
            -len(x.member_ids()),
        )

    # Prefer top-layer eyes, then max membership (min of tier, -n).
    u = min(units, key=_pick_key)
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
    # Light uncross: keep portal side signs so distribution holds.
    pinned = {u.portal_a, u.portal_b}
    is_petal = classify_dual_unit(u) == "petal"
    out.positions = _uncross_unit(
        out.positions,
        unit_links,
        pinned,
        preserve_side=is_petal,
    )
    x_unit = count_edge_crossings(out.positions, unit_links)

    # Gentle overlap resolve if it does not worsen unit crossings.
    stf = out
    cand = fix_overlaps_local(out, params).state
    if u.portal_a in pos:
        cand.positions[u.portal_a] = pos[u.portal_a]
    if u.portal_b in pos:
        cand.positions[u.portal_b] = pos[u.portal_b]
    for n in pinned:
        if n in pos:
            cand.positions[n] = pos[n]
    x_after = count_edge_crossings(cand.positions, unit_links)
    if x_after <= x_unit:
        stf = cand

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
        "require_zero_cross": require_zero_cross,
    }
    # Default: accept any laid-out eye (max coverage). Optional hard gate.
    accepted = (x_unit == 0) if require_zero_cross else True
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
            "require_zero_cross": require_zero_cross,
            "zero_cross": x_unit == 0,
        },
        note=(
            f"layout_dual_unit:paths={len(u.paths)} nodes={len(members)} x={x_unit}"
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
            "Dual-portal eyes walk top→down (core–core → core–agg → agg–agg), "
            "then maximize membership. Petal objective = fewer crossings with "
            "readable spread (ellipse bands; hollow mid; low overlap). "
            "Residual mesh chords OK; re-run layout_dual_unit if the eye "
            "breaks — do not polish-straighten."
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
    if "require_zero_cross" in overrides:
        out["require_zero_cross"] = bool(overrides.get("require_zero_cross"))
    return out
