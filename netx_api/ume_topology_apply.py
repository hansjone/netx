"""Apply docked UME topology into Fabric (local attrs + edges, union with LLDP)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from .models import TopoFabricEdge, TopoFabricNode, UmeInventoryNE, UmeTopoLink, UmeTopoNode
from .topology_common import (
    _edge_attrs,
    _purge_edge_if_due,
    _set_edge_missing,
    _utcnow,
)
from .topology_fabric_links import (
    _primary_source,
    _sources_from_attrs,
    find_fabric_edge_compatible,
    upsert_fabric_edge,
)
from .ume_port_normalize import (
    is_label_placeholder_port,
    label_placeholder_ports,
    prefer_richer_ifname,
    resolve_link_ifnames,
)
from .topology_fabric_nodes import _normalize_endpoints, ensure_fabric_node_for_ume, refresh_fabric_stats
from .topology_lldp import normalize_ifname

_log = logging.getLogger("netx.ume.topo_apply")


def apply_ume_topology_to_fabric(db: Session) -> dict[str, Any]:
    """Upsert UME ME local coords/links into Fabric; reconcile ume provenance only.

    Local UME xPos/yPos are stored in attrs (ume_local_*), not as global world_*.
    Flat-world packing runs via ``recompute_flat_world_coords`` after apply.

    Edge presence = UME ∪ LLDP ∪ manual. Removing ume from dump only drops the
    ``ume`` source mark; if no sources remain, mark missing (未发现).
    """
    now = _utcnow()
    stats: dict[str, Any] = {
        "nodes_seen": 0,
        "nodes_ensured": 0,
        "nodes_coords": 0,
        "links_seen": 0,
        "edges_upserted": 0,
        "edges_merged": 0,
        "edges_skipped": 0,
        "ume_source_cleared": 0,
        "edges_missing": 0,
        "edges_purged": 0,
        "flat_coords": {},
    }

    inv_by_id = {
        str(r.ne_id): r
        for r in db.query(UmeInventoryNE).all()
        if str(r.ne_id or "").strip()
    }
    fabric_by_ume: dict[str, TopoFabricNode] = {
        str(n.ume_ne_id): n
        for n in db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id.isnot(None)).all()
        if str(n.ume_ne_id or "").strip()
    }

    me_nodes = (
        db.query(UmeTopoNode)
        .filter(UmeTopoNode.node_type == "TOPO_NODE_ME")
        .all()
    )
    # Advisory xact locks + nested savepoints accumulate; commit in batches or PG
    # hits OutOfMemory / max_locks_per_transaction on ~15k MEs.
    _NODE_BATCH = 250
    for i, tn in enumerate(me_nodes, start=1):
        uid = str(tn.ume_ne_id or tn.node_id or "").strip()
        if not uid:
            continue
        stats["nodes_seen"] += 1
        inv = inv_by_id.get(uid)
        if inv is not None:
            fn = ensure_fabric_node_for_ume(db, inv)
        else:
            fn = fabric_by_ume.get(uid)
            if fn is None:
                fn = TopoFabricNode(
                    id=uuid4().hex,
                    managed_ne_id=None,
                    ume_ne_id=uid,
                    name=(tn.user_label or uid)[:256],
                    ip="",
                    vendor="ZTE",
                    device_type="zte_zxros",
                    attrs={},
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(fn)
                db.flush()
            else:
                if tn.user_label:
                    fn.name = str(tn.user_label)[:256]
                fn.last_seen_at = now
                fn.updated_at = now
        stats["nodes_ensured"] += 1
        attrs = dict(fn.attrs or {})
        sources = set(attrs.get("sources") or [])
        if not isinstance(sources, set):
            sources = {str(x) for x in (sources or []) if str(x).strip()}
        sources.add("ume")
        attrs["sources"] = sorted(sources)
        if tn.x_pos is not None:
            attrs["ume_local_x"] = float(tn.x_pos)
            stats["nodes_coords"] += 1
        if tn.y_pos is not None:
            attrs["ume_local_y"] = float(tn.y_pos)
            if tn.x_pos is None:
                stats["nodes_coords"] += 1
        if tn.parent_node:
            attrs["ume_parent_node"] = str(tn.parent_node)[:512]
            attrs["ume_sbn_id"] = str(tn.parent_node)[:128]
        fn.attrs = attrs
        fabric_by_ume[uid] = fn
        if i % _NODE_BATCH == 0:
            db.commit()
            if i % 2000 == 0:
                _log.info("ume topology apply nodes progress %s/%s", i, len(me_nodes))

    db.commit()

    seen_edge_ids: set[str] = set()
    links = db.query(UmeTopoLink).all()
    for link in links:
        stats["links_seen"] += 1
        a_uid = str(link.a_ume_ne_id or "").strip()
        z_uid = str(link.z_ume_ne_id or "").strip()
        if not a_uid or not z_uid or a_uid == z_uid:
            stats["edges_skipped"] += 1
            continue
        a_fn = fabric_by_ume.get(a_uid)
        z_fn = fabric_by_ume.get(z_uid)
        if a_fn is None or z_fn is None:
            stats["edges_skipped"] += 1
            continue
        # Always re-resolve from TP+userLabel (new normalize rules); keep richer
        # of dock-stored vs fresh so Fabric ports stay aligned after ifname backfill.
        fresh_a, fresh_z = resolve_link_ifnames(
            a_end_tp_ref=link.a_end_tp_ref or "",
            z_end_tp_ref=link.z_end_tp_ref or "",
            user_label=link.user_label or "",
        )
        stored_a = normalize_ifname(str(link.a_ifname or "").strip())
        stored_z = normalize_ifname(str(link.z_ifname or "").strip())
        fresh_a = normalize_ifname(fresh_a)
        fresh_z = normalize_ifname(fresh_z)
        a_if = prefer_richer_ifname(stored_a, fresh_a) or fresh_a or stored_a
        z_if = prefer_richer_ifname(stored_z, fresh_z) or fresh_z or stored_z
        if a_if != stored_a or z_if != stored_z:
            link.a_ifname = (a_if or "")[:128]
            link.z_ifname = (z_if or "")[:128]
            link.last_seen_at = now
        display_label = str(link.user_label or "").strip()
        label_only = False
        if not a_if or not z_if:
            # No EQ+PTP → do not invent A/Z ports; show userLabel on canvas.
            if not display_label:
                stats["edges_skipped"] += 1
                continue
            a_if, z_if = label_placeholder_ports(str(link.link_id or ""))
            label_only = True

        existing = None
        if not label_only:
            existing = find_fabric_edge_compatible(
                db,
                a_node_id=a_fn.id,
                b_node_id=z_fn.id,
                a_port=a_if,
                b_port=z_if,
            )
        if existing is not None:
            # Merge onto existing LLDP/manual edge; keep/upgrade to richer port strings.
            _a, _b, nap, nbp = _normalize_endpoints(a_fn.id, z_fn.id, a_if, z_if)
            existing.a_port = prefer_richer_ifname(existing.a_port, nap)[:128]
            existing.b_port = prefer_richer_ifname(existing.b_port, nbp)[:128]
            attrs = _edge_attrs(existing)
            sources = _sources_from_attrs(attrs, fallback=existing.source or "")
            sources.add("ume")
            attrs["sources"] = sorted(sources)
            attrs["ume_link_id"] = str(link.link_id or "")[:128]
            if display_label:
                attrs["display_label"] = display_label[:512]
            existing.attrs = _clear_and_keep(attrs)
            if (existing.source or "") != "manual":
                existing.source = _primary_source(sources)
            existing.status = "active"
            existing.last_seen_at = now
            existing.updated_at = now
            if existing.discovered_at is None:
                existing.discovered_at = now
            seen_edge_ids.add(existing.id)
            stats["edges_merged"] += 1
            continue

        edge, action = upsert_fabric_edge(
            db,
            a_node_id=a_fn.id,
            b_node_id=z_fn.id,
            a_port=a_if,
            b_port=z_if,
            source="ume",
            now=now,
        )
        if edge is None:
            stats["edges_skipped"] += 1
            continue
        attrs = _edge_attrs(edge)
        attrs["ume_link_id"] = str(link.link_id or "")[:128]
        if display_label:
            attrs["display_label"] = display_label[:512]
        if label_only or is_label_placeholder_port(a_if):
            attrs["label_only"] = True
        edge.attrs = attrs
        seen_edge_ids.add(edge.id)
        stats["edges_upserted"] += 1
        if action == "kept_manual":
            stats["edges_merged"] += 1

    # Drop ume provenance from edges not seen this round.
    for edge in db.query(TopoFabricEdge).all():
        attrs = _edge_attrs(edge)
        sources = _sources_from_attrs(attrs, fallback=edge.source or "")
        if "ume" not in sources and str(edge.source or "").lower() != "ume":
            continue
        if edge.id in seen_edge_ids:
            continue
        sources.discard("ume")
        stats["ume_source_cleared"] += 1
        if "manual" in sources:
            attrs["sources"] = sorted(sources)
            edge.attrs = attrs
            edge.source = "manual"
            edge.updated_at = now
            continue
        if "lldp" in sources:
            attrs["sources"] = sorted(sources)
            edge.attrs = attrs
            edge.source = "lldp"
            # Keep active — LLDP still claims the edge until discover marks missing.
            edge.updated_at = now
            continue
        # No remaining discovery source → 未发现
        attrs["sources"] = []
        edge.attrs = attrs
        if _set_edge_missing(edge, now):
            stats["edges_missing"] += 1
        if _purge_edge_if_due(db, edge):
            stats["edges_purged"] += 1

    db.commit()
    try:
        from .ume_topology_flat_coords import recompute_flat_world_coords

        stats["flat_coords"] = recompute_flat_world_coords(db)
    except Exception:
        _log.exception("recompute_flat_world_coords after ume apply failed")
    try:
        refresh_fabric_stats(db)
    except Exception:
        _log.exception("refresh_fabric_stats after ume apply failed")
    _log.info("ume topology apply done: %s", stats)
    return stats


def ume_topology_apply_gap(db: Session) -> dict[str, Any]:
    """Detect dock→Fabric/World gap for startup heal and UI hints."""
    from sqlalchemy import func

    from .models import TopoFabricNode, TopoFolder, UmeSyncJob, UmeTopoNode
    from .ume_topology_world import get_world_container_folder

    dock_me = int(
        db.query(func.count(UmeTopoNode.node_id))
        .filter(UmeTopoNode.node_type == "TOPO_NODE_ME")
        .scalar()
        or 0
    )
    fabric_ume = int(
        db.query(func.count(TopoFabricNode.id))
        .filter(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != "")
        .scalar()
        or 0
    )
    world = get_world_container_folder(db)
    world_exists = world is not None
    latest = (
        db.query(UmeSyncJob)
        .filter(UmeSyncJob.domain == "topology", UmeSyncJob.ended_at.isnot(None))
        .order_by(UmeSyncJob.ended_at.desc())
        .first()
    )
    latest_status = str(getattr(latest, "status", "") or "")
    latest_error = str(getattr(latest, "error_message", "") or "")
    partial = latest_status == "partial" or latest_error.startswith("dock_ok_fabric_apply_failed")
    needs_apply = bool(dock_me > 0 and (fabric_ume == 0 or not world_exists or partial))
    return {
        "dock_me_count": dock_me,
        "fabric_ume_count": fabric_ume,
        "world_exists": world_exists,
        "world_folder_id": str(getattr(world, "id", "") or ""),
        "latest_topology_status": latest_status,
        "latest_topology_error": latest_error[:240],
        "partial_apply": partial,
        "needs_apply": needs_apply,
    }


def apply_ume_dock_to_fabric_if_needed(
    db: Session, *, reason: str = "manual", force: bool = False
) -> dict[str, Any] | None:
    """Apply dock tables into Fabric/World when gap detected (or force)."""
    from .ume_topology_world import ensure_ume_world_and_sbn_folders

    gap = ume_topology_apply_gap(db)
    if not force and not gap.get("needs_apply"):
        return None
    _log.info(
        "ume dock→fabric apply start reason=%s force=%s gap=%s",
        reason,
        force,
        {k: gap.get(k) for k in ("dock_me_count", "fabric_ume_count", "world_exists", "partial_apply")},
    )
    apply_stats = apply_ume_topology_to_fabric(db)
    world_stats = ensure_ume_world_and_sbn_folders(db)
    out = {
        "ok": True,
        "reason": reason,
        "before": gap,
        "fabric_apply": apply_stats,
        "world": world_stats,
    }
    _log.info(
        "ume dock→fabric apply done reason=%s nodes_ensured=%s",
        reason,
        (apply_stats or {}).get("nodes_ensured"),
    )
    return out


def _clear_and_keep(attrs: dict[str, Any]) -> dict[str, Any]:
    from .topology_common import _clear_miss_attrs

    return _clear_miss_attrs(attrs)
