"""Select dual-unit batches to sink from a root canvas onto a child region.

Pure selection / park / per-unit layout helpers — HTTP moves live in http_tools.
"""

from __future__ import annotations

from typing import Any

from netx_topology_mcp.layout_ops.dual_units import DualUnit, layout_dual_unit
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState


def _portal_share_counts(units: list[DualUnit]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for u in units:
        for p in (u.portal_a, u.portal_b):
            counts[p] = counts.get(p, 0) + 1
    return counts


def unit_detach_score(u: DualUnit, share: dict[str, int]) -> tuple[int, int, int]:
    """Tie-break only: less shared portals, then lower unit_id (stable)."""
    shared = sum(1 for p in (u.portal_a, u.portal_b) if share.get(p, 0) > 1)
    share_sum = share.get(u.portal_a, 0) + share.get(u.portal_b, 0)
    return (shared, share_sum, int(u.unit_id))


def select_dual_unit_batch(
    units: list[DualUnit],
    *,
    max_units: int = 3,
    min_nodes: int = 8,
    max_nodes: int = 300,
    max_batch_nodes: int = 400,
    exclude_ids: set[str] | None = None,
    keep_ids: set[str] | None = None,
    sink_ids: set[str] | None = None,
    prefer_pure: bool = False,
    prefer_core_eye: bool = True,
    prefer_top_eye: bool | None = None,
    layers: dict[str, str] | None = None,
) -> list[DualUnit]:
    """Greedy pick detachable dual-units within size / batch caps.

    Eyes walk **top→down** (core → agg → access): prefer core/agg portal
    pairs and maximize movable membership. ``prefer_core_eye`` is an alias
    for ``prefer_top_eye`` (default true).
    """
    exclude_ids = exclude_ids or set()
    keep_ids = keep_ids or set()
    layers = layers or {}
    if prefer_top_eye is None:
        prefer_top_eye = prefer_core_eye
    share = _portal_share_counts(units)
    candidates: list[DualUnit] = []
    for u in units:
        members = u.member_ids()
        if not members:
            continue
        movable = members - keep_ids
        if sink_ids is not None:
            movable = movable & sink_ids
        if not movable or movable.issubset(exclude_ids):
            continue
        n = len(members)
        if n < int(min_nodes) or n > int(max_nodes):
            continue
        if members and members.issubset(exclude_ids | keep_ids):
            continue
        # Need enough *movable* mass for this level phase.
        if len(movable) < max(2, min(4, int(min_nodes) // 2)):
            continue
        candidates.append(u)

    def _movable(u: DualUnit) -> set[str]:
        m = u.member_ids() - keep_ids
        if sink_ids is not None:
            m = m & sink_ids
        return m

    def _portal_tier(nid: str) -> int:
        ly = (layers.get(nid) or "").strip().lower()
        if ly == "core":
            return 0
        if ly == "agg":
            return 1
        if ly == "access":
            return 2
        return 3

    def _eye_key(u: DualUnit) -> tuple[int, int]:
        t = sorted((_portal_tier(u.portal_a), _portal_tier(u.portal_b)))
        return (t[0], t[1])

    def _score(u: DualUnit) -> tuple:
        mov = _movable(u)
        keep_portals = sum(1 for p in (u.portal_a, u.portal_b) if p in keep_ids)
        pure_penalty = keep_portals if prefer_pure else 0
        # Top-down eye tier, then max movable / membership.
        eye = _eye_key(u) if prefer_top_eye else (0, 0)
        return (
            pure_penalty,
            eye,
            -len(mov),
            -len(u.member_ids()),
            *unit_detach_score(u, share),
        )

    candidates.sort(key=_score)

    picked: list[DualUnit] = []
    claimed: set[str] = set()
    for u in candidates:
        if len(picked) >= int(max_units):
            break
        movable = _movable(u)
        interior = movable - {u.portal_a, u.portal_b}
        if interior & claimed:
            continue
        next_ids = claimed | movable
        if len(next_ids) > int(max_batch_nodes):
            continue
        picked.append(u)
        claimed |= movable
    return picked


def batch_node_ids(
    units: list[DualUnit],
    *,
    keep_ids: set[str] | None = None,
    sink_ids: set[str] | None = None,
) -> list[str]:
    keep_ids = keep_ids or set()
    out: list[str] = []
    seen: set[str] = set()
    for u in units:
        for nid in sorted(u.member_ids()):
            if not nid or nid in seen or nid in keep_ids:
                continue
            if sink_ids is not None and nid not in sink_ids:
                continue
            seen.add(nid)
            out.append(nid)
    return out


def leftover_batch_ids(
    source_ids: list[str],
    *,
    max_batch_nodes: int = 120,
    exclude_ids: set[str] | None = None,
    keep_ids: set[str] | None = None,
    sink_ids: set[str] | None = None,
) -> list[str]:
    """When dual_units are exhausted, take a plain leftover chunk."""
    exclude_ids = exclude_ids or set()
    keep_ids = keep_ids or set()
    out: list[str] = []
    for nid in source_ids:
        sid = str(nid or "").strip()
        if not sid or sid.startswith("region:"):
            continue
        if sid in exclude_ids or sid in keep_ids:
            continue
        if sink_ids is not None and sid not in sink_ids:
            continue
        out.append(sid)
        if len(out) >= int(max_batch_nodes):
            break
    return out


def park_positions(
    src_pos: dict[str, tuple[float, float]],
    node_ids: list[str],
    *,
    sink_pos: dict[str, tuple[float, float]] | None = None,
    pad: float = 280.0,
    links: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Park batch via compose_orbit-style block sweep (not fixed right)."""
    pts = {nid: src_pos[nid] for nid in node_ids if nid in src_pos}
    if not pts:
        out: list[dict[str, Any]] = []
        cols = max(4, int(len(node_ids) ** 0.5) or 1)
        for i, nid in enumerate(node_ids):
            out.append(
                {
                    "fabric_node_id": nid,
                    "x": float((i % cols) * 160),
                    "y": float((i // cols) * 120),
                }
            )
        return out

    # Normalize local bbox to origin before orbit attach.
    xs = [p[0] for p in pts.values()]
    ys = [p[1] for p in pts.values()]
    min_x, min_y = min(xs), min(ys)
    local = {nid: (x - min_x, y - min_y) for nid, (x, y) in pts.items()}
    world, _meta = orbit_attach_to_sink(
        local,
        sink_pos or {},
        links=links or [],
        pad=pad,
    )
    return positions_to_patch(world)


def units_as_batch_rows(
    units: list[DualUnit],
    names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    names = names or {}
    rows: list[dict[str, Any]] = []
    for u in units:
        d = u.as_dict(names)
        rows.append(
            {
                "unit_id": d.get("unit_id"),
                "node_count": d.get("node_count"),
                "portals": [d.get("portal_a"), d.get("portal_b")],
                "names": [d.get("portal_a_name"), d.get("portal_b_name")],
                "node_ids": d.get("node_ids") or [],
            }
        )
    return rows


def subgraph_state(state: LayoutState, members: set[str]) -> LayoutState:
    """Induced subgraph for one dual-unit (local layout_dual_unit)."""
    ids = {m for m in members if m}
    adj = {n: {v for v in state.adj.get(n, ()) if v in ids} for n in ids}
    links = [(a, b) for a, b in state.links if a in ids and b in ids]
    return LayoutState(
        positions={n: state.positions[n] for n in ids if n in state.positions},
        names={n: state.names.get(n, n) for n in ids},
        layers={n: state.layers.get(n, "access") for n in ids},
        links=links,
        adj=adj,
        meta={"ids": sorted(ids)},
    )


def _bbox(pos: dict[str, tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    return min(xs), min(ys), max(xs), max(ys)


def _translate(
    pos: dict[str, tuple[float, float]], dx: float, dy: float
) -> dict[str, tuple[float, float]]:
    return {n: (x + dx, y + dy) for n, (x, y) in pos.items()}


def orbit_attach_to_sink(
    local: dict[str, tuple[float, float]],
    sink_pos: dict[str, tuple[float, float]],
    *,
    links: list[tuple[str, str]] | None = None,
    pad: float = 280.0,
    angle_step: int = 30,
    radii: tuple[float, ...] = (0.75, 1.0, 1.25, 1.55, 1.95),
    cand_cap: int = 96,
) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
    """Block-sweep attach: reuse compose_orbit orphan/portal orbit pick.

    Scores candidates by (partial crossings, footprint hits, bridge length)
    against the existing sink world — same spirit as compose_orbit hang-block.
    """
    from netx_topology_mcp.layout_ops.compose_orbit import (
        _orbit_candidates_one_portal,
        _orbit_candidates_orphan,
        _orbit_candidates_two_portal,
        _pick_best,
        _thin_candidates,
    )
    from netx_topology_mcp.layout_ops.compose_views import (
        _rank_shared_pivots,
        _rigid_align_to_world,
    )

    link_list = list(links or [])
    if not local:
        return {}, {"via": "empty", "cands": 0}

    if not sink_pos:
        xs = [p[0] for p in local.values()]
        ys = [p[1] for p in local.values()]
        min_x, min_y = min(xs), min(ys)
        seeded = {
            n: (x - min_x + 40.0, y - min_y + 40.0) for n, (x, y) in local.items()
        }
        return seeded, {"via": "seed", "cands": 1, "score": [0, 0, 0.0]}

    world = dict(sink_pos)
    shared_raw = [nid for nid in local if nid in world]
    # membership hint: portals already on sink count as glue
    membership = {n: 2 for n in shared_raw}
    shared = _rank_shared_pivots(shared_raw, membership)
    exclusive = [n for n in local if n not in set(shared)]
    old_ids = set(world)

    if shared:
        prefer = None
        wxs = [p[0] for p in world.values()]
        wys = [p[1] for p in world.values()]
        if wxs and wys:
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

    cands = _thin_candidates(cands, cand_cap)

    new_ids = set(exclusive) if exclusive else set(local) - old_ids
    # Fresh rescore every call — never sticky pick.
    best, best_sc, best_i = _pick_best(cands, link_list, new_ids, old_ids)
    out = {n: best[n] for n in local if n in best}
    meta = {
        "via": via,
        "shared_n": len(shared),
        "shared": shared[:4],
        "cands": len(cands),
        "best_i": best_i,
        "score": [best_sc[0], best_sc[1], round(best_sc[2], 1)],
        "exclusive_n": len(exclusive),
    }
    return out, meta


def layout_and_pack_batch(
    state: LayoutState,
    units: list[DualUnit],
    *,
    sink_pos: dict[str, tuple[float, float]] | None = None,
    pad: float = 280.0,
    unit_gap: float = 220.0,
    params: LayoutParams | None = None,
    links: list[tuple[str, str]] | None = None,
) -> tuple[dict[str, tuple[float, float]], list[dict[str, Any]], dict[str, Any]]:
    """layout_dual_unit each unit, strip-pack, then orbit-attach onto sink.

    Attach uses compose_orbit block sweep (partial crossings / overlap /
    bridge length) — not a fixed right-side park.
    """
    params = params or LayoutParams()
    reports: list[dict[str, Any]] = []
    packed: dict[str, tuple[float, float]] = {}
    cursor_x = 0.0
    row_max_h = 0.0

    for u in units:
        members = u.member_ids()
        if not members:
            continue
        sub = subgraph_state(state, members)
        # Seed missing coords so layout_dual_unit has a canvas.
        for n in members:
            if n not in sub.positions:
                sub.positions[n] = (0.0, 0.0)
        op = layout_dual_unit(sub, params, unit=u)
        local = {
            n: op.state.positions[n]
            for n in members
            if n in op.state.positions
        }
        if not local:
            reports.append(
                {
                    "unit_id": u.unit_id,
                    "accepted": False,
                    "note": "no_positions",
                    "unit_crossings": None,
                }
            )
            continue

        # Align shared portals already placed by an earlier unit in this batch.
        shared = [p for p in (u.portal_a, u.portal_b) if p in packed and p in local]
        if shared:
            # Translate so first shared portal matches world.
            piv = shared[0]
            dx = packed[piv][0] - local[piv][0]
            dy = packed[piv][1] - local[piv][1]
            local = _translate(local, dx, dy)
            # Exclusive members only — keep prior portal coords.
            for n, xy in local.items():
                if n not in packed:
                    packed[n] = xy
            min_x, min_y, max_x, max_y = _bbox(
                {n: packed[n] for n in members if n in packed}
            )
            cursor_x = max(cursor_x, max_x + float(unit_gap))
            row_max_h = max(row_max_h, max_y - min_y)
        else:
            min_x, min_y, max_x, max_y = _bbox(local)
            local = _translate(local, cursor_x - min_x, -min_y)
            for n, xy in local.items():
                if n not in packed:
                    packed[n] = xy
            w = max_x - min_x
            h = max_y - min_y
            cursor_x += w + float(unit_gap)
            row_max_h = max(row_max_h, h)

        reports.append(
            {
                "unit_id": u.unit_id,
                "accepted": bool(op.params.get("accepted")),
                "unit_crossings": op.params.get("unit_crossings"),
                "note": op.note,
                "node_count": len(members),
                "portals": [u.portal_a, u.portal_b],
            }
        )

    if not packed:
        return {}, reports, {"via": "empty"}

    # Prefer fabric links from caller (sink∪source); fall back to state links.
    attach_links = list(links) if links is not None else list(state.links)
    world, attach_meta = orbit_attach_to_sink(
        packed,
        sink_pos or {},
        links=attach_links,
        pad=pad,
    )
    return world, reports, attach_meta


def positions_to_patch(
    pos: dict[str, tuple[float, float]],
) -> list[dict[str, Any]]:
    return [
        {"fabric_node_id": nid, "x": float(xy[0]), "y": float(xy[1])}
        for nid, xy in sorted(pos.items())
    ]


def merge_view_links(
    *payloads: dict[str, Any],
) -> list[tuple[str, str]]:
    """Undirected fabric pairs from one or more view GET payloads."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for payload in payloads:
        for e in payload.get("edges") or []:
            if not isinstance(e, dict):
                continue
            a = str(e.get("a_node_id") or e.get("source") or "").strip()
            b = str(e.get("b_node_id") or e.get("target") or "").strip()
            if not a or not b or a == b:
                continue
            key = (a, b) if a < b else (b, a)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out
