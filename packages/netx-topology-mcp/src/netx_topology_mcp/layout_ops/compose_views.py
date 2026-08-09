"""Compose multiple view layouts via portal-aware packing + shared-node merge.

Dual-unit staging views intentionally overlap on portals. Pack order grows by
shared membership (Prim-style), then block origins come from a spring layout on
the portal-weighted block graph so units that share hubs sit near each other.
Orphan/misc blocks strip-pack below the glued component — cutting long
cross-unit edges that area-first square packing leaves behind.

When ``merge_shared`` (default), later blocks that share fabric_node_ids are
rigidly translated/rotated onto the first owner's world coordinates.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

DEFAULT_SLOT_PAD = 600.0


@dataclass(frozen=True)
class ComposeBlock:
    """One staging canvas / dual-unit view to pack."""

    key: str
    positions: dict[str, tuple[float, float]]  # fabric_node_id -> local xy


def _membership_counts(blocks: list[ComposeBlock]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in blocks:
        for nid in b.positions:
            counts[nid] = counts.get(nid, 0) + 1
    return counts


def _shared_weight(
    ids_a: set[str],
    ids_b: set[str],
    membership: dict[str, int],
) -> float:
    """Higher when blocks share portal-like hubs (multi-membership nodes)."""
    shared = ids_a & ids_b
    if not shared:
        return 0.0
    # membership>=2 are glue points; boost hubs that appear in many units.
    return float(sum(max(1, membership.get(n, 1)) ** 2 for n in shared))


def _fabric_bridge_hits(
    ids_a: set[str],
    ids_b: set[str],
    links: list[tuple[str, str]],
) -> int:
    """Count fabric edges with one end in A and the other in B."""
    if not links or not ids_a or not ids_b:
        return 0
    n = 0
    for a, b in links:
        if (a in ids_a and b in ids_b) or (a in ids_b and b in ids_a):
            n += 1
    return n


def _portal_grow_order(
    blocks: list[ComposeBlock],
    slot_meta: dict[str, dict[str, float]],
    membership: dict[str, int],
    *,
    links: list[tuple[str, str]] | None = None,
    fabric_bridges: bool = True,
) -> list[str]:
    """Order blocks by shared-portal growth (Prim), not raw area.

    Seed = block with strongest multi-membership glue (ignore orphan-only
    misc), then attach max shared-weight neighbor. Fabric bridges (optional)
    also count as glue so long-edge peers attach before pure orphans.
    """
    if not blocks:
        return []
    id_sets = {b.key: set(b.positions) for b in blocks}
    remaining = {b.key for b in blocks}
    link_list = list(links or []) if fabric_bridges else []

    def glue_mass(key: str) -> float:
        return float(
            sum(
                membership.get(n, 1) ** 2
                for n in id_sets[key]
                if membership.get(n, 1) >= 2
            )
        )

    def fabric_to(ids: set[str], other: set[str]) -> float:
        if not link_list:
            return 0.0
        return float(_fabric_bridge_hits(ids, other, link_list))

    glued = [k for k in remaining if glue_mass(k) > 0]
    if not glued and link_list:
        # Prefer a block that participates in any inter-block fabric edge.
        seeded = [
            k
            for k in remaining
            if any(
                _fabric_bridge_hits(id_sets[k], id_sets[o], link_list) > 0
                for o in remaining
                if o != k
            )
        ]
        glued = seeded
    if glued:
        seed = max(glued, key=lambda k: (glue_mass(k), slot_meta[k]["area"], k))
    else:
        seed = max(remaining, key=lambda k: (slot_meta[k]["area"], k))
    order = [seed]
    remaining.remove(seed)
    placed_nodes = set(id_sets[seed])

    while remaining:
        best_key = None
        best_score: tuple[float, float, float, float, str] | None = None
        for k in remaining:
            w = _shared_weight(id_sets[k], placed_nodes, membership)
            fb = fabric_to(id_sets[k], placed_nodes)
            rest_nodes: set[str] = set()
            for o in remaining:
                if o != k:
                    rest_nodes |= id_sets[o]
            w_rest = (
                _shared_weight(id_sets[k], rest_nodes, membership)
                if w <= 0 and fb <= 0
                else 0.0
            )
            fb_rest = (
                fabric_to(id_sets[k], rest_nodes) if w <= 0 and fb <= 0 else 0.0
            )
            # Orphans (no portal/fabric glue): larger → later.
            if w <= 0 and fb <= 0 and w_rest <= 0 and fb_rest <= 0:
                area_term = -slot_meta[k]["area"]
            else:
                area_term = slot_meta[k]["area"]
            score = (w + fb * 4.0, w_rest + fb_rest * 2.0, area_term, fb, k)
            if best_score is None or score > best_score:
                best_score = score
                best_key = k
        assert best_key is not None
        order.append(best_key)
        remaining.remove(best_key)
        placed_nodes |= id_sets[best_key]
    return order


def _rank_shared_pivots(
    shared: list[str], membership: dict[str, int]
) -> list[str]:
    """Prefer multi-unit hubs as rigid-align pivots (not corridor alpha order)."""
    return sorted(shared, key=lambda n: (-membership.get(n, 1), n))


def _block_graph(
    blocks: list[ComposeBlock],
    membership: dict[str, int],
    *,
    links: list[tuple[str, str]] | None = None,
    fabric_bridges: bool = True,
    bridge_boost: float = 8.0,
) -> dict[str, dict[str, float]]:
    """Undirected weighted graph: portal glue + optional fabric-bridge boost.

    ``bridge_boost`` is MCP-tunable: each inter-block fabric edge adds this
    much weight so long-spoke peers sit near each other in the spring pack.
    """
    id_sets = {b.key: set(b.positions) for b in blocks}
    keys = [b.key for b in blocks]
    adj: dict[str, dict[str, float]] = {k: {} for k in keys}
    boost = max(0.0, float(bridge_boost))
    link_list = list(links or []) if fabric_bridges and boost > 0 else []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1 :]:
            w = _shared_weight(id_sets[ka], id_sets[kb], membership)
            if link_list:
                w += boost * float(
                    _fabric_bridge_hits(id_sets[ka], id_sets[kb], link_list)
                )
            if w <= 0:
                continue
            adj[ka][kb] = w
            adj[kb][ka] = w
    return adj


def _reflect_about_axis(
    pos: dict[str, tuple[float, float]],
    members: list[str],
    p0: tuple[float, float],
    p1: tuple[float, float],
) -> dict[str, tuple[float, float]]:
    ax, ay = p0
    bx, by = p1
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return dict(pos)
    out = dict(pos)
    for n in members:
        if n not in out:
            continue
        x, y = out[n]
        t = ((x - ax) * dx + (y - ay) * dy) / L2
        px, py = ax + t * dx, ay + t * dy
        out[n] = (2 * px - x, 2 * py - y)
    return out


def _external_bridge_cost(
    placed: dict[str, tuple[float, float]],
    world: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    new_ids: set[str],
) -> float:
    """Sum of squared lengths for edges that leave this block into ``world``."""
    if not links or not new_ids:
        return 0.0
    cost = 0.0
    for a, b in links:
        a_new = a in new_ids
        b_new = b in new_ids
        if a_new == b_new:
            continue
        pa = placed.get(a) if a_new else world.get(a)
        pb = placed.get(b) if b_new else world.get(b)
        if pa is None or pb is None:
            continue
        dx = pa[0] - pb[0]
        dy = pa[1] - pb[1]
        cost += dx * dx + dy * dy
    return cost


def _portal_centroid_origins(
    key_order: list[str],
    slot_meta: dict[str, dict[str, float]],
    graph: dict[str, dict[str, float]],
    *,
    pad: float,
    ideal_scale: float = 0.55,
    spring_iters: int = 80,
) -> dict[str, tuple[float, float]]:
    """Spring-embed block centroids on the portal/fabric graph; orphans strip below.

    Origins are used for strip_pack seeds (and as a soft hint before merge).
    ``ideal_scale`` / ``spring_iters`` are MCP-tunable (smaller scale → denser).
    """
    if not key_order:
        return {}
    glued = [k for k in key_order if graph.get(k)]
    orphans = [k for k in key_order if k not in set(glued)]

    origins: dict[str, tuple[float, float]] = {}
    if not glued:
        # Fall back to square strip for everything.
        n = len(key_order)
        cols = max(1, int(math.ceil(math.sqrt(n))))
        col_w = [0.0] * cols
        row_h: dict[int, float] = defaultdict(float)
        for i, key in enumerate(key_order):
            c, r = i % cols, i // cols
            col_w[c] = max(col_w[c], slot_meta[key]["w"])
            row_h[r] = max(row_h[r], slot_meta[key]["h"])
        cox = [0.0]
        for c in range(cols - 1):
            cox.append(cox[-1] + col_w[c])
        roy = {0: 0.0}
        for r in range(1, (n // cols) + 2):
            roy[r] = roy.get(r - 1, 0.0) + row_h.get(r - 1, 0.0)
        for i, key in enumerate(key_order):
            c, r = i % cols, i // cols
            origins[key] = (cox[c], roy[r])
        return origins

    scale = max(0.25, min(float(ideal_scale), 1.2))
    iters = max(20, min(int(spring_iters), 240))

    # Ideal edge length ~ mean diagonal of the two slots.
    def ideal(a: str, b: str) -> float:
        da = math.hypot(slot_meta[a]["w"], slot_meta[a]["h"])
        db = math.hypot(slot_meta[b]["w"], slot_meta[b]["h"])
        return scale * (da + db) + float(pad)

    # Init on a circle scaled by total size.
    n_g = len(glued)
    span = sum(math.hypot(slot_meta[k]["w"], slot_meta[k]["h"]) for k in glued) / max(
        n_g, 1
    )
    radius = max(span * 0.7 * math.sqrt(n_g), 400.0)
    pos: dict[str, list[float]] = {}
    for i, key in enumerate(glued):
        ang = 2.0 * math.pi * i / n_g
        pos[key] = [radius * math.cos(ang), radius * math.sin(ang)]

    # Lightweight spring / Fruchterman-style iterations (no numpy).
    max_w = max((w for nbrs in graph.values() for w in nbrs.values()), default=1.0)
    for _ in range(iters):
        disp = {k: [0.0, 0.0] for k in glued}
        # Attractive along portal / fabric edges.
        for a in glued:
            for b, w in graph.get(a, {}).items():
                if b not in pos or a >= b:
                    continue
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                dist = math.hypot(dx, dy) or 1e-6
                L = ideal(a, b)
                # Stronger weight → stronger pull toward ideal length.
                force = (dist - L) * (0.15 + 0.35 * (w / max_w))
                ux, uy = dx / dist, dy / dist
                disp[a][0] += ux * force
                disp[a][1] += uy * force
                disp[b][0] -= ux * force
                disp[b][1] -= uy * force
        # Mild repulsion so slots do not stack.
        for i, a in enumerate(glued):
            for b in glued[i + 1 :]:
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                dist = math.hypot(dx, dy) or 1e-6
                min_d = 0.45 * (
                    math.hypot(slot_meta[a]["w"], slot_meta[a]["h"])
                    + math.hypot(slot_meta[b]["w"], slot_meta[b]["h"])
                )
                if dist >= min_d:
                    continue
                push = (min_d - dist) * 0.35
                ux, uy = dx / dist, dy / dist
                disp[a][0] -= ux * push
                disp[a][1] -= uy * push
                disp[b][0] += ux * push
                disp[b][1] += uy * push
        for k in glued:
            pos[k][0] += max(-800.0, min(800.0, disp[k][0]))
            pos[k][1] += max(-800.0, min(800.0, disp[k][1]))

    # Convert centers → top-left origins (slot min corner).
    for key in glued:
        cx, cy = pos[key]
        origins[key] = (
            cx - 0.5 * slot_meta[key]["w"],
            cy - 0.5 * slot_meta[key]["h"],
        )

    # Orphans: horizontal strip under the glued bbox.
    if orphans:
        xs = [origins[k][0] for k in glued]
        ys = [origins[k][1] for k in glued]
        ys2 = [origins[k][1] + slot_meta[k]["h"] for k in glued]
        base_y = max(ys2) + float(pad)
        base_x = min(xs)
        x_cursor = base_x
        for key in orphans:
            origins[key] = (x_cursor, base_y)
            x_cursor += slot_meta[key]["w"] + 0.25 * float(pad)

    # Normalize so min corner is near origin.
    min_x = min(o[0] for o in origins.values())
    min_y = min(o[1] for o in origins.values())
    return {k: (x - min_x + 40.0, y - min_y + 40.0) for k, (x, y) in origins.items()}


def _rigid_align_to_world(
    local: dict[str, tuple[float, float]],
    world: dict[str, tuple[float, float]],
    shared: list[str],
    *,
    prefer_center: tuple[float, float] | None = None,
    links: list[tuple[str, str]] | None = None,
) -> dict[str, tuple[float, float]]:
    """Translate (+ rotate about first shared) so shared nodes match world.

    With two+ shared pivots, orientation follows the portal chord; also try the
    flip across that chord and keep the side with fewer crossings on the
    partial world (then shorter external bridges / closer to ``prefer_center``).
    With a single pivot, rotate so the block centroid aims at ``prefer_center``.
    """
    if not shared:
        return dict(local)
    p0 = shared[0]
    lx0, ly0 = local[p0]
    wx0, wy0 = world[p0]
    angle = 0.0
    if len(shared) >= 2:
        p1 = shared[1]
        ldx, ldy = local[p1][0] - lx0, local[p1][1] - ly0
        wdx, wdy = world[p1][0] - wx0, world[p1][1] - wy0
        if (ldx * ldx + ldy * ldy) > 1e-12 and (wdx * wdx + wdy * wdy) > 1e-12:
            angle = math.atan2(wdy, wdx) - math.atan2(ldy, ldx)
    elif prefer_center is not None and len(local) >= 2:
        # Local centroid relative to portal.
        cx = sum(p[0] for p in local.values()) / len(local)
        cy = sum(p[1] for p in local.values()) / len(local)
        ldx, ldy = cx - lx0, cy - ly0
        tdx = prefer_center[0] - wx0
        tdy = prefer_center[1] - wy0
        if (ldx * ldx + ldy * ldy) > 1e-8 and (tdx * tdx + tdy * tdy) > 1e-8:
            angle = math.atan2(tdy, tdx) - math.atan2(ldy, ldx)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    out: dict[str, tuple[float, float]] = {}
    for nid, (lx, ly) in local.items():
        dx, dy = lx - lx0, ly - ly0
        rx = dx * cos_a - dy * sin_a
        ry = dx * sin_a + dy * cos_a
        out[nid] = (wx0 + rx, wy0 + ry)

    if len(shared) < 2:
        return out

    # Dual-portal flip: same chord, opposite bank.
    shared_set = set(shared)
    new_ids = {n for n in local if n not in shared_set}
    link_list = list(links or [])

    def _score(cand: dict[str, tuple[float, float]]) -> tuple[int, float, float]:
        trial = dict(world)
        for nid, xy in cand.items():
            if nid in shared_set:
                continue
            trial[nid] = xy
        xcount = (
            count_edge_crossings(trial, link_list) if link_list else 0
        )
        bridge = _external_bridge_cost(cand, world, link_list, new_ids)
        if prefer_center is None:
            aim = 0.0
        else:
            excl = [cand[n] for n in new_ids if n in cand]
            if not excl:
                aim = 0.0
            else:
                cx = sum(p[0] for p in excl) / len(excl)
                cy = sum(p[1] for p in excl) / len(excl)
                aim = (cx - prefer_center[0]) ** 2 + (cy - prefer_center[1]) ** 2
        return (int(xcount), bridge, aim)

    flipped = _reflect_about_axis(
        out, list(local.keys()), world[shared[0]], world[shared[1]]
    )
    for p in shared:
        if p in world:
            flipped[p] = world[p]
    if _score(flipped) < _score(out):
        return flipped
    return out


def strip_pack_blocks(
    blocks: list[ComposeBlock],
    *,
    pad: float = DEFAULT_SLOT_PAD,
    merge_shared: bool = True,
    links: list[tuple[str, str]] | None = None,
    fabric_bridges: bool = True,
    bridge_boost: float = 8.0,
    ideal_scale: float = 0.55,
    spring_iters: int = 80,
) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
    """Pack blocks; return merged positions + meta.

    With ``merge_shared`` (default): later blocks that share node ids with
    already-placed nodes are rigidly aligned (portal merge). First owner wins
    for shared coordinates. Blocks without shared anchors use strip-pack slots.

    Pack order prefers portal connectivity growth so dual units that share
    hubs stay glued; large orphan/misc blocks are placed last. Optional
    ``links`` + ``fabric_bridges`` add fabric-bridge weight to the spring and
    guide dual-portal flip by partial-graph crossings (then bridge length).

    MCP knobs: ``pad``, ``bridge_boost``, ``ideal_scale``, ``spring_iters``,
    ``fabric_bridges``.
    """
    if not blocks:
        return {}, {"slots": 0, "nodes": 0, "pad": pad, "merge_shared": merge_shared}

    slot_meta: dict[str, dict[str, float]] = {}
    valid: list[ComposeBlock] = []
    for b in blocks:
        if not b.positions:
            continue
        xs = [p[0] for p in b.positions.values()]
        ys = [p[1] for p in b.positions.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        w = max(max_x - min_x, 1.0) + float(pad)
        h = max(max_y - min_y, 1.0) + float(pad)
        slot_meta[b.key] = {
            "min_x": min_x,
            "min_y": min_y,
            "w": w,
            "h": h,
            "area": w * h,
            "n": float(len(b.positions)),
        }
        valid.append(b)

    if not valid:
        return {}, {"slots": 0, "nodes": 0, "pad": pad, "merge_shared": merge_shared}

    membership = _membership_counts(valid)
    link_list = list(links or [])
    do_fabric = bool(fabric_bridges)
    boost = max(0.0, float(bridge_boost))
    if merge_shared:
        key_order = _portal_grow_order(
            valid,
            slot_meta,
            membership,
            links=link_list,
            fabric_bridges=do_fabric,
        )
        order_mode = "portal_grow"
        graph = _block_graph(
            valid,
            membership,
            links=link_list,
            fabric_bridges=do_fabric,
            bridge_boost=boost,
        )
        origins = _portal_centroid_origins(
            key_order,
            slot_meta,
            graph,
            pad=float(pad),
            ideal_scale=float(ideal_scale),
            spring_iters=int(spring_iters),
        )
        pack_mode = "portal_centroid"
        cols = 0
    else:
        key_order = [
            b.key
            for b in sorted(valid, key=lambda b: (-slot_meta[b.key]["area"], b.key))
        ]
        order_mode = "area"
        pack_mode = "strip"
        n = max(1, len(key_order))
        cols = max(1, int(math.ceil(math.sqrt(n))))
        col_widths: list[float] = [0.0] * cols
        row_heights: dict[int, float] = defaultdict(float)
        for i, key in enumerate(key_order):
            c, r = i % cols, i // cols
            meta = slot_meta[key]
            col_widths[c] = max(col_widths[c], meta["w"])
            row_heights[r] = max(row_heights[r], meta["h"])

        col_origin = [0.0]
        for c in range(cols - 1):
            col_origin.append(col_origin[-1] + col_widths[c])
        row_origin: dict[int, float] = {0: 0.0}
        max_row = max((i // cols for i in range(n)), default=0)
        for r in range(1, max_row + 2):
            row_origin[r] = row_origin.get(r - 1, 0.0) + row_heights.get(r - 1, 0.0)

        origins = {}
        for i, key in enumerate(key_order):
            c, r = i % cols, i // cols
            origins[key] = (col_origin[c], row_origin[r])

    by_key = {b.key: b for b in valid}
    out: dict[str, tuple[float, float]] = {}
    owner: dict[str, str] = {}
    merged_via: dict[str, str] = {}
    align_meta: list[dict[str, Any]] = []
    # Per-block members as placed (whole sub-region = rigid body).
    group_nodes: dict[str, list[str]] = {k: [] for k in key_order}
    group_pivots: dict[str, list[str]] = {k: [] for k in key_order}

    for key in key_order:
        block = by_key[key]
        meta = slot_meta[key]
        shared_raw = [nid for nid in block.positions if nid in out]
        shared = _rank_shared_pivots(shared_raw, membership)
        if merge_shared and shared:
            ox, oy = origins[key]
            prefer = (
                ox + 0.5 * meta["w"],
                oy + 0.5 * meta["h"],
            )
            aligned = _rigid_align_to_world(
                block.positions,
                out,
                shared,
                prefer_center=prefer,
                links=link_list,
            )
            placed_new = 0
            for nid, xy in aligned.items():
                group_nodes[key].append(nid)
                if nid in out:
                    continue
                out[nid] = xy
                owner[nid] = key
                placed_new += 1
            # Freeze pivots = high-membership shared (true portals), not every
            # overlapping corridor id — soft polish needs corridors movable.
            portal_pivots = [n for n in shared if membership.get(n, 1) >= 2][:8]
            group_pivots[key] = portal_pivots or list(shared[:2])
            merged_via[key] = "rigid_shared"
            align_meta.append(
                {
                    "key": key,
                    "shared": shared[:6],
                    "shared_n": len(shared),
                    "placed_new": placed_new,
                    "pivot_n": len(group_pivots[key]),
                }
            )
            continue

        ox, oy = origins[key]
        for nid, (lx, ly) in block.positions.items():
            group_nodes[key].append(nid)
            if nid in out:
                group_pivots[key].append(nid)
                continue
            out[nid] = (ox + (lx - meta["min_x"]), oy + (ly - meta["min_y"]))
            owner[nid] = key
        merged_via[key] = "strip_pack"

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
        {**g, "cores": list(g.get("pivots") or []), "soft": True}
        for g in rigid_groups
    ]

    return out, {
        "slots": len(key_order),
        "nodes": len(out),
        "pad": float(pad),
        "merge_shared": bool(merge_shared),
        "fabric_bridges": do_fabric if merge_shared else False,
        "bridge_boost": boost if merge_shared else 0.0,
        "ideal_scale": float(ideal_scale) if merge_shared else None,
        "spring_iters": int(spring_iters) if merge_shared else None,
        "order_mode": order_mode,
        "pack_mode": pack_mode,
        "cols": cols,
        "order": key_order,
        "origins": {k: [round(v[0], 2), round(v[1], 2)] for k, v in origins.items()},
        "owner": {k: owner[k] for k in list(owner)[:40]},
        "merged_via": merged_via,
        "alignments": align_meta[:20],
        "rigid_groups": rigid_groups,
        "mass_groups": mass_groups,
        "soft": True,
        "tip": (
            "Compose: portal+fabric spring seed; prefer mass_merge "
            "(core/ring/chain attract) then polish_crossings."
        ),
        "slot_meta": {
            k: {
                "w": round(v["w"], 1),
                "h": round(v["h"], 1),
                "area": round(v["area"], 1),
                "n": int(v["n"]),
            }
            for k, v in slot_meta.items()
        },
    }


def blocks_from_position_maps(
    maps: list[tuple[str, dict[str, tuple[float, float]]]],
) -> list[ComposeBlock]:
    return [ComposeBlock(key=k, positions=dict(pos)) for k, pos in maps if pos]


def compose_into_state(
    state: LayoutState,
    blocks: list[ComposeBlock],
    params: LayoutParams | None = None,
    *,
    pad: float = DEFAULT_SLOT_PAD,
    merge_shared: bool = True,
    fabric_bridges: bool = True,
    bridge_boost: float = 8.0,
    ideal_scale: float = 0.55,
    spring_iters: int = 80,
) -> OpResult:
    """Write strip-packed / merge-aligned coords onto ``state``."""
    del params
    merged, meta = strip_pack_blocks(
        blocks,
        pad=pad,
        merge_shared=merge_shared,
        links=list(state.links or []),
        fabric_bridges=fabric_bridges,
        bridge_boost=bridge_boost,
        ideal_scale=ideal_scale,
        spring_iters=spring_iters,
    )
    if not merged:
        return OpResult(
            state=state,
            moved=set(),
            op="compose_views",
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
        op="compose_views",
        params={**meta, "moved_n": len(moved)},
        note=(
            f"compose_views:slots={meta.get('slots')} nodes={meta.get('nodes')} "
            f"merge_shared={meta.get('merge_shared')} order={meta.get('order_mode')}"
        ),
    )


def compose_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not overrides:
        return out
    if overrides.get("pad") is not None:
        try:
            out["pad"] = float(overrides["pad"])
        except (TypeError, ValueError):
            pass
    for flag in ("merge_shared", "fabric_bridges"):
        if flag not in overrides:
            continue
        v = overrides[flag]
        if isinstance(v, bool):
            out[flag] = v
        else:
            out[flag] = str(v).strip().lower() in {"1", "true", "yes", "on"}
    if overrides.get("bridge_boost") is not None:
        try:
            out["bridge_boost"] = max(0.0, float(overrides["bridge_boost"]))
        except (TypeError, ValueError):
            pass
    if overrides.get("ideal_scale") is not None:
        try:
            out["ideal_scale"] = max(0.25, min(1.2, float(overrides["ideal_scale"])))
        except (TypeError, ValueError):
            pass
    if overrides.get("spring_iters") is not None:
        try:
            out["spring_iters"] = max(20, min(240, int(overrides["spring_iters"])))
        except (TypeError, ValueError):
            pass
    src = overrides.get("source_view_ids")
    if isinstance(src, list):
        out["source_view_ids"] = [str(x).strip() for x in src if str(x).strip()]
    elif isinstance(src, str) and src.strip():
        out["source_view_ids"] = [s.strip() for s in src.split(",") if s.strip()]
    return out
