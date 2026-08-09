"""Mass-field merge: ForceAtlas2-style forces + dual-unit islands.

Gravity clustering (stage-1), Gephi-ForceAtlas2 inspired:
  - Long-range node repulsion ∝ (m_i·m_j)/d  (FA2 scaling).
  - Lin-log edge attraction (short edges push apart, long edges log-pull).
  - Gravity toward **home dual-unit core** (not canvas centroid).
  - Inter-group centroid sep/pack so units stay separate islands.
  - Global canvas gravity off by default (was collapsing the fabric).

Optional cross-block core_pull / capture remain for later merge polish.
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.force_densify import (
    _MAX_EDGE_PULL,
    _bbox_area,
    _spatial_bins,
)
from netx_topology_mcp.layout_ops.mass_field import (
    build_mass_field,
    capture_pass,
    core_centroid,
    evolve_chains_to_rings,
    geo_score,
    group_effective_mass,
    groups_from_mass_or_rigid,
)
from netx_topology_mcp.layout_ops.rigid_units import frozen_ids_for_protect
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_ops.state import LayoutState, OpResult

_COORD_ABS_MAX = 1.0e6
_STEP_CAP = {
    "core": 0.35,
    "ring": 0.65,
    "chain": 1.4,
    "free": 1.0,
}
_FOLLOW = {
    "chain": 1.4,
    "ring": 0.5,
    "core": 0.15,
    "free": 1.0,
}
_L0_MUL = {
    "ring": 1.0,
    "chain": 0.85,
    "bridge": 1.35,  # bridges prefer longer — keep islands apart
    "plain": 1.0,
}


def _edge_key(a: str, b: str) -> str:
    return f"{a}|{b}" if a <= b else f"{b}|{a}"


def _group_records(
    groups: list[dict[str, Any]],
    pos: dict[str, tuple[float, float]],
    nodes: dict[str, Any],
) -> list[tuple[str, float, tuple[float, float], set[str]]]:
    """(key, mass, centroid, members) for groups that have a usable centroid."""
    out: list[tuple[str, float, tuple[float, float], set[str]]] = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        key = str(g.get("key") or "")
        members = {str(x) for x in (g.get("node_ids") or []) if str(x) in pos}
        if len(members) < 1:
            continue
        c = core_centroid(g, pos, nodes)
        if c is None:
            xs = [pos[n][0] for n in members]
            ys = [pos[n][1] for n in members]
            c = (sum(xs) / len(xs), sum(ys) / len(ys))
        M = group_effective_mass(g, nodes)
        out.append((key, M, c, members))
    return out


def _home_map(
    gmeta: list[tuple[str, float, tuple[float, float], set[str]]],
) -> dict[str, str]:
    home: dict[str, str] = {}
    for key, _M, _c, members in gmeta:
        for n in members:
            if n not in home:
                home[n] = key
    return home


def _centroid_sep_stats(
    gmeta: list[tuple[str, float, tuple[float, float], set[str]]],
) -> dict[str, float]:
    """Nearest-neighbor distances among group centroids."""
    if len(gmeta) < 2:
        return {"mean_nn": 0.0, "min_nn": 0.0, "n": float(len(gmeta))}
    nns: list[float] = []
    for i, (_k, _M, ci, _m) in enumerate(gmeta):
        best = None
        for j, (_k2, _M2, cj, _m2) in enumerate(gmeta):
            if i == j:
                continue
            d = math.hypot(ci[0] - cj[0], ci[1] - cj[1])
            if best is None or d < best:
                best = d
        if best is not None:
            nns.append(best)
    return {
        "mean_nn": round(sum(nns) / len(nns), 2) if nns else 0.0,
        "min_nn": round(min(nns), 2) if nns else 0.0,
        "n": float(len(gmeta)),
    }


def _seed_nodes_disk(
    pos: dict[str, tuple[float, float]],
    ids: list[str],
    *,
    pitch: float,
    frozen: set[str],
) -> dict[str, tuple[float, float]]:
    """Pack free nodes into a round disk (golden-angle spiral) — FA2 circular seed."""
    free = sorted(n for n in ids if n not in frozen and n in pos)
    if len(free) < 3:
        return pos
    xs = [pos[n][0] for n in free]
    ys = [pos[n][1] for n in free]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    out = dict(pos)
    p = max(float(pitch), 80.0)
    for i, nid in enumerate(free):
        ang = i * 2.399963229728653
        rad = p * 0.55 * math.sqrt(i + 1.0)
        out[nid] = (cx + math.cos(ang) * rad, cy + math.sin(ang) * rad)
    return out


def _seed_group_circle(
    pos: dict[str, tuple[float, float]],
    gmeta: list[tuple[str, float, tuple[float, float], set[str]]],
    *,
    sep_ideal: float,
    frozen: set[str],
) -> dict[str, tuple[float, float]]:
    """Place **cluster centroids** on a round golden-angle pack (keep islands).

    Overall footprint is circular; each dual-unit stays a rigid translate of
    its members (home map once — no stacked deltas).
    """
    n = len(gmeta)
    pitch = max(float(sep_ideal), 400.0)
    ordered = sorted(gmeta, key=lambda t: (-t[1], t[0]))
    home = _home_map(ordered)
    targets: dict[str, tuple[float, float]] = {}
    for i, (key, _M, _c, _m) in enumerate(ordered):
        if i == 0:
            targets[key] = (0.0, 0.0)
            continue
        ang = (i - 1) * 2.399963229728653
        rad = pitch * 0.62 * math.sqrt(float(i))
        targets[key] = (math.cos(ang) * rad, math.sin(ang) * rad)
    gcx = sum(c[0] for _k, _M, c, _m in gmeta) / n
    gcy = sum(c[1] for _k, _M, c, _m in gmeta) / n
    tcx = sum(t[0] for t in targets.values()) / n
    tcy = sum(t[1] for t in targets.values()) / n
    delta: dict[str, tuple[float, float]] = {}
    for key, _M, (cx, cy), _mem in ordered:
        tx, ty = targets[key]
        delta[key] = ((tx - tcx + gcx) - cx, (ty - tcy + gcy) - cy)
    out = dict(pos)
    for nid, hk in home.items():
        if nid in frozen or hk not in delta:
            continue
        dx, dy = delta[hk]
        x, y = out[nid]
        out[nid] = (x + dx, y + dy)
    # Unpack only crushed islands (local spiral); do not flatten the whole canvas.
    by_home: dict[str, list[str]] = {}
    for nid, hk in home.items():
        if nid in frozen:
            continue
        by_home.setdefault(hk, []).append(nid)
    local_pitch = max(float(sep_ideal) * 0.1, 130.0)
    for _hk, members in by_home.items():
        if len(members) < 2:
            continue
        members = sorted(members)
        xs = [out[n][0] for n in members]
        ys = [out[n][1] for n in members]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        nns: list[float] = []
        for i, a in enumerate(members[:80]):
            ax, ay = out[a]
            best = None
            for j, b in enumerate(members[:80]):
                if i == j:
                    continue
                d = math.hypot(ax - out[b][0], ay - out[b][1])
                if best is None or d < best:
                    best = d
            if best is not None:
                nns.append(best)
        med_nn = sorted(nns)[len(nns) // 2] if nns else span
        if span >= local_pitch * 3.5 and med_nn >= local_pitch * 0.45:
            continue
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        for i, nid in enumerate(members):
            ang = i * 2.399963229728653
            rad = local_pitch * 0.65 * math.sqrt(i + 1.0)
            out[nid] = (cx + math.cos(ang) * rad, cy + math.sin(ang) * rad)
    return out


def _seed_group_spread(
    pos: dict[str, tuple[float, float]],
    gmeta: list[tuple[str, float, tuple[float, float], set[str]]],
    *,
    sep_ideal: float,
    frozen: set[str],
) -> tuple[dict[str, tuple[float, float]], bool, str]:
    """Repack cluster centroids into a round pack when stacked/sparse/collapsed."""
    if len(gmeta) < 3:
        return pos, False, "skip"
    stats = _centroid_sep_stats(gmeta)
    mean_nn = stats["mean_nn"]
    min_nn = stats["min_nn"]
    # Also reseat when the cloud of islands is a long strip.
    cens = [c for _k, _M, c, _m in gmeta]
    cw = max(c[0] for c in cens) - min(c[0] for c in cens)
    ch = max(c[1] for c in cens) - min(c[1] for c in cens)
    aspect = max(cw, ch) / max(min(cw, ch), 1.0)
    if min_nn < sep_ideal * 0.35:
        reason = "min_stacked"
    elif mean_nn < sep_ideal * 0.45:
        reason = "collapsed"
    elif mean_nn > sep_ideal * 2.0:
        reason = "too_sparse"
    elif aspect > 1.45:
        reason = "strip"
    else:
        return pos, False, "ok"
    return _seed_group_circle(pos, gmeta, sep_ideal=sep_ideal, frozen=frozen), True, reason


def _accumulate_mass_forces(
    pos: dict[str, tuple[float, float]],
    links: list[tuple[str, str]],
    ids: list[str],
    *,
    mass: dict[str, Any],
    ideal_len: float,
    nn_floor: float,
    attract_k: float,
    repulse_k: float,
    gravity_k: float,
    group_sep_k: float,
    group_pack_k: float,
    sep_ideal: float,
    global_gravity_k: float,
    core_pull_k: float,
    lambda_core: float,
    frozen: set[str],
    fa2: bool = True,
    scaling: float = 8.0,
    linlog: bool = True,
) -> dict[str, tuple[float, float]]:
    nodes = mass.get("nodes") or {}
    edges = mass.get("edges") or {}
    groups = mass.get("groups") or []
    fx = {n: 0.0 for n in ids}
    fy = {n: 0.0 for n in ids}
    id_set = set(ids)

    free = [n for n in ids if n not in frozen and n in pos]
    gmeta = _group_records(groups, pos, nodes)
    home = _home_map(gmeta)
    by_key = {k: (M, c, mem) for k, M, c, mem in gmeta}

    # Degree proxy for FA2 mass: use annotated mass (cores heavier).
    def _m(n: str) -> float:
        return max(float((nodes.get(n) or {}).get("mass") or 1.0), 0.2)

    # FA2 global gravity → canvas barycenter (keeps overall shape round, not a strip).
    if global_gravity_k > 1e-9 and len(free) >= 2:
        gcx = sum(pos[n][0] for n in free) / len(free)
        gcy = sum(pos[n][1] for n in free) / len(free)
        for n in free:
            dx, dy = gcx - pos[n][0], gcy - pos[n][1]
            dist = math.hypot(dx, dy)
            if dist < nn_floor * 0.5:
                continue
            # Stronger than before: ∝ dist (FA2), scaled by node mass.
            mag = (
                global_gravity_k
                * _m(n)
                * min(dist * (0.045 if fa2 else 0.015), _MAX_EDGE_PULL * 1.1)
            )
            fx[n] += (dx / dist) * mag
            fy[n] += (dy / dist) * mag

    # Home-group gravity (FA2 gravity toward region center = dual-unit core).
    if gravity_k > 1e-9 and by_key:
        for n in free:
            hk = home.get(n)
            if not hk or hk not in by_key:
                continue
            _M, (cx, cy), _mem = by_key[hk]
            dx, dy = cx - pos[n][0], cy - pos[n][1]
            dist = math.hypot(dx, dy)
            if dist < nn_floor * 0.35:
                continue
            role = str((nodes.get(n) or {}).get("role") or "free")
            role_w = {"core": 0.15, "ring": 1.0, "chain": 1.2, "free": 0.95}.get(
                role, 1.0
            )
            # FA2: gravity ∝ mass; stronger glue for leaves.
            mag = (
                gravity_k
                * role_w
                * _m(n)
                * min(dist * (0.055 if fa2 else 0.04), _MAX_EDGE_PULL * 0.9)
            )
            fx[n] += (dx / dist) * mag
            fy[n] += (dy / dist) * mag

    # Inter-group: repulse if too close, pack if too far (keep islands compact).
    if (group_sep_k > 1e-9 or group_pack_k > 1e-9) and len(gmeta) >= 2:
        target = max(float(sep_ideal), nn_floor * 6.0)
        pack_lo = target * 1.55
        for i, (ki, Mi, ci, memi) in enumerate(gmeta):
            for j, (kj, Mj, cj, memj) in enumerate(gmeta):
                if j <= i:
                    continue
                dx, dy = ci[0] - cj[0], ci[1] - cj[1]
                dist = math.hypot(dx, dy)
                if dist < 1e-6:
                    ang = hash((ki, kj)) % 360
                    rad = math.radians(float(ang))
                    dx, dy = math.cos(rad), math.sin(rad)
                    dist = 1.0
                ux, uy = dx / dist, dy / dist
                w_sum = max(Mi, 0.2) + max(Mj, 0.2)
                if dist < target and group_sep_k > 1e-9:
                    gap = target - dist
                    push = group_sep_k * min(gap * 0.08, _MAX_EDGE_PULL * 1.4)
                    pi = push * (max(Mj, 0.2) / w_sum)
                    pj = push * (max(Mi, 0.2) / w_sum)
                    for n in memi:
                        if n in frozen or n not in fx or home.get(n) != ki:
                            continue
                        fx[n] += ux * pi
                        fy[n] += uy * pi
                    for n in memj:
                        if n in frozen or n not in fx or home.get(n) != kj:
                            continue
                        fx[n] -= ux * pj
                        fy[n] -= uy * pj
                elif dist > pack_lo and group_pack_k > 1e-9:
                    gap = dist - target
                    pull = group_pack_k * min(gap * 0.035, _MAX_EDGE_PULL * 1.1)
                    pi = pull * (max(Mj, 0.2) / w_sum)
                    pj = pull * (max(Mi, 0.2) / w_sum)
                    for n in memi:
                        if n in frozen or n not in fx or home.get(n) != ki:
                            continue
                        fx[n] -= ux * pi
                        fy[n] -= uy * pi
                    for n in memj:
                        if n in frozen or n not in fx or home.get(n) != kj:
                            continue
                        fx[n] += ux * pj
                        fy[n] += uy * pj

    # Edges: FA2 lin-log attraction, or legacy spring.
    dead = max(ideal_len * 0.06, nn_floor * 0.25)
    for a, b in links:
        if a not in pos or b not in pos:
            continue
        if a not in id_set and b not in id_set:
            continue
        erow = edges.get(_edge_key(a, b)) or {}
        role = str(erow.get("role") or "plain")
        ae = float(erow.get("attract") or 1.0)
        L0 = ideal_len * _L0_MUL.get(role, 1.0)
        ax, ay = pos[a]
        bx, by = pos[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 1e-6:
            dx, dy, L = 1.0, 0.0, 1.0
        ux, uy = dx / L, dy / L
        ma, mb = _m(a), _m(b)
        inv = 1.0 / ma + 1.0 / mb
        na = float((nodes.get(a) or {}).get("attract") or 1.0)
        nb = float((nodes.get(b) or {}).get("attract") or 1.0)
        w = ae * math.sqrt(max(na * nb, 1e-6))

        if fa2:
            # Lin-log (Gephi default for clustered graphs): pull ∝ log(1+d).
            # Below L0: strong push so corridors don't collapse onto portals.
            if L < L0:
                gap = L0 - L
                mag = attract_k * w * min(gap * 0.09, _MAX_EDGE_PULL * 1.2)
                sign = -1.0  # push apart along edge
            else:
                if linlog:
                    mag = attract_k * w * math.log1p(L / max(L0, 1.0)) * 18.0
                else:
                    mag = attract_k * w * min((L - L0) * 0.05, _MAX_EDGE_PULL)
                mag = min(mag, _MAX_EDGE_PULL * 1.3)
                sign = 1.0  # pull together
            if role == "bridge" and sign > 0:
                mag *= 0.4
        else:
            delta = L - L0
            if abs(delta) <= dead:
                continue
            capped = max(-_MAX_EDGE_PULL * 8.0, min(delta, _MAX_EDGE_PULL * 12.0))
            mag = attract_k * w * min(abs(capped) * 0.045, _MAX_EDGE_PULL)
            if capped < 0:
                mag *= 0.75
            if role == "bridge" and capped > 0:
                mag *= 0.55
            sign = 1.0 if capped > 0 else -1.0

        fa = mag * ((1.0 / ma) / inv)
        fb = mag * ((1.0 / mb) / inv)
        if a in fx and a not in frozen:
            fx[a] += ux * fa * sign
            fy[a] += uy * fa * sign
        if b in fx and b not in frozen:
            fx[b] -= ux * fb * sign
            fy[b] -= uy * fb * sign

    # Repulsion: FA2 long-range ∝ m_i·m_j / d ; legacy short nn bump otherwise.
    if fa2:
        # scaling ≈ Gephi scalingRatio; range grows with ideal island pitch.
        r_max = max(float(sep_ideal) * 0.85, nn_floor * 10.0, ideal_len * 4.0)
        cell = max(r_max * 0.45, nn_floor * 2.0)
        bins = _spatial_bins(pos, ids, cell)
        # 5×5 neighborhood ≈ long-range without full O(n²).
        offsets = tuple((dx, dy) for dx in range(-2, 3) for dy in range(-2, 3))
        scale = max(float(scaling), 0.2) * float(repulse_k)
        for (cx, cy), bucket in bins.items():
            neighbors: list[str] = []
            for dx, dy in offsets:
                neighbors.extend(bins.get((cx + dx, cy + dy), ()))
            seen: set[tuple[str, str]] = set()
            for a in bucket:
                if a in frozen:
                    continue
                ax, ay = pos[a]
                ma = _m(a)
                ra = float((nodes.get(a) or {}).get("repulse") or 1.0)
                for b in neighbors:
                    if b == a:
                        continue
                    pair = (a, b) if a < b else (b, a)
                    if pair in seen:
                        continue
                    seen.add(pair)
                    bx, by = pos[b]
                    dx, dy = ax - bx, ay - by
                    d = math.hypot(dx, dy)
                    if d >= r_max:
                        continue
                    if d < 1e-6:
                        ang = hash(pair) % 360
                        rad = math.radians(float(ang))
                        dx, dy, d = math.cos(rad), math.sin(rad), 1.0
                    mb = _m(b)
                    rb = float((nodes.get(b) or {}).get("repulse") or 1.0)
                    # FA2: F = k * (m_i m_j) / d ; boost near-field to lift nn_p50.
                    mag = scale * ma * mb * math.sqrt(max(ra * rb, 1e-6)) / d
                    if d < nn_floor * 2.0:
                        mag *= 1.0 + (nn_floor * 2.0 - d) / max(nn_floor, 1.0)
                    mag = min(mag, _MAX_EDGE_PULL * 2.8)
                    ux, uy = dx / d, dy / d
                    if a not in frozen:
                        fx[a] += ux * mag
                        fy[a] += uy * mag
                    if b not in frozen:
                        fx[b] -= ux * mag
                        fy[b] -= uy * mag
    else:
        r0 = max(nn_floor * 1.35, 100.0)
        cell = max(r0, 1.0)
        bins = _spatial_bins(pos, ids, cell)
        for (cx, cy), bucket in bins.items():
            neighbors = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbors.extend(bins.get((cx + dx, cy + dy), ()))
            seen = set()
            for a in bucket:
                if a in frozen:
                    continue
                ax, ay = pos[a]
                ra = float((nodes.get(a) or {}).get("repulse") or 1.0)
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
                    rb = float((nodes.get(b) or {}).get("repulse") or 1.0)
                    ux, uy = dx / d, dy / d
                    mag = (
                        repulse_k
                        * math.sqrt(max(ra * rb, 1e-6))
                        * min(r0 - d, r0)
                        * 0.4
                    )
                    mag = min(mag, _MAX_EDGE_PULL * 0.9)
                    fx[a] += ux * mag
                    fy[a] += uy * mag
                    if b not in frozen:
                        fx[b] -= ux * mag
                        fy[b] -= uy * mag

    # Weak foreign-core attract (optional steal); keep mild so islands survive.
    if core_pull_k > 1e-9 and gmeta:
        for n in free:
            role = str((nodes.get(n) or {}).get("role") or "free")
            follow = _FOLLOW.get(role, 1.0)
            if follow < 1e-6:
                continue
            nx, ny = pos[n]
            hk = home.get(n)
            for key, M, (cx, cy), _members in gmeta:
                if key == hk:
                    continue
                dx, dy = cx - nx, cy - ny
                d = math.hypot(dx, dy)
                if d < nn_floor * 0.5 or d > lambda_core * 2.5:
                    continue
                mag = (
                    core_pull_k
                    * M
                    * follow
                    * math.exp(-d / max(lambda_core, 1.0))
                    * 0.015
                )
                mag = min(mag, _MAX_EDGE_PULL * 0.7)
                fx[n] += (dx / d) * mag
                fy[n] += (dy / d) * mag

    return {n: (fx[n], fy[n]) for n in ids}


def mass_merge_round(
    state: LayoutState,
    *,
    groups: list[dict[str, Any]] | None = None,
    iters: int = 16,
    step: float = 0.32,
    max_step: float = 160.0,
    ideal_len: float | None = None,
    nn_floor: float = 90.0,
    attract_k: float = 1.0,
    repulse_k: float = 1.15,
    gravity_k: float = 0.85,
    group_sep_k: float = 1.0,
    group_pack_k: float = 1.1,
    sep_ideal: float | None = None,
    global_gravity_k: float = 0.0,
    core_pull_k: float = 0.2,
    lambda_core: float | None = None,
    protect_rigid: bool | str = "off",
    evolve_every: int = 8,
    kappa_node: float = 1.25,
    kappa_block: float = 2.0,
    rho_ideal: float = 6.0,
    x_slack: int | None = None,
    damping: float = 0.85,
    capture: bool = False,
    cluster_seed: bool = True,
    fa2: bool = True,
    scaling: float = 8.0,
    linlog: bool = True,
    use_dual_units: bool = True,
) -> OpResult:
    """Run FA2-style mass merge with dual-unit clusters.

    Islands = dual-unit groups; soft global gravity keeps the **cloud of
    islands** roughly round (repulsion + edge traction), not a strip.
    """
    from netx_topology_mcp.layout_jobs import (
        raise_if_cancelled,
        report_progress,
        touch_heartbeat,
    )

    st = state.copy()
    cv = (st.meta or {}).get("compose_views") or {}
    if use_dual_units:
        grp = groups if groups is not None else groups_from_mass_or_rigid(cv)
    else:
        # Temporarily ignore dual-unit / compose mass groups — pure FA2.
        grp = []
    mass = (st.meta or {}).get("mass_field") if use_dual_units else None
    if not isinstance(mass, dict) or not mass.get("nodes"):
        # units=[] skips dual-unit detection inside build_mass_field.
        mass = build_mass_field(
            st, units=[] if not use_dual_units else None, groups=grp
        )
    else:
        mass = dict(mass)
        if grp and not mass.get("groups"):
            mass["groups"] = grp
        if not use_dual_units:
            mass["groups"] = []
            mass["units"] = []

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
            state=st, moved=set(), op="mass_merge", note="mass_merge:too_few"
        )

    frozen = (
        frozen_ids_for_protect(st, protect_rigid)
        if protect_rigid not in (False, "false", "off", "none", "0")
        else set()
    )
    for n in list(ids):
        if str(n).startswith("region:"):
            frozen.add(n)

    before = score_state(st, fast=True)
    before_x = int((before.get("summary") or {}).get("crossings") or 0)
    before_geo = geo_score(st, mass)
    slack = (
        max(20, int(before_x * 0.12))
        if x_slack is None
        else max(0, int(x_slack))
    )
    ideal = (
        float(ideal_len)
        if ideal_len is not None
        else max(float(nn_floor) * 6.0, 520.0)
    )
    lam = float(lambda_core) if lambda_core is not None else 2.5 * ideal
    g0 = _group_records(mass.get("groups") or [], st.positions, mass.get("nodes") or {})
    # Keep island pitch moderate — do NOT scale with sqrt(n) (blew 91 units apart).
    sep_tgt = (
        float(sep_ideal)
        if sep_ideal is not None
        else max(ideal * 2.0, 700.0)
    )
    before_sep = _centroid_sep_stats(g0)
    # Soft barycenter gravity → round envelope of the whole graph.
    # With clusters: mild (islands stay apart via group_sep). Without: stronger.
    if global_gravity_k <= 1e-12:
        global_gravity_k = (0.45 if use_dual_units else 1.05) if fa2 else 0.4
    if not use_dual_units:
        gravity_k = 0.0
        group_sep_k = 0.0
        group_pack_k = 0.0
        core_pull_k = 0.0

    pos = {n: (float(st.positions[n][0]), float(st.positions[n][1])) for n in ids}
    seeded = False
    seed_reason = "skip"
    if cluster_seed and g0:
        pos, seeded, seed_reason = _seed_group_spread(
            pos, g0, sep_ideal=sep_tgt, frozen=frozen
        )
        if seeded:
            st.positions = {**st.positions, **pos}
            g0 = _group_records(
                mass.get("groups") or [], pos, mass.get("nodes") or {}
            )
            before_sep = _centroid_sep_stats(g0)
    elif cluster_seed and not g0:
        # Fallback only when there are no clusters: disk if strip/collapsed.
        xs = [pos[n][0] for n in ids if n not in frozen]
        ys = [pos[n][1] for n in ids if n not in frozen]
        if len(xs) >= 8:
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            aspect = max(w, h) / max(min(w, h), 1.0)
            sample = ids[: min(80, len(ids))]
            nns: list[float] = []
            for a in sample:
                if a in frozen:
                    continue
                ax, ay = pos[a]
                best = None
                for b in sample:
                    if a == b or b in frozen:
                        continue
                    d = math.hypot(ax - pos[b][0], ay - pos[b][1])
                    if best is None or d < best:
                        best = d
                if best is not None:
                    nns.append(best)
            med = sorted(nns)[len(nns) // 2] if nns else 0.0
            if aspect > 1.55 or med < nn_floor * 0.7:
                disk_pitch = max(float(nn_floor) * 1.15, ideal * 0.22, 100.0)
                pos = _seed_nodes_disk(pos, ids, pitch=disk_pitch, frozen=frozen)
                st.positions = {**st.positions, **pos}
                seeded = True
                seed_reason = "disk_aspect" if aspect > 1.55 else "disk_collapsed"

    links = list(st.links)
    n_iters = max(1, min(48, int(iters)))
    step_k = max(0.05, min(1.0, float(step)))
    cap = max(20.0, float(max_step))
    damp = max(0.2, min(0.98, float(damping)))
    every = max(2, int(evolve_every))

    report_progress(
        "mass_merge",
        pct=48.0,
        message=(
            f"iters={n_iters} groups={len(mass.get('groups') or [])} "
            f"sep_ideal={sep_tgt:.0f} seed={seed_reason}"
        ),
        nodes=len(ids),
    )

    vel = {n: (0.0, 0.0) for n in ids}
    moved: set[str] = set()
    area0 = _bbox_area(pos)
    best_pos = dict(pos)
    best_mass = mass
    # Prefer separation + geo; do NOT reward bbox crush.
    best_key = (
        before_sep["mean_nn"],
        before_geo,
        -before_x,
    )
    best_iter = 0
    capture_reports: list[dict[str, Any]] = []
    evolve_reports: list[dict[str, Any]] = []

    nodes_m = mass.get("nodes") or {}

    for it in range(n_iters):
        raise_if_cancelled()
        touch_heartbeat()
        if it % 2 == 0:
            report_progress(
                "mass_merge",
                pct=48.0 + 24.0 * (it / max(n_iters, 1)),
                message=f"iter {it + 1}/{n_iters}",
                iter=it + 1,
            )
        forces = _accumulate_mass_forces(
            pos,
            links,
            ids,
            mass=mass,
            ideal_len=ideal,
            nn_floor=float(nn_floor),
            attract_k=float(attract_k),
            repulse_k=float(repulse_k),
            gravity_k=float(gravity_k),
            group_sep_k=float(group_sep_k),
            group_pack_k=float(group_pack_k),
            sep_ideal=float(sep_tgt),
            global_gravity_k=float(global_gravity_k),
            core_pull_k=float(core_pull_k),
            lambda_core=lam,
            frozen=frozen,
            fa2=bool(fa2),
            scaling=float(scaling),
            linlog=bool(linlog),
        )
        for n in ids:
            if n in frozen:
                continue
            fx, fy = forces[n]
            row = nodes_m.get(n) or {}
            m = max(float(row.get("mass") or 1.0), 0.2)
            role = str(row.get("role") or "free")
            role_cap = cap * _STEP_CAP.get(role, 1.0)
            dx, dy = (fx / m) * step_k, (fy / m) * step_k
            vx = damp * vel[n][0] + dx
            vy = damp * vel[n][1] + dy
            spd = math.hypot(vx, vy)
            if spd > role_cap:
                s = role_cap / spd
                vx, vy = vx * s, vy * s
            vel[n] = (vx, vy)
            if abs(vx) + abs(vy) < 1e-4:
                continue
            x, y = pos[n]
            pos[n] = (x + vx, y + vy)
            moved.add(n)

        if (it + 1) % every == 0 or it == n_iters - 1:
            st.positions = {**st.positions, **pos}
            if use_dual_units:
                mass = evolve_chains_to_rings(st, mass)
                ev = mass.get("evolve") or {}
                evolve_reports.append(dict(ev))
            if capture and use_dual_units:
                mass, crep = capture_pass(
                    st,
                    mass,
                    kappa_node=kappa_node,
                    kappa_block=kappa_block,
                    rho_ideal=rho_ideal,
                    ideal_len=ideal,
                )
                capture_reports.append(crep)
            nodes_m = mass.get("nodes") or {}

        if it % 2 == 1 or it == n_iters - 1:
            st.positions = {**st.positions, **pos}
            g = geo_score(st, mass)
            x_now = count_edge_crossings(pos, links)
            soft_cap = before_x + max(slack * 4, int(before_x * 0.7) + 80)
            # FA2 stage-1: crossings will spike while islands form — don't discard.
            if (not fa2) and x_now > soft_cap:
                continue
            gnow = _group_records(
                mass.get("groups") or [], pos, mass.get("nodes") or {}
            )
            sep = _centroid_sep_stats(gnow)
            if fa2:
                key = (sep["mean_nn"], g, -abs(sep["mean_nn"] - sep_tgt))
            else:
                key = (sep["mean_nn"], g, -x_now)
            if key > best_key:
                best_key = key
                best_pos = dict(pos)
                best_mass = mass
                best_iter = it + 1

    if best_iter == 0:
        st.positions = {**st.positions, **pos}
        last_geo = geo_score(st, mass)
        last_x = count_edge_crossings(pos, links)
        soft_cap = before_x + max(slack * 4, int(before_x * 0.7) + 80)
        gnow = _group_records(mass.get("groups") or [], pos, mass.get("nodes") or {})
        sep = _centroid_sep_stats(gnow)
        if last_x <= soft_cap and (
            sep["mean_nn"] >= before_sep["mean_nn"] * 0.95
            or last_geo >= before_geo - 1.0
        ):
            best_pos = dict(pos)
            best_mass = mass
            best_iter = n_iters

    pos = best_pos
    mass = best_mass
    st.positions = {**st.positions, **pos}
    fin = score_state(st, fast=True)
    end_x = int((fin.get("summary") or {}).get("crossings") or 0)
    end_geo = geo_score(st, mass)
    end_gmeta = _group_records(
        mass.get("groups") or [], pos, mass.get("nodes") or {}
    )
    end_sep = _centroid_sep_stats(end_gmeta)
    area1 = _bbox_area(pos)
    area_ratio = area0 / max(area1, 1e-6)

    meta = {
        "reverted": False,
        "start_crossings": before_x,
        "end_crossings": end_x,
        "start_geo": round(before_geo, 3),
        "end_geo": round(end_geo, 3),
        "start_sep_nn": before_sep["mean_nn"],
        "end_sep_nn": end_sep["mean_nn"],
        "min_sep_nn": end_sep["min_nn"],
        "sep_ideal": round(sep_tgt, 1),
        "cluster_seeded": seeded,
        "seed_reason": seed_reason,
        "moved_n": len(moved),
        "iters": n_iters,
        "best_iter": best_iter,
        "ideal_len": round(ideal, 1),
        "gravity_k": float(gravity_k),
        "group_sep_k": float(group_sep_k),
        "group_pack_k": float(group_pack_k),
        "global_gravity_k": float(global_gravity_k),
        "core_pull_k": float(core_pull_k),
        "capture": bool(capture),
        "fa2": bool(fa2),
        "scaling": float(scaling),
        "linlog": bool(linlog),
        "use_dual_units": bool(use_dual_units),
        "kappa_node": float(kappa_node),
        "kappa_block": float(kappa_block),
        "evolve_every": every,
        "capture_log": capture_reports[-3:] if capture_reports else [],
        "evolve": evolve_reports[-3:] if evolve_reports else [],
        "groups_n": len(mass.get("groups") or []),
        "bbox_area_ratio": round(area_ratio, 4),
        "x_slack": slack,
    }

    sep_up = end_sep["mean_nn"] > before_sep["mean_nn"] * 1.05 + 20.0
    geo_up = end_geo > before_geo + 0.02
    x_ok = end_x <= before_x + max(slack * 4, int(before_x * 0.7) + 80)
    # Lattice/repack seed is itself the stage-1 gain — keep it even if x rises.
    gain = sep_up or geo_up or seeded or (
        len(moved) >= 2 and end_geo >= before_geo - 1.0
    )
    # FA2 clustering tolerates crossing spikes; only revert if nothing improved.
    if (not fa2) and (not x_ok) and (not sep_up) and (not geo_up) and (not seeded):
        meta["reverted"] = True
        meta["reason"] = "crossing_rise"
        st0 = state.copy()
        st0.meta = dict(st0.meta or {})
        st0.meta["mass_merge"] = meta
        return OpResult(
            state=st0,
            moved=set(),
            op="mass_merge",
            params=meta,
            note="mass_merge:reverted crossing_rise",
        )
    if best_iter == 0 and not gain:
        meta["reverted"] = True
        meta["reason"] = "no_sep_gain"
        st0 = state.copy()
        st0.meta = dict(st0.meta or {})
        st0.meta["mass_merge"] = meta
        return OpResult(
            state=st0,
            moved=set(),
            op="mass_merge",
            params=meta,
            note="mass_merge:reverted no_sep_gain",
        )
    # Seeded but no checkpoint accepted: still keep last iterated / seeded pos.
    if best_iter == 0 and seeded:
        best_pos = dict(pos)
        best_mass = mass
        best_iter = max(1, n_iters)
        pos = best_pos
        mass = best_mass
        st.positions = {**st.positions, **pos}
        end_x = count_edge_crossings(pos, links)
        end_geo = geo_score(st, mass)
        end_gmeta = _group_records(
            mass.get("groups") or [], pos, mass.get("nodes") or {}
        )
        end_sep = _centroid_sep_stats(end_gmeta)
        meta["end_crossings"] = end_x
        meta["end_geo"] = round(end_geo, 3)
        meta["end_sep_nn"] = end_sep["mean_nn"]
        meta["min_sep_nn"] = end_sep["min_nn"]
        meta["best_iter"] = best_iter
        meta["kept_seed"] = True

    st.meta = dict(st.meta or {})
    st.meta["mass_field"] = mass
    st.meta["mass_merge"] = meta
    cv2 = dict(st.meta.get("compose_views") or cv or {})
    cv2["mass_groups"] = mass.get("groups") or []
    cv2["soft"] = True
    st.meta["compose_views"] = cv2
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="mass_merge",
        params=meta,
        note=(
            f"mass_merge sep {before_sep['mean_nn']:.0f}->{end_sep['mean_nn']:.0f} "
            f"geo {before_geo:.1f}->{end_geo:.1f} x {before_x}->{end_x} "
            f"moved={len(moved)}"
        ),
    )


def mass_merge_params_from_overrides(
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    o = overrides or {}
    out: dict[str, Any] = {}
    for key, lo, hi, cast in (
        ("iters", 1, 48, int),
        ("step", 0.05, 1.0, float),
        ("max_step", 20.0, 800.0, float),
        ("ideal_len", 40.0, 4000.0, float),
        ("nn_floor", 40.0, 400.0, float),
        ("attract_k", 0.05, 4.0, float),
        ("repulse_k", 0.05, 4.0, float),
        ("gravity_k", 0.0, 4.0, float),
        ("group_sep_k", 0.0, 4.0, float),
        ("group_pack_k", 0.0, 4.0, float),
        ("sep_ideal", 200.0, 20000.0, float),
        ("global_gravity_k", 0.0, 3.0, float),
        ("core_pull_k", 0.0, 4.0, float),
        ("lambda_core", 100.0, 8000.0, float),
        ("evolve_every", 2, 24, int),
        ("kappa_node", 0.5, 4.0, float),
        ("kappa_block", 1.1, 6.0, float),
        ("rho_ideal", 2.0, 20.0, float),
        ("damping", 0.2, 0.98, float),
        ("x_slack", 0, 5000, int),
        ("scaling", 0.2, 40.0, float),
    ):
        if o.get(key) is None:
            continue
        try:
            v = cast(o[key])
            out[key] = max(lo, min(hi, v))
        except (TypeError, ValueError):
            pass
    for flag in ("capture", "cluster_seed", "fa2", "linlog", "use_dual_units"):
        if flag in o:
            v = o.get(flag)
            if isinstance(v, bool):
                out[flag] = v
            else:
                out[flag] = str(v).strip().lower() in {"1", "true", "yes", "on"}
    if "protect_rigid" in o:
        v = o.get("protect_rigid")
        if isinstance(v, bool):
            out["protect_rigid"] = "portals" if v else "off"
        else:
            key = str(v or "off").strip().lower()
            if key in {"0", "false", "no", "off", "none"}:
                out["protect_rigid"] = "off"
            elif key in {"all", "full", "rigid"}:
                out["protect_rigid"] = "all"
            else:
                out["protect_rigid"] = "portals"
    return out
