"""Local hotspots: overlaps / dense cells — scope without touching whole canvas."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import replace
from typing import Any

from netx_topology_mcp.layout_metrics import REC_CENTER_DX, REC_CENTER_DY, node_footprint
from netx_topology_mcp.layout_ops.scope import select_scope
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult
from netx_topology_mcp.layout_ops.transforms import (
    explode_clusters,
    pack_utilization,
    resolve_overlaps,
)


def _aabb_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def overlapping_nodes(state: LayoutState) -> set[str]:
    """Nodes whose icon+label footprints collide."""
    ids = list(state.positions)
    fps: dict[str, tuple[float, float, float, float]] = {}
    for n in ids:
        x, y = state.positions[n]
        fx0, fy0, fx1, fy1 = node_footprint(state.names.get(n, n))
        fps[n] = (x + fx0, y + fy0, x + fx1, y + fy1)
    hit: set[str] = set()
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if _aabb_overlap(fps[a], fps[b]):
                hit.add(a)
                hit.add(b)
    return hit


def expand_hops(
    seeds: set[str], adj: dict[str, set[str]], *, hops: int = 1
) -> set[str]:
    out = set(seeds)
    frontier = set(seeds)
    for _ in range(max(0, hops)):
        nxt: set[str] = set()
        for u in frontier:
            nxt |= set(adj.get(u, ()))
        nxt -= out
        out |= nxt
        frontier = nxt
        if not frontier:
            break
    return out


def dense_blocks(
    state: LayoutState,
    *,
    cell_w: float = REC_CENTER_DX,
    cell_h: float = REC_CENTER_DY,
    min_count: int = 5,
) -> list[set[str]]:
    """Grid cells with too many centers — local overcrowding."""
    if len(state.positions) < min_count:
        return []
    buckets: dict[tuple[int, int], set[str]] = defaultdict(set)
    for n, (x, y) in state.positions.items():
        buckets[(int(math.floor(x / cell_w)), int(math.floor(y / cell_h)))].add(n)
    return [nodes for nodes in buckets.values() if len(nodes) >= min_count]


def close_clusters(
    state: LayoutState, *, thr: float = 100.0
) -> list[set[str]]:
    """Union-find clusters of centers closer than thr (each cluster = one local scope)."""
    ids = list(state.positions)
    parent = {n: n for n in ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def uni(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    cell = max(thr, 40.0)
    buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    for n, (x, y) in state.positions.items():
        buckets[(int(x // cell), int(y // cell))].append(n)
    for n, (x, y) in state.positions.items():
        cx, cy = int(x // cell), int(y // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for m in buckets[(cx + dx, cy + dy)]:
                    if m <= n:
                        continue
                    mx, my = state.positions[m]
                    if math.hypot(x - mx, y - my) < thr:
                        uni(n, m)
    groups: dict[str, set[str]] = defaultdict(set)
    for n in ids:
        groups[find(n)].add(n)
    return [g for g in groups.values() if len(g) >= 2]


def sprawled_component_scopes(
    state: LayoutState,
    *,
    target_util: float,
    min_size: int = 8,
    max_size: int = 80,
) -> list[set[str]]:
    """Graph components with low util — skip huge ones (those need layout, not local pack)."""
    from netx_topology_mcp.layout_ops.graph_util import bbox, connected_components

    comps = connected_components(set(state.positions), state.adj)
    out: list[set[str]] = []
    tile = REC_CENTER_DX * REC_CENTER_DY
    for c in comps:
        if len(c) < min_size or len(c) > max_size:
            continue
        sub = {n: state.positions[n] for n in c}
        x0, y0, x1, y1 = bbox(sub)
        area = max((x1 - x0) * (y1 - y0), 1.0)
        util = len(c) * tile / area
        if util < target_util:
            out.append(set(c))
    return out


def _merge_scopes(scopes: list[set[str]], *, max_size: int = 64) -> list[set[str]]:
    """Merge intersecting scopes but never grow past max_size."""
    merged: list[set[str]] = []
    for s in scopes:
        if not s or len(s) < 2:
            continue
        if len(s) > max_size:
            continue
        hit = None
        for m in merged:
            if s & m and len(m | s) <= max_size:
                hit = m
                break
        if hit is None:
            merged.append(set(s))
        else:
            hit |= s
    return [m for m in merged if len(m) >= 2]


def hotspot_scopes(
    state: LayoutState,
    *,
    hops: int = 1,
    dense_min: int = 5,
    close_thr: float = 100.0,
    target_util: float | None = None,
    max_scope: int = 64,
) -> list[set[str]]:
    """Local scopes only: overlap / close clusters / dense cells / small sprawled comps."""
    seeds_list: list[set[str]] = []
    ov = overlapping_nodes(state)
    if ov and len(ov) <= max_scope:
        seeds_list.append(ov)
    seeds_list.extend(close_clusters(state, thr=close_thr))
    seeds_list.extend(dense_blocks(state, min_count=dense_min))
    if target_util is not None:
        seeds_list.extend(
            sprawled_component_scopes(
                state, target_util=target_util, max_size=max_scope
            )
        )

    if not seeds_list:
        return []

    expanded: list[set[str]] = []
    for s in seeds_list:
        if len(s) < 2 or len(s) > max_scope:
            continue
        e = expand_hops(s, state.adj, hops=hops) & set(state.positions)
        if len(e) > max_scope:
            e = set(s)  # drop hop expansion if it balloons
        expanded.append(e)
    return _merge_scopes(expanded, max_size=max_scope)


def fix_overlaps_local(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """Pull apart only overlapping nodes (+1-hop). No global scale/pack."""
    from netx_topology_mcp.layout_metrics import count_edge_crossings

    params = params or LayoutParams()
    st = state.copy()
    seeds = overlapping_nodes(st)
    if not seeds:
        return OpResult(
            state=st,
            moved=set(),
            op="fix_overlaps_local",
            params={"seeds": 0},
            note="no overlaps",
        )

    c0 = count_edge_crossings(st.positions, st.links)
    baseline = st.copy()
    scope = expand_hops(seeds, st.adj, hops=1) & set(st.positions)
    # Prefer moving smaller set; keep rest as obstacles
    st = select_scope(st, mode="ids", node_ids=scope).state
    p = replace(
        params,
        overlap_iters=max(220, int(params.overlap_iters)),
        overlap_step=max(4.0, float(params.overlap_step)),
    )
    moved: set[str] = set()
    r1 = resolve_overlaps(st, p, mode="free")
    st, moved = r1.state, moved | r1.moved
    r2 = resolve_overlaps(st, p, mode="lateral")
    st, moved = r2.state, moved | r2.moved

    # Still stuck? explode tiny coincidences inside scope, then resolve again
    still = overlapping_nodes(st) & scope
    if still:
        st = select_scope(st, mode="ids", node_ids=expand_hops(still, st.adj, hops=1)).state
        r3 = explode_clusters(st, p, thr=12.0, gap=max(40.0, p.cluster_gap), axis="along")
        st, moved = r3.state, moved | r3.moved
        r4 = resolve_overlaps(st, p, mode="free")
        st, moved = r4.state, moved | r4.moved
        r5 = resolve_overlaps(st, p, mode="lateral")
        st, moved = r5.state, moved | r5.moved

    st = select_scope(st, mode="all").state
    c1 = count_edge_crossings(st.positions, st.links)
    # Free/explode can shred metro geometry — fall back to lateral-only unstick.
    if c1 > max(c0 * 1.35, c0 + 60):
        st = baseline
        st = select_scope(st, mode="ids", node_ids=scope).state
        moved = set()
        p2 = replace(p, overlap_iters=max(320, int(p.overlap_iters)))
        r = resolve_overlaps(st, p2, mode="lateral")
        st, moved = r.state, r.moved
        still = overlapping_nodes(st) & scope
        if still:
            st = select_scope(
                st, mode="ids", node_ids=expand_hops(still, st.adj, hops=1)
            ).state
            r = resolve_overlaps(st, p2, mode="lateral")
            st, moved = r.state, moved | r.moved
        st = select_scope(st, mode="all").state
        c1 = count_edge_crossings(st.positions, st.links)

    left = overlapping_nodes(st)
    return OpResult(
        state=st,
        moved=moved,
        op="fix_overlaps_local",
        params={
            "seeds": len(seeds),
            "scope_n": len(scope),
            "moved_n": len(moved),
            "overlaps_left": len(left),
            "crossings_before": c0,
            "crossings_after": c1,
        },
        note=f"local unstick seeds={len(seeds)} left={len(left)} cross={c0}->{c1}",
    )


def _block_util(state: LayoutState, block: set[str]) -> float:
    from netx_topology_mcp.layout_ops.graph_util import bbox

    if len(block) < 2:
        return 1.0
    sub = {n: state.positions[n] for n in block if n in state.positions}
    if len(sub) < 2:
        return 1.0
    x0, y0, x1, y1 = bbox(sub)
    area = max((x1 - x0) * (y1 - y0), 1.0)
    return len(sub) * REC_CENTER_DX * REC_CENTER_DY / area


def _block_has_close(state: LayoutState, block: set[str], thr: float) -> bool:
    ids = [n for n in block if n in state.positions]
    for i, a in enumerate(ids):
        ax, ay = state.positions[a]
        for b in ids[i + 1 :]:
            bx, by = state.positions[b]
            if math.hypot(ax - bx, ay - by) < thr:
                return True
    return False


def relax_hotspots(
    state: LayoutState, params: LayoutParams | None = None
) -> OpResult:
    """Per-hotspot local polish — never global compress.

    - Sprawled block (low util): pack + resolve only
    - Crowded block (close pairs / overlaps): mild explode + resolve (no pack)
    """
    params = params or LayoutParams()
    st = state.copy()
    scopes = hotspot_scopes(
        st, hops=1, dense_min=4, close_thr=100.0, target_util=params.target_util
    )
    if not scopes:
        from netx_topology_mcp.layout_ops.scope import list_blocks

        scopes = [b for b in list_blocks(st, mode="component") if 4 <= len(b) <= 64]

    scopes = sorted(scopes, key=len, reverse=True)[:24]
    total_moved: set[str] = set()
    block_notes: list[dict[str, Any]] = []
    close_thr = 55.0  # only true local crowding, not soft nn band

    for i, block in enumerate(scopes):
        if len(block) < 2:
            continue
        st = select_scope(st, mode="ids", node_ids=block).state
        before_ov = len(overlapping_nodes(st) & block)
        util = _block_util(st, block)
        crowded = before_ov > 0 or _block_has_close(st, block, close_thr)
        sprawled = util < params.target_util
        mode = "pack" if sprawled and not crowded else ("unstick" if crowded else "skip")
        moved_here: set[str] = set()

        if mode == "unstick":
            r1 = explode_clusters(
                st, params, thr=close_thr, gap=max(40.0, params.cluster_gap), axis="along"
            )
            st = r1.state
            moved_here |= r1.moved
            r2 = resolve_overlaps(st, params, mode="lateral")
            st = r2.state
            moved_here |= r2.moved
            r3 = resolve_overlaps(st, params, mode="free")
            st = r3.state
            moved_here |= r3.moved
        elif mode == "pack":
            r1 = pack_utilization(st, params)
            st = r1.state
            moved_here |= r1.moved
            r2 = resolve_overlaps(st, params, mode="lateral")
            st = r2.state
            moved_here |= r2.moved

        total_moved |= moved_here
        after_ov = len(overlapping_nodes(st) & block)
        block_notes.append(
            {
                "i": i,
                "n": len(block),
                "mode": mode,
                "util": round(util, 4),
                "ov_before": before_ov,
                "ov_after": after_ov,
                "moved": len(moved_here),
            }
        )

    st = select_scope(st, mode="all").state
    fin = fix_overlaps_local(st, params)
    st = fin.state
    total_moved |= fin.moved
    return OpResult(
        state=st,
        moved=total_moved,
        op="relax_hotspots",
        params={"blocks": block_notes, "blocks_n": len(scopes), "moved_n": len(total_moved)},
        note=f"relax blocks={len(scopes)} moved={len(total_moved)}",
    )
