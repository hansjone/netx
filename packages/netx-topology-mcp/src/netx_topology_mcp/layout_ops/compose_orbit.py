"""Incremental compose: attach staging blocks one-by-one with orbit search.

Unlike spring strip-pack ``compose_views``, this grows the world in Prim portal
order. After each block is rigidly aligned onto shared portals, exclusive nodes
are swept on concentric circles / angle grids about the portal pivot (same
spirit as ``orbit_sweep``) and the candidate with fewest partial crossings is
kept.

Degrees of freedom by glue:
- 0 shared: place near hull, then orbit whole block about nearest bridge tip
- 1 shared: rotate+scale-radius of exclusive members about the portal
- 2+ shared: chord lock + flip bank + small bulge rotations about chord mid
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import (
    count_edge_crossings,
    segments_properly_intersect,
)
from netx_topology_mcp.layout_ops.compose_views import (
    ComposeBlock,
    _membership_counts,
    _portal_grow_order,
    _rank_shared_pivots,
    _reflect_about_axis,
    _rigid_align_to_world,
    compose_params_from_overrides,
)
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

_DEFAULT_ANGLE_STEP = 30
_DEFAULT_RADII = (0.75, 1.0, 1.25, 1.55, 1.95)


def _seg_bbox(
    p: tuple[float, float], q: tuple[float, float]
) -> tuple[float, float, float, float]:
    return (min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))


def _bbox_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def crossings_touching(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    focus: set[str],
) -> int:
    """Count crossings where at least one endpoint is in ``focus`` (partial QA)."""
    if not focus or not links:
        return 0
    focus_segs: list[
        tuple[tuple[float, float], tuple[float, float], tuple[float, float, float, float]]
    ] = []
    other_segs: list[
        tuple[tuple[float, float], tuple[float, float], tuple[float, float, float, float]]
    ] = []
    for u, v in links:
        if u not in pos or v not in pos:
            continue
        p, q = pos[u], pos[v]
        bb = _seg_bbox(p, q)
        if u in focus or v in focus:
            focus_segs.append((p, q, bb))
        else:
            other_segs.append((p, q, bb))
    n = 0
    # focus vs other
    for p1, p2, b1 in focus_segs:
        for p3, p4, b2 in other_segs:
            if not _bbox_overlap(b1, b2):
                continue
            if segments_properly_intersect(p1, p2, p3, p4):
                n += 1
    # focus vs focus (exclusive internal)
    for i in range(len(focus_segs)):
        p1, p2, b1 = focus_segs[i]
        for j in range(i + 1, len(focus_segs)):
            p3, p4, b2 = focus_segs[j]
            if not _bbox_overlap(b1, b2):
                continue
            if segments_properly_intersect(p1, p2, p3, p4):
                n += 1
    return n


def _bridge_len(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    new_ids: set[str],
    old_ids: set[str],
) -> float:
    total = 0.0
    for a, b in links:
        if a in new_ids and b in old_ids:
            if a in pos and b in pos:
                total += math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])
        elif b in new_ids and a in old_ids:
            if a in pos and b in pos:
                total += math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])
    return total


def _apply_polar(
    base: dict[str, tuple[float, float]],
    members: list[str],
    pivot: tuple[float, float],
    *,
    angle: float,
    radius_scale: float,
) -> dict[str, tuple[float, float]]:
    """Rotate members about pivot and scale their distance from pivot."""
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    cx, cy = pivot
    out = dict(base)
    for n in members:
        if n not in out:
            continue
        x, y = out[n]
        dx, dy = x - cx, y - cy
        rx = (dx * cos_a - dy * sin_a) * radius_scale
        ry = (dx * sin_a + dy * cos_a) * radius_scale
        out[n] = (cx + rx, cy + ry)
    return out


def _footprint_hits(
    pos: dict[str, tuple[float, float]],
    new_ids: set[str],
    old_ids: set[str],
    *,
    min_dist: float = 90.0,
) -> int:
    """Cheap exclusive-vs-old center collisions (not full AABB)."""
    hits = 0
    md2 = min_dist * min_dist
    old_pts = [(pos[n][0], pos[n][1]) for n in old_ids if n in pos]
    for n in new_ids:
        if n not in pos:
            continue
        x, y = pos[n]
        for ox, oy in old_pts:
            dx, dy = x - ox, y - oy
            if dx * dx + dy * dy < md2:
                hits += 1
                break
    return hits


def _score_attach(
    trial: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    new_ids: set[str],
    old_ids: set[str],
) -> tuple[int, int, float]:
    x = crossings_touching(trial, links, new_ids)
    ov = _footprint_hits(trial, new_ids, old_ids)
    br = _bridge_len(trial, links, new_ids, old_ids)
    return (x, ov, br)


def _orbit_candidates_one_portal(
    world: dict[str, tuple[float, float]],
    aligned: dict[str, tuple[float, float]],
    portal: str,
    exclusive: list[str],
    *,
    angle_step: int,
    radii: tuple[float, ...],
) -> list[dict[str, tuple[float, float]]]:
    if portal not in world:
        return [aligned]
    pivot = world[portal]
    base = dict(world)
    for nid, xy in aligned.items():
        if nid == portal:
            continue
        base[nid] = xy
    # Keep portal frozen.
    base[portal] = world[portal]
    cands = [dict(base)]
    step = max(10, min(90, int(angle_step)))
    for deg in range(0, 360, step):
        if deg == 0:
            angles = [0.0]
        else:
            angles = [math.radians(deg)]
        for ang in angles:
            for rs in radii:
                if deg == 0 and abs(rs - 1.0) < 1e-9:
                    continue
                trial = _apply_polar(base, exclusive, pivot, angle=ang, radius_scale=rs)
                trial[portal] = world[portal]
                cands.append(trial)
    return cands


def _orbit_candidates_two_portal(
    world: dict[str, tuple[float, float]],
    aligned: dict[str, tuple[float, float]],
    portals: list[str],
    exclusive: list[str],
    local: dict[str, tuple[float, float]],
    *,
    angle_step: int,
) -> list[dict[str, tuple[float, float]]]:
    """Chord-locked: base + flip + small bulge twists about chord midpoint."""
    p0, p1 = portals[0], portals[1]
    if p0 not in world or p1 not in world:
        return [aligned]
    base = dict(world)
    for nid, xy in aligned.items():
        if nid in (p0, p1):
            continue
        base[nid] = xy
    base[p0] = world[p0]
    base[p1] = world[p1]
    cands = [dict(base)]

    flipped = _reflect_about_axis(base, exclusive + [p0, p1], world[p0], world[p1])
    flipped[p0] = world[p0]
    flipped[p1] = world[p1]
    cands.append(flipped)

    # Small rotations of exclusive bulge about chord midpoint (keep portals).
    mx = 0.5 * (world[p0][0] + world[p1][0])
    my = 0.5 * (world[p0][1] + world[p1][1])
    step = max(15, min(45, int(angle_step)))
    for src in (base, flipped):
        for deg in (-step, step, 2 * step, -2 * step):
            trial = _apply_polar(
                src, exclusive, (mx, my), angle=math.radians(deg), radius_scale=1.0
            )
            trial[p0] = world[p0]
            trial[p1] = world[p1]
            cands.append(trial)
    return cands


def _orbit_candidates_orphan(
    world: dict[str, tuple[float, float]],
    local: dict[str, tuple[float, float]],
    *,
    pad: float,
    angle_step: int,
    radii: tuple[float, ...],
    links: list[tuple[str, str]],
) -> list[dict[str, tuple[float, float]]]:
    """No shared portal: park outside bbox then orbit about nearest tip."""
    if not world:
        # Seed: place local as-is near origin.
        xs = [p[0] for p in local.values()]
        ys = [p[1] for p in local.values()]
        min_x, min_y = min(xs), min(ys)
        placed = {n: (x - min_x + 40.0, y - min_y + 40.0) for n, (x, y) in local.items()}
        return [placed]

    wxs = [p[0] for p in world.values()]
    wys = [p[1] for p in world.values()]
    min_x, max_x = min(wxs), max(wxs)
    min_y, max_y = min(wys), max(wys)
    # Default park below bbox.
    lxs = [p[0] for p in local.values()]
    lys = [p[1] for p in local.values()]
    lmin_x, lmin_y = min(lxs), min(lys)
    ox, oy = min_x, max_y + float(pad)
    placed0 = {
        n: (ox + (x - lmin_x), oy + (y - lmin_y)) for n, (x, y) in local.items()
    }

    # Bridge tip: fabric edge from local ids into world.
    local_ids = set(local)
    tip = None
    for a, b in links:
        if a in local_ids and b in world:
            tip = world[b]
            break
        if b in local_ids and a in world:
            tip = world[a]
            break
    if tip is None:
        tip = (0.5 * (min_x + max_x), max_y)

    members = list(local.keys())
    # Always tip-orbit sweep + score (no sticky/default park index).
    cx0 = sum(placed0[n][0] for n in members) / len(members)
    cy0 = sum(placed0[n][1] for n in members) / len(members)
    step = max(15, min(60, int(angle_step)))
    base_r = max(200.0, float(pad))
    cands: list[dict[str, tuple[float, float]]] = []
    for deg in range(0, 360, step):
        ang = math.radians(deg)
        for rs in radii:
            r = base_r * rs
            tx = tip[0] + r * math.cos(ang)
            ty = tip[1] + r * math.sin(ang)
            dx, dy = tx - cx0, ty - cy0
            trial = dict(world)
            for n in members:
                x, y = placed0[n]
                trial[n] = (x + dx, y + dy)
            cands.append(trial)
    return cands or [dict(world) | placed0]


def _pick_best(
    cands: list[dict[str, tuple[float, float]]],
    links: list[tuple[str, str]],
    new_ids: set[str],
    old_ids: set[str],
) -> tuple[dict[str, tuple[float, float]], tuple[int, int, float], int]:
    """Rescore every candidate; return the best (no sticky pick)."""
    if not cands:
        return {}, (0, 0, 0.0), -1
    best = cands[0]
    best_sc = _score_attach(best, links, new_ids, old_ids)
    best_i = 0
    for i, c in enumerate(cands[1:], start=1):
        sc = _score_attach(c, links, new_ids, old_ids)
        if sc < best_sc:
            best, best_sc, best_i = c, sc, i
    return best, best_sc, best_i


def _thin_candidates(
    cands: list[dict[str, tuple[float, float]]],
    cand_cap: int,
) -> list[dict[str, tuple[float, float]]]:
    """Even subsample — do not privilege index 0."""
    if len(cands) <= cand_cap:
        return cands
    step = max(1, (len(cands) + cand_cap - 1) // cand_cap)
    return cands[::step][:cand_cap]


def orbit_pack_blocks(
    blocks: list[ComposeBlock],
    *,
    pad: float = 500.0,
    links: list[tuple[str, str]] | None = None,
    fabric_bridges: bool = True,
    angle_step: int = _DEFAULT_ANGLE_STEP,
    radii: tuple[float, ...] = _DEFAULT_RADII,
    cand_cap: int = 80,
) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
    """Grow world block-by-block with orbit attach search."""
    if not blocks:
        return {}, {"slots": 0, "nodes": 0, "mode": "compose_orbit"}

    valid = [b for b in blocks if b.positions]
    if not valid:
        return {}, {"slots": 0, "nodes": 0, "mode": "compose_orbit"}

    slot_meta: dict[str, dict[str, float]] = {}
    for b in valid:
        xs = [p[0] for p in b.positions.values()]
        ys = [p[1] for p in b.positions.values()]
        w = max(max(xs) - min(xs), 1.0) + float(pad)
        h = max(max(ys) - min(ys), 1.0) + float(pad)
        slot_meta[b.key] = {"w": w, "h": h, "area": w * h, "n": float(len(b.positions))}

    membership = _membership_counts(valid)
    link_list = list(links or [])
    key_order = _portal_grow_order(
        valid,
        slot_meta,
        membership,
        links=link_list if fabric_bridges else None,
        fabric_bridges=fabric_bridges,
    )
    by_key = {b.key: b for b in valid}

    world: dict[str, tuple[float, float]] = {}
    owner: dict[str, str] = {}
    group_nodes: dict[str, list[str]] = {k: [] for k in key_order}
    group_pivots: dict[str, list[str]] = {k: [] for k in key_order}
    attach_trace: list[dict[str, Any]] = []
    merged_via: dict[str, str] = {}

    for key in key_order:
        block = by_key[key]
        local = dict(block.positions)
        shared_raw = [nid for nid in local if nid in world]
        shared = _rank_shared_pivots(shared_raw, membership)
        old_ids = set(world)
        exclusive = [n for n in local if n not in set(shared)]

        if not world:
            # Seed
            xs = [p[0] for p in local.values()]
            ys = [p[1] for p in local.values()]
            min_x, min_y = min(xs), min(ys)
            for nid, (x, y) in local.items():
                world[nid] = (x - min_x + 40.0, y - min_y + 40.0)
                owner[nid] = key
                group_nodes[key].append(nid)
            group_pivots[key] = list(shared[:2]) if shared else []
            merged_via[key] = "seed"
            attach_trace.append(
                {
                    "key": key,
                    "via": "seed",
                    "shared_n": len(shared),
                    "cands": 1,
                    "pick": 0,
                    "score": [0, 0, 0.0],
                }
            )
            continue

        if shared:
            prefer = None
            if world:
                wxs = [p[0] for p in world.values()]
                wys = [p[1] for p in world.values()]
                prefer = (0.5 * (min(wxs) + max(wxs)), 0.5 * (min(wys) + max(wys)))
            aligned = _rigid_align_to_world(
                local,
                world,
                shared,
                prefer_center=prefer,
                links=link_list,
            )
            if len(shared) >= 2:
                cands = _orbit_candidates_two_portal(
                    world,
                    aligned,
                    shared[:2],
                    exclusive,
                    local,
                    angle_step=angle_step,
                )
                via = "orbit_dual"
            else:
                cands = _orbit_candidates_one_portal(
                    world,
                    aligned,
                    shared[0],
                    exclusive,
                    angle_step=angle_step,
                    radii=radii,
                )
                via = "orbit_portal"
            portal_pivots = [n for n in shared if membership.get(n, 1) >= 2][:8]
            group_pivots[key] = portal_pivots or list(shared[:2])
        else:
            cands = _orbit_candidates_orphan(
                world,
                local,
                pad=pad,
                angle_step=angle_step,
                radii=radii,
                links=link_list,
            )
            via = "orbit_orphan"
            group_pivots[key] = []

        cands = _thin_candidates(cands, cand_cap)

        new_ids = set(exclusive) if exclusive else set(local) - old_ids
        best, best_sc, best_i = _pick_best(cands, link_list, new_ids, old_ids)

        for nid, xy in best.items():
            if nid not in world:
                world[nid] = xy
                owner[nid] = key
            # portals already in world keep first-owner coords
            group_nodes[key].append(nid)
        # Ensure shared listed
        for nid in shared:
            if nid not in group_nodes[key]:
                group_nodes[key].append(nid)

        merged_via[key] = via
        attach_trace.append(
            {
                "key": key,
                "via": via,
                "shared_n": len(shared),
                "shared": shared[:4],
                "cands": len(cands),
                "best_i": best_i,
                "score": [best_sc[0], best_sc[1], round(best_sc[2], 1)],
                "exclusive_n": len(exclusive),
            }
        )

    rigid_groups = [
        {
            "key": k,
            "node_ids": sorted(set(group_nodes[k])),
            "pivots": sorted(set(group_pivots.get(k) or [])),
            "via": merged_via.get(k),
            "soft": True,
        }
        for k in key_order
        if group_nodes.get(k)
    ]
    mass_groups = [
        {
            **g,
            "cores": list(g.get("pivots") or []),
            "soft": True,
        }
        for g in rigid_groups
    ]

    # Final global crossing for meta (cheap enough once).
    final_x = count_edge_crossings(world, link_list) if link_list else 0
    meta: dict[str, Any] = {
        "slots": len(key_order),
        "nodes": len(world),
        "pad": float(pad),
        "merge_shared": True,
        "mode": "compose_orbit",
        "order_mode": "portal_grow",
        "pack_mode": "orbit_attach",
        "order": key_order,
        "angle_step": int(angle_step),
        "radii": list(radii),
        "attach_trace": attach_trace[:80],
        "rigid_groups": rigid_groups,
        "mass_groups": mass_groups,
        "soft": True,
        "merged_via": merged_via,
        "final_crossings": final_x,
        "tip": (
            "compose_orbit: Prim portal order + portal align seed; prefer "
            "mass_merge (core/ring/chain attract) over exclusive rigid polish."
        ),
    }
    return world, meta


def compose_orbit_into_state(
    state: LayoutState,
    blocks: list[ComposeBlock],
    params: LayoutParams | None = None,
    *,
    pad: float = 500.0,
    fabric_bridges: bool = True,
    angle_step: int = _DEFAULT_ANGLE_STEP,
    radii: tuple[float, ...] | None = None,
    cand_cap: int = 80,
) -> OpResult:
    del params
    merged, meta = orbit_pack_blocks(
        blocks,
        pad=pad,
        links=list(state.links or []),
        fabric_bridges=fabric_bridges,
        angle_step=angle_step,
        radii=radii or _DEFAULT_RADII,
        cand_cap=cand_cap,
    )
    if not merged:
        return OpResult(
            state=state,
            moved=set(),
            op="compose_orbit",
            params=meta,
            note="no_blocks",
        )
    out = state.copy()
    moved: set[str] = set()
    for nid, xy in merged.items():
        if nid not in out.positions:
            out.positions[nid] = xy
            moved.add(nid)
            continue
        if out.positions[nid] != xy:
            out.positions[nid] = xy
            moved.add(nid)
    out.meta = dict(out.meta or {})
    out.meta["compose_views"] = meta
    try:
        from netx_topology_mcp.layout_ops.mass_field import (
            attach_mass_to_compose_meta,
            build_mass_field,
        )

        mass = build_mass_field(out, groups=meta.get("mass_groups") or [])
        out.meta["mass_field"] = mass
        out.meta["compose_views"] = attach_mass_to_compose_meta(meta, mass)
    except Exception:
        pass
    out.last_moved = moved
    return OpResult(
        state=out,
        moved=moved,
        op="compose_orbit",
        params={**meta, "moved_n": len(moved)},
        note=(
            f"compose_orbit:slots={meta.get('slots')} nodes={meta.get('nodes')} "
            f"x={meta.get('final_crossings')}"
        ),
    )


def compose_orbit_params_from_overrides(
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    base = compose_params_from_overrides(overrides)
    o = overrides or {}
    try:
        base["angle_step"] = int(o.get("angle_step") or _DEFAULT_ANGLE_STEP)
    except (TypeError, ValueError):
        base["angle_step"] = _DEFAULT_ANGLE_STEP
    raw_r = o.get("radii")
    if isinstance(raw_r, (list, tuple)) and raw_r:
        try:
            base["radii"] = tuple(float(x) for x in raw_r)
        except (TypeError, ValueError):
            base["radii"] = _DEFAULT_RADII
    else:
        base["radii"] = _DEFAULT_RADII
    try:
        base["cand_cap"] = max(12, min(200, int(o.get("cand_cap") or 80)))
    except (TypeError, ValueError):
        base["cand_cap"] = 80
    return base
