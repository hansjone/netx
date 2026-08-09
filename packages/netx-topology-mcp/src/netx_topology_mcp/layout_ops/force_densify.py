"""Semi-rigid force densify: bidirectional edge springs + local repulsion.

Metro util is killed by long bridges; uniform pack crushes nn into overlaps,
rigid shrink spikes crossings. This runs a few damped force iterations:

- **Edge spring (双向)**: Hookean toward ``ideal_len`` — too long → pull
  together, too short → push apart (push can also densify by expanding
  crushed corridors into readable spacing while gravity/long edges shrink
  the global bbox).
- **Node repulse**: spatial-hash neighbors push apart below ~1.35×nn_floor.
- **Gravity**: free nodes gently pull toward free-centroid (bbox extremities).
- **Semi-rigid**: dual-unit exclusive bodies share a group translation
  (``rigid_strength``) plus optional per-node deform residual.
- **Mass / weights**: hubs move less / repulse stronger; leaves follow springs.

Gates: util or bbox gain within soft crossing budget; then polish.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_ops.hotspots import overlapping_nodes
from netx_topology_mcp.layout_ops.rigid_units import (
    frozen_ids_for_protect,
    groups_from_compose_meta,
)
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_ops.state import LayoutState, OpResult

_COORD_ABS_MAX = 1.0e6
_DEFAULT_ITERS = 12
_DEFAULT_STEP = 0.28
_DEFAULT_MAX_STEP = 100.0
_DEFAULT_RIGID = 0.9
_DEFAULT_DEFORM = 0.12
_DEFAULT_ATTRACT = 0.85
_DEFAULT_REPULSE = 1.35
_DEFAULT_NN_FLOOR = 90.0
# Only pull edges longer than this multiple of nn_floor (metro bridges).
_IDEAL_NN_MUL = 6.0
_MAX_EDGE_PULL = 90.0


def _bbox_area(pos: dict[str, tuple[float, float]]) -> float:
    pts = [
        p
        for p in pos.values()
        if abs(p[0]) <= _COORD_ABS_MAX
        and abs(p[1]) <= _COORD_ABS_MAX
        and math.isfinite(p[0])
        and math.isfinite(p[1])
    ]
    if len(pts) < 2:
        return 1.0
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(max(xs) - min(xs), 1e-6) * max(max(ys) - min(ys), 1e-6)


def _parse_groups(
    groups: list[dict[str, Any]],
    valid: set[str],
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    raw: list[tuple[str, list[str], list[str]]] = []
    for g in groups:
        key = str(g.get("key") or "")
        members = [str(n) for n in (g.get("node_ids") or []) if str(n) in valid]
        if len(members) < 2:
            continue
        pivots = [str(p) for p in (g.get("pivots") or []) if str(p) in valid]
        for n in members:
            counts[n] = counts.get(n, 0) + 1
        raw.append((key, members, pivots))
    shared = {n for n, c in counts.items() if c > 1}
    out: list[dict[str, Any]] = []
    for key, members, pivots in raw:
        exclusive = [n for n in members if n not in shared]
        out.append(
            {
                "key": key,
                "members": members,
                "pivots": pivots or [n for n in members if n in shared],
                "exclusive": exclusive,
                "shared": [n for n in members if n in shared],
            }
        )
    return out


def _mass_and_weights(
    st: LayoutState,
    ids: list[str],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Return mass, attract_w, repulse_w per node."""
    mass: dict[str, float] = {}
    att: dict[str, float] = {}
    rep: dict[str, float] = {}
    for n in ids:
        deg = len(st.adj.get(n, ()))
        layer = str(st.layers.get(n) or "")
        # Hubs = heavy + strong territory; leaves = light + strong spring follow.
        m = 1.0 + 0.28 * float(deg)
        if layer in ("core", "agg") or deg >= 8:
            m *= 1.35
            a_w = 0.65
            r_w = 1.55
        elif deg <= 2:
            m *= 0.75
            a_w = 1.35
            r_w = 0.85
        else:
            a_w = 1.0
            r_w = 1.0
        mass[n] = m
        att[n] = a_w
        rep[n] = r_w
    return mass, att, rep


def _spatial_bins(
    pos: dict[str, tuple[float, float]],
    ids: list[str],
    cell: float,
) -> dict[tuple[int, int], list[str]]:
    bins: dict[tuple[int, int], list[str]] = {}
    inv = 1.0 / max(cell, 1e-6)
    for n in ids:
        x, y = pos[n]
        key = (int(math.floor(x * inv)), int(math.floor(y * inv)))
        bins.setdefault(key, []).append(n)
    return bins


def _accumulate_forces(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    ids: list[str],
    *,
    ideal_len: float,
    nn_floor: float,
    attract_k: float,
    repulse_k: float,
    gravity_k: float,
    att_w: dict[str, float],
    rep_w: dict[str, float],
    frozen: set[str],
) -> dict[str, tuple[float, float]]:
    fx = {n: 0.0 for n in ids}
    fy = {n: 0.0 for n in ids}
    id_set = set(ids)

    # Weak gravity toward free-node centroid (shrinks bbox extremities).
    free = [n for n in ids if n not in frozen and n in pos]
    if gravity_k > 1e-9 and len(free) >= 2:
        gcx = sum(pos[n][0] for n in free) / len(free)
        gcy = sum(pos[n][1] for n in free) / len(free)
        for n in free:
            dx, dy = gcx - pos[n][0], gcy - pos[n][1]
            dist = math.hypot(dx, dy)
            if dist < nn_floor * 2.0:
                continue
            # Stronger pull for outliers far from center.
            mag = gravity_k * min(dist * 0.02, _MAX_EDGE_PULL * 0.8)
            fx[n] += (dx / dist) * mag
            fy[n] += (dy / dist) * mag

    # Bidirectional edge spring: L>ideal pull; L<ideal push (Hookean).
    # Dead-band around ideal avoids jitter; push uses same attract_k scale
    # (repulse_k still handles non-adjacent crowding).
    dead = max(ideal_len * 0.06, nn_floor * 0.25)
    for a, b in links:
        if a not in pos or b not in pos:
            continue
        if a not in id_set and b not in id_set:
            continue
        ax, ay = pos[a]
        bx, by = pos[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 1e-6:
            # Coincident endpoints: invent a tiny axis so push can separate.
            dx, dy, L = 1.0, 0.0, 1.0
        delta = L - ideal_len
        if abs(delta) <= dead:
            continue
        ux, uy = dx / L, dy / L
        # Cap |δ| so ultra-long bridges / crushed stubs don't explode.
        capped = max(-_MAX_EDGE_PULL * 8.0, min(delta, _MAX_EDGE_PULL * 12.0))
        # Sign: positive δ → pull a toward b (force on a along +u);
        # negative δ → push a away from b (force on a along -u).
        mag = attract_k * min(abs(capped) * 0.045, _MAX_EDGE_PULL)
        if capped < 0:
            # Slightly softer push than pull (avoid blasting clusters open).
            mag *= 0.75
        sign = 1.0 if capped > 0 else -1.0
        wa = att_w.get(a, 1.0) if a not in frozen else 0.0
        wb = att_w.get(b, 1.0) if b not in frozen else 0.0
        wsum = wa + wb
        if wsum <= 1e-12:
            continue
        fa = mag * (wa / wsum)
        fb = mag * (wb / wsum)
        # On a: toward b when pulling (sign>0); away when pushing (sign<0).
        if a in fx and a not in frozen:
            fx[a] += ux * fa * sign
            fy[a] += uy * fa * sign
        if b in fx and b not in frozen:
            fx[b] -= ux * fb * sign
            fy[b] -= uy * fb * sign

    # Local repulsion — tight floor only (avoid pushing bbox outward).
    r0 = max(nn_floor * 1.35, 100.0)
    cell = max(r0, 1.0)
    bins = _spatial_bins(pos, ids, cell)
    for (cx, cy), bucket in bins.items():
        neighbors: list[str] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbors.extend(bins.get((cx + dx, cy + dy), ()))
        seen: set[tuple[str, str]] = set()
        for a in bucket:
            if a in frozen:
                continue
            ax, ay = pos[a]
            for b in neighbors:
                if b == a or b in frozen:
                    continue
                pair = (a, b) if a < b else (b, a)
                if pair in seen:
                    continue
                seen.add(pair)
                bx, by = pos[b]
                dx, dy = ax - bx, ay - by
                d = math.hypot(dx, dy)
                if d >= r0 or d < 1e-6:
                    continue
                ux, uy = dx / d, dy / d
                mag = repulse_k * min(r0 - d, r0) * 0.4 * (
                    (rep_w.get(a, 1.0) + rep_w.get(b, 1.0)) * 0.5
                )
                mag = min(mag, _MAX_EDGE_PULL * 0.9)
                if a not in frozen:
                    fx[a] += ux * mag
                    fy[a] += uy * mag
                if b not in frozen:
                    fx[b] -= ux * mag
                    fy[b] -= uy * mag
    return {n: (fx[n], fy[n]) for n in ids}


def _apply_semi_rigid(
    deltas: dict[str, tuple[float, float]],
    groups: list[dict[str, Any]],
    frozen: set[str],
    *,
    rigid_strength: float,
    deform: float,
) -> dict[str, tuple[float, float]]:
    """Blend group mean translation with per-node residual."""
    out = dict(deltas)
    rs = max(0.0, min(1.0, float(rigid_strength)))
    df = max(0.0, min(1.0, float(deform)))
    # When rigid=1 and deform=0 → pure mean translate for exclusive.
    for g in groups:
        excl = [n for n in g.get("exclusive") or [] if n in out and n not in frozen]
        if len(excl) < 2:
            continue
        mx = sum(out[n][0] for n in excl) / len(excl)
        my = sum(out[n][1] for n in excl) / len(excl)
        for n in excl:
            ix, iy = out[n]
            # rs=1,df=0 → pure group translate; df>0 allows limited shape stretch.
            out[n] = (
                rs * mx + (1.0 - rs) * ix + df * rs * (ix - mx),
                rs * my + (1.0 - rs) * iy + df * rs * (iy - my),
            )
    return out


def force_densify_round(
    state: LayoutState,
    *,
    groups: list[dict[str, Any]] | None = None,
    iters: int = _DEFAULT_ITERS,
    step: float = _DEFAULT_STEP,
    max_step: float = _DEFAULT_MAX_STEP,
    ideal_len: float | None = None,
    nn_floor: float = _DEFAULT_NN_FLOOR,
    attract_k: float = _DEFAULT_ATTRACT,
    repulse_k: float = _DEFAULT_REPULSE,
    gravity_k: float = 0.55,
    rigid_strength: float = _DEFAULT_RIGID,
    deform: float = _DEFAULT_DEFORM,
    protect_rigid: bool | str = "portals",
    x_slack: int | None = None,
    damping: float = 0.85,
) -> OpResult:
    """Run damped attract/repulse densify with semi-rigid dual-unit bodies."""
    from netx_topology_mcp.layout_jobs import (
        raise_if_cancelled,
        report_progress,
        touch_heartbeat,
    )

    st = state.copy()
    groups = groups if groups is not None else groups_from_compose_meta(
        (st.meta or {}).get("compose_views")
    )
    valid = {
        n
        for n, (x, y) in st.positions.items()
        if abs(x) <= _COORD_ABS_MAX
        and abs(y) <= _COORD_ABS_MAX
        and math.isfinite(x)
        and math.isfinite(y)
    }
    ids = sorted(valid)
    if len(ids) < 3:
        return OpResult(
            state=st, moved=set(), op="force_densify", note="force_densify:too_few"
        )

    parsed = _parse_groups(groups or [], valid)
    frozen = frozen_ids_for_protect(st, protect_rigid) if protect_rigid not in (
        False,
        "false",
        "off",
        "none",
        "0",
    ) else set()
    # Pin region phantoms.
    for n in list(ids):
        if str(n).startswith("region:"):
            frozen.add(n)

    before = score_state(st, fast=True)
    before_util = float((before.get("summary") or {}).get("util") or 0.0)
    before_x = int((before.get("summary") or {}).get("crossings") or 0)
    before_ov = len(overlapping_nodes(st))
    area0 = _bbox_area(st.positions)
    slack = (
        max(12, int(before_x * 0.1))
        if x_slack is None
        else max(0, int(x_slack))
    )
    # Default ideal targets long corridors only (not near-nn edges).
    ideal = (
        float(ideal_len)
        if ideal_len is not None
        else max(float(nn_floor) * _IDEAL_NN_MUL, 520.0)
    )
    mass, att_w, rep_w = _mass_and_weights(st, ids)

    pos = {n: (float(st.positions[n][0]), float(st.positions[n][1])) for n in ids}
    links = list(st.links)
    n_iters = max(1, min(40, int(iters)))
    step_k = max(0.05, min(1.0, float(step)))
    cap = max(20.0, float(max_step))
    damp = max(0.2, min(0.98, float(damping)))

    report_progress(
        "force_densify",
        pct=48.0,
        message=(
            f"iters={n_iters} ideal={ideal:.0f} "
            f"rigid={rigid_strength:.2f} deform={deform:.2f}"
        ),
        nodes=len(ids),
        groups=len(parsed),
    )

    vel = {n: (0.0, 0.0) for n in ids}
    moved: set[str] = set()
    # Keep best checkpoint within x_slack (smaller bbox / higher util proxy).
    _cell = 90.0 * 90.0
    util_proxy0 = len(ids) * _cell / max(area0, 1e-6)
    best_pos = dict(pos)
    # tier=2 within slack, tier=1 soft densify, tier=0 baseline
    best_key = (0, util_proxy0, -area0, -before_x)
    best_meta_x = before_x
    best_meta_util = util_proxy0
    best_iter = 0

    from netx_topology_mcp.layout_metrics import count_edge_crossings

    for it in range(n_iters):
        raise_if_cancelled()
        touch_heartbeat()
        if it % 2 == 0:
            report_progress(
                "force_densify",
                pct=48.0 + 24.0 * (it / max(n_iters, 1)),
                message=f"iter {it + 1}/{n_iters}",
                iter=it + 1,
            )
        forces = _accumulate_forces(
            pos,
            links,
            ids,
            ideal_len=ideal,
            nn_floor=float(nn_floor),
            attract_k=float(attract_k),
            repulse_k=float(repulse_k),
            gravity_k=float(gravity_k),
            att_w=att_w,
            rep_w=rep_w,
            frozen=frozen,
        )
        deltas: dict[str, tuple[float, float]] = {}
        for n in ids:
            if n in frozen:
                deltas[n] = (0.0, 0.0)
                continue
            fx, fy = forces[n]
            m = max(mass.get(n, 1.0), 0.2)
            dx, dy = (fx / m) * step_k, (fy / m) * step_k
            vx = damp * vel[n][0] + dx
            vy = damp * vel[n][1] + dy
            spd = math.hypot(vx, vy)
            if spd > cap:
                s = cap / spd
                vx, vy = vx * s, vy * s
            vel[n] = (vx, vy)
            deltas[n] = (vx, vy)

        deltas = _apply_semi_rigid(
            deltas,
            parsed,
            frozen,
            rigid_strength=rigid_strength,
            deform=deform,
        )
        for n, (dx, dy) in deltas.items():
            if n in frozen:
                continue
            spd = math.hypot(dx, dy)
            if spd > cap:
                s = cap / spd
                dx, dy = dx * s, dy * s
            if abs(dx) + abs(dy) < 1e-4:
                continue
            x, y = pos[n]
            pos[n] = (x + dx, y + dy)
            moved.add(n)

        # Checkpoint every 2 iters (and last). Prefer within x_slack; else
        # still keep densifying states (metro: short x rise → polish after).
        if it % 2 == 1 or it == n_iters - 1:
            raise_if_cancelled()
            touch_heartbeat()
            x_now = count_edge_crossings(pos, links)
            area_now = _bbox_area(pos)
            util_proxy = len(ids) * _cell / max(area_now, 1e-6)
            densified = util_proxy > util_proxy0 * 1.002 or area_now < area0 * 0.998
            if not densified:
                continue
            soft_cap = before_x + max(slack * 3, int(before_x * 0.45) + 40)
            if x_now > soft_cap:
                continue
            tier = 2 if x_now <= before_x + slack else 1
            key = (tier, util_proxy, -area_now, -x_now)
            if key > best_key:
                best_key = key
                best_pos = dict(pos)
                best_meta_x = x_now
                best_meta_util = util_proxy
                best_iter = it + 1

    pos = best_pos
    st.positions = {**st.positions, **pos}
    fin = score_state(st, fast=True)
    end_util = float((fin.get("summary") or {}).get("util") or 0.0)
    end_x = int((fin.get("summary") or {}).get("crossings") or 0)
    end_ov = len(overlapping_nodes(st))
    area1 = _bbox_area(st.positions)
    area_ratio = area0 / max(area1, 1e-6)

    util_up = end_util > before_util + 1e-6
    area_up = area_ratio >= 1.004
    x_down = end_x < before_x
    # Soft final gate: densify may spend up to soft_cap crossings; polish next.
    soft_final = before_x + max(slack * 3, int(before_x * 0.45) + 40)
    x_ok = end_x <= soft_final
    gain = util_up or area_up or x_down

    meta = {
        "reverted": False,
        "start_util": before_util,
        "end_util": end_util,
        "start_crossings": before_x,
        "end_crossings": end_x,
        "start_overlaps": before_ov,
        "end_overlaps": end_ov,
        "bbox_area_ratio": round(area_ratio, 4),
        "moved_n": len(moved),
        "iters": n_iters,
        "best_iter": best_iter,
        "ideal_len": round(ideal, 1),
        "nn_floor": float(nn_floor),
        "attract_k": float(attract_k),
        "repulse_k": float(repulse_k),
        "gravity_k": float(gravity_k),
        "rigid_strength": float(rigid_strength),
        "deform": float(deform),
        "x_slack": slack,
        "x_soft_cap": soft_final,
        "checkpoint_x": best_meta_x,
        "checkpoint_util_proxy": round(best_meta_util, 6),
        "protect_rigid": (
            protect_rigid
            if isinstance(protect_rigid, str)
            else ("portals" if protect_rigid else "off")
        ),
        "groups_n": len(parsed),
        "frozen_n": len(frozen),
    }

    def _revert(reason: str) -> OpResult:
        meta["reverted"] = True
        meta["reason"] = reason
        st0 = state.copy()
        st0.meta = dict(st0.meta or {})
        st0.meta["force_densify"] = meta
        return OpResult(
            state=st0,
            moved=set(),
            op="force_densify",
            params=meta,
            note=f"force_densify:reverted {reason}",
        )

    if best_iter == 0 and not gain:
        return _revert("no_gain")
    if not x_ok:
        return _revert("crossing_rise")
    if not gain:
        return _revert("no_gain")
    # Residual overlaps OK if densified — layout_tool ensure_zero_overlap repairs.

    st.meta = dict(st.meta or {})
    st.meta["force_densify"] = meta
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="force_densify",
        params=meta,
        note=(
            f"force_densify util {before_util:.4f}->{end_util:.4f} "
            f"x {before_x}->{end_x} area×{area_ratio:.3f} moved={len(moved)}"
        ),
    )


def force_densify_params_from_overrides(
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    o = overrides or {}
    out: dict[str, Any] = {}
    for key, lo, hi, cast in (
        ("iters", 1, 40, int),
        ("step", 0.05, 1.0, float),
        ("max_step", 20.0, 800.0, float),
        ("ideal_len", 40.0, 4000.0, float),
        ("nn_floor", 40.0, 400.0, float),
        ("attract_k", 0.05, 4.0, float),
        ("repulse_k", 0.05, 4.0, float),
        ("gravity_k", 0.0, 3.0, float),
        ("rigid_strength", 0.0, 1.0, float),
        ("deform", 0.0, 1.0, float),
        ("damping", 0.2, 0.98, float),
        ("x_slack", 0, 5000, int),
    ):
        if o.get(key) is None:
            continue
        try:
            v = cast(o[key])
            out[key] = max(lo, min(hi, v))
        except (TypeError, ValueError):
            pass
    if "protect_rigid" in o:
        v = o.get("protect_rigid")
        if isinstance(v, bool):
            out["protect_rigid"] = "portals" if v else "off"
        else:
            key = str(v or "portals").strip().lower()
            if key in {"0", "false", "no", "off", "none"}:
                out["protect_rigid"] = "off"
            elif key in {"all", "full", "rigid"}:
                out["protect_rigid"] = "all"
            else:
                out["protect_rigid"] = "portals"
    groups = o.get("rigid_groups") or o.get("_rigid_groups")
    if isinstance(groups, list):
        out["groups"] = groups
    return out
