"""Detach fabric ↔ inventory links; purge orphan / non-managed fabric nodes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .device_types import LLDP_DISCOVERED_NE_SOURCE, WEBCRT_NE_SOURCE, is_placeholder_ne_source
from .models import (
    ManagedNE,
    TopoFabricEdge,
    TopoFabricNode,
    TopoView,
    TopoViewEdgeStyle,
    TopoViewNode,
    UmeInventoryNE,
)

UME_SYNC_SOURCE = "ume_sync"
# ManagedNE.source values that are not "real" ops inventory (placeholders / sessions).
_NON_INVENTORY_MANAGED_SOURCES = frozenset(
    {
        LLDP_DISCOVERED_NE_SOURCE,
        WEBCRT_NE_SOURCE,
        "lldp",
        "webcrt",
        "topology",
    }
)


def _utcnow() -> datetime:
    return datetime.utcnow()


def _norm_ids(ids: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in ids or []:
        s = str(raw or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def fabric_link_status(n: TopoFabricNode) -> str:
    mid = str(n.managed_ne_id or "").strip()
    uid = str(n.ume_ne_id or "").strip()
    if mid and uid:
        return "both"
    if mid:
        return "managed"
    if uid:
        return "ume"
    return "orphaned"


def managed_source_label(source: str | None) -> str:
    src = str(source or "").strip().lower()
    if src == UME_SYNC_SOURCE:
        return "ume_sync"
    if is_placeholder_ne_source(src):
        if src in {"topology"}:
            return "topology"
        return "lldp"
    if src == WEBCRT_NE_SOURCE or src == "webcrt":
        return "webcrt"
    if not src:
        return "manual"
    return src


def _strip_membership_ids(
    db: Session,
    *,
    managed_ne_ids: set[str] | None = None,
    ume_ne_ids: set[str] | None = None,
) -> int:
    """Remove deleted inventory ids from view membership JSON. Returns views touched."""
    mids = managed_ne_ids or set()
    uids = ume_ne_ids or set()
    if not mids and not uids:
        return 0
    touched = 0
    for view in db.query(TopoView).all():
        filt = dict(view.filter or {})
        mem = filt.get("membership")
        if not isinstance(mem, dict):
            continue
        changed = False
        if mids and isinstance(mem.get("managed_ne_ids"), list):
            next_m = [str(x).strip() for x in mem["managed_ne_ids"] if str(x).strip() not in mids]
            if next_m != [str(x).strip() for x in mem["managed_ne_ids"] if str(x).strip()]:
                mem["managed_ne_ids"] = next_m
                changed = True
        if uids and isinstance(mem.get("ume_ne_ids"), list):
            next_u = [str(x).strip() for x in mem["ume_ne_ids"] if str(x).strip() not in uids]
            if next_u != [str(x).strip() for x in mem["ume_ne_ids"] if str(x).strip()]:
                mem["ume_ne_ids"] = next_u
                changed = True
        if not changed:
            continue
        filt["membership"] = mem
        view.filter = filt
        flag_modified(view, "filter")
        view.updated_at = _utcnow()
        touched += 1
    return touched


def detach_fabric_from_managed(db: Session, managed_ne_ids: list[str]) -> dict[str, int]:
    """Clear fabric.managed_ne_id for deleted ManagedNEs; keep nodes/placements/edges."""
    ids = _norm_ids(managed_ne_ids)
    if not ids:
        return {"detached_nodes": 0, "membership_views": 0}
    now = _utcnow()
    rows = (
        db.query(TopoFabricNode)
        .filter(TopoFabricNode.managed_ne_id.in_(ids))
        .all()
    )
    for row in rows:
        row.managed_ne_id = None
        row.updated_at = now
    views = _strip_membership_ids(db, managed_ne_ids=set(ids))
    return {"detached_nodes": len(rows), "membership_views": views}


def detach_fabric_from_ume(db: Session, ume_ne_ids: list[str]) -> dict[str, int]:
    """Clear fabric.ume_ne_id for deleted UME inventory rows; keep topology traces."""
    ids = _norm_ids(ume_ne_ids)
    if not ids:
        return {"detached_nodes": 0, "membership_views": 0}
    now = _utcnow()
    rows = db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id.in_(ids)).all()
    for row in rows:
        row.ume_ne_id = None
        row.updated_at = now
    views = _strip_membership_ids(db, ume_ne_ids=set(ids))
    return {"detached_nodes": len(rows), "membership_views": views}


def reconcile_dangling_fabric_links(db: Session) -> dict[str, int]:
    """Unbind fabric ids that no longer exist in managed_ne / ume_inventory_ne."""
    managed_alive = {str(x[0]) for x in db.query(ManagedNE.id).all() if str(x[0] or "").strip()}
    ume_alive = {
        str(x[0]) for x in db.query(UmeInventoryNE.ne_id).all() if str(x[0] or "").strip()
    }
    dangling_m: list[str] = []
    dangling_u: list[str] = []
    for n in db.query(TopoFabricNode).all():
        mid = str(n.managed_ne_id or "").strip()
        uid = str(n.ume_ne_id or "").strip()
        if mid and mid not in managed_alive:
            dangling_m.append(mid)
        if uid and uid not in ume_alive:
            dangling_u.append(uid)
    m_stats = detach_fabric_from_managed(db, dangling_m)
    u_stats = detach_fabric_from_ume(db, dangling_u)
    return {
        "dangling_managed_refs": len(set(dangling_m)),
        "dangling_ume_refs": len(set(dangling_u)),
        "detached_managed_nodes": int(m_stats.get("detached_nodes") or 0),
        "detached_ume_nodes": int(u_stats.get("detached_nodes") or 0),
        "membership_views": int(m_stats.get("membership_views") or 0)
        + int(u_stats.get("membership_views") or 0),
    }


def fabric_node_is_deletable(db: Session, n: TopoFabricNode) -> bool:
    """True for orphaned / dangling / LLDP·WebCRT placeholder fabric rows.

    UME-only and real managed inventory (manual / ume_sync) cannot be deleted here.
    """
    mid = str(n.managed_ne_id or "").strip()
    uid = str(n.ume_ne_id or "").strip()
    if not mid:
        # Orphaned only — keep UME-linked fabric rows.
        return not uid
    row = db.get(ManagedNE, mid)
    if row is None:
        return True
    src = str(row.source or "").strip().lower()
    return src in _NON_INVENTORY_MANAGED_SOURCES


def _strip_fabric_ids_from_membership(db: Session, fabric_ids: set[str]) -> int:
    if not fabric_ids:
        return 0
    touched = 0
    for view in db.query(TopoView).all():
        filt = dict(view.filter or {})
        mem = filt.get("membership")
        if not isinstance(mem, dict):
            continue
        changed = False
        for key in ("seed_fabric_node_ids", "member_fabric_node_ids"):
            raw = mem.get(key)
            if not isinstance(raw, list):
                continue
            next_ids = [str(x).strip() for x in raw if str(x).strip() not in fabric_ids]
            if next_ids != [str(x).strip() for x in raw if str(x).strip()]:
                mem[key] = next_ids
                changed = True
        if not changed:
            continue
        filt["membership"] = mem
        view.filter = filt
        flag_modified(view, "filter")
        view.updated_at = _utcnow()
        touched += 1
    return touched


def delete_fabric_nodes(db: Session, fabric_node_ids: list[str]) -> dict[str, int]:
    """Hard-delete fabric nodes (placements + edges). Does not touch managed/UME inventory."""
    ids = _norm_ids(fabric_node_ids)
    if not ids:
        raise HTTPException(status_code=400, detail="fabric_node_ids_required")
    rows = db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(ids)).all()
    found = {str(r.id): r for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"fabric_node_not_found:{missing[0]}")
    blocked = [i for i, r in found.items() if not fabric_node_is_deletable(db, r)]
    if blocked:
        raise HTTPException(
            status_code=400,
            detail=f"fabric_node_not_deletable:{blocked[0]}",
        )

    edge_ids = [
        str(e.id)
        for e in db.query(TopoFabricEdge)
        .filter(
            (TopoFabricEdge.a_node_id.in_(ids)) | (TopoFabricEdge.b_node_id.in_(ids))
        )
        .all()
    ]
    if edge_ids:
        db.query(TopoViewEdgeStyle).filter(
            TopoViewEdgeStyle.fabric_edge_id.in_(edge_ids)
        ).delete(synchronize_session=False)
        db.query(TopoFabricEdge).filter(TopoFabricEdge.id.in_(edge_ids)).delete(
            synchronize_session=False
        )
    placements = (
        db.query(TopoViewNode)
        .filter(TopoViewNode.fabric_node_id.in_(ids))
        .delete(synchronize_session=False)
    )
    _strip_fabric_ids_from_membership(db, set(ids))
    for r in rows:
        db.delete(r)
    db.commit()
    try:
        from .topology_service import refresh_fabric_stats

        refresh_fabric_stats(db)
    except Exception:  # noqa: BLE001
        pass
    return {
        "deleted": len(rows),
        "edges_deleted": len(edge_ids),
        "placements_deleted": int(placements or 0),
    }


def enrich_fabric_node_dicts(
    db: Session, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add link_status / alive flags / managed_source onto FabricNodeOut dicts."""
    if not items:
        return items
    mids = [str(x.get("managed_ne_id") or "").strip() for x in items]
    uids = [str(x.get("ume_ne_id") or "").strip() for x in items]
    mids_f = [x for x in mids if x]
    uids_f = [x for x in uids if x]
    managed_by_id: dict[str, ManagedNE] = {}
    if mids_f:
        for row in db.query(ManagedNE).filter(ManagedNE.id.in_(mids_f)).all():
            managed_by_id[str(row.id)] = row
    ume_alive: set[str] = set()
    if uids_f:
        ume_alive = {
            str(x[0])
            for x in db.query(UmeInventoryNE.ne_id)
            .filter(UmeInventoryNE.ne_id.in_(uids_f))
            .all()
            if str(x[0] or "").strip()
        }
    for item in items:
        mid = str(item.get("managed_ne_id") or "").strip()
        uid = str(item.get("ume_ne_id") or "").strip()
        if mid and uid:
            item["link_status"] = "both"
        elif mid:
            item["link_status"] = "managed"
        elif uid:
            item["link_status"] = "ume"
        else:
            item["link_status"] = "orphaned"
        mrow = managed_by_id.get(mid) if mid else None
        item["managed_alive"] = bool(mrow)
        item["ume_alive"] = bool(uid and uid in ume_alive)
        item["managed_source"] = managed_source_label(mrow.source) if mrow else ""
        if not mid:
            item["deletable"] = not bool(uid)
        elif mrow is None:
            item["deletable"] = True
        else:
            item["deletable"] = (
                str(mrow.source or "").strip().lower() in _NON_INVENTORY_MANAGED_SOURCES
            )
    return items
