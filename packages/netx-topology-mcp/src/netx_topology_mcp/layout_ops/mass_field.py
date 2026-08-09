"""Per-node / per-edge attract+repulse mass field for soft compose merge.

Replaces uniform exclusive-island rigidity: cores pull hard, rings hold shape,
chains are tearable and may evolve into rings (raising mass).
"""

from __future__ import annotations

import math
from typing import Any

from netx_topology_mcp.layout_ops.dual_units import DualUnit, find_dual_portal_units
from netx_topology_mcp.layout_ops.state import LayoutState

# role -> (attract, repulse, mass)
_ROLE_NODE: dict[str, tuple[float, float, float]] = {
    "core": (4.0, 1.8, 3.0),
    "ring": (2.4, 1.2, 1.8),
    "chain": (0.7, 0.6, 0.5),
    "free": (1.0, 1.0, 1.0),
}

_ROLE_EDGE: dict[str, tuple[float, float]] = {
    "ring": (2.6, 1.0),
    "chain": (0.5, 0.4),
    "bridge": (1.2, 0.8),
    "plain": (1.0, 1.0),
}

_ROLE_RANK = {"free": 0, "chain": 1, "ring": 2, "core": 3}
_STAB = {"chain": 0.35, "ring": 1.2, "core": 3.0, "free": 0.7}


def _edge_key(a: str, b: str) -> str:
    return f"{a}|{b}" if a <= b else f"{b}|{a}"


def _nest_boost(path_count: int) -> float:
    extra = max(0, int(path_count) - 2)
    return 1.0 + 0.15 * float(extra)


def annotate_dual_unit(unit: DualUnit) -> dict[str, Any]:
    """Role tags for one DualUnit (portals→core, paths→ring, tails→chain)."""
    nest = max(0, len(unit.paths))
    boost = _nest_boost(nest)
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def _set_node(nid: str, role: str) -> None:
        cur = nodes.get(nid)
        if cur is None or _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(
            str(cur.get("role") or "free"), 0
        ):
            a, r, m = _ROLE_NODE[role]
            if role == "ring":
                a *= boost
                m *= 1.0 + 0.08 * max(0, nest - 2)
            nodes[nid] = {
                "role": role,
                "attract": round(a, 4),
                "repulse": round(r, 4),
                "mass": round(m, 4),
                "unit_id": unit.unit_id,
            }

    def _set_edge(u: str, v: str, role: str) -> None:
        if u == v:
            return
        key = _edge_key(u, v)
        cur = edges.get(key)
        if cur is None or _ROLE_RANK.get(role, 0) > _ROLE_RANK.get(
            str(cur.get("role") or "plain"), 0
        ):
            ae, re = _ROLE_EDGE[role]
            if role == "ring":
                ae *= boost
            edges[key] = {
                "role": role,
                "attract": round(ae, 4),
                "repulse": round(re, 4),
                "a": u if u <= v else v,
                "b": v if u <= v else u,
            }

    for p in (unit.portal_a, unit.portal_b):
        _set_node(p, "core")
    for path in unit.paths:
        for i, n in enumerate(path):
            if n in (unit.portal_a, unit.portal_b):
                _set_node(n, "core")
            else:
                _set_node(n, "ring")
            if i + 1 < len(path):
                _set_edge(path[i], path[i + 1], "ring")
    for chain in unit.tails:
        prev = None
        # Attach edge from nearest portal/core if chain starts at neighbor — handled
        # when full graph links are known; here tag chain nodes + consecutive edges.
        for n in chain:
            _set_node(n, "chain")
            if prev is not None:
                _set_edge(prev, n, "chain")
            prev = n

    return {
        "unit_id": unit.unit_id,
        "nest_depth": nest,
        "portal_a": unit.portal_a,
        "portal_b": unit.portal_b,
        "nodes": nodes,
        "edges": edges,
    }


def merge_mass_dicts(
    *parts: dict[str, dict[str, Any]],
    rank_key: str = "role",
    rank_map: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    ranks = rank_map or _ROLE_RANK
    out: dict[str, dict[str, Any]] = {}
    for part in parts:
        for k, row in part.items():
            cur = out.get(k)
            if cur is None or ranks.get(str(row.get(rank_key) or ""), 0) > ranks.get(
                str(cur.get(rank_key) or ""), 0
            ):
                out[k] = dict(row)
    return out


def _degree_tweak(
    state: LayoutState,
    nodes: dict[str, dict[str, Any]],
) -> None:
    for n, row in nodes.items():
        deg = len(state.adj.get(n, ()))
        layer = str(state.layers.get(n) or "")
        m = float(row.get("mass") or 1.0)
        a = float(row.get("attract") or 1.0)
        if layer in ("core", "agg") or deg >= 8:
            m *= 1.15
            a *= 1.08
        elif deg <= 2 and str(row.get("role")) == "chain":
            m *= 0.92
            a *= 0.95
        row["mass"] = round(m, 4)
        row["attract"] = round(a, 4)


def build_mass_field(
    state: LayoutState,
    *,
    units: list[DualUnit] | None = None,
    groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build mass_field from dual units (+ optional compose groups).

    Pass ``units=[]`` to skip dual-unit detection (plain FA2 mass tags only).
    """
    found = units if units is not None else find_dual_portal_units(state)
    node_parts: list[dict[str, dict[str, Any]]] = []
    edge_parts: list[dict[str, dict[str, Any]]] = []
    unit_rows: list[dict[str, Any]] = []
    for u in found:
        ann = annotate_dual_unit(u)
        unit_rows.append(
            {
                "unit_id": ann["unit_id"],
                "nest_depth": ann["nest_depth"],
                "portal_a": ann["portal_a"],
                "portal_b": ann["portal_b"],
            }
        )
        node_parts.append(ann["nodes"])
        edge_parts.append(ann["edges"])

    nodes = merge_mass_dicts(*node_parts) if node_parts else {}
    edges = merge_mass_dicts(
        *edge_parts, rank_key="role", rank_map={"plain": 0, "bridge": 1, "chain": 2, "ring": 3}
    ) if edge_parts else {}

    # Tag remaining nodes + mark fabric bridges between different group homes.
    home = home_map_from_groups(groups or [])
    for n in state.positions:
        if n not in nodes:
            nodes[n] = {
                "role": "free",
                "attract": 1.0,
                "repulse": 1.0,
                "mass": 1.0,
            }
    for a, b in state.links:
        key = _edge_key(a, b)
        if key in edges:
            continue
        ha, hb = home.get(a), home.get(b)
        role = "bridge" if ha and hb and ha != hb else "plain"
        ae, re = _ROLE_EDGE[role]
        edges[key] = {
            "role": role,
            "attract": ae,
            "repulse": re,
            "a": a if a <= b else b,
            "b": b if a <= b else a,
        }

    _degree_tweak(state, nodes)

    soft_groups = []
    for g in groups or []:
        key = str(g.get("key") or "")
        members = [str(x) for x in (g.get("node_ids") or g.get("members") or [])]
        pivots = [str(x) for x in (g.get("pivots") or [])]
        cores = [
            n
            for n in members
            if str((nodes.get(n) or {}).get("role")) == "core" or n in pivots
        ]
        if not cores:
            cores = list(pivots)
        soft_groups.append(
            {
                "key": key,
                "node_ids": members,
                "pivots": pivots,
                "cores": cores,
                "soft": True,
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "units": unit_rows,
        "groups": soft_groups,
        "tip": (
            "mass_field: core/ring/chain attract+repulse; use mass_merge "
            "(not exclusive rigid islands) after compose"
        ),
    }


def home_map_from_groups(groups: list[dict[str, Any]]) -> dict[str, str]:
    """First group wins for multi-membership; portals often multi-home."""
    home: dict[str, str] = {}
    counts: dict[str, int] = {}
    for g in groups:
        key = str(g.get("key") or "")
        for n in g.get("node_ids") or g.get("members") or []:
            nid = str(n)
            counts[nid] = counts.get(nid, 0) + 1
            if nid not in home:
                home[nid] = key
    # Shared portals: keep first home but mark multi via absence of exclusive.
    return home


def group_effective_mass(
    group: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
) -> float:
    cores = list(group.get("cores") or group.get("pivots") or [])
    if not cores:
        # Fall back to top attract members.
        members = [str(x) for x in (group.get("node_ids") or [])]
        members.sort(
            key=lambda n: -float((nodes.get(n) or {}).get("attract") or 0.0)
        )
        cores = members[:2]
    total = 0.0
    for c in cores:
        row = nodes.get(c) or {}
        total += float(row.get("mass") or 1.0) * float(row.get("attract") or 1.0)
    return max(total, 0.1)


def core_centroid(
    group: dict[str, Any],
    pos: dict[str, tuple[float, float]],
    nodes: dict[str, dict[str, Any]],
) -> tuple[float, float] | None:
    cores = [c for c in (group.get("cores") or group.get("pivots") or []) if c in pos]
    if not cores:
        members = [n for n in (group.get("node_ids") or []) if n in pos]
        if not members:
            return None
        members.sort(
            key=lambda n: -float((nodes.get(n) or {}).get("attract") or 0.0)
        )
        cores = members[:3]
    if not cores:
        return None
    cx = sum(pos[c][0] for c in cores) / len(cores)
    cy = sum(pos[c][1] for c in cores) / len(cores)
    return (cx, cy)


def evolve_chains_to_rings(
    state: LayoutState,
    mass: dict[str, Any],
) -> dict[str, Any]:
    """Promote chain nodes that sit on newly detected dual-portal corridors."""
    units = find_dual_portal_units(state)
    nodes = dict(mass.get("nodes") or {})
    edges = dict(mass.get("edges") or {})
    promoted_n = 0
    promoted_e = 0
    for u in units:
        ann = annotate_dual_unit(u)
        for nid, row in ann["nodes"].items():
            if str(row.get("role")) != "ring":
                continue
            cur = nodes.get(nid) or {}
            if str(cur.get("role")) == "chain":
                nodes[nid] = row
                promoted_n += 1
            elif nid not in nodes or _ROLE_RANK.get(
                str(row.get("role")), 0
            ) > _ROLE_RANK.get(str(cur.get("role") or "free"), 0):
                nodes[nid] = row
        for key, row in ann["edges"].items():
            if str(row.get("role")) != "ring":
                continue
            cur = edges.get(key) or {}
            if str(cur.get("role")) == "chain":
                edges[key] = row
                promoted_e += 1
            elif key not in edges:
                edges[key] = row
    _degree_tweak(state, nodes)
    out = dict(mass)
    out["nodes"] = nodes
    out["edges"] = edges
    out["evolve"] = {
        "promoted_nodes": promoted_n,
        "promoted_edges": promoted_e,
        "units_n": len(units),
    }
    return out


def capture_pass(
    state: LayoutState,
    mass: dict[str, Any],
    *,
    kappa_node: float = 1.25,
    kappa_block: float = 2.0,
    rho_ideal: float = 6.0,
    ideal_len: float = 540.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rewrite soft group membership: steal nodes / whole weak blocks."""
    groups = [dict(g) for g in (mass.get("groups") or [])]
    if len(groups) < 2:
        return mass, {"stolen_nodes": 0, "stolen_blocks": 0}
    nodes = mass.get("nodes") or {}
    pos = state.positions
    # Rebuild member lists as sets
    by_key: dict[str, dict[str, Any]] = {}
    for g in groups:
        key = str(g.get("key") or "")
        g["node_ids"] = [str(x) for x in (g.get("node_ids") or [])]
        g["_set"] = set(g["node_ids"])
        by_key[key] = g

    masses = {k: group_effective_mass(g, nodes) for k, g in by_key.items()}
    cents = {k: core_centroid(g, pos, nodes) for k, g in by_key.items()}

    # Home: exclusive preferred (membership count==1)
    count: dict[str, int] = {}
    for g in groups:
        for n in g["node_ids"]:
            count[n] = count.get(n, 0) + 1
    home: dict[str, str] = {}
    for g in groups:
        k = str(g.get("key") or "")
        for n in g["node_ids"]:
            if count.get(n, 0) == 1:
                home[n] = k
            elif n not in home:
                home[n] = k

    stolen_nodes = 0
    for n, gkey in list(home.items()):
        if count.get(n, 0) > 1:
            continue  # shared portals stay
        role = str((nodes.get(n) or {}).get("role") or "free")
        if role == "core":
            continue
        if n not in pos:
            continue
        nx, ny = pos[n]
        hold = masses.get(gkey, 0.1) * _STAB.get(role, 0.7)
        best_h = None
        best_pull = 0.0
        for hk, hc in cents.items():
            if hk == gkey or hc is None:
                continue
            d = math.hypot(nx - hc[0], ny - hc[1])
            pull = masses.get(hk, 0.1) / (d + 1.0)
            if pull > best_pull:
                best_pull = pull
                best_h = hk
        if best_h is None:
            continue
        if best_pull > kappa_node * max(hold, 1e-6):
            # Move membership
            by_key[gkey]["_set"].discard(n)
            by_key[best_h]["_set"].add(n)
            home[n] = best_h
            stolen_nodes += 1

    stolen_blocks = 0
    # Block capture: weak whole group re-parented (merge members into strong).
    keys = list(by_key.keys())
    for i, wk in enumerate(keys):
        for sk in keys[i + 1 :]:
            for weak, strong in ((wk, sk), (sk, wk)):
                mw, ms = masses.get(weak, 0.1), masses.get(strong, 0.1)
                if ms < kappa_block * mw:
                    continue
                cw, cs = cents.get(weak), cents.get(strong)
                if cw is None or cs is None:
                    continue
                dist = math.hypot(cw[0] - cs[0], cw[1] - cs[1])
                if dist > rho_ideal * ideal_len:
                    continue
                # Contact: any bridge edge or shared portal
                contact = False
                wset = by_key[weak]["_set"]
                sset = by_key[strong]["_set"]
                if wset & sset:
                    contact = True
                else:
                    for a, b in state.links:
                        if (a in wset and b in sset) or (b in wset and a in sset):
                            contact = True
                            break
                if not contact:
                    continue
                # Absorb exclusive weak members into strong (keep soft parent key
                # on strong; clear weak exclusive into strong set).
                moved = [n for n in list(wset) if count.get(n, 0) == 1]
                if len(moved) < 2:
                    continue
                for n in moved:
                    by_key[weak]["_set"].discard(n)
                    by_key[strong]["_set"].add(n)
                    home[n] = strong
                stolen_blocks += 1
                masses[strong] = group_effective_mass(by_key[strong], nodes)
                masses[weak] = group_effective_mass(by_key[weak], nodes)
                cents[strong] = core_centroid(by_key[strong], pos, nodes)
                cents[weak] = core_centroid(by_key[weak], pos, nodes)
                break

    new_groups = []
    for g in groups:
        key = str(g.get("key") or "")
        members = sorted(by_key[key]["_set"])
        pivots = [p for p in (g.get("pivots") or []) if p in by_key[key]["_set"]]
        cores = [
            n
            for n in members
            if str((nodes.get(n) or {}).get("role")) == "core" or n in pivots
        ]
        new_groups.append(
            {
                "key": key,
                "node_ids": members,
                "pivots": pivots,
                "cores": cores or pivots,
                "soft": True,
            }
        )

    out = dict(mass)
    out["groups"] = new_groups
    report = {
        "stolen_nodes": stolen_nodes,
        "stolen_blocks": stolen_blocks,
        "kappa_node": kappa_node,
        "kappa_block": kappa_block,
    }
    return out, report


def geo_score(
    state: LayoutState,
    mass: dict[str, Any],
) -> float:
    """Higher is better: ring edge length consistency + core compactness."""
    nodes = mass.get("nodes") or {}
    edges = mass.get("edges") or {}
    pos = state.positions
    ring_lens: list[float] = []
    chain_lens: list[float] = []
    for key, row in edges.items():
        a, b = str(row.get("a") or ""), str(row.get("b") or "")
        if a not in pos or b not in pos:
            # key form
            if "|" in key:
                a, b = key.split("|", 1)
            else:
                continue
        if a not in pos or b not in pos:
            continue
        L = math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])
        role = str(row.get("role") or "plain")
        if role == "ring":
            ring_lens.append(L)
        elif role == "chain":
            chain_lens.append(L)

    score = 0.0
    if len(ring_lens) >= 2:
        mean = sum(ring_lens) / len(ring_lens)
        var = sum((x - mean) ** 2 for x in ring_lens) / len(ring_lens)
        score += 100.0 / (1.0 + math.sqrt(var) / max(mean, 1.0))
    elif ring_lens:
        score += 40.0

    # Penalize very long chains (should be tearable / reattached).
    if chain_lens:
        mean_c = sum(chain_lens) / len(chain_lens)
        score -= min(40.0, mean_c / 80.0)

    # Core neighborhood compactness
    cores = [n for n, r in nodes.items() if str(r.get("role")) == "core" and n in pos]
    if len(cores) >= 2:
        cx = sum(pos[n][0] for n in cores) / len(cores)
        cy = sum(pos[n][1] for n in cores) / len(cores)
        spread = sum(math.hypot(pos[n][0] - cx, pos[n][1] - cy) for n in cores) / len(
            cores
        )
        score += 80.0 / (1.0 + spread / 400.0)

    return float(score)


def attach_mass_to_compose_meta(
    meta: dict[str, Any],
    mass: dict[str, Any],
) -> dict[str, Any]:
    """Write mass_groups + mass_field onto compose meta (keep rigid_groups)."""
    out = dict(meta)
    groups = mass.get("groups") or []
    if not groups and out.get("rigid_groups"):
        groups = [
            {**g, "soft": True, "cores": list(g.get("pivots") or [])}
            for g in out["rigid_groups"]
            if isinstance(g, dict)
        ]
        mass = dict(mass)
        mass["groups"] = groups
    out["mass_groups"] = groups
    out["mass_field"] = {
        "nodes_n": len(mass.get("nodes") or {}),
        "edges_n": len(mass.get("edges") or {}),
        "units": mass.get("units") or [],
        "tip": mass.get("tip"),
    }
    # Full field stored at state.meta["mass_field"] by caller.
    out["soft"] = True
    return out


def groups_from_mass_or_rigid(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Prefer soft mass_groups, fall back to rigid_groups."""
    if not meta:
        return []
    raw = meta.get("mass_groups") or meta.get("rigid_groups") or []
    out: list[dict[str, Any]] = []
    for g in raw:
        if not isinstance(g, dict):
            continue
        nodes = [str(x) for x in (g.get("node_ids") or []) if str(x)]
        if len(nodes) < 2:
            continue
        pivots = [str(x) for x in (g.get("pivots") or []) if str(x)]
        out.append(
            {
                "key": str(g.get("key") or ""),
                "node_ids": nodes,
                "pivots": pivots,
                "cores": [str(x) for x in (g.get("cores") or pivots) if str(x)],
                "soft": bool(g.get("soft", True)),
            }
        )
    return out
