"""Stage-2: pin core horizontal beam + agg row via rigid soft-block translates."""

from __future__ import annotations

from collections import deque
from typing import Any

from netx_topology_mcp.layout_metrics import count_edge_crossings
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local
from netx_topology_mcp.layout_ops.partition import partition_soft_blocks
from netx_topology_mcp.layout_ops.score import score_state
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.transforms import normalize_origin


def _hops_to_cores(
    src: str, cores: list[str], adj: dict[str, set[str]]
) -> tuple[int, str]:
    if not cores:
        return 99, ""
    core_set = set(cores)
    if src in core_set:
        return 0, src
    seen = {src}
    q: deque[tuple[str, int]] = deque([(src, 0)])
    while q:
        u, d = q.popleft()
        for v in adj.get(u, ()):
            if v in seen:
                continue
            if v in core_set:
                return d + 1, v
            seen.add(v)
            q.append((v, d + 1))
    return 99, cores[0]


def _core_order(state: LayoutState) -> list[str]:
    cores = [
        n
        for n in state.positions
        if state.layers.get(n) == "core"
    ]
    if len(cores) < 2:
        # fall back: high-degree hubs that look like CN
        for n, nm in state.names.items():
            if "-CN" in (nm or "") and n not in cores:
                cores.append(n)
    cores = sorted(
        set(cores),
        key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n)),
    )
    return cores


def _agg_hubs(state: LayoutState, *, min_degree: int = 4) -> list[str]:
    aggs = [
        n
        for n in state.positions
        if state.layers.get(n) == "agg"
        and len(state.adj.get(n, ())) >= min_degree
    ]
    return sorted(
        aggs,
        key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n)),
    )


def _apply_targets(
    state: LayoutState,
    targets: dict[str, tuple[float, float]],
    by_hub: dict[str, Any],
) -> LayoutState:
    pos = dict(state.positions)
    for hid, (tx, ty) in targets.items():
        block = by_hub.get(hid)
        if not block or hid not in pos:
            if hid in pos:
                pos[hid] = (tx, ty)
            continue
        hx, hy = pos[hid]
        dx, dy = tx - hx, ty - hy
        for n in block.node_ids:
            if n in pos:
                x, y = pos[n]
                pos[n] = (x + dx, y + dy)
    st = state.copy()
    st.positions = pos
    st = fix_overlaps_local(st, LayoutParams()).state
    st = normalize_origin(st, LayoutParams()).state
    return st


def pin_beam_rigid(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    step: float | None = None,
    gap_scales: tuple[float, ...] = (1.0, 1.3, 1.6, 0.85),
) -> OpResult:
    """Rigid-translate soft blocks so cores form a beam and aggs sit on a row.

    Picks the trial with lowest (overlaps, crossings). Does not worsen crossings
    vs the input when a better trial exists; otherwise returns input unchanged.
    """
    params = params or LayoutParams()
    step_px = float(step if step is not None else max(params.pitch, 180.0))
    cores = _core_order(state)
    if len(cores) < 2:
        return OpResult(
            state=state,
            moved=set(),
            op="pin_beam",
            params={"cores": cores},
            note="need_ge_2_cores",
        )

    aggs = _agg_hubs(state)
    aggs = sorted(
        aggs,
        key=lambda a: (
            _hops_to_cores(a, cores, state.adj)[0],
            state.names.get(a, a),
        ),
    )
    blocks = partition_soft_blocks(state, mode="hub_territory")
    by_hub = {b.hub_id: b for b in blocks if b.hub_id}

    links = list(state.links)
    base_cross = count_edge_crossings(state.positions, links)
    base_score = score_state(state)
    base_ov = int(base_score.get("footprint_overlap_pairs") or 0) + int(
        base_score.get("label_overlap_pairs") or 0
    )

    gap0 = step_px * 2.4
    beam_y = 0.0
    trials: list[tuple[str, LayoutState, int, int]] = []

    for gs in gap_scales:
        gap = gap0 * gs
        for petal_mul in (4.0, 4.5, 5.5):
            petal_dy = step_px * petal_mul
            targets: dict[str, tuple[float, float]] = {}
            for i, c in enumerate(cores):
                targets[c] = (i * gap, beam_y)
            if aggs:
                lo = targets[cores[0]][0] - gap * 0.5
                hi = targets[cores[-1]][0] + gap * 0.5
                for i, a in enumerate(aggs):
                    _, pref = _hops_to_cores(a, cores, state.adj)
                    prefer_x = targets.get(pref, targets[cores[0]])[0]
                    if len(aggs) == 1:
                        x = prefer_x
                    else:
                        x = lo + (hi - lo) * i / (len(aggs) - 1)
                        x = 0.55 * x + 0.45 * prefer_x
                    targets[a] = (x, beam_y + petal_dy)
            st2 = _apply_targets(state, targets, by_hub)
            sc = score_state(st2)
            ov = int(sc.get("footprint_overlap_pairs") or 0) + int(
                sc.get("label_overlap_pairs") or 0
            )
            cross = int(sc.get("edge_crossings") or 0)
            trials.append((f"g{gs}_dy{petal_dy:.0f}", st2, ov, cross))

    # Y-align only (preserve x): often safer on already-readable canvases
    cy = sum(state.positions[c][1] for c in cores if c in state.positions) / len(
        cores
    )
    targets_y: dict[str, tuple[float, float]] = {}
    for c in cores:
        if c in state.positions:
            targets_y[c] = (state.positions[c][0], cy)
    ay = cy + step_px * 4.0
    for a in aggs:
        if a in state.positions:
            targets_y[a] = (state.positions[a][0], ay)
    if targets_y:
        st_y = _apply_targets(state, targets_y, by_hub)
        sc = score_state(st_y)
        ov = int(sc.get("footprint_overlap_pairs") or 0) + int(
            sc.get("label_overlap_pairs") or 0
        )
        cross = int(sc.get("edge_crossings") or 0)
        trials.append(("y_align", st_y, ov, cross))

    if not trials:
        return OpResult(
            state=state, moved=set(), op="pin_beam", note="no_trials"
        )

    best_name, best_st, best_ov, best_cross = min(
        trials, key=lambda t: (t[2], t[3])
    )
    # Refuse internal accept if worse than input on both axes we care about
    if best_ov > base_ov or best_cross > base_cross + max(20, int(base_cross * 0.05)):
        return OpResult(
            state=state,
            moved=set(),
            op="pin_beam",
            params={
                "cores": cores,
                "aggs": aggs,
                "best_trial": best_name,
                "best_crossings": best_cross,
                "base_crossings": base_cross,
                "accepted": False,
            },
            note="no_improvement",
        )

    moved = {
        n
        for n, p in best_st.positions.items()
        if n in state.positions and p != state.positions[n]
    }
    best_st.meta = dict(best_st.meta or {})
    best_st.meta["pin_beam"] = {
        "trial": best_name,
        "cores": cores,
        "aggs": aggs,
        "crossings_before": base_cross,
        "crossings_after": best_cross,
        "overlaps_after": best_ov,
    }
    return OpResult(
        state=best_st,
        moved=moved,
        op="pin_beam",
        params={
            "cores": cores,
            "aggs": aggs,
            "best_trial": best_name,
            "accepted": True,
            "crossings_before": base_cross,
            "crossings_after": best_cross,
        },
        note=f"pin_beam:{best_name} {base_cross}->{best_cross}",
    )


def pin_beam_params_from_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not overrides:
        return out
    if overrides.get("step") is not None:
        try:
            out["step"] = float(overrides["step"])
        except (TypeError, ValueError):
            pass
    return out
