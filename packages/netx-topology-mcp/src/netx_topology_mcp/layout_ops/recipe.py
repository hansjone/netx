"""Compose atomic ops into multipass recipes (supports per-block scope)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from netx_topology_mcp.layout_ops.score import score_op, score_state
from netx_topology_mcp.layout_ops.scope import list_blocks, map_blocks, select_scope
from netx_topology_mcp.layout_ops.sides import place_side_branches
from netx_topology_mcp.layout_ops.skeleton import build_skeleton
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.hotspots import fix_overlaps_local, relax_hotspots
from netx_topology_mcp.layout_ops.rings import build_ring_skeleton
from netx_topology_mcp.layout_ops.partition import pack_soft_blocks
from netx_topology_mcp.layout_ops.transforms import (
    enforce_min_gap,
    explode_clusters,
    normalize_origin,
    pack_utilization,
    resolve_overlaps,
    scale_edge_axes,
    scale_region,
    select_pins,
    soft_nn_scale,
)

OpFn = Callable[..., OpResult]

REGISTRY: dict[str, OpFn] = {
    "build_skeleton": build_skeleton,
    "build_ring_skeleton": build_ring_skeleton,
    "place_side_branches": place_side_branches,
    "select_pins": select_pins,
    "select_scope": select_scope,
    "map_blocks": map_blocks,
    "scale_region": scale_region,
    "scale_edge_axes": scale_edge_axes,
    "pack_utilization": pack_utilization,
    "pack_soft_blocks": pack_soft_blocks,
    "resolve_overlaps": resolve_overlaps,
    "explode_clusters": explode_clusters,
    "enforce_min_gap": enforce_min_gap,
    "soft_nn_scale": soft_nn_scale,
    "normalize_origin": normalize_origin,
    "fix_overlaps_local": fix_overlaps_local,
    "relax_hotspots": relax_hotspots,
    "score": score_op,
}


def smd_corridor_v1_passes() -> list[dict[str, Any]]:
    """Global structure, then per-component explode+resolve (scoped)."""
    return [
        {"op": "build_skeleton"},
        {"op": "place_side_branches"},
        {"op": "select_scope", "kwargs": {"mode": "all"}},
        {"op": "scale_edge_axes"},
        {"op": "soft_nn_scale"},
        {"op": "map_blocks", "kwargs": {"mode": "component"}},
        # Expanded at runtime by foreach_blocks
        {
            "op": "foreach_blocks",
            "kwargs": {
                "mode": "component",
                "passes": [
                    {
                        "op": "explode_clusters",
                        "kwargs": {"thr": 20.0, "gap": 80.0, "axis": "perp"},
                    },
                    {"op": "resolve_overlaps", "kwargs": {"mode": "lateral"}},
                ],
            },
        },
        {"op": "select_scope", "kwargs": {"mode": "all"}},
        {"op": "normalize_origin"},
        {"op": "score"},
    ]


def smd_corridor_unstick_v1_passes() -> list[dict[str, Any]]:
    """v1 + second per-block free resolve for stubborn overlaps."""
    return [
        *smd_corridor_v1_passes()[:-2],
        {
            "op": "foreach_blocks",
            "kwargs": {
                "mode": "component",
                "passes": [
                    {"op": "resolve_overlaps", "kwargs": {"mode": "free"}},
                ],
            },
        },
        {"op": "select_scope", "kwargs": {"mode": "all"}},
        {"op": "normalize_origin"},
        {"op": "score"},
    ]


def agg_rings_v1_passes() -> list[dict[str, Any]]:
    """Rings recipe: dual-hub min-rings when eligible, else UME petals.

    Light overlap fix only — avoid explode that destroys ring geometry.
    """
    return [
        {"op": "build_ring_skeleton"},
        {"op": "select_pins", "kwargs": {"mode": "agg"}},
        {"op": "select_scope", "kwargs": {"mode": "all"}},
        {"op": "fix_overlaps_local"},
        {"op": "normalize_origin"},
        {"op": "score"},
    ]


def smd_corridor_compact_v1_passes() -> list[dict[str, Any]]:
    """Corridor skeleton, then pack/unstick **per component** — no global crush.

    Global isotropic pack destroys crossings; islands are compacted in-place.
    Ends with surgical local overlap fix.
    """
    return [
        {"op": "build_skeleton"},
        {"op": "place_side_branches"},
        {"op": "select_scope", "kwargs": {"mode": "all"}},
        {"op": "scale_edge_axes"},
        {"op": "soft_nn_scale"},
        {"op": "map_blocks", "kwargs": {"mode": "component"}},
        {
            "op": "foreach_blocks",
            "kwargs": {
                "mode": "component",
                "min_size": 3,
                "passes": [
                    {"op": "pack_utilization"},
                    {
                        "op": "explode_clusters",
                        "kwargs": {"thr": 8.0, "gap": 35.0, "axis": "along"},
                    },
                    {"op": "resolve_overlaps", "kwargs": {"mode": "lateral"}},
                ],
            },
        },
        {"op": "select_scope", "kwargs": {"mode": "all"}},
        {"op": "fix_overlaps_local"},
        {"op": "normalize_origin"},
        {"op": "score"},
    ]


RECIPES: dict[str, Callable[[], list[dict[str, Any]]]] = {
    "smd_corridor_v1": smd_corridor_v1_passes,
    "smd_corridor_unstick_v1": smd_corridor_unstick_v1_passes,
    "smd_corridor_compact_v1": smd_corridor_compact_v1_passes,
    "agg_rings_v1": agg_rings_v1_passes,
}


def _apply_op(
    st: LayoutState,
    name: str,
    params: LayoutParams,
    kwargs: dict[str, Any],
    *,
    ume_reference: bool,
) -> OpResult:
    if name == "score":
        return score_op(st, ume_reference=ume_reference)
    if name == "select_pins":
        return select_pins(st, **kwargs)
    if name == "select_scope":
        return select_scope(st, **kwargs)
    if name == "map_blocks":
        return map_blocks(st, **kwargs)
    if name == "pack_soft_blocks":
        return pack_soft_blocks(st, params, **kwargs)
    if name == "scale_region":
        return scale_region(st, params, **kwargs)
    if name == "resolve_overlaps":
        return resolve_overlaps(st, params, **kwargs)
    if name == "explode_clusters":
        return explode_clusters(st, params, **kwargs)
    if name == "enforce_min_gap":
        return enforce_min_gap(st, params, **kwargs)
    if name == "fix_overlaps_local":
        return fix_overlaps_local(st, params)
    if name == "relax_hotspots":
        return relax_hotspots(st, params)
    if name == "build_ring_skeleton":
        return build_ring_skeleton(st, params)
    fn = REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"unknown op: {name}")
    return fn(st, params)


def _trace_entry(
    i: int,
    op: str,
    note: str,
    params: dict[str, Any],
    moved_n: int,
    pinned_n: int,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    m = metrics or {}
    return {
        "i": i,
        "op": op,
        "note": note,
        "params": params,
        "moved_n": moved_n,
        "pinned_n": pinned_n,
        "edge_crossings": m.get("edge_crossings"),
        "nn_p50": m.get("nn_p50"),
        "footprint_overlap_pairs": m.get("footprint_overlap_pairs"),
        "label_overlap_pairs": m.get("label_overlap_pairs"),
        "space_utilization": m.get("space_utilization"),
        "bbox": m.get("bbox"),
        "grade": (m.get("grade") or {}).get("overall") if m else None,
    }


def _expand_foreach(
    st: LayoutState,
    kwargs: dict[str, Any],
    params: LayoutParams,
    *,
    ume_reference: bool,
    base_i: int,
) -> tuple[LayoutState, list[dict[str, Any]]]:
    """Run sub-passes once per block. Score only once after all blocks (fast)."""
    mode = str(kwargs.get("mode") or "component")
    sub_passes: list[dict[str, Any]] = list(kwargs.get("passes") or [])
    min_size = int(kwargs.get("min_size") or 2)
    blocks = list_blocks(st, mode=mode)
    # Record resolved mode (auto → hub_territory|component) for agents/trace.
    from netx_topology_mcp.layout_ops.partition import resolve_block_mode

    resolved_mode = resolve_block_mode(st, mode) if mode == "auto" else mode
    st = st.copy()
    st.meta["block_mode"] = resolved_mode
    st.meta["blocks"] = [sorted(b) for b in blocks]
    trace: list[dict[str, Any]] = []
    step_i = base_i
    total_moved = 0

    for bi, block in enumerate(blocks):
        if len(block) < min_size:
            continue
        st = select_scope(st, mode="ids", node_ids=block).state
        block_moved = 0
        for step in sub_passes:
            name = str(step.get("op") or "")
            kw = dict(step.get("kwargs") or {})
            result = _apply_op(st, name, params, kw, ume_reference=ume_reference)
            st = result.state
            block_moved += len(result.moved)
            total_moved += len(result.moved)
        trace.append(
            _trace_entry(
                step_i,
                "foreach_block",
                f"block[{bi}] n={len(block)} moved≈{block_moved}",
                {"block_index": bi, "scope_n": len(block), "mode": mode},
                block_moved,
                len(st.pinned),
            )
        )
        step_i += 1

    st = select_scope(st, mode="all").state
    metrics = score_state(st, ume_reference=ume_reference)
    trace.append(
        _trace_entry(
            step_i,
            "foreach_blocks",
            f"done blocks={len(blocks)} moved≈{total_moved}",
            {"mode": mode, "blocks_n": len(blocks), "moved_n": total_moved},
            total_moved,
            len(st.pinned),
            metrics,
        )
    )
    return st, trace


def run_recipe(
    state: LayoutState,
    recipe: str | list[dict[str, Any]] = "smd_corridor_v1",
    params: LayoutParams | None = None,
    *,
    ume_reference: bool = False,
) -> tuple[LayoutState, list[dict[str, Any]], dict[str, Any]]:
    """Run passes; return final state, per-pass trace, final score."""
    params = params or LayoutParams()
    passes = RECIPES[recipe]() if isinstance(recipe, str) else recipe
    st = state.copy()
    trace: list[dict[str, Any]] = []
    i = 0

    for step in passes:
        name = str(step.get("op") or "")
        kwargs = dict(step.get("kwargs") or {})
        if name == "foreach_blocks":
            st, sub = _expand_foreach(
                st, kwargs, params, ume_reference=ume_reference, base_i=i
            )
            trace.extend(sub)
            i = (trace[-1]["i"] + 1) if trace else i + 1
            continue

        result = _apply_op(st, name, params, kwargs, ume_reference=ume_reference)
        st = result.state
        # Lightweight steps skip full score; score at structural checkpoints + end.
        want_score = name in {
            "build_skeleton",
            "place_side_branches",
            "soft_nn_scale",
            "normalize_origin",
            "score",
            "pack_utilization",
        }
        metrics = score_state(st, ume_reference=ume_reference) if want_score else None
        trace.append(
            _trace_entry(
                i,
                result.op,
                result.note,
                result.params,
                len(result.moved),
                len(st.pinned),
                metrics,
            )
        )
        i += 1

    final = score_state(st, ume_reference=ume_reference)
    final["recipe"] = recipe if isinstance(recipe, str) else "custom"
    final["params"] = asdict(params)
    final["passes"] = len(trace)
    return st, trace, final


def positions_for_api(state: LayoutState) -> list[dict[str, float | str]]:
    return [
        {
            "fabric_node_id": n,
            "x": round(state.positions[n][0], 1),
            "y": round(state.positions[n][1], 1),
        }
        for n in state.positions
    ]
