"""Soft partitioning for layout init — own algorithms first, igraph optional.

Design (partial igraph use):
  1. Hard blocks = connected components (existing ``list_blocks(component)``).
  2. Soft blocks inside a giant CC = **hub-seeded territory** (our BFS; default).
  3. Optional igraph:
     - ``leiden``: only refine leftover / oversized mesh pockets (hubs pinned).
     - ``fr_pack``: place soft-block *centers* on a supergraph (not final metro coords).
  4. Per-block geometry stays ours: beam / stub petals / chain spine / ume_petals.

Never use force-directed as the final access layout.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Iterable

from netx_topology_mcp.layout_ops.graph_util import connected_components
from netx_topology_mcp.layout_ops.state import LayoutParams, LayoutState, OpResult

# Preferred soft-block step when packing centers (px).
_BLOCK_GAP = 420.0


def resolve_block_mode(state: LayoutState, mode: str | None = None) -> str:
    """Resolve ``auto`` → hub_territory when one giant CC has multiple hubs."""
    m = (mode or "component").strip().lower()
    if m != "auto":
        return m
    active = {
        n
        for n in state.positions
        if state.layers.get(n) in ("core", "agg", "access")
    }
    comps = connected_components(active, state.adj) if active else []
    hubs = pick_hub_seeds(state)
    big = max((len(c) for c in comps), default=0)
    if len(comps) <= 2 and big >= 40 and len(hubs) >= 2:
        return "hub_territory"
    return "component"


def igraph_available() -> bool:
    try:
        import igraph  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass(frozen=True)
class SoftBlock:
    block_id: int
    hub_id: str | None
    method: str
    node_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "hub_id": self.hub_id,
            "method": self.method,
            "size": len(self.node_ids),
            "node_ids": list(self.node_ids)[:80],
            "node_count": len(self.node_ids),
        }


def pick_hub_seeds(
    state: LayoutState,
    *,
    min_degree: int = 2,
    max_hubs: int = 24,
) -> list[str]:
    """core/agg hubs by degree; fall back to high-degree nodes."""
    ids = list(state.positions.keys())
    layered = [
        n
        for n in ids
        if state.layers.get(n) in ("core", "agg")
        and len(state.adj.get(n, ())) >= min_degree
    ]
    if layered:
        layered.sort(
            key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n))
        )
        return layered[:max_hubs]
    ranked = sorted(
        ids,
        key=lambda n: (-len(state.adj.get(n, ())), state.names.get(n, n)),
    )
    return [n for n in ranked if len(state.adj.get(n, ())) >= max(3, min_degree)][
        :max_hubs
    ]


def hub_territory_partition(
    state: LayoutState,
    *,
    hubs: Iterable[str] | None = None,
) -> list[SoftBlock]:
    """Multi-source BFS from each hub's 1-hop stubs — one soft block per hub.

    Hubs themselves are included in their block. Nodes claimed by the nearest
    stub (first visit). Leftovers become CC soft-blocks (``method=leftover``).
    """
    hub_list = [h for h in (hubs or pick_hub_seeds(state)) if h in state.positions]
    if not hub_list:
        comps = connected_components(set(state.positions), state.adj)
        return [
            SoftBlock(i, None, "component", tuple(sorted(c)))
            for i, c in enumerate(comps)
            if c
        ]

    pinned = set(hub_list)
    owner: dict[str, str] = {}
    q: deque[str] = deque()
    for h in hub_list:
        for stub in sorted(state.adj.get(h, ()), key=lambda i: state.names.get(i, i)):
            if stub in pinned or stub in owner:
                continue
            owner[stub] = h
            q.append(stub)
    while q:
        u = q.popleft()
        h = owner[u]
        for v in state.adj.get(u, ()):
            if v in pinned or v in owner:
                continue
            owner[v] = h
            q.append(v)

    by_hub: dict[str, list[str]] = {h: [h] for h in hub_list}
    for nid, h in owner.items():
        by_hub.setdefault(h, [h]).append(nid)

    blocks: list[SoftBlock] = []
    claimed = set(hub_list) | set(owner)
    for h in hub_list:
        nodes = tuple(sorted(set(by_hub.get(h, [h])), key=lambda i: state.names.get(i, i)))
        blocks.append(SoftBlock(len(blocks), h, "hub_territory", nodes))

    rest = set(state.positions) - claimed
    if rest:
        for comp in connected_components(rest, state.adj):
            if not comp:
                continue
            blocks.append(
                SoftBlock(
                    len(blocks),
                    None,
                    "leftover",
                    tuple(sorted(comp, key=lambda i: state.names.get(i, i))),
                )
            )
    return blocks


def _leiden_membership(
    node_ids: list[str],
    adj: dict[str, set[str]],
    *,
    resolution: float = 1.0,
) -> list[int] | None:
    if not igraph_available() or len(node_ids) < 6:
        return None
    import igraph as ig

    idx = {n: i for i, n in enumerate(node_ids)}
    edges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for a in node_ids:
        for b in adj.get(a, ()):
            if b not in idx or a >= b:
                continue
            ea, eb = idx[a], idx[b]
            key = (ea, eb) if ea < eb else (eb, ea)
            if key in seen:
                continue
            seen.add(key)
            edges.append(key)
    if not edges:
        return None
    g = ig.Graph(n=len(node_ids), edges=edges, directed=False)
    try:
        part = g.community_leiden(
            objective_function="modularity",
            resolution=resolution,
            n_iterations=2,
        )
    except Exception:
        try:
            part = g.community_multilevel()
        except Exception:
            return None
    return list(part.membership)


def leiden_refine_leftovers(
    state: LayoutState,
    blocks: list[SoftBlock],
    *,
    resolution: float = 0.8,
    min_split_size: int = 12,
) -> list[SoftBlock]:
    """Optionally split leftover/mesh pockets with Leiden; hubs stay untouched.

    If igraph is missing, returns ``blocks`` unchanged.
    """
    if not igraph_available():
        return blocks
    out: list[SoftBlock] = []
    for b in blocks:
        if b.method != "leftover" or len(b.node_ids) < min_split_size:
            out.append(SoftBlock(len(out), b.hub_id, b.method, b.node_ids))
            continue
        nodes = list(b.node_ids)
        memb = _leiden_membership(nodes, state.adj, resolution=resolution)
        if memb is None:
            out.append(SoftBlock(len(out), b.hub_id, b.method, b.node_ids))
            continue
        groups: dict[int, list[str]] = defaultdict(list)
        for n, m in zip(nodes, memb):
            groups[int(m)].append(n)
        if len(groups) <= 1:
            out.append(SoftBlock(len(out), b.hub_id, b.method, b.node_ids))
            continue
        for gid in sorted(groups.keys()):
            ids = tuple(sorted(groups[gid], key=lambda i: state.names.get(i, i)))
            out.append(SoftBlock(len(out), None, "leiden", ids))
    return out


def partition_soft_blocks(
    state: LayoutState,
    *,
    mode: str = "hub_territory",
    hubs: Iterable[str] | None = None,
    leiden_resolution: float = 0.8,
) -> list[SoftBlock]:
    """Public entry: ``hub_territory`` (default) | ``leiden`` | ``soft``.

    - hub_territory: our seeded BFS only
    - leiden: hub territories + Leiden on leftovers (needs igraph)
    - soft: alias of leiden if igraph else hub_territory
    """
    m = (mode or "hub_territory").strip().lower()
    base = hub_territory_partition(state, hubs=hubs)
    if m in {"leiden", "soft"} and igraph_available():
        return leiden_refine_leftovers(
            state, base, resolution=leiden_resolution
        )
    return base


def soft_blocks_as_sets(blocks: list[SoftBlock]) -> list[set[str]]:
    return [set(b.node_ids) for b in blocks if b.node_ids]


def _block_centroids(
    state: LayoutState, blocks: list[SoftBlock]
) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    for b in blocks:
        pts = [state.positions[n] for n in b.node_ids if n in state.positions]
        if not pts:
            continue
        out[b.block_id] = (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )
    return out


def _centroid_spread(centroids: dict[int, tuple[float, float]]) -> float:
    """Max pairwise distance among centroids (0 if <2)."""
    ids = list(centroids)
    if len(ids) < 2:
        return 0.0
    best = 0.0
    for i, a in enumerate(ids):
        ax, ay = centroids[a]
        for b in ids[i + 1 :]:
            bx, by = centroids[b]
            d = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            if d > best:
                best = d
    return best


def _median_nn(centroids: dict[int, tuple[float, float]]) -> float:
    ids = list(centroids)
    if len(ids) < 2:
        return 0.0
    nns: list[float] = []
    for a in ids:
        ax, ay = centroids[a]
        best = None
        for b in ids:
            if a == b:
                continue
            bx, by = centroids[b]
            d = ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
            if best is None or d < best:
                best = d
        if best is not None:
            nns.append(best)
    if not nns:
        return 0.0
    nns.sort()
    return nns[len(nns) // 2]


def _pack_centers_preserve(
    centroids: dict[int, tuple[float, float]],
    *,
    gap: float,
) -> dict[int, tuple[float, float]]:
    """Keep relative constellation; scale about COM so median NN ≈ gap."""
    if len(centroids) < 2:
        return dict(centroids)
    cx = sum(p[0] for p in centroids.values()) / len(centroids)
    cy = sum(p[1] for p in centroids.values()) / len(centroids)
    med = _median_nn(centroids)
    if med < 1e-6:
        return dict(centroids)
    # Only densify when already too sparse; never explode a tight pack here.
    scale = min(1.0, gap / med)
    if abs(scale - 1.0) < 1e-3:
        return dict(centroids)
    return {
        bid: (cx + (x - cx) * scale, cy + (y - cy) * scale)
        for bid, (x, y) in centroids.items()
    }


def _pack_centers_fr_or_grid(
    state: LayoutState,
    blocks: list[SoftBlock],
    *,
    gap: float,
) -> dict[int, tuple[float, float]]:
    """Fresh placement when centroids are collapsed (init / stacked)."""
    id_of = {}
    for b in blocks:
        for n in b.node_ids:
            id_of[n] = b.block_id
    cut: dict[tuple[int, int], int] = defaultdict(int)
    for a, b in state.links:
        ia, ib = id_of.get(a), id_of.get(b)
        if ia is None or ib is None or ia == ib:
            continue
        key = (ia, ib) if ia < ib else (ib, ia)
        cut[key] += 1

    n = len(blocks)
    if igraph_available() and n >= 2 and cut:
        import igraph as ig

        edges = list(cut.keys())
        weights = [float(cut[e]) for e in edges]
        g = ig.Graph(n=n, edges=edges, directed=False)
        try:
            layout = g.layout_fruchterman_reingold(weights=weights, niter=200)
        except Exception:
            layout = g.layout_fruchterman_reingold(niter=200)
        xs = [float(p[0]) for p in layout]
        ys = [float(p[1]) for p in layout]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        span = max(maxx - minx, maxy - miny, 1e-6)
        target = gap * max(2.0, n**0.5)
        scale = target / span
        return {
            blocks[i].block_id: ((xs[i] - minx) * scale, (ys[i] - miny) * scale)
            for i in range(n)
        }

    cols = max(1, int(n**0.5 + 0.999))
    out: dict[int, tuple[float, float]] = {}
    for i, b in enumerate(blocks):
        r, c = divmod(i, cols)
        out[b.block_id] = (c * gap, r * gap)
    return out


def pack_block_centers(
    state: LayoutState,
    blocks: list[SoftBlock],
    *,
    gap: float = _BLOCK_GAP,
    strategy: str = "auto",
) -> dict[int, tuple[float, float]]:
    """Suggest soft-block centroids (init only; does not move nodes).

    ``strategy``:
      - ``preserve``: keep current relative centroids; densify so median NN≈gap
      - ``grid`` / ``fr``: ignore current world placement (FR if igraph else grid)
      - ``auto``: if centroids already spread (≥0.75·gap), preserve; else FR/grid
    """
    if not blocks:
        return {}
    centroids = _block_centroids(state, blocks)
    if len(centroids) < 2:
        return centroids

    strat = (strategy or "auto").strip().lower()
    spread = _centroid_spread(centroids)
    if strat == "preserve" or (strat == "auto" and spread >= gap * 0.75):
        return _pack_centers_preserve(centroids, gap=gap)
    return _pack_centers_fr_or_grid(state, blocks, gap=gap)


def partition_report(state: LayoutState, *, mode: str = "soft") -> dict[str, Any]:
    """Structure-facing summary for analyzeTopologyViewLayout."""
    blocks = partition_soft_blocks(state, mode=mode)
    centers = pack_block_centers(state, blocks)
    return {
        "mode": mode if mode != "soft" else ("leiden" if igraph_available() else "hub_territory"),
        "igraph": igraph_available(),
        "block_count": len(blocks),
        "blocks": [
            {
                **b.as_dict(),
                "center_hint": (
                    [round(centers[b.block_id][0], 1), round(centers[b.block_id][1], 1)]
                    if b.block_id in centers
                    else None
                ),
            }
            for b in blocks[:40]
        ],
        "tip": (
            "Soft blocks for init only: lay each hub territory / leftover with our "
            "petal/spine algorithms; use center_hint to pack blocks. "
            "igraph Leiden/FR are optional — install netx-topology-mcp[layout]."
        ),
    }


def pack_soft_blocks(
    state: LayoutState,
    params: LayoutParams | None = None,
    *,
    mode: str = "hub_territory",
    gap: float | None = None,
    strategy: str = "auto",
) -> OpResult:
    """Rigid-translate each soft block so centroids match ``pack_block_centers``.

    Init packing only — does not reshape petals inside a block. No-op if <2 blocks.
    ``strategy=auto`` preserves an already-spread constellation (densify only);
    collapsed stacks still get FR/grid separation.
    """
    del params  # reserved for future spacing knobs
    st = state.copy()
    raw = (mode or "hub_territory").strip().lower()
    resolved = resolve_block_mode(st, raw) if raw == "auto" else raw
    if resolved == "component":
        resolved = "hub_territory"
    blocks = partition_soft_blocks(st, mode=resolved)
    if len(blocks) < 2:
        return OpResult(
            state=st,
            moved=set(),
            op="pack_soft_blocks",
            params={"mode": resolved, "blocks_n": len(blocks)},
            note="skip soft pack (<2 blocks)",
        )
    g = float(gap or _BLOCK_GAP)
    strat = (strategy or "auto").strip().lower()
    before = _block_centroids(st, blocks)
    targets = pack_block_centers(st, blocks, gap=g, strategy=strat)
    used = (
        "preserve"
        if strat == "preserve"
        or (strat == "auto" and _centroid_spread(before) >= g * 0.75)
        else ("fr" if igraph_available() else "grid")
    )
    moved: set[str] = set()
    for b in blocks:
        tid = b.block_id
        if tid not in targets or not b.node_ids:
            continue
        pts = [st.positions[n] for n in b.node_ids if n in st.positions]
        if not pts:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        tx, ty = targets[tid]
        dx, dy = tx - cx, ty - cy
        if abs(dx) < 1e-6 and abs(dy) < 1e-6:
            continue
        for n in b.node_ids:
            if n not in st.positions:
                continue
            x, y = st.positions[n]
            st.positions[n] = (x + dx, y + dy)
            moved.add(n)
    st.meta["soft_pack"] = {
        "mode": resolved,
        "strategy": used,
        "blocks_n": len(blocks),
        "moved_n": len(moved),
        "igraph_centers": igraph_available(),
    }
    st.last_moved = moved
    return OpResult(
        state=st,
        moved=moved,
        op="pack_soft_blocks",
        params={
            "mode": resolved,
            "strategy": used,
            "blocks_n": len(blocks),
            "moved_n": len(moved),
        },
        note=f"packed soft blocks={len(blocks)} strategy={used} moved={len(moved)}",
    )
