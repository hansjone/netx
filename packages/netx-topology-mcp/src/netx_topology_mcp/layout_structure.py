"""Graph structure stats for layout planning (shape / gravity / blocks).

Read-only: no coordinates required. Feeds analyzeTopologyViewLayout(detail=structure).

Order of judgment (important):
  1) global / per-component **shape** (chains first — simplest & common)
  2) hub gravity (core_bar / agg_bar / mixed) only when not chain-like
  3) complex canvases = pack of blocks; lay each block, then compose
  4) soft_blocks: hub territories inside giant CCs (+ optional igraph leftovers)
"""

from __future__ import annotations

from collections import defaultdict, deque
from statistics import mean, median
from typing import Any

from netx_topology_mcp.layout_metrics import collapse_links
from netx_topology_mcp.layout_ops.graph_util import infer_layer


def _pct(xs: list[int], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    i = min(len(s) - 1, max(0, int(round((len(s) - 1) * p))))
    return float(s[i])


def _connected_components(
    ids: list[str], adj: dict[str, set[str]], names: dict[str, str]
) -> list[list[str]]:
    seen: set[str] = set()
    out: list[list[str]] = []
    for s in sorted(ids, key=lambda x: names.get(x, x)):
        if s in seen:
            continue
        q: deque[str] = deque([s])
        seen.add(s)
        comp: list[str] = []
        while q:
            u = q.popleft()
            comp.append(u)
            for v in adj.get(u, ()):
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        out.append(comp)
    out.sort(key=lambda c: (-len(c), names.get(c[0], c[0])))
    return out


def _farthest(
    start: str, allowed: set[str], adj: dict[str, set[str]]
) -> tuple[tuple[str, int], dict[str, str | None]]:
    q: deque[tuple[str, int]] = deque([(start, 0)])
    seen = {start}
    best = (start, 0)
    parent: dict[str, str | None] = {start: None}
    while q:
        u, d = q.popleft()
        if d > best[1]:
            best = (u, d)
        for v in adj.get(u, ()):
            if v in allowed and v not in seen:
                seen.add(v)
                parent[v] = u
                q.append((v, d + 1))
    return best, parent


def _diameter_path(
    comp: list[str], adj: dict[str, set[str]], names: dict[str, str]
) -> list[str]:
    s = set(comp)
    if not s:
        return []
    leaves = [n for n in comp if len(adj.get(n, ())) <= 1] or list(comp)
    start = sorted(leaves, key=lambda n: names.get(n, n))[0]
    (e1, _), _ = _farthest(start, s, adj)
    (e2, _), parent = _farthest(e1, s, adj)
    path = [e2]
    while parent[path[-1]] is not None:
        path.append(parent[path[-1]] or "")
    path.reverse()
    return [n for n in path if n]


def _block_shape(
    comp: list[str],
    adj: dict[str, set[str]],
    names: dict[str, str],
    layers: dict[str, str],
) -> dict[str, Any]:
    """Classify one connected component: chain | star | mesh | tiny."""
    n = len(comp)
    if n <= 2:
        return {
            "shape": "tiny",
            "node_count": n,
            "link_count": sum(len(adj[u]) for u in comp) // 2,
            "spine_len": n,
            "chain_frac": 1.0,
            "max_degree": max((len(adj[u]) for u in comp), default=0),
            "mean_degree": round(mean(len(adj[u]) for u in comp), 2) if comp else 0.0,
        }
    degs = [len(adj[u]) for u in comp]
    low = sum(1 for d in degs if d <= 2) / n
    max_d = max(degs)
    mean_d = mean(degs)
    path = _diameter_path(comp, adj, names)
    spine_len = len(path)
    # nodes on diameter path / n — high ⇒ path-like
    on_spine = spine_len / n
    # edges ≈ n-1 ⇒ tree/path; denser ⇒ mesh
    e = sum(degs) // 2
    treeish = e <= n  # forest/tree (path has e=n-1)

    shape = "mesh"
    # Path / corridor first (CN/AN on spine with deg≤5 still counts as chain)
    nearly_path = mean_d <= 2.45 and low >= 0.62 and max_d <= 5 and e <= n + 2
    if nearly_path or (treeish and low >= 0.65 and mean_d <= 2.5 and max_d <= 5):
        shape = "chain"
    elif treeish and low >= 0.72 and mean_d <= 2.35 and max_d <= 6:
        shape = "chain"
    elif on_spine >= 0.25 and low >= 0.68 and mean_d <= 2.4 and max_d <= 5:
        shape = "chain"
    elif max_d >= 7 and low >= 0.55 and on_spine < 0.4:
        shape = "star"
    elif max_d >= 8 or (mean_d >= 2.8 and e > n + max(2, n // 10)):
        shape = "mesh"
    else:
        if on_spine >= 0.35 and low >= 0.6:
            shape = "chain"
        elif max_d >= 6 and low >= 0.5:
            shape = "star"

    hubs = sorted(
        [u for u in comp if layers.get(u) in {"core", "agg"}],
        key=lambda u: (-len(adj[u]), names.get(u, u)),
    )[:4]
    return {
        "shape": shape,
        "node_count": n,
        "link_count": e,
        "spine_len": spine_len,
        "on_spine_frac": round(on_spine, 3),
        "chain_frac": round(low, 3),  # frac deg<=2
        "max_degree": max_d,
        "mean_degree": round(mean_d, 2),
        "treeish": treeish,
        "hub_ids": hubs,
        "spine_ends": [path[0], path[-1]] if path else [],
        "spine_end_names": [names.get(path[0], ""), names.get(path[-1], "")] if path else [],
    }


def _bfs_territory(
    hubs: list[str],
    access: set[str],
    adj: dict[str, set[str]],
) -> dict[str, str]:
    """Multi-source Voronoi on access: each access node → nearest hub (via stubs)."""
    owner: dict[str, str] = {}
    q: deque[str] = deque()
    for h in sorted(hubs):
        for nb in adj.get(h, ()):
            if nb in access and nb not in owner:
                owner[nb] = h
                q.append(nb)
    while q:
        u = q.popleft()
        h = owner[u]
        for v in adj.get(u, ()):
            if v in access and v not in owner:
                owner[v] = h
                q.append(v)
    return owner


def analyze_graph_structure(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    hub_top_k: int = 12,
    stub_top_k: int = 24,
) -> dict[str, Any]:
    """Return shape/blocks, hubs, gravity type, recipe preference."""
    names: dict[str, str] = {}
    layers: dict[str, str] = {}
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

    adj: dict[str, set[str]] = {i: set() for i in ids}
    links = collapse_links(edges)
    for a, b in links:
        if a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)

    deg = {i: len(adj[i]) for i in ids}
    access = {i for i in ids if layers.get(i) == "access"}
    layer_known = sum(
        1 for i in ids if layers.get(i) in {"external", "core", "agg", "access"}
    )
    if layer_known < max(3, len(ids) // 5) and ids:
        ranked = sorted(ids, key=lambda i: (-deg[i], names[i]))
        hub_budget = max(2, min(8, len(ids) // 15 + 2))
        for i in ranked[:hub_budget]:
            if layers.get(i) == "other":
                layers[i] = "agg"
        for i in ids:
            if layers.get(i) == "other" and deg[i] <= 2:
                layers[i] = "access"
        access = {i for i in ids if layers.get(i) == "access"}

    # --- blocks / shape first ---
    comps = _connected_components(ids, adj, names)
    blocks: list[dict[str, Any]] = []
    for idx, comp in enumerate(comps):
        b = _block_shape(comp, adj, names, layers)
        b["block_id"] = idx
        b["sample_names"] = [names[n] for n in sorted(comp, key=lambda x: names[x])[:3]]
        blocks.append(b)

    n_nodes = max(len(ids), 1)
    chain_nodes = sum(b["node_count"] for b in blocks if b["shape"] in {"chain", "tiny"})
    star_nodes = sum(b["node_count"] for b in blocks if b["shape"] == "star")
    mesh_nodes = sum(b["node_count"] for b in blocks if b["shape"] == "mesh")
    chain_frac_nodes = chain_nodes / n_nodes
    global_low = (sum(1 for i in ids if deg[i] <= 2) / n_nodes) if ids else 0.0
    global_mean = mean(deg.values()) if deg else 0.0
    global_max = max(deg.values()) if deg else 0

    primary_shape = "mesh"
    if chain_frac_nodes >= 0.55 or (global_low >= 0.72 and global_mean <= 2.35 and global_max <= 6):
        primary_shape = "chains"
    elif star_nodes / n_nodes >= 0.45:
        primary_shape = "star"
    elif mesh_nodes / n_nodes >= 0.45:
        primary_shape = "mesh"
    elif chain_frac_nodes >= 0.35:
        primary_shape = "mixed_blocks"  # chain corridors + some hub blocks

    shape_block = {
        "primary": primary_shape,
        "component_count": len(comps),
        "chain_node_frac": round(chain_frac_nodes, 3),
        "star_node_frac": round(star_nodes / n_nodes, 3),
        "mesh_node_frac": round(mesh_nodes / n_nodes, 3),
        "deg_le2_frac": round(global_low, 3),
        "mean_degree": round(global_mean, 2),
        "max_degree": global_max,
        "blocks": blocks[:40],
        "strategy": (
            "Lay each connected component as its own block: "
            "chain→horizontal spine + short stubs; star→hub + petals; "
            "mesh→corridor/compact. Pack blocks in rows. "
            "Do NOT treat a chain canvas as hub-and-spoke rings."
        ),
    }

    by_layer: dict[str, list[str]] = defaultdict(list)
    for i in ids:
        by_layer[layers.get(i, "other")].append(i)

    owner = _bfs_territory(
        [i for i in ids if layers.get(i) in {"core", "agg"}],
        access,
        adj,
    )
    territory_count: dict[str, int] = defaultdict(int)
    for h in owner.values():
        territory_count[h] += 1

    def layer_stats(layer: str) -> dict[str, Any]:
        members = by_layer.get(layer, [])
        if not members:
            return {
                "count": 0,
                "max_degree": 0,
                "mean_degree": 0.0,
                "access_neighbor_sum": 0,
                "access_cover": 0,
                "access_cover_frac": 0.0,
                "territory": 0,
                "territory_frac": 0.0,
            }
        degs = [deg[i] for i in members]
        covered: set[str] = set()
        access_nb_sum = 0
        terr = 0
        for i in members:
            nbs = [x for x in adj.get(i, ()) if x in access]
            access_nb_sum += len(nbs)
            covered.update(nbs)
            terr += territory_count.get(i, 0)
        n_access = max(len(access), 1)
        return {
            "count": len(members),
            "max_degree": max(degs) if degs else 0,
            "mean_degree": round(mean(degs), 2) if degs else 0.0,
            "access_neighbor_sum": access_nb_sum,
            "access_cover": len(covered),
            "access_cover_frac": round(len(covered) / n_access, 3),
            "territory": terr,
            "territory_frac": round(terr / n_access, 3),
        }

    layer_block = {
        "external": layer_stats("external"),
        "core": layer_stats("core"),
        "agg": layer_stats("agg"),
        "access": {
            "count": len(access),
            "max_degree": max((deg[i] for i in access), default=0),
            "mean_degree": round(mean([deg[i] for i in access]), 2) if access else 0.0,
        },
        "other": layer_stats("other"),
    }

    hub_scores: list[tuple[float, str]] = []
    for i in ids:
        if layers.get(i) not in {"core", "agg"}:
            continue
        an = sum(1 for x in adj.get(i, ()) if x in access)
        score = deg[i] * 1.0 + an * 2.0 + territory_count.get(i, 0) * 0.5
        hub_scores.append((score, i))
    hub_scores.sort(key=lambda t: (-t[0], names[t[1]]))
    hubs_out: list[dict[str, Any]] = []
    for score, i in hub_scores[: max(1, min(40, hub_top_k))]:
        an = sorted(
            (x for x in adj.get(i, ()) if x in access),
            key=lambda x: (-deg[x], names[x]),
        )
        hubs_out.append(
            {
                "fabric_node_id": i,
                "name": names[i],
                "layer": layers[i],
                "degree": deg[i],
                "access_neighbors": len(an),
                "territory": territory_count.get(i, 0),
                "score": round(score, 2),
                "stub_ids": an[:8],
            }
        )

    stub_rows: list[dict[str, Any]] = []
    for h in hubs_out[:8]:
        hid = h["fabric_node_id"]
        for sid in h.get("stub_ids") or []:
            stub_rows.append(
                {
                    "hub_id": hid,
                    "hub_name": h["name"],
                    "stub_id": sid,
                    "stub_name": names.get(sid, sid),
                    "stub_degree": deg.get(sid, 0),
                }
            )
    stub_rows.sort(key=lambda r: (-r["stub_degree"], r["hub_name"], r["stub_name"]))
    stub_rows = stub_rows[: max(1, min(60, stub_top_k))]

    core_s = layer_block["core"]
    agg_s = layer_block["agg"]
    rationale: list[str] = []
    gravity_type = "unclear"
    confidence = 0.35
    anchor_layer = "access"
    decorative: list[str] = []
    geometry = "chain_rows"
    recipe_pref = ["corridor", "compact", "rings"]

    c_terr = float(core_s["territory_frac"])
    a_terr = float(agg_s["territory_frac"])
    c_deg = int(core_s["max_degree"])
    a_deg = int(agg_s["max_degree"])
    c_n = int(core_s["count"])
    a_n = int(agg_s["count"])

    # 1) Chain-first — never fall through to hub-bar on path graphs
    if primary_shape == "chains":
        gravity_type = "chains"
        confidence = min(0.97, 0.55 + chain_frac_nodes * 0.4)
        anchor_layer = "access"
        geometry = "chain_rows"
        recipe_pref = ["corridor", "compact", "rings"]
        rationale.append(
            f"shape=chains; deg_le2_frac={global_low:.2f}; "
            f"components={len(comps)}; lay spines+stubs per block"
        )
    elif primary_shape == "mixed_blocks":
        gravity_type = "mixed_blocks"
        confidence = 0.7
        anchor_layer = "agg" if a_n else ("core" if c_n else "access")
        geometry = "block_pack"
        recipe_pref = ["corridor", "compact", "rings"]
        rationale.append("mixed chain blocks + hub blocks; process per component")
    elif c_n == 0 and a_n > 0 and (a_terr >= 0.35 or a_deg >= 4):
        gravity_type = "agg_bar"
        confidence = min(0.95, 0.55 + a_terr * 0.4)
        anchor_layer = "agg"
        geometry = "agg_bar_mid"
        recipe_pref = ["rings", "compact", "corridor"]
        rationale.append("no_core_layer; agg holds access territory")
        if a_n >= 2:
            rationale.append(
                "dual_agg: rings uses min-ring path nesting (parallel corridors) "
                "before per-AN petals"
            )
    elif c_n > 0 and (
        c_terr >= a_terr * 1.35 or (c_deg >= max(6, int(a_deg * 1.4)) and c_terr >= 0.25)
    ):
        gravity_type = "core_bar"
        confidence = min(0.95, 0.5 + c_terr * 0.45)
        anchor_layer = "core"
        if a_n > 0 and a_terr < 0.2 and a_deg <= max(4, c_deg // 2):
            decorative.append("agg")
            rationale.append("agg_low_attachment; treat as decorative bar")
        core_hubs = [h for h in hubs_out if h["layer"] == "core"][:4]
        if len(core_hubs) >= 2 and core_hubs[1]["degree"] >= max(
            4, core_hubs[0]["degree"] * 0.5
        ):
            geometry = "core_center"
            rationale.append("dual_core_hubs; prefer horizontal beam at mid/upper-mid")
        else:
            geometry = "core_top"
            rationale.append("core_holds_access; default core beam above access fans")
        recipe_pref = ["compact", "corridor", "rings"]
        rationale.append("core_territory_or_degree_dominates_agg")
        rationale.append(
            "prefer dual_units → sinkTopologyDualUnits; "
            "small core: layout compact|corridor"
        )
    elif c_n > 0 and a_n > 0 and c_terr >= 0.15 and a_terr >= 0.15:
        gravity_type = "mixed"
        confidence = min(0.9, 0.45 + min(c_terr, a_terr) * 0.5)
        anchor_layer = "core" if c_terr >= a_terr else "agg"
        geometry = "core_center" if anchor_layer == "core" else "agg_bar_mid"
        recipe_pref = ["compact", "rings", "corridor"]
        rationale.append("both_core_and_agg_own_access_territory")
    elif hubs_out and primary_shape == "star":
        top = hubs_out[0]
        gravity_type = "agg_bar" if top["layer"] == "agg" else "core_bar"
        confidence = 0.55
        anchor_layer = top["layer"]
        geometry = "agg_bar_mid" if top["layer"] == "agg" else "core_center"
        recipe_pref = (
            ["rings", "compact", "corridor"]
            if top["layer"] == "agg"
            else ["compact", "corridor", "rings"]
        )
        rationale.append("shape=star; hub-and-spoke")
    elif hubs_out:
        top = hubs_out[0]
        gravity_type = "core_bar" if top["layer"] == "core" else "agg_bar"
        confidence = 0.45
        anchor_layer = top["layer"]
        geometry = "core_center" if top["layer"] == "core" else "agg_bar_mid"
        recipe_pref = (
            ["compact", "corridor", "rings"]
            if top["layer"] == "core"
            else ["rings", "compact", "corridor"]
        )
        rationale.append("fallback_top_hub_layer")
    else:
        gravity_type = "chains" if global_low >= 0.6 else "unclear"
        geometry = "chain_rows" if gravity_type == "chains" else "compact"
        recipe_pref = ["corridor", "compact", "rings"]
        rationale.append("no_clear_hubs; corridor baseline")

    degs = list(deg.values())
    chain_mode = gravity_type in {"chains", "mixed_blocks"}
    advice = {
        "pin_anchors": (
            []
            if gravity_type == "chains"
            else [h["fabric_node_id"] for h in hubs_out[:4]]
        ),
        "preview_recipes": list(recipe_pref[:2]),
        "skip_rings_first": gravity_type in {"core_bar", "chains", "mixed_blocks"},
        "geometry": geometry,
        "layout_over_rank": True,
        "decompose_by_component": True,
        "decompose_soft_blocks": primary_shape in {"star", "mesh", "mixed_blocks"}
        or (len(comps) <= 2 and star_nodes >= 40),
        "block_plan": [
            {
                "block_id": b["block_id"],
                "shape": b["shape"],
                "n": b["node_count"],
                "how": (
                    "spine+short stubs, row pack"
                    if b["shape"] in {"chain", "tiny"}
                    else (
                        "hub center + petals"
                        if b["shape"] == "star"
                        else "corridor/compact then local untangle"
                    )
                ),
            }
            for b in blocks[:20]
        ],
        "next": (
            "CHAIN canvas: for each structure.shape.blocks item with shape=chain, "
            "lay diameter spine horizontally, hang deg-1/short branches as stubs, "
            "stack components as rows; preview corridor/compact (skip rings); "
            "untangle residual."
            if chain_mode
            else (
                "Giant star/core_bar: prefer structure.dual_units "
                "(two portals + ≥2 interior-disjoint corridors). "
                "Batch sinkTopologyDualUnits (layout_batch) → orbit_sweep → "
                "polish_crossings → clear_edge_hits. "
                "Portals may appear in multiple units. "
                "Small graphs: layout compact|corridor|rings. "
                "agg_bar: preview rings first."
            )
        ),
        "prefer_dual_units": (
            not chain_mode
            and (
                primary_shape in {"star", "mesh", "mixed_blocks"}
                or gravity_type in {"core_bar", "mixed", "mixed_blocks"}
                or star_nodes >= 40
            )
        ),
    }

    # Soft blocks inside giant CCs (hub BFS; igraph optional for leftovers).
    soft_blocks: dict[str, Any] = {"block_count": 0, "blocks": [], "igraph": False}
    dual_units: dict[str, Any] = {"unit_count": 0, "units": []}
    try:
        from netx_topology_mcp.layout_ops.dual_units import dual_units_report
        from netx_topology_mcp.layout_ops.graph_util import build_state_from_nodes_edges
        from netx_topology_mcp.layout_ops.partition import partition_report

        st = build_state_from_nodes_edges(nodes, edges)
        soft_mode = "soft" if advice.get("decompose_soft_blocks") else "hub_territory"
        soft_blocks = partition_report(st, mode=soft_mode)
        dual_units = dual_units_report(st)
    except Exception as exc:  # pragma: no cover - defensive
        soft_blocks = {
            "block_count": 0,
            "blocks": [],
            "igraph": False,
            "error": str(exc)[:120],
        }
        dual_units = {"unit_count": 0, "units": [], "error": str(exc)[:120]}

    anchors = []
    if gravity_type == "chains":
        # expose spine ends of largest chain blocks as soft anchors
        for b in blocks[:6]:
            if b["shape"] not in {"chain", "tiny"}:
                continue
            for end, nm in zip(b.get("spine_ends") or [], b.get("spine_end_names") or []):
                anchors.append(
                    {
                        "fabric_node_id": end,
                        "name": nm,
                        "layer": layers.get(end, "access"),
                        "degree": deg.get(end, 0),
                        "territory": 0,
                        "role": "spine_end",
                    }
                )
    else:
        anchors = [
            {
                "fabric_node_id": h["fabric_node_id"],
                "name": h["name"],
                "layer": h["layer"],
                "degree": h["degree"],
                "territory": h["territory"],
            }
            for h in hubs_out[:6]
            if h["layer"] == anchor_layer
            or (gravity_type in {"mixed", "mixed_blocks"} and h["layer"] in {"core", "agg"})
        ]

    return {
        "node_count": len(ids),
        "link_count": len(links),
        "degree": {
            "max": max(degs) if degs else 0,
            "mean": round(mean(degs), 2) if degs else 0.0,
            "median": float(median(degs)) if degs else 0.0,
            "p90": _pct(degs, 0.9),
            "le2_frac": round(global_low, 3),
        },
        "shape": shape_block,
        "layers": layer_block,
        "hubs": hubs_out,
        "stubs": stub_rows,
        "soft_blocks": soft_blocks,
        "dual_units": dual_units,
        "gravity": {
            "type": gravity_type,
            "confidence": round(confidence, 3),
            "anchor_layer": anchor_layer,
            "decorative_layers": decorative,
            "geometry_hint": geometry,
            "recipe_preference": recipe_pref,
            "rationale": rationale,
            "anchors": anchors,
        },
        "advice": advice,
        "headline": (
            f"shape={primary_shape} gravity={gravity_type} "
            f"comps={len(comps)} geometry={geometry} try={'>'.join(recipe_pref[:2])}"
        ),
    }
