"""MCP-facing layout runner: full layout + local fix/relax (no temp scripts)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from netx_topology_mcp.layout_ops import (
    LayoutParams,
    build_state_from_nodes_edges,
    positions_for_api,
    run_recipe,
    score_state,
)
from netx_topology_mcp.layout_ops.hotspots import (
    fix_overlaps_local,
    hotspot_scopes,
    overlapping_nodes,
)
from netx_topology_mcp.layout_ops.recipe import RECIPES
from netx_topology_mcp.layout_ops.state import LayoutState
from netx_topology_mcp.layout_ops.transforms import normalize_origin
from netx_topology_mcp.layout_ops.channels import (
    straighten_channels_greedy,
    straighten_params_from_overrides,
)
from netx_topology_mcp.layout_ops.dual_units import (
    dual_unit_params_from_overrides,
    layout_dual_unit,
)
from netx_topology_mcp.layout_ops.rigid_units import groups_from_membership
from netx_topology_mcp.layout_ops.untangle import (
    untangle_crossings,
    untangle_params_from_overrides,
)
from netx_topology_mcp.layout_ops.press_crossings import (
    park_phantom_nodes,
    polish_crossings,
    press_params_from_overrides,
)
from netx_topology_mcp.layout_ops.clear_edge_hits import (
    clear_edge_hits,
    clear_edge_params_from_overrides,
)
from netx_topology_mcp.layout_ops.orbit_sweep import (
    apply_orbit_pick,
    orbit_params_from_overrides,
    orbit_sweep_node,
    orbit_sweep_round,
)

# Public recipe names → internal multipass ids
RECIPE_ALIASES: dict[str, str] = {
    "rings": "agg_rings_v1",
    "corridor": "smd_corridor_v1",
    "compact": "smd_corridor_compact_v1",
    "unstick": "smd_corridor_unstick_v1",
    "agg_rings_v1": "agg_rings_v1",
    "smd_corridor_v1": "smd_corridor_v1",
    "smd_corridor_compact_v1": "smd_corridor_compact_v1",
    "smd_corridor_unstick_v1": "smd_corridor_unstick_v1",
}

PRESETS: dict[str, dict[str, float]] = {
    "loose": {
        "target_nn": 170.0,
        "scale_cap": 2.8,
        "target_util": 0.12,
        "pack_min_scale": 0.35,
        "pack_iters": 6,
        "island_pad_x": 200.0,
        "island_pad_y": 180.0,
        "lane": 300.0,
        "x_gain": 1.8,
        "pitch": 220.0,
        "side": 200.0,
        "an_gap": 560.0,
        "width_mul": 2.4,
        "height_mul": 1.5,
    },
    "balanced": {
        "target_nn": 155.0,
        "scale_cap": 2.2,
        "target_util": 0.18,
        "pack_min_scale": 0.28,
        "pack_iters": 6,
        "island_pad_x": 180.0,
        "island_pad_y": 160.0,
        "lane": 260.0,
        "x_gain": 1.8,
        "width_mul": 2.0,
        "height_mul": 1.35,
    },
    "dense": {
        "target_nn": 145.0,
        "scale_cap": 2.0,
        "target_util": 0.28,
        "pack_min_scale": 0.22,
        "pack_iters": 8,
        "island_pad_x": 160.0,
        "island_pad_y": 160.0,
        "lane": 240.0,
        "x_gain": 1.8,
        "width_mul": 1.7,
        "height_mul": 1.2,
    },
}

_PARAM_KEYS = (
    "target_nn",
    "scale_cap",
    "target_util",
    "pack_min_scale",
    "pack_iters",
    "pack_nn_floor",
    "island_pad_x",
    "island_pad_y",
    "cluster_gap",
    "cluster_thr",
    "x_gain",
    "lane",
    "overlap_iters",
    "overlap_step",
    "width_mul",
    "height_mul",
    "pitch",
    "side",
    "an_gap",
)

ACTIONS = (
    "layout",
    "fix_overlaps",
    "resolve_overlaps",  # alias of fix_overlaps
    "untangle",
    "straighten_channels",
    "layout_dual_unit",
    "polish_crossings",
    "clear_edge_hits",
    "orbit_sweep",
)


def resolve_recipe(name: str | None) -> str:
    key = str(name or "rings").strip().lower() or "rings"
    if key not in RECIPE_ALIASES:
        raise ValueError(f"unknown_recipe:{key}")
    internal = RECIPE_ALIASES[key]
    if internal not in RECIPES:
        raise ValueError(f"recipe_not_registered:{internal}")
    return internal


def build_params(
    *,
    preset: str = "balanced",
    overrides: dict[str, Any] | None = None,
) -> LayoutParams:
    preset_key = str(preset or "balanced").strip().lower() or "balanced"
    if preset_key not in PRESETS:
        raise ValueError(f"unknown_preset:{preset_key}")
    base = LayoutParams()
    merged = {**PRESETS[preset_key]}
    for k, v in (overrides or {}).items():
        if k not in _PARAM_KEYS or v is None:
            continue
        try:
            merged[k] = float(v) if k not in {"pack_iters", "overlap_iters"} else int(v)
        except (TypeError, ValueError):
            continue
    return replace(base, **merged)


def _rank_key(fin: dict[str, Any]) -> tuple:
    rk = (fin.get("score") or {}).get("rank_key")
    if isinstance(rk, list) and rk:
        return tuple(rk)
    ov = int(fin.get("footprint_overlap_pairs") or 0) + int(
        fin.get("label_overlap_pairs") or 0
    )
    total = float((fin.get("score") or {}).get("total") or 0.0)
    cross = int(fin.get("edge_crossings") or 0)
    return (ov, -total, cross)


def _tune_grid(base: LayoutParams) -> list[LayoutParams]:
    """Sweep knobs that actually move util/nn/crossings (incl. skeleton scale)."""
    out: list[LayoutParams] = []
    for tu, pms in (
        (0.08, base.pack_min_scale),
        (0.14, max(0.45, base.pack_min_scale - 0.05)),
        (0.20, max(0.40, base.pack_min_scale - 0.10)),
        (0.12, min(0.70, base.pack_min_scale + 0.05)),
    ):
        for wm, hm in ((2.2, 1.4), (2.8, 1.7), (3.5, 2.0)):
            for tnn in (base.target_nn, 160.0, 140.0):
                out.append(
                    replace(
                        base,
                        target_util=tu,
                        pack_min_scale=pms,
                        target_nn=tnn,
                        width_mul=wm,
                        height_mul=hm,
                    )
                )
    seen: set[tuple] = set()
    uniq: list[LayoutParams] = []
    for p in out:
        key = (
            p.target_util,
            p.pack_min_scale,
            p.target_nn,
            p.width_mul,
            p.height_mul,
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq[:12]


def _pack_result(
    st: LayoutState,
    fin: dict[str, Any],
    *,
    action: str,
    recipe: str | None,
    recipe_id: str | None,
    preset: str,
    params: LayoutParams,
    tune: bool,
    tried: list[dict[str, Any]] | None,
    local: dict[str, Any] | None = None,
) -> dict[str, Any]:
    report = dict(fin.get("report") or {})
    meta = st.meta or {}
    return {
        "ok": True,
        "action": action,
        "recipe": recipe,
        "recipe_id": recipe_id,
        "preset": preset,
        "tune": bool(tune),
        "tried": tried,
        "local": local,
        "rings_mode": meta.get("rings_mode"),
        "min_rings": meta.get("min_rings"),
        "params_used": {
            k: getattr(params, k)
            for k in (
                "target_nn",
                "scale_cap",
                "target_util",
                "pack_min_scale",
                "island_pad_x",
                "island_pad_y",
                "cluster_gap",
                "x_gain",
                "lane",
            )
        },
        "node_count": len(st.positions),
        "positions": positions_for_api(st),
        "verdict": report.get("verdict"),
        "size": report.get("size"),
        "overlap": report.get("overlap"),
        "crossing": report.get("crossing"),
        "spacing": report.get("spacing"),
        "sparsity": report.get("sparsity"),
        "edges": report.get("edges"),
        "chains": report.get("chains"),
        "rings": report.get("rings"),
        "score": report.get("score"),
        "summary": fin.get("summary") or {},
        "guide": report.get("guide"),
    }


def _ensure_zero_overlap(st: LayoutState, params: LayoutParams) -> tuple[LayoutState, dict[str, Any]]:
    """If any overlap remains, surgical local pull-apart (not global scale)."""
    before = len(overlapping_nodes(st))
    if before == 0:
        return st, {"ran": False, "overlaps_before": 0, "overlaps_after": 0}
    op = fix_overlaps_local(st, params)
    after = len(overlapping_nodes(op.state))
    return op.state, {
        "ran": True,
        "overlaps_before": before,
        "overlaps_after": after,
        "moved_n": len(op.moved),
        "note": op.note,
    }


def run_layout_on_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    action: str = "layout",
    recipe: str = "rings",
    preset: str = "balanced",
    params: dict[str, Any] | None = None,
    tune: bool = False,
) -> dict[str, Any]:
    """Compute layout or local polish on existing positions. Does not PATCH."""
    action_key = str(action or "layout").strip().lower() or "layout"
    if action_key not in ACTIONS:
        raise ValueError(
            f"unknown_action:{action_key}; "
            f"allowed={','.join(ACTIONS)}"
        )
    if action_key == "resolve_overlaps":
        action_key = "fix_overlaps"
    base_params = build_params(preset=preset, overrides=params)
    st0 = build_state_from_nodes_edges(nodes, edges)
    park_phantom_nodes(st0)

    # Inject rigid/portal groups from staging membership when provided.
    if params:
        raw = params.get("_rigid_membership") or params.get("rigid_membership")
        if isinstance(raw, list):
            groups: list[dict[str, Any]] = []
            pairs: list[tuple[str, list[str]]] = []
            any_pivots = False
            for row in raw:
                if not isinstance(row, dict):
                    continue
                key = str(row.get("key") or "").strip()
                ids = [str(x) for x in (row.get("node_ids") or []) if str(x)]
                pivots = [str(x) for x in (row.get("pivots") or []) if str(x)]
                if not key or len(ids) < 2:
                    continue
                if pivots:
                    any_pivots = True
                groups.append({"key": key, "node_ids": ids, "pivots": pivots})
                pairs.append((key, ids))
            if groups:
                if not any_pivots:
                    groups = groups_from_membership(pairs)
                st0 = st0.copy()
                st0.meta = dict(st0.meta or {})
                st0.meta["compose_views"] = {
                    **(st0.meta.get("compose_views") or {}),
                    "rigid_groups": groups,
                }

    # --- local actions: keep current coordinates, do not rebuild skeleton ---
    if action_key == "fix_overlaps":
        op = fix_overlaps_local(st0, base_params)
        st, fix_meta = _ensure_zero_overlap(op.state, base_params)
        fin = score_state(st)
        return _pack_result(
            st,
            fin,
            action=action_key,
            recipe=None,
            recipe_id=None,
            preset=preset,
            params=base_params,
            tune=False,
            tried=None,
            local={"op": op.params, "ensure": fix_meta, "hotspots": len(hotspot_scopes(st0))},
        )

    if action_key == "untangle":
        knobs = untangle_params_from_overrides(params)
        op = untangle_crossings(st0, base_params, **knobs)
        # Origin shift only — soft_nn_scale can inflate an already-good human
        # layout; local jumps are capped inside untangle_crossings.
        st = normalize_origin(op.state, base_params).state
        # Overlap crush must not erase untangle gains.
        from netx_topology_mcp.layout_metrics import count_edge_crossings as _cx

        x_before_fix = _cx(st.positions, st.links)
        st2, fix_meta = _ensure_zero_overlap(st, base_params)
        x_after_fix = _cx(st2.positions, st2.links)
        if x_after_fix <= x_before_fix + 2:
            st = st2
        else:
            fix_meta = {
                **fix_meta,
                "reverted": True,
                "crossings_before": x_before_fix,
                "crossings_after": x_after_fix,
                "reason": "overlap_fix_raised_crossings",
            }
        fin = score_state(st)
        return _pack_result(
            st,
            fin,
            action=action_key,
            recipe=None,
            recipe_id=None,
            preset=preset,
            params=base_params,
            tune=False,
            tried=None,
            local={"op": op.params, "note": op.note, "meta": st.meta.get("untangle"), "ensure": fix_meta},
        )

    if action_key == "straighten_channels":
        knobs = straighten_params_from_overrides(params)
        op = straighten_channels_greedy(st0, base_params, **knobs)
        st, fix_meta = _ensure_zero_overlap(op.state, base_params)
        fin = score_state(st)
        return _pack_result(
            st,
            fin,
            action=action_key,
            recipe=None,
            recipe_id=None,
            preset=preset,
            params=base_params,
            tune=False,
            tried=None,
            local={
                "op": op.params,
                "note": op.note,
                "meta": st.meta.get("straighten_channels"),
                "ensure": fix_meta,
            },
        )

    if action_key == "layout_dual_unit":
        knobs = dual_unit_params_from_overrides(params)
        op = layout_dual_unit(
            st0,
            base_params,
            unit_id=knobs.get("unit_id"),
            portal_a=knobs.get("portal_a"),
            portal_b=knobs.get("portal_b"),
        )
        accepted = bool(op.params.get("accepted", False))
        # Always return best-effort dual geometry (even if accepted=False).
        # Do NOT run global overlap crush — it reintroduces crossings.
        st = op.state
        fin = score_state(st)
        return _pack_result(
            st,
            fin,
            action=action_key,
            recipe=None,
            recipe_id=None,
            preset=preset,
            params=base_params,
            tune=False,
            tried=None,
            local={
                "op": op.params,
                "note": op.note,
                "accepted": accepted,
                "meta": st.meta.get("layout_dual_unit"),
                "ensure": {"ran": False, "reason": "dual_unit_preserves_zero_cross"},
            },
        )

    if action_key == "orbit_sweep":
        knobs = orbit_params_from_overrides(params)
        do_round = bool(knobs.get("round"))
        node_id = str(knobs.get("node_id") or "").strip()
        frozen = knobs.get("frozen_ids")
        protect = knobs.get("protect_rigid", "off")
        if do_round:
            op = orbit_sweep_round(
                st0,
                params=base_params,
                top_n=int(knobs.get("top_n") or 12),
                max_degree=int(knobs.get("max_degree") or 9),
                max_jump=knobs.get("max_jump"),
                angle_step=knobs.get("angle_step"),
                nn_floor=float(knobs.get("nn_floor") or 36.0),
                min_angle_sep=float(knobs.get("min_angle_sep") or 35.0),
                protect_rigid=protect,
                frozen_ids=frozen,
                focus_ids=knobs.get("focus_ids"),
            )
            st = normalize_origin(op.state, base_params).state
            fin = score_state(st)
            return _pack_result(
                st,
                fin,
                action=action_key,
                recipe=None,
                recipe_id=None,
                preset=preset,
                params=base_params,
                tune=False,
                tried=None,
                local={
                    "op": op.params,
                    "note": op.note,
                    "meta": st.meta.get("orbit_sweep"),
                    "round": True,
                },
            )
        if not node_id:
            raise ValueError(
                "orbit_sweep_requires_node_id_or_round:"
                "params.node_id=… or params.round=true"
            )
        sweep = orbit_sweep_node(
            st0,
            node_id,
            params=base_params,
            max_jump=knobs.get("max_jump"),
            angle_step=knobs.get("angle_step"),
            nn_floor=float(knobs.get("nn_floor") or 36.0),
            min_angle_sep=float(knobs.get("min_angle_sep") or 35.0),
            cand_cap=int(knobs.get("cand_cap") or 280),
            protect_rigid=protect,
            frozen_ids=frozen,
            top_k=int(knobs.get("top_k") or 3),
        )
        if not sweep.get("ok"):
            fin = score_state(st0)
            return _pack_result(
                st0,
                fin,
                action=action_key,
                recipe=None,
                recipe_id=None,
                preset=preset,
                params=base_params,
                tune=False,
                tried=None,
                local={"op": sweep, "note": f"orbit_sweep:{sweep.get('error')}"},
            )
        # Suggest-only keeps coords. Apply path sets params.pick (HTTP injects
        # pick=1 on mode=apply) so the chosen candidate is in positions.
        apply_pick = knobs.get("pick")
        if apply_pick is not None:
            op = apply_orbit_pick(st0, sweep, pick=int(apply_pick or 1))
            st = normalize_origin(op.state, base_params).state
            fin = score_state(st)
            return _pack_result(
                st,
                fin,
                action=action_key,
                recipe=None,
                recipe_id=None,
                preset=preset,
                params=base_params,
                tune=False,
                tried=None,
                local={
                    "op": op.params,
                    "note": op.note,
                    "sweep": sweep,
                    "pick": int(apply_pick or 1),
                },
            )
        fin = score_state(st0)
        return _pack_result(
            st0,
            fin,
            action=action_key,
            recipe=None,
            recipe_id=None,
            preset=preset,
            params=base_params,
            tune=False,
            tried=None,
            local={
                "op": {
                    "node_id": sweep.get("node_id"),
                    "crossings_before": sweep.get("crossings_before"),
                    "candidates": sweep.get("candidates"),
                    "sampled": sweep.get("sampled"),
                    "improving_n": sweep.get("improving_n"),
                },
                "note": (
                    f"orbit_sweep top3 improving={sweep.get('improving_n')} "
                    f"sampled={sweep.get('sampled')}"
                ),
                "sweep": sweep,
                "hint": sweep.get("hint"),
            },
        )

    if action_key == "clear_edge_hits":
        knobs = clear_edge_params_from_overrides(params)
        preserve = bool(knobs.get("preserve_axis"))
        op = clear_edge_hits(
            st0,
            base_params,
            top_n=int(knobs.get("top_n") or 12),
            thr=float(knobs.get("thr") or 40.0),
            margin=float(knobs.get("margin") or 20.0),
            max_moves=int(knobs.get("max_moves") or 24),
            preserve_axis=preserve,
            pitch=knobs.get("pitch"),
            side=knobs.get("side"),
            rounds=int(knobs.get("rounds") or (6 if preserve else 1)),
        )
        st = normalize_origin(op.state, base_params).state
        from netx_topology_mcp.layout_metrics import count_edge_crossings as _cx

        x0 = _cx(st.positions, st.links)
        st2, fix_meta = _ensure_zero_overlap(st, base_params)
        x1 = _cx(st2.positions, st2.links)
        if x1 <= x0 + 2:
            st = st2
        else:
            fix_meta = {"ran": False, "reason": "overlap_fix_would_raise_crossings"}
        fin = score_state(st)
        return _pack_result(
            st,
            fin,
            action=action_key,
            recipe=None,
            recipe_id=None,
            preset=preset,
            params=base_params,
            tune=False,
            tried=None,
            local={
                "op": op.params,
                "note": op.note,
                "meta": st.meta.get("clear_edge_hits"),
                "ensure": fix_meta,
            },
        )

    if action_key == "polish_crossings":
        knobs = press_params_from_overrides(params)
        # Omit knobs so polish can auto-scale budgets on large E (MCP timeout).
        # Internal polish still calls straighten / press_hot_edges /
        # press_crossers / untangle.
        op = polish_crossings(
            st0,
            base_params,
            portal_ids=knobs.get("portal_ids"),
            straighten=knobs.get("straighten"),
            max_degree=int(knobs.get("max_degree") or 9),
            untangle_rounds=knobs.get("untangle_rounds"),
            top_n=knobs.get("top_n"),
            max_moves=knobs.get("max_moves"),
            max_sweeps=knobs.get("max_sweeps"),
        )
        st = normalize_origin(op.state, base_params).state
        # Overlap crush must not erase crossing gains.
        from netx_topology_mcp.layout_metrics import count_edge_crossings as _cx

        x0 = _cx(st.positions, st.links)
        st2, fix_meta = _ensure_zero_overlap(st, base_params)
        x1 = _cx(st2.positions, st2.links)
        if x1 <= x0 + 2:
            st = st2
        else:
            fix_meta = {
                **fix_meta,
                "reverted": True,
                "reason": "overlap_fix_raised_crossings",
            }
        fin = score_state(st)
        return _pack_result(
            st,
            fin,
            action=action_key,
            recipe=None,
            recipe_id=None,
            preset=preset,
            params=base_params,
            tune=False,
            tried=None,
            local={
                "op": op.params,
                "note": op.note,
                "meta": st.meta.get(action_key),
                "ensure": fix_meta,
            },
        )

    # --- full layout recipe ---
    recipe_id = resolve_recipe(recipe)
    tried: list[dict[str, Any]] = []
    best_st = None
    best_fin: dict[str, Any] | None = None
    best_params = base_params
    combos = _tune_grid(base_params) if tune else [base_params]

    for i, p in enumerate(combos):
        st, _trace, fin = run_recipe(st0, recipe_id, p)
        st, fix_meta = _ensure_zero_overlap(st, p)
        fin = score_state(st)
        row = {
            "i": i,
            "total": (fin.get("score") or {}).get("total"),
            "overlaps": fin.get("footprint_overlap_pairs"),
            "crossings": fin.get("edge_crossings"),
            "nn_p50": fin.get("nn_p50"),
            "util": fin.get("space_utilization"),
            "fix": fix_meta,
            "headline": ((fin.get("report") or {}).get("verdict") or {}).get("headline"),
        }
        tried.append(row)
        if best_fin is None or _rank_key(fin) < _rank_key(best_fin):
            best_st, best_fin, best_params = st, fin, p

    assert best_st is not None and best_fin is not None
    return _pack_result(
        best_st,
        best_fin,
        action=action_key,
        recipe=recipe if recipe in RECIPE_ALIASES else recipe_id,
        recipe_id=recipe_id,
        preset=preset,
        params=best_params,
        tune=bool(tune),
        tried=tried if tune else None,
        local={"auto_fix_overlaps": True},
    )


def list_layout_catalog() -> dict[str, Any]:
    from netx_topology_mcp import NETX_MCP_REV, __version__

    return {
        "version": __version__,
        "rev": NETX_MCP_REV,
        "actions": {
            "layout": "全图配方（骨架）；块内 pack，结束时局部解叠",
            "fix_overlaps": "只拉开当前重叠点（+1 跳邻居），不重排全图",
            "resolve_overlaps": "fix_overlaps 别名",
            "straighten_channels": (
                "拉直 deg≤2 通道（弦/横/纵）；仅全局交叉下降才接受该通道"
                "（params: step/min_len）"
            ),
            "layout_dual_unit": (
                "双门户单元美化（多走廊→平行 H/V 道；链→拉直）；"
                "单元内交叉必须为 0 才接受（params: unit_id）"
            ),
            "untangle": (
                "贪心挪低度数点降交叉；默认 protect_rigid=portals "
                "只冻共享门户，走廊/触手可动（all=全冻，off=不冻）；"
                "可用 focus_ids / source_view_ids"
            ),
            "polish_crossings": (
                "一键压交叉：straighten→press_hot_edges→press_crossers→"
                "untangle(portals)；优先用 source_view_ids 冻门户 "
                "（勿写临时 py）"
            ),
            "clear_edge_hits": (
                "把贴在非关联边上的网元沿垂直方向弹开（直角偏好 H/V）；"
                "门控：不增交叉、不增重叠。"
                "params: top_n/thr/margin/max_moves"
            ),
            "orbit_sweep": (
                "压交叉（默认可动门户）：以网元为圆心不定长扫角 top-3；"
                "preview+node_id / apply+pick / round=true；"
                "protect_rigid 默认 off（portals/all 可恢复刚体冻）"
            ),
            "job_status": (
                "轮询后台 job：params.job_id；返回 progress.phase/pct、elapsed_ms、"
                "heartbeat_age_ms、stale（软警告，不改状态）"
            ),
            "job_cancel": (
                "协作式取消后台 job：params.job_id；下一检查点退出；"
                "若已 PATCH 则保留写入并回报 applied"
            ),
            "move_nodes": (
                "成员迁移（双向）：params.fabric_node_ids 从 source_view_id→view_id；"
                "默认 remove_from_source；对调两 view 即回迁；"
                "copy_positions|park；mode=preview|apply"
            ),
            "sink_nodes": "move_nodes 别名",
        },
        "recipes": {
            "rings": "环+链花瓣；core_bar 时梁优先（多 CN 不塞进 AN 列）",
            "corridor": "Tutte 走廊骨架，交叉常更少，易偏空——稳基线",
            "compact": "走廊 + 分块 pack（禁止全局压扁）——稳基线",
            "unstick": "corridor 后再强解重叠",
        },
        "presets": {
            "loose": "偏疏、交叉友好",
            "balanced": "默认折中",
            "dense": "偏紧、抬 util",
        },
        "modes": {
            "preview": "只算分+坐标，不写库",
            "apply": "PATCH 到 view_id（有残留重叠则拒绝落笔）",
        },
        "workflow": (
            "主路径：analyze(structure) → layout_dual_unit（或小图 layout "
            "compact|corridor|rings）→ sinkTopologyDualUnits / move_nodes(park) → "
            "orbit_sweep(round) → polish_crossings → clear_edge_hits → "
            "手拖微调。禁止临时 py 算坐标。"
        ),
    }
