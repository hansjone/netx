"""Same-canvas dual-unit beautify: parallel lanes + straight chains.

Role: after gravity (`mass_merge`) has clustered the canvas, reshape each
dual-unit neighborhood in place — multi-corridor → H/V lanes; chains →
straight (no 回字). Does **not** redistribute the whole canvas by default.

Modes:
  - refine (default / auto when spread): beautify onto current world portals
  - full / repark: optional orbit_pack (legacy redistribute) then beautify

Typical agent flow:
  mass_merge (gravity) → polish_crossings → dual_mass ↔ polish
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.compose_orbit import orbit_pack_blocks
from netx_topology_mcp.layout_ops.compose_views import ComposeBlock, _rigid_align_to_world
from netx_topology_mcp.layout_ops.dual_units import (
    DualUnit,
    beautify_dual_unit_positions,
    classify_dual_unit,
    find_dual_portal_units,
)
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local
from netx_topology_mcp.layout_ops.mass_field import (
    attach_mass_to_compose_meta,
    build_mass_field,
)
from netx_topology_mcp.layout_ops.mass_merge import mass_merge_round
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult


def _unit_links(
    unit: DualUnit,
    adj: dict[str, tuple[str, ...]],
) -> list[tuple[str, str]]:
    members = unit.member_ids()
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for n in members:
        for v in adj.get(n, ()):
            if v not in members:
                continue
            pair = (n, v) if n <= v else (v, n)
            if pair in seen:
                continue
            seen.add(pair)
            out.append(pair)
    return out


def _misc_grid_positions(
    node_ids: list[str],
    *,
    pitch: float = 200.0,
) -> dict[str, tuple[float, float]]:
    if not node_ids:
        return {}
    cols = max(1, int(math.ceil(math.sqrt(len(node_ids)))))
    pos: dict[str, tuple[float, float]] = {}
    for i, nid in enumerate(sorted(node_ids)):
        r, c = divmod(i, cols)
        pos[nid] = (c * pitch, r * pitch)
    return pos


def _unit_centroids(
    positions: dict[str, tuple[float, float]],
    units: list[DualUnit],
) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    for u in units:
        ids = [n for n in u.member_ids() if n in positions]
        if not ids:
            continue
        xs = [positions[n][0] for n in ids]
        ys = [positions[n][1] for n in ids]
        out[int(u.unit_id)] = (sum(xs) / len(xs), sum(ys) / len(ys))
    return out


def _centroid_drift(
    before: dict[int, tuple[float, float]],
    after: dict[int, tuple[float, float]],
) -> dict[str, float]:
    keys = sorted(set(before) & set(after))
    if not keys:
        return {"mean": 0.0, "max": 0.0, "n": 0.0}
    dists = [
        math.hypot(after[k][0] - before[k][0], after[k][1] - before[k][1])
        for k in keys
    ]
    return {
        "mean": round(sum(dists) / len(dists), 2),
        "max": round(max(dists), 2),
        "n": float(len(dists)),
    }


def _knobs_from_meta(state: LayoutState) -> dict[str, Any]:
    raw = (state.meta or {}).get("_dual_mass") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _resolve_mode(state: LayoutState, mode: str) -> str:
    m = str(mode or "auto").strip().lower() or "auto"
    if m in {"full", "seed", "pack", "repark"}:
        return "full"
    if m in {"refine", "sweep", "stabilize", "beautify"}:
        return "refine"
    # auto: beautify in place once the canvas is already spread / has meta.
    if (state.meta or {}).get("dual_mass"):
        return "refine"
    xs = [p[0] for p in state.positions.values()]
    ys = [p[1] for p in state.positions.values()]
    if not xs:
        return "refine"
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    # Collapsed dump → optional repark; otherwise in-place beautify.
    return "full" if span < 800.0 else "refine"


def _beautify_blocks(
    st: LayoutState,
    units: list[DualUnit],
    params: LayoutParams,
) -> tuple[list[ComposeBlock], int, int, set[str], dict[str, int]]:
    blocks: list[ComposeBlock] = []
    unit_ok = 0
    unit_fail = 0
    covered: set[str] = set()
    kinds: dict[str, int] = {"petal": 0, "hui": 0, "straight": 0}
    for u in units:
        kind = classify_dual_unit(u)
        kinds[kind] = kinds.get(kind, 0) + 1
        local = beautify_dual_unit_positions(st, u, params)
        links_u = _unit_links(u, st.adj)
        x_u = count_edge_crossings(local, links_u) if links_u else 0
        if x_u != 0:
            unit_fail += 1
        else:
            unit_ok += 1
        covered |= set(local.keys())
        blocks.append(ComposeBlock(key=f"u{u.unit_id}", positions=local))
    return blocks, unit_ok, unit_fail, covered, kinds


def _refine_beautify_onto_world(
    st: LayoutState,
    units: list[DualUnit],
    params: LayoutParams,
) -> tuple[LayoutState, set[str], int, int, dict[str, int]]:
    """Beautify each unit rigidly onto current portal world coords (no re-orbit)."""
    world = dict(st.positions)
    moved: set[str] = set()
    unit_ok = 0
    unit_fail = 0
    kinds: dict[str, int] = {"petal": 0, "hui": 0, "straight": 0}
    for u in units:
        kind = classify_dual_unit(u)
        kinds[kind] = kinds.get(kind, 0) + 1
        local = beautify_dual_unit_positions(st, u, params)
        links_u = _unit_links(u, st.adj)
        x_u = count_edge_crossings(local, links_u) if links_u else 0
        if x_u != 0:
            unit_fail += 1
        else:
            unit_ok += 1
        shared = [p for p in (u.portal_a, u.portal_b) if p in world and p in local]
        if not shared:
            continue
        prefer = None
        if len(shared) == 1 and len(local) >= 2:
            cx = sum(p[0] for p in world.values()) / max(len(world), 1)
            cy = sum(p[1] for p in world.values()) / max(len(world), 1)
            prefer = (cx, cy)
        aligned = _rigid_align_to_world(
            local,
            world,
            shared,
            prefer_center=prefer,
            links=list(st.links or []),
        )
        for nid, xy in aligned.items():
            if nid in shared:
                continue
            if world.get(nid) != xy:
                moved.add(nid)
            world[nid] = xy
    out = st.copy()
    out.positions = world
    return out, moved, unit_ok, unit_fail, kinds


def _ensure_mass_meta(
    st: LayoutState,
    units: list[DualUnit],
    groups: list[dict[str, Any]] | None = None,
) -> tuple[LayoutState, dict[str, Any], list[dict[str, Any]]]:
    grp = list(groups or [])
    if not grp:
        for u in units:
            ids = sorted(u.member_ids())
            grp.append(
                {
                    "key": f"u{u.unit_id}",
                    "node_ids": ids,
                    "pivots": [u.portal_a, u.portal_b],
                    "cores": [u.portal_a, u.portal_b],
                    "soft": True,
                }
            )
    mass = build_mass_field(st, units=units, groups=grp)
    st = st.copy()
    st.meta = dict(st.meta or {})
    st.meta["mass_field"] = mass
    packish = {
        "mode": "dual_mass",
        "mass_groups": mass.get("groups") or grp,
        "rigid_groups": grp,
        "soft": True,
    }
    st.meta["compose_views"] = attach_mass_to_compose_meta(packish, mass)
    return st, mass, grp


def layout_dual_mass(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    pad: float = 500.0,
    fabric_bridges: bool = True,
    angle_step: int = 30,
    mass_merge: bool = False,
    mass_iters: int = 12,
    mode: str = "auto",
    rounds: int = 1,
    gravity_first: bool = False,
    stable_drift: float = 120.0,
) -> OpResult:
    """Beautify dual-portal neighborhoods (petal / 回 / straight) in place."""
    params = params or LayoutParams()
    knobs = _knobs_from_meta(state)
    if "mode" in knobs:
        mode = str(knobs["mode"])
    if "rounds" in knobs:
        rounds = int(knobs["rounds"] or 1)
    if "mass_iters" in knobs:
        mass_iters = int(knobs["mass_iters"] or mass_iters)
    if "pad" in knobs:
        pad = float(knobs["pad"] or pad)
    if "angle_step" in knobs:
        angle_step = int(knobs["angle_step"] or angle_step)
    if "mass_merge" in knobs:
        v = knobs["mass_merge"]
        mass_merge = bool(v) if not isinstance(v, str) else v.strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
    if "gravity_first" in knobs:
        v = knobs["gravity_first"]
        gravity_first = (
            bool(v)
            if not isinstance(v, str)
            else v.strip().lower() in {"1", "true", "yes", "on"}
        )
    if "stable_drift" in knobs:
        stable_drift = float(knobs["stable_drift"] or stable_drift)

    st = state.copy()
    units = find_dual_portal_units(st)
    if not units:
        return OpResult(
            state=st,
            moved=set(),
            op="layout_dual_mass",
            params={"units_n": 0},
            note="no_dual_units",
        )

    resolved = _resolve_mode(st, mode)
    rounds = max(1, min(int(rounds), 8))
    moved: set[str] = set()
    unit_ok = 0
    unit_fail = 0
    leftovers: list[str] = []
    pack_meta: dict[str, Any] = {}
    mass_meta: dict[str, Any] | None = None
    kinds: dict[str, int] = {"petal": 0, "hui": 0, "straight": 0}
    drifts: list[dict[str, float]] = []
    stable = False

    if gravity_first:
        st, mass, groups = _ensure_mass_meta(st, units)
        mop = mass_merge_round(
            st,
            groups=mass.get("groups") or groups,
            iters=max(4, int(mass_iters) // 2),
            protect_rigid="portals",
            gravity_k=0.45,
            core_pull_k=1.2,
        )
        st = mop.state
        moved |= mop.moved
        mass_meta = mop.params if isinstance(mop.params, dict) else None

    for ri in range(rounds):
        cens_before = _unit_centroids(st.positions, units)
        pass_mode = resolved if ri == 0 else "refine"

        if pass_mode == "full":
            blocks, unit_ok, unit_fail, covered, kinds = _beautify_blocks(
                st, units, params
            )
            leftovers = [
                n
                for n in st.positions
                if n not in covered and not str(n).startswith("region:")
            ]
            if leftovers:
                blocks.append(
                    ComposeBlock(
                        key="misc",
                        positions=_misc_grid_positions(
                            leftovers, pitch=max(float(params.pitch), 180.0)
                        ),
                    )
                )
            merged, pack_meta = orbit_pack_blocks(
                blocks,
                pad=float(pad),
                links=list(st.links or []),
                fabric_bridges=bool(fabric_bridges),
                angle_step=int(angle_step),
            )
            if not merged:
                return OpResult(
                    state=st,
                    moved=moved,
                    op="layout_dual_mass",
                    params={
                        "units_n": len(units),
                        "pack": pack_meta,
                        "mode": pass_mode,
                        "kinds": kinds,
                    },
                    note="pack_empty",
                )
            for nid, xy in merged.items():
                if st.positions.get(nid) != xy:
                    moved.add(nid)
                st.positions[nid] = xy
            for nid in state.positions:
                if nid not in st.positions:
                    st.positions[nid] = state.positions[nid]
            groups = pack_meta.get("mass_groups") or pack_meta.get("rigid_groups") or []
            st, mass, groups = _ensure_mass_meta(st, units, groups)
        else:
            st, eye_moved, unit_ok, unit_fail, kinds = _refine_beautify_onto_world(
                st, units, params
            )
            moved |= eye_moved
            st, mass, groups = _ensure_mass_meta(st, units)

        if mass_merge and len(st.positions) >= 3:
            mop = mass_merge_round(
                st,
                groups=mass.get("groups") or groups,
                iters=max(1, int(mass_iters)),
                protect_rigid="portals",
            )
            st = mop.state
            moved |= mop.moved
            mass_meta = mop.params if isinstance(mop.params, dict) else None

        cens_after = _unit_centroids(st.positions, units)
        drift = _centroid_drift(cens_before, cens_after)
        drifts.append(drift)
        if drift["n"] > 0 and drift["mean"] <= float(stable_drift) and ri > 0:
            stable = True
            break
        resolved = "refine"

    fop = fix_overlaps_local(st, params)
    st = fop.state
    moved |= fop.moved

    report = {
        "units_n": len(units),
        "unit_ok": unit_ok,
        "unit_fail_cross": unit_fail,
        "kinds": kinds,
        "mode": resolved if rounds == 1 else "multi",
        "mode_first": _resolve_mode(state, mode),
        "rounds_ran": len(drifts),
        "rounds_requested": rounds,
        "centroid_drift": drifts,
        "stable": stable,
        "stable_drift": float(stable_drift),
        "misc_n": len(leftovers),
        "pack_slots": pack_meta.get("slots"),
        "pack_final_crossings": pack_meta.get("final_crossings"),
        "mass_merge": mass_meta,
        "mass_groups_n": len(
            (st.meta.get("compose_views") or {}).get("mass_groups") or []
        ),
        "gravity_first": bool(gravity_first),
        "role": "beautify",
    }
    st.meta = dict(st.meta or {})
    st.meta["dual_mass"] = report
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="layout_dual_mass",
        params=report,
        note=(
            f"layout_dual_mass units={len(units)} kinds={kinds} "
            f"mode={report['mode_first']} rounds={report['rounds_ran']} "
            f"stable={stable} moved={len(moved)}"
        ),
    )


def dual_mass_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Pick dual_mass knobs from layout params."""
    if not overrides:
        return {}
    out: dict[str, Any] = {}
    for k in (
        "mode",
        "rounds",
        "mass_iters",
        "pad",
        "angle_step",
        "mass_merge",
        "gravity_first",
        "stable_drift",
        "fabric_bridges",
    ):
        if k in overrides:
            out[k] = overrides[k]
    return out
