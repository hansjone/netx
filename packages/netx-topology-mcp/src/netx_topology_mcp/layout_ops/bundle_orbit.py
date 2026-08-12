"""Contract pure chains / ring+chain into a super-node, orbit, then expand.

When single-point ``orbit_sweep`` stalls, long chords are often owned by a
*corridor* (deg≤2 chain) or a small ring with a dangling chain. Moving any
interior node alone barely changes global crossings; moving the whole bundle
as a unit can.

Expand rule (hard)
------------------
Expansion must **not** raise global crossings. If a full-length expand invades
crowded space, probe **minimized** packings (shorter step along the ray) and
only accept a candidate that clears the gate after expand. No silent “apply
now, fix later”.

Modes
-----
- **chain**: tip/centroid samples → expand mobile nodes on anchor→tip ray
  with scale probes.
- **ring_chain**: triangle tip (+ dangling chain) sweeps; ring base stays;
  expand chain outward with the same minimize-probe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    top_crossing_nodes,
)
from netx_topology_mcp.layout_ops.orbit_sweep import (
    _MAX_JUMP_CAP,
    _far_field_guides,
    _polar_grid,
)
from netx_topology_mcp.layout_ops.ring_faces import extract_ring_faces
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_topology_quality import extract_chain_paths

# Compact-first: tight packs before full-length (fits crowded pockets).
# Floor must clear icon+caption; smaller steps cause member self-overlap → apply reject.
_EXPAND_SCALES = (0.35, 0.45, 0.55, 0.7, 0.85, 1.0)
_MIN_STEP = 80.0
_TIP_SAMPLE_CAP = 48


@dataclass(frozen=True)
class Bundle:
    """Mobile corridor with ordered path and optional fixed anchor."""

    kind: str  # chain | ring_chain
    member_ids: tuple[str, ...]
    tip_id: str
    base_ids: tuple[str, ...] = ()
    path: tuple[str, ...] = ()  # ordered anchor…tip (anchor may be fixed)
    anchor_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.tip_id}:{len(self.member_ids)}"


def _centroid(
    pos: dict[str, tuple[float, float]], ids: list[str] | tuple[str, ...]
) -> tuple[float, float] | None:
    pts = [pos[n] for n in ids if n in pos]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _mean_step(path: list[str] | tuple[str, ...], pos: dict[str, tuple[float, float]]) -> float:
    pts = [pos[n] for n in path if n in pos]
    if len(pts) < 2:
        return 140.0
    total = 0.0
    for i in range(len(pts) - 1):
        total += math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
    return max(_MIN_STEP, total / (len(pts) - 1))


def _walk_dangling_chain(
    start: str,
    adj: dict[str, set[str]],
    *,
    blocked: set[str],
) -> list[str]:
    """From ``start`` (just outside blocked), walk a deg≤2 corridor away."""
    if start in blocked or start not in adj:
        return []
    path = [start]
    prev = None
    cur = start
    while True:
        nbs = [v for v in adj.get(cur, ()) if v != prev and v not in blocked]
        if len(path) == 1:
            low = [v for v in nbs if len(adj.get(v, ())) <= 2]
            if len(low) == 1:
                nxt = low[0]
            elif len(nbs) == 1 and len(adj.get(nbs[0], ())) <= 2:
                nxt = nbs[0]
            else:
                break
        else:
            if len(adj.get(cur, ())) > 2:
                break
            if len(nbs) != 1:
                break
            nxt = nbs[0]
            if len(adj.get(nxt, ())) > 2 and nxt not in blocked:
                break
        path.append(nxt)
        prev, cur = cur, nxt
        if len(path) > 40:
            break
    return path


def detect_chain_bundles(
    adj: dict[str, set[str]],
    pos: dict[str, tuple[float, float]],
    *,
    frozen: set[str],
    min_nodes: int = 3,
) -> list[Bundle]:
    """Pure (or portal-ended) chains → mobile interiors as expandable bundles."""
    out: list[Bundle] = []
    seen: set[frozenset[str]] = set()
    for full in extract_chain_paths(adj):
        if len(full) < min_nodes:
            continue
        # Orient: high-deg / frozen portal first when possible.
        ordered = list(full)
        d0 = len(adj.get(ordered[0], ()))
        d1 = len(adj.get(ordered[-1], ()))
        if d1 > d0 or (ordered[-1] in frozen and ordered[0] not in frozen):
            ordered = list(reversed(ordered))

        mobile = [
            n
            for n in ordered
            if n in pos
            and n not in frozen
            and not (len(adj.get(n, ())) > 2 and n in {ordered[0], ordered[-1]})
        ]
        if len(mobile) < max(2, min_nodes - 1):
            continue
        key = frozenset(mobile)
        if key in seen:
            continue
        seen.add(key)

        # Tip = free end of the oriented path (last mobile).
        tip = mobile[-1]
        bases = [n for n in (ordered[0], ordered[-1]) if n not in key and n in pos]
        anchor = None
        if ordered[0] not in key and ordered[0] in pos:
            anchor = ordered[0]
        elif bases:
            anchor = bases[0]

        # Path for expand: anchor (optional) + mobiles in corridor order.
        if anchor:
            # mobiles already follow corridor from hub side
            path = (anchor, *mobile)
        else:
            path = tuple(mobile)

        out.append(
            Bundle(
                kind="chain",
                member_ids=tuple(mobile),
                tip_id=tip,
                base_ids=tuple(bases),
                path=path,
                anchor_id=anchor,
            )
        )
    out.sort(key=lambda b: (-len(b.member_ids), b.tip_id))
    return out


def detect_ring_chain_bundles(
    state: LayoutState,
    *,
    frozen: set[str],
    max_ring_len: int = 5,
) -> list[Bundle]:
    """Ring + dangling chain → sweep the attachment tip (triangle vertex)."""
    adj = state.adj
    pos = state.positions
    out: list[Bundle] = []
    seen: set[frozenset[str]] = set()
    faces = extract_ring_faces(state, max_len=max_ring_len, max_cycles=60)
    for face in faces:
        rset = set(face.node_ids)
        if len(rset) < 3:
            continue
        for tip in face.node_ids:
            if tip in frozen or tip not in pos:
                continue
            outs = [v for v in adj.get(tip, ()) if v not in rset]
            for seed in outs:
                chain = _walk_dangling_chain(seed, adj, blocked=rset)
                if len(chain) < 1:
                    continue
                mobile = [tip, *chain]
                mobile = [n for n in mobile if n not in frozen and n in pos]
                if len(mobile) < 2:
                    continue
                key = frozenset(mobile)
                if key in seen:
                    continue
                seen.add(key)
                base = tuple(n for n in face.node_ids if n != tip)
                # path: tip is anchor of the dangling chain (ring vertex stays
                # the triangle tip we move); chain packs outward from tip.
                path = tuple([tip, *[n for n in chain if n in key]])
                out.append(
                    Bundle(
                        kind="ring_chain",
                        member_ids=tuple(mobile),
                        tip_id=tip,
                        base_ids=base,
                        path=path,
                        anchor_id=tip,
                    )
                )
    out.sort(
        key=lambda b: (
            0 if len(b.base_ids) == 2 else 1,
            -len(b.member_ids),
            b.tip_id,
        )
    )
    return out


def detect_bundles(
    state: LayoutState,
    *,
    frozen: set[str] | None = None,
) -> list[Bundle]:
    frozen = set(frozen or ())
    chains = detect_chain_bundles(state.adj, state.positions, frozen=frozen)
    rings = detect_ring_chain_bundles(state, frozen=frozen)
    used: set[str] = set()
    out: list[Bundle] = []
    for b in list(rings) + list(chains):
        members = set(b.member_ids)
        if members & used:
            continue
        if any(n in frozen for n in b.member_ids):
            continue
        used |= members
        out.append(b)
    return out


def _bundle_ok(
    members: set[str],
    trial: dict[str, tuple[float, float]],
    names: dict[str, str],
    nn_floor: float,
) -> bool:
    """Gate: no footprint invade (members∪outsiders) and nn_floor vs outsiders."""
    from netx_topology_mcp.layout_ops.orbit_sweep import (
        _box,
        _centers_may_overlap,
    )
    from netx_topology_mcp.layout_metrics import node_footprint

    # Apply refuses any footprint overlap — members must clear each other too.
    mem_list = [n for n in members if n in trial]
    for i, n in enumerate(mem_list):
        ax0, ay0, ax1, ay1 = _box(n, trial, names)
        nx, ny = trial[n]
        fa = node_footprint(names.get(n, ""))
        # vs other members
        for m in mem_list[i + 1 :]:
            mx, my = trial[m]
            fb = node_footprint(names.get(m, ""))
            if not _centers_may_overlap(nx, ny, mx, my, fa, fb):
                continue
            bx0, by0, bx1, by1 = _box(m, trial, names)
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                return False
        # vs outsiders + nn_floor
        floor2 = nn_floor * nn_floor if nn_floor > 0 else 0.0
        for b, (x, y) in trial.items():
            if b in members:
                continue
            if floor2 and (x - nx) * (x - nx) + (y - ny) * (y - ny) < floor2:
                return False
            fb = node_footprint(names.get(b, ""))
            if not _centers_may_overlap(nx, ny, x, y, fa, fb):
                continue
            bx0, by0, bx1, by1 = _box(b, trial, names)
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                return False
    return True


def _expand_path_placements(
    path: list[str],
    *,
    origin: tuple[float, float],
    ux: float,
    uy: float,
    step: float,
    mobile: set[str],
    tip_xy: tuple[float, float] | None = None,
    pin_tip: bool = False,
) -> dict[str, tuple[float, float]]:
    """Place mobile nodes along ray; optionally pin free tip at tip_xy."""
    ox, oy = origin
    out: dict[str, tuple[float, float]] = {}
    movables = [n for n in path if n in mobile]
    if not movables:
        return out
    if pin_tip and tip_xy is not None and len(movables) >= 1:
        # Chord pack: first mobile near origin+step, last at tip_xy.
        tx, ty = tip_xy
        if len(movables) == 1:
            out[movables[0]] = (tx, ty)
            return out
        # Keep tip fixed; distribute interiors on chord origin→tip.
        for i, n in enumerate(movables):
            t = (i + 1) / len(movables)
            # start a bit off the anchor
            out[n] = (ox + (tx - ox) * t, oy + (ty - oy) * t)
        out[movables[-1]] = (tx, ty)
        return out
    for i, n in enumerate(movables):
        k = i + 1
        out[n] = (ox + ux * step * k, oy + uy * step * k)
    return out


def _probe_expand(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    names: dict[str, str],
    bundle: Bundle,
    *,
    tip_xy: tuple[float, float],
    global0: int,
    nn_floor: float,
    min_delta: int,
) -> dict[str, Any] | None:
    """Try full→minimized expands toward tip_xy; keep first non-increasing best.

    Returns candidate dict with placements, or None if every expand raises
    crossings / invades.
    """
    members = [n for n in bundle.member_ids if n in pos]
    mem_set = set(members)
    if len(members) < 2:
        return None

    path = [n for n in (bundle.path or bundle.member_ids) if n in pos or n == bundle.anchor_id]
    if not path:
        path = list(members)

    anchor = bundle.anchor_id
    if anchor and anchor in pos:
        ox, oy = pos[anchor]
    elif bundle.base_ids:
        bpts = [pos[n] for n in bundle.base_ids if n in pos]
        if not bpts:
            c0 = _centroid(pos, members)
            if not c0:
                return None
            ox, oy = c0
        else:
            ox = sum(p[0] for p in bpts) / len(bpts)
            oy = sum(p[1] for p in bpts) / len(bpts)
    else:
        # No fixed anchor: use opposite end of path as soft origin (old tip side).
        fixed = [n for n in path if n not in mem_set and n in pos]
        if fixed:
            ox, oy = pos[fixed[0]]
        else:
            c0 = _centroid(pos, members)
            if not c0:
                return None
            ox, oy = c0

    tx, ty = tip_xy
    dx, dy = tx - ox, ty - oy
    dist = math.hypot(dx, dy)
    if dist < 60.0:
        return None
    ux, uy = dx / dist, dy / dist
    base_step = _mean_step(path if len(path) >= 2 else members, pos)
    n_mobile = len(members)

    for scale in _EXPAND_SCALES:
        step = max(_MIN_STEP, base_step * float(scale))
        # Prefer tip-pinned chord pack: interiors on origin→tip.
        # Minimized scales shorten tip reach so the footprint fits clearance.
        max_reach = step * max(1, n_mobile)
        reach = min(dist, max_reach) if scale < 0.99 else dist
        tip_use = (ox + ux * reach, oy + uy * reach)
        placed = _expand_path_placements(
            path,
            origin=(ox, oy),
            ux=ux,
            uy=uy,
            step=step,
            mobile=mem_set,
            tip_xy=tip_use,
            pin_tip=True,
        )
        if len(placed) < len(mem_set):
            # fallback: step-only for missing
            for n in members:
                if n not in placed:
                    continue
        if not placed:
            continue
        trial = dict(pos)
        trial.update(placed)
        # _bundle_ok already rejects outsider footprint invasion + nn_floor.
        if not _bundle_ok(mem_set, trial, names, nn_floor):
            continue
        g1 = count_edge_crossings(trial, links)
        # Hard rule: expand must not raise crossings.
        if g1 > global0:
            continue
        delta = g1 - global0
        if delta > -max(1, int(min_delta)):
            continue
        return {
            "x": round(tip_use[0], 1),
            "y": round(tip_use[1], 1),
            "r": round(math.hypot(tip_use[0] - ox, tip_use[1] - oy), 1),
            "angle_deg": round(math.degrees(math.atan2(uy, ux)) % 360.0, 1),
            "crossings": {"global": g1},
            "delta": {"global": int(delta)},
            "stretch": round(step / max(base_step, 1.0), 3),
            "expand_scale": round(float(scale), 3),
            "step": round(step, 1),
            "placements": {
                k: (round(v[0], 1), round(v[1], 1)) for k, v in placed.items()
            },
            "origin": [round(ox, 1), round(oy, 1)],
        }
    return None


def orbit_bundle(
    state: LayoutState,
    bundle: Bundle,
    *,
    max_jump: float = 4000.0,
    angle_step: int = 20,
    cand_cap: int = 220,
    nn_floor: float = 28.0,
    min_delta: int = 1,
    y_min: float | None = None,
    y_max: float | None = None,
) -> dict[str, Any]:
    """Sample tip directions; probe minimized expands; keep improving only."""
    pos = dict(state.positions)
    names = dict(state.names)
    links = list(state.links)
    members = tuple(n for n in bundle.member_ids if n in pos)
    if len(members) < 2:
        return {"ok": False, "error": "bundle_too_small", "bundle": bundle.key}

    c0 = _centroid(pos, members)
    if c0 is None:
        return {"ok": False, "error": "no_centroid", "bundle": bundle.key}
    cx, cy = c0
    jump = max(200.0, min(float(max_jump), _MAX_JUMP_CAP))
    global0 = count_edge_crossings(pos, links)

    # Tip seed samples around current tip / centroid.
    tip0 = pos.get(bundle.tip_id, (cx, cy))
    ext_nbs: set[str] = set()
    mem_set = set(members)
    for n in members:
        for v in state.adj.get(n, ()):
            if v not in mem_set and v in pos:
                ext_nbs.add(v)
    pseudo = f"__bundle__{bundle.tip_id}"
    c_adj = {pseudo: set(ext_nbs)}
    for v in ext_nbs:
        c_adj.setdefault(v, set()).add(pseudo)
    c_pos = {pseudo: tip0, **{v: pos[v] for v in ext_nbs}}

    samples: list[tuple[float, float]] = []
    for sx, sy, _r, _ang in _far_field_guides(c_pos, pseudo, c_adj, jump):
        samples.append((sx, sy))
    for sx, sy, _r, _ang in _polar_grid(
        tip0[0], tip0[1], jump=jump, angle_step=max(12, int(angle_step))
    ):
        samples.append((sx, sy))
    samples.append((cx, cy))
    if bundle.base_ids:
        base_pts = [pos[n] for n in bundle.base_ids if n in pos]
        if base_pts:
            bx = sum(p[0] for p in base_pts) / len(base_pts)
            by = sum(p[1] for p in base_pts) / len(base_pts)
            dx, dy = tip0[0] - bx, tip0[1] - by
            L = math.hypot(dx, dy) or 1.0
            ux, uy = dx / L, dy / L
            for dist in (0.35 * jump, 0.6 * jump, 0.9 * jump):
                samples.append((tip0[0] + ux * dist, tip0[1] + uy * dist))
                samples.append((tip0[0] - ux * dist, tip0[1] - uy * dist))

    seen: set[tuple[int, int]] = set()
    scored: list[dict[str, Any]] = []
    tip_cap = min(int(cand_cap), _TIP_SAMPLE_CAP)
    for sx, sy in samples:
        if y_min is not None and sy < y_min:
            continue
        if y_max is not None and sy > y_max:
            continue
        key = (int(round(sx / 12.0) * 12), int(round(sy / 12.0) * 12))
        if key in seen:
            continue
        seen.add(key)
        if len(seen) > tip_cap:
            break
        if math.hypot(sx - tip0[0], sy - tip0[1]) < 40.0 and math.hypot(
            sx - cx, sy - cy
        ) < 40.0:
            continue
        probed = _probe_expand(
            pos,
            links,
            names,
            bundle,
            tip_xy=(sx, sy),
            global0=global0,
            nn_floor=nn_floor,
            min_delta=min_delta,
        )
        if probed is None:
            continue
        if int(probed["delta"]["global"]) >= 0:
            continue
        scored.append(probed)
        # Enough improving tips — rank later.
        if len(scored) >= 8:
            break

    scored.sort(
        key=lambda c: (
            int(c["delta"]["global"]),
            float(c.get("expand_scale") or 1.0),
            float(c.get("r") or 0.0),
        )
    )
    improving = [
        c
        for c in scored
        if int(c["delta"]["global"]) <= -max(1, int(min_delta))
    ]
    top = improving[:5]
    for i, c in enumerate(top, start=1):
        c["rank"] = i
    return {
        "ok": True,
        "bundle": bundle.key,
        "kind": bundle.kind,
        "tip_id": bundle.tip_id,
        "tip_name": names.get(bundle.tip_id, bundle.tip_id),
        "member_n": len(members),
        "member_ids": list(members)[:40],
        "base_ids": list(bundle.base_ids)[:12],
        "anchor_id": bundle.anchor_id,
        "centroid0": [round(cx, 1), round(cy, 1)],
        "crossings_before": global0,
        "candidates": top,
        "improving_n": len(improving),
        "sampled": len(seen),
        "max_jump": jump,
        "expand_mode": "minimize_probe",
    }


def apply_bundle_pick(
    state: LayoutState,
    bundle: Bundle,
    sweep: dict[str, Any],
    *,
    pick: int = 1,
) -> OpResult:
    pos = dict(state.positions)
    cands = list(sweep.get("candidates") or [])
    if not cands:
        return OpResult(
            state=state,
            moved=set(),
            op="bundle_orbit",
            params={"error": "no_candidates", "bundle": bundle.key},
            note="bundle_orbit:no_candidates",
        )
    idx = max(1, min(len(cands), int(pick))) - 1
    cand = cands[idx]
    placements = cand.get("placements") or {}
    if not placements:
        return OpResult(
            state=state,
            moved=set(),
            op="bundle_orbit",
            params={"error": "no_placements", "bundle": bundle.key},
            note="bundle_orbit:no_placements",
        )

    # Safety: re-score expand; refuse if crossings rose (stale / race).
    trial = dict(pos)
    moved: set[str] = set()
    for nid, xy in placements.items():
        if nid not in trial:
            continue
        trial[nid] = (float(xy[0]), float(xy[1]))
        moved.add(nid)
    g0 = count_edge_crossings(pos, state.links)
    g1 = count_edge_crossings(trial, state.links)
    if g1 > g0:
        return OpResult(
            state=state,
            moved=set(),
            op="bundle_orbit",
            params={
                "error": "expand_raises_crossings",
                "bundle": bundle.key,
                "start_crossings": g0,
                "end_crossings": g1,
                "hint": "Expand probe failed gate; try smaller scale / other tip.",
            },
            note="bundle_orbit:expand_raises_crossings",
        )

    # Minimize-expand must not leave footprint overlaps (apply gate).
    from netx_topology_mcp.layout_ops.orbit_sweep import _has_any_footprint_overlap

    if _has_any_footprint_overlap(trial, state.names):
        return OpResult(
            state=state,
            moved=set(),
            op="bundle_orbit",
            params={
                "error": "expand_invades_space",
                "bundle": bundle.key,
                "start_crossings": g0,
                "end_crossings": g1,
                "expand_scale": cand.get("expand_scale"),
                "hint": "Space too tight; tip/placement wrong — probe smaller scale or other tip.",
            },
            note="bundle_orbit:expand_invades_space",
        )

    st = state.copy()
    st.positions = trial
    st.last_moved = moved
    meta = {
        "mode": "bundle_orbit",
        "kind": bundle.kind,
        "bundle": bundle.key,
        "tip_id": bundle.tip_id,
        "pick": idx + 1,
        "member_n": len(moved),
        "delta": int(g1 - g0),
        "crossings": int(g1),
        "expand_scale": cand.get("expand_scale"),
        "step": cand.get("step"),
        "expand_mode": "minimize_probe",
    }
    st.meta["bundle_orbit"] = meta
    return OpResult(
        state=st,
        moved=moved,
        op="bundle_orbit",
        params=meta,
        note=(
            f"bundle_orbit {bundle.kind} tip={bundle.tip_id} "
            f"n={len(moved)} scale={cand.get('expand_scale')} Δ={meta['delta']}"
        ),
    )


def rank_bundles_by_hotspots(
    bundles: list[Bundle],
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    names: dict[str, str],
    *,
    top_n: int = 12,
) -> list[Bundle]:
    """Prefer bundles whose tip / members participate in crossings."""
    if not bundles:
        return []
    from netx_topology_mcp.layout_metrics import crossing_participation

    _n, node_hit = crossing_participation(pos, links)[:2]
    hot = {
        str(r["fabric_node_id"]): int(r.get("crossing_hits") or 0)
        for r in top_crossing_nodes(
            pos, links, names=names, top_n=40, participation=node_hit
        )
    }

    def score(b: Bundle) -> tuple[int, int, int]:
        tip_h = int(node_hit.get(b.tip_id) or hot.get(b.tip_id) or 0)
        mem_h = sum(int(node_hit.get(n) or 0) for n in b.member_ids)
        return (tip_h + mem_h, len(b.member_ids), 1 if b.kind == "ring_chain" else 0)

    ranked = sorted(bundles, key=score, reverse=True)
    return ranked[: max(1, int(top_n))]


def bundle_orbit_until_progress(
    state: LayoutState,
    *,
    frozen_ids: set[str] | None = None,
    max_jump: float = 5000.0,
    max_bundles: int = 10,
    min_delta: int = 1,
    cand_cap: int = 200,
    angle_step: int = 20,
    nn_floor: float = 28.0,
    params: LayoutParams | None = None,
) -> OpResult:
    """Try hotspot-ranked bundles until one expand-gated move applies."""
    del params
    st = state.copy()
    frozen = set(frozen_ids or ())
    bundles = detect_bundles(st, frozen=frozen)
    ranked = rank_bundles_by_hotspots(
        bundles, st.positions, st.links, st.names, top_n=max_bundles
    )
    global0 = count_edge_crossings(st.positions, st.links)
    tried: list[dict[str, Any]] = []
    for b in ranked:
        if any(n in frozen for n in b.member_ids):
            continue
        sweep = orbit_bundle(
            st,
            b,
            max_jump=max_jump,
            angle_step=angle_step,
            cand_cap=cand_cap,
            nn_floor=nn_floor,
            min_delta=min_delta,
        )
        tried.append(
            {
                "bundle": b.key,
                "kind": b.kind,
                "improving_n": int(sweep.get("improving_n") or 0),
            }
        )
        if int(sweep.get("improving_n") or 0) <= 0:
            continue
        op = apply_bundle_pick(st, b, sweep, pick=1)
        if not op.moved:
            tried[-1]["apply_error"] = (op.params or {}).get("error")
            continue
        end_g = count_edge_crossings(op.state.positions, op.state.links)
        if end_g > global0:
            # Should be unreachable due to apply gate; skip defensively.
            continue
        meta = {
            **(op.params or {}),
            "start_crossings": global0,
            "end_crossings": end_g,
            "tried": tried[:20],
            "bundle_n": len(bundles),
        }
        op.state.meta["bundle_orbit"] = meta
        return OpResult(
            state=op.state,
            moved=op.moved,
            op="bundle_orbit",
            params=meta,
            note=op.note,
        )

    return OpResult(
        state=st,
        moved=set(),
        op="bundle_orbit",
        params={
            "mode": "bundle_orbit",
            "start_crossings": global0,
            "end_crossings": global0,
            "delta": 0,
            "tried": tried[:20],
            "bundle_n": len(bundles),
            "stop_reason": "no_candidates",
            "expand_mode": "minimize_probe",
        },
        note="bundle_orbit:no_candidates",
    )
