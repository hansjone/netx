"""Block / scope selection — ops act only on active nodes."""

from __future__ import annotations

from typing import Any, Iterable

from netx_topology_mcp.layout_ops.graph_util import bbox, connected_components
from netx_topology_mcp.layout_ops.state import LayoutState, OpResult


def active_nodes(state: LayoutState) -> set[str]:
    """Nodes currently in scope (all if scope is None/empty meaning unrestricted)."""
    if state.scope is None:
        return set(state.positions.keys())
    return {n for n in state.scope if n in state.positions}


def movable_nodes(state: LayoutState, *, respect_pins: bool = True) -> set[str]:
    """Nodes an op may move: in scope, not hard agg/core, optionally not soft-pinned."""
    act = active_nodes(state)
    hard = {n for n in act if state.layers.get(n) in ("agg", "core")}
    out = act - hard
    if respect_pins:
        out -= set(state.pinned)
    return out


def list_blocks(
    state: LayoutState,
    *,
    mode: str = "component",
) -> list[set[str]]:
    """Partition graph into blocks for per-block recipes.

    Modes:
      - component: hard CC (default; existing recipes)
      - layer / bbox_quad: coarse buckets
      - hub_territory: soft blocks by hub-seeded BFS (our algo)
      - leiden / soft: hub territories + optional igraph Leiden on leftovers
    """
    ids = set(state.positions.keys())
    m = (mode or "component").strip().lower()
    if m == "auto":
        from netx_topology_mcp.layout_ops.partition import resolve_block_mode

        m = resolve_block_mode(state, "auto")
    if m == "component":
        active = {
            n
            for n in ids
            if state.layers.get(n) in ("core", "agg", "access")
        }
        comps = connected_components(active, state.adj)
        blocks = [set(c) for c in comps if c]
        # orphans / other
        rest = ids - {n for b in blocks for n in b}
        if rest:
            blocks.append(rest)
        return blocks
    if m in {"hub_territory", "leiden", "soft"}:
        from netx_topology_mcp.layout_ops.partition import (
            partition_soft_blocks,
            soft_blocks_as_sets,
        )

        return soft_blocks_as_sets(partition_soft_blocks(state, mode=m))
    if m == "layer":
        by: dict[str, set[str]] = {}
        for n in ids:
            by.setdefault(state.layers.get(n, "other"), set()).add(n)
        return [by[k] for k in sorted(by.keys()) if by[k]]
    if m == "bbox_quad":
        if not ids:
            return []
        x0, y0, x1, y1 = bbox(state.positions)
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        quads: list[set[str]] = [set() for _ in range(4)]
        for n, (x, y) in state.positions.items():
            qi = (0 if x < mx else 1) + (0 if y < my else 2)
            quads[qi].add(n)
        return [q for q in quads if q]
    # single full block
    return [ids] if ids else []


def select_scope(
    state: LayoutState,
    *,
    mode: str = "all",
    node_ids: Iterable[str] | None = None,
    component_index: int | None = None,
    layer: str | None = None,
    bbox_region: tuple[float, float, float, float] | None = None,
) -> OpResult:
    """Set state.scope. mode=all clears scope (full graph)."""
    st = state.copy()
    pos = st.positions
    if mode == "all" or (mode == "ids" and not node_ids):
        st.scope = None
        return OpResult(
            state=st, moved=set(), op="select_scope", params={"mode": "all"}, note="scope=all"
        )

    if mode == "ids" and node_ids is not None:
        scope = {n for n in node_ids if n in pos}
    elif mode == "component":
        blocks = list_blocks(st, mode="component")
        idx = 0 if component_index is None else int(component_index)
        scope = blocks[idx] if 0 <= idx < len(blocks) else set()
    elif mode == "layer":
        lyr = layer or "access"
        scope = {n for n in pos if st.layers.get(n) == lyr}
    elif mode == "bbox" and bbox_region is not None:
        x0, y0, x1, y1 = bbox_region
        scope = {
            n
            for n, (x, y) in pos.items()
            if x0 <= x <= x1 and y0 <= y <= y1
        }
    elif mode == "bbox_quad":
        blocks = list_blocks(st, mode="bbox_quad")
        idx = 0 if component_index is None else int(component_index)
        scope = blocks[idx] if 0 <= idx < len(blocks) else set()
    else:
        scope = set(pos.keys())

    st.scope = scope
    return OpResult(
        state=st,
        moved=set(),
        op="select_scope",
        params={"mode": mode, "scope_n": len(scope), "component_index": component_index},
        note=f"scope={mode} n={len(scope)}",
    )


def map_blocks(
    state: LayoutState,
    *,
    mode: str = "component",
) -> OpResult:
    """Record block partition in meta (for agents / foreach). Does not change scope."""
    st = state.copy()
    blocks = list_blocks(st, mode=mode)
    st.meta["blocks"] = [sorted(b) for b in blocks]
    st.meta["block_mode"] = mode
    return OpResult(
        state=st,
        moved=set(),
        op="map_blocks",
        params={"mode": mode, "blocks_n": len(blocks), "sizes": [len(b) for b in blocks]},
        note=f"blocks={len(blocks)} mode={mode}",
    )
