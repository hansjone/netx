"""Graph helpers for layout ops."""

from __future__ import annotations

from collections import deque

from netx_topology_mcp.layout_ops.level_util import infer_layer

__all__ = [
    "infer_layer",
    "bbox",
    "connected_components",
    "order_ans",
    "chain_order",
]


def bbox(pos: dict[str, tuple[float, float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    return min(xs), min(ys), max(xs), max(ys)


def connected_components(nodes: set[str], g: dict[str, set[str]]) -> list[list[str]]:
    seen: set[str] = set()
    out: list[list[str]] = []
    for s in sorted(nodes):
        if s in seen:
            continue
        q = deque([s])
        seen.add(s)
        comp: list[str] = []
        while q:
            u = q.popleft()
            comp.append(u)
            for v in g.get(u, ()):
                if v in nodes and v not in seen:
                    seen.add(v)
                    q.append(v)
        out.append(comp)
    out.sort(key=lambda c: -len(c))
    return out


def order_ans(
    ans: list[str], ens: set[str], g: dict[str, set[str]], names: dict[str, str]
) -> list[str]:
    if len(ans) <= 1:
        return list(ans)

    def dist(a: str, b: str) -> int:
        q = deque([(a, 0)])
        seen = {a}
        while q:
            u, d = q.popleft()
            for v in g.get(u, ()):
                if v == b:
                    return d + 1
                if v in seen:
                    continue
                if v in ens or v in ans:
                    seen.add(v)
                    q.append((v, d + 1))
        return 10**6

    start = max(ans, key=lambda a: (len([x for x in g.get(a, ()) if x in ens]), names[a]))
    seq = [start]
    rest = set(ans) - {start}
    while rest:
        last = seq[-1]
        nxt = min(rest, key=lambda a: (dist(last, a), names[a]))
        seq.append(nxt)
        rest.remove(nxt)
    return seq


def chain_order(
    nodes: list[str], g: dict[str, set[str]], names: dict[str, str]
) -> list[str]:
    """Covering walk (may include all nodes). Prefer spine_backbone for true spine."""
    s = set(nodes)
    if not s:
        return []
    sub = {n: [x for x in g.get(n, ()) if x in s] for n in nodes}
    ends = [n for n in nodes if len(sub[n]) <= 1] or list(nodes)
    start = sorted(ends, key=lambda n: (len(sub[n]), names.get(n, n)))[0]
    ordered = [start]
    prev = None
    cur = start
    seen = {start}
    while len(ordered) < len(nodes):
        nxts = [x for x in sub[cur] if x != prev and x not in seen]
        if not nxts:
            cand = [v for u in ordered for v in sub[u] if v not in seen]
            if not cand:
                rest = [n for n in nodes if n not in seen]
                if not rest:
                    break
                nxts = [sorted(rest, key=lambda n: names.get(n, n))[0]]
            else:
                nxts = [sorted(cand, key=lambda n: names.get(n, n))[0]]
            prev = None
        nxts.sort(key=lambda n: names.get(n, n))
        prev, cur = cur, nxts[0]
        ordered.append(cur)
        seen.add(cur)
    return ordered


def spine_backbone(
    nodes: list[str], g: dict[str, set[str]], names: dict[str, str]
) -> list[str]:
    """Longest shortest-path among `nodes` (approx diameter). Side branches stay off-spine."""
    s = set(nodes)
    if not s:
        return []
    if len(s) == 1:
        return list(s)

    def bfs(src: str) -> tuple[dict[str, int], dict[str, str | None]]:
        dist = {src: 0}
        parent: dict[str, str | None] = {src: None}
        q = deque([src])
        while q:
            u = q.popleft()
            for v in g.get(u, ()):
                if v not in s or v in dist:
                    continue
                dist[v] = dist[u] + 1
                parent[v] = u
                q.append(v)
        return dist, parent

    # eccentricity from an arbitrary end, then from farthest
    seed = sorted(nodes, key=lambda n: (len([x for x in g.get(n, ()) if x in s]), names.get(n, n)))[0]
    d1, _ = bfs(seed)
    a = max(d1, key=lambda n: (d1[n], names.get(n, n)))
    d2, parent = bfs(a)
    b = max(d2, key=lambda n: (d2[n], names.get(n, n)))
    path = [b]
    while parent.get(path[-1]) is not None:
        path.append(parent[path[-1]] or "")
    path.reverse()
    return [n for n in path if n]


def build_state_from_nodes_edges(
    nodes: list[dict], edges: list[dict]
) -> "LayoutState":  # noqa: F821
    from netx_topology_mcp.layout_ops.state import LayoutState
    from netx_topology_mcp.layout_metrics import collapse_links
    from netx_topology_mcp.layout_ops.level_util import _parse_level

    names: dict[str, str] = {}
    layers: dict[str, str] = {}
    levels: dict[str, float] = {}
    ids: list[str] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        fid = str(n.get("fabric_node_id") or n.get("id") or "").strip()
        if not fid:
            continue
        nm = str(n.get("name") or n.get("label") or fid)
        ids.append(fid)
        names[fid] = nm
        layers[fid] = infer_layer(nm, n.get("role"), n.get("level"))
        lv = _parse_level(n.get("level"))
        if lv is not None:
            levels[fid] = float(lv)

    adj: dict[str, set[str]] = {i: set() for i in ids}
    links = collapse_links(edges)
    for a, b in links:
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)

    positions: dict[str, tuple[float, float]] = {}
    for n in nodes:
        if not isinstance(n, dict):
            continue
        fid = str(n.get("fabric_node_id") or n.get("id") or "").strip()
        if not fid:
            continue
        if n.get("x") is not None and n.get("y") is not None:
            try:
                positions[fid] = (float(n["x"]), float(n["y"]))
            except (TypeError, ValueError):
                pass

    return LayoutState(
        positions=positions,
        names=names,
        layers=layers,
        levels=levels,
        links=[(a, b) for a, b in links if a in adj and b in adj],
        adj=adj,
        meta={"ids": ids},
    )
