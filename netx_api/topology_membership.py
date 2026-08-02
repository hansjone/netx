"""View membership boundaries — keep leaf canvases from sucking in the whole fabric."""

from __future__ import annotations

from typing import Any

VIEW_ROLE_CORE = "core"
VIEW_ROLE_AGGREGATION = "aggregation"
VIEW_ROLE_ACCESS = "access"
VIEW_ROLES = frozenset({VIEW_ROLE_CORE, VIEW_ROLE_AGGREGATION, VIEW_ROLE_ACCESS})

VIEW_KIND_PHYSICAL = "physical"
VIEW_KIND_CUSTOM = "custom"
VIEW_KINDS = frozenset({VIEW_KIND_PHYSICAL, VIEW_KIND_CUSTOM})

ROLE_DEFAULT_MAX_NODES = {
    VIEW_ROLE_CORE: 80,
    VIEW_ROLE_AGGREGATION: 200,
    VIEW_ROLE_ACCESS: 300,
}
KIND_DEFAULT_MAX_NODES = {
    VIEW_KIND_PHYSICAL: 500,
    VIEW_KIND_CUSTOM: 300,
}

MEMBERSHIP_HARD_CAP = 2000
DEFAULT_EXPAND_HOPS = 1
MAX_EXPAND_HOPS = 3


def normalize_view_role(role: str | None) -> str:
    r = str(role or "").strip().lower()
    if r in VIEW_ROLES:
        return r
    return VIEW_ROLE_CORE


def normalize_view_kind(kind: str | None) -> str:
    k = str(kind or "").strip().lower()
    if k in VIEW_KINDS:
        return k
    return VIEW_KIND_CUSTOM


def default_max_nodes_for_role(role: str) -> int:
    return int(ROLE_DEFAULT_MAX_NODES.get(normalize_view_role(role), 300))


def default_max_nodes_for_kind(kind: str) -> int:
    return int(KIND_DEFAULT_MAX_NODES.get(normalize_view_kind(kind), 300))


def default_membership(
    role: str = VIEW_ROLE_CORE, *, kind: str = VIEW_KIND_CUSTOM
) -> dict[str, Any]:
    k = normalize_view_kind(kind)
    max_nodes = (
        default_max_nodes_for_kind(k)
        if k == VIEW_KIND_PHYSICAL
        else default_max_nodes_for_role(role)
    )
    return {
        "mode": "hybrid",
        "managed_ne_ids": [],
        "tags_any": [],
        "vendors": [],
        "device_types": [],
        "keyword": "",
        "seed_fabric_node_ids": [],
        "expand_hops": DEFAULT_EXPAND_HOPS,
        "max_nodes": max_nodes,
        "frozen": False,
    }


def parse_membership(
    filt: dict[str, Any] | None,
    *,
    role: str = VIEW_ROLE_CORE,
    kind: str = VIEW_KIND_CUSTOM,
) -> dict[str, Any]:
    base = default_membership(role, kind=kind)
    raw = dict(filt or {})
    mem = raw.get("membership")
    if not isinstance(mem, dict):
        return base
    out = dict(base)
    out["mode"] = str(mem.get("mode") or base["mode"]).strip() or "hybrid"
    out["managed_ne_ids"] = [
        str(x).strip() for x in (mem.get("managed_ne_ids") or []) if str(x).strip()
    ]
    out["tags_any"] = [str(x).strip() for x in (mem.get("tags_any") or []) if str(x).strip()]
    out["vendors"] = [str(x).strip() for x in (mem.get("vendors") or []) if str(x).strip()]
    out["device_types"] = [
        str(x).strip() for x in (mem.get("device_types") or []) if str(x).strip()
    ]
    out["keyword"] = str(mem.get("keyword") or "").strip()
    out["seed_fabric_node_ids"] = [
        str(x).strip() for x in (mem.get("seed_fabric_node_ids") or []) if str(x).strip()
    ]
    try:
        hops = int(mem.get("expand_hops", DEFAULT_EXPAND_HOPS))
    except (TypeError, ValueError):
        hops = DEFAULT_EXPAND_HOPS
    out["expand_hops"] = max(0, min(MAX_EXPAND_HOPS, hops))
    try:
        mx = int(mem.get("max_nodes", base["max_nodes"]))
    except (TypeError, ValueError):
        mx = int(base["max_nodes"])
    out["max_nodes"] = max(1, min(MEMBERSHIP_HARD_CAP, mx))
    out["frozen"] = bool(mem.get("frozen", False))
    return out


def merge_filter_with_membership(
    filt: dict[str, Any] | None,
    *,
    role: str,
    kind: str = VIEW_KIND_CUSTOM,
    membership: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(filt or {})
    layer = str(out.get("layer") or "physical").strip() or "physical"
    out["layer"] = layer
    mem = (
        membership
        if membership is not None
        else parse_membership(out, role=role, kind=kind)
    )
    out["membership"] = mem
    return out


def has_hard_scope(mem: dict[str, Any]) -> bool:
    return bool(
        mem.get("managed_ne_ids")
        or mem.get("tags_any")
        or mem.get("vendors")
        or mem.get("device_types")
        or mem.get("keyword")
    )
