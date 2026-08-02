"""Fabric nodes/edges, stats, inventory matching, and merge."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cli_resolve import get_default_profile, infer_device_type_vendor
from .device_types import LLDP_DISCOVERED_NE_SOURCE, WEBCRT_NE_SOURCE
from .models import (
    ManagedNE,
    TopoFabricEdge,
    TopoFabricNode,
    TopoFabricStats,
    TopoViewEdgeStyle,
    TopoViewNode,
    UmeInventoryNE,
)
from .topology_common import (
    PAGE_DEFAULT,
    PAGE_MAX,
    VIEW_GRAPH_EDGE_HARD_CAP,
    _ADV_NS_FABRIC_MANAGED,
    _ADV_NS_FABRIC_UME,
    _EDGE_STATUS_MISSING,
    _EDGE_STATUS_MISSING_COMPAT,
    _MISS_PURGE_AFTER_CYCLES,
    _advisory_xact_lock,
    _clear_miss_attrs,
    _empty_to_none,
    _is_deadlock_error,
    _is_postgres,
    _norm_host,
    _normalize_edge_status,
    _purge_edge_if_due,
    _set_edge_missing,
    _sleep_deadlock_backoff,
    _utcnow,
    _edge_attrs,
)
from .topology_lldp import NeighborHit, normalize_ifname
from .topology_schemas import (
    FabricEdgeOut,
    FabricNeighborhoodOut,
    FabricNodeOut,
    FabricSummaryOut,
)

# ---------------------------------------------------------------------------
# Fabric nodes / edges helpers
# ---------------------------------------------------------------------------


def _node_out(n: TopoFabricNode) -> FabricNodeOut:
    return FabricNodeOut(
        role=str(getattr(n, "role", "") or ""),
        region_folder_id=str(getattr(n, "region_folder_id", None) or "") or None,
        role_source=str(getattr(n, "role_source", "") or ""),
        region_source=str(getattr(n, "region_source", "") or ""),
        id=n.id,
        managed_ne_id=n.managed_ne_id or "",
        ume_ne_id=n.ume_ne_id or "",
        name=n.name or "",
        ip=n.ip or "",
        vendor=n.vendor or "",
        device_type=n.device_type or "",
        attrs=dict(n.attrs or {}),
        last_seen_at=n.last_seen_at,
    )


def _edge_out(
    e: TopoFabricEdge,
    *,
    nodes_by_id: dict[str, TopoFabricNode] | None = None,
) -> FabricEdgeOut:
    src = str(e.source or "lldp").strip().lower() or "lldp"
    if src == "stale":
        src = "lldp"
    a_node = (nodes_by_id or {}).get(e.a_node_id)
    b_node = (nodes_by_id or {}).get(e.b_node_id)
    return FabricEdgeOut(
        id=e.id,
        layer=e.layer or "physical",
        a_node_id=e.a_node_id,
        b_node_id=e.b_node_id,
        a_port=e.a_port or "",
        b_port=e.b_port or "",
        a_name=(a_node.name if a_node else "") or "",
        b_name=(b_node.name if b_node else "") or "",
        a_ip=(a_node.ip if a_node else "") or "",
        b_ip=(b_node.ip if b_node else "") or "",
        source=src,
        status=_normalize_edge_status(e.status or "active"),
        attrs=dict(e.attrs or {}),
        discovered_at=e.discovered_at,
        last_seen_at=e.last_seen_at,
        updated_at=e.updated_at,
    )


def _nodes_by_ids(db: Session, ids: set[str]) -> dict[str, TopoFabricNode]:
    clean = {str(i).strip() for i in ids if str(i or "").strip()}
    if not clean:
        return {}
    rows = db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(list(clean))).all()
    return {r.id: r for r in rows}


def _normalize_endpoints(
    a_id: str, b_id: str, a_port: str, b_port: str
) -> tuple[str, str, str, str]:
    ap = normalize_ifname(a_port)
    bp = normalize_ifname(b_port)
    if a_id <= b_id:
        return a_id, b_id, ap, bp
    return b_id, a_id, bp, ap


def ensure_fabric_node_for_managed(db: Session, ne: ManagedNE) -> TopoFabricNode:
    mid = str(ne.id or "").strip()
    now = _utcnow()

    def _apply(row: TopoFabricNode) -> TopoFabricNode:
        row.name = (ne.name or row.name or "")[:256]
        row.ip = (ne.ip_address or row.ip or "")[:128]
        row.vendor = (ne.vendor or row.vendor or "")[:64]
        row.device_type = (ne.device_type or row.device_type or "")[:64]
        row.last_seen_at = now
        row.updated_at = now
        return row

    row = db.query(TopoFabricNode).filter(TopoFabricNode.managed_ne_id == mid).one_or_none()
    if row is not None:
        return _apply(row)
    # Serialize same-key creates across workers (cross-key deadlocks still retried upstream).
    _advisory_xact_lock(db, _ADV_NS_FABRIC_MANAGED, mid)
    row = db.query(TopoFabricNode).filter(TopoFabricNode.managed_ne_id == mid).one_or_none()
    if row is not None:
        return _apply(row)
    try:
        with db.begin_nested():
            row = TopoFabricNode(
                id=uuid4().hex,
                managed_ne_id=mid,
                ume_ne_id=None,
                name=(ne.name or "")[:256],
                ip=(ne.ip_address or "")[:128],
                vendor=(ne.vendor or "")[:64],
                device_type=(ne.device_type or "")[:64],
                attrs={},
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            return row
    except IntegrityError:
        existing = db.query(TopoFabricNode).filter(TopoFabricNode.managed_ne_id == mid).one_or_none()
        if existing is None:
            raise
        return _apply(existing)


def ensure_fabric_node_for_ume(
    db: Session, ume: UmeInventoryNE, *, device_type: str = "", vendor: str = ""
) -> TopoFabricNode:
    uid = str(ume.ne_id or "").strip()
    now = _utcnow()
    name = (ume.host_name or ume.ne_name or ume.user_label or ume.ip_address or uid).strip()

    def _apply(row: TopoFabricNode) -> TopoFabricNode:
        row.name = name[:256]
        row.ip = (ume.ip_address or row.ip or "")[:128]
        if vendor:
            row.vendor = vendor[:64]
        if device_type:
            row.device_type = device_type[:64]
        row.last_seen_at = now
        row.updated_at = now
        return row

    row = db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id == uid).one_or_none()
    if row is not None:
        return _apply(row)
    _advisory_xact_lock(db, _ADV_NS_FABRIC_UME, uid)
    row = db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id == uid).one_or_none()
    if row is not None:
        return _apply(row)
    try:
        with db.begin_nested():
            row = TopoFabricNode(
                id=uuid4().hex,
                managed_ne_id=None,
                ume_ne_id=uid,
                name=name[:256],
                ip=(ume.ip_address or "")[:128],
                vendor=(vendor or ume.vendor or "ZTE")[:64],
                device_type=(device_type or "zte_zxros")[:64],
                attrs={},
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            db.flush()
            return row
    except IntegrityError:
        existing = db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id == uid).one_or_none()
        if existing is None:
            raise
        return _apply(existing)


def refresh_fabric_stats(db: Session) -> TopoFabricStats:
    now = _utcnow()
    row = db.get(TopoFabricStats, "global")
    if row is None:
        row = TopoFabricStats(id="global")
        db.add(row)
    row.node_count = int(db.query(func.count(TopoFabricNode.id)).scalar() or 0)
    row.edge_count = int(db.query(func.count(TopoFabricEdge.id)).scalar() or 0)
    row.edge_active = int(
        db.query(func.count(TopoFabricEdge.id))
        .filter(TopoFabricEdge.status == "active")
        .scalar()
        or 0
    )
    row.edge_stale = int(
        db.query(func.count(TopoFabricEdge.id))
        .filter(TopoFabricEdge.status.in_(list(_EDGE_STATUS_MISSING_COMPAT)))
        .scalar()
        or 0
    )
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


def get_fabric_summary(db: Session) -> FabricSummaryOut:
    row = db.get(TopoFabricStats, "global")
    if row is None:
        row = refresh_fabric_stats(db)
    return FabricSummaryOut(
        node_count=row.node_count,
        edge_count=row.edge_count,
        edge_active=row.edge_active,
        edge_stale=row.edge_stale,
        edge_missing=row.edge_stale,
        last_discover_at=row.last_discover_at,
        updated_at=row.updated_at,
    )


def list_fabric_nodes(
    db: Session,
    *,
    keyword: str = "",
    role: str = "",
    region_folder_id: str = "",
    unmatched: str = "",
    link_status: str = "",
    page: int = 1,
    page_size: int = PAGE_DEFAULT,
) -> dict[str, Any]:
    from .topology_inventory_lifecycle import enrich_fabric_node_dicts

    page = max(1, int(page or 1))
    page_size = max(1, min(PAGE_MAX, int(page_size or PAGE_DEFAULT)))
    q = db.query(TopoFabricNode)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            or_(
                TopoFabricNode.name.ilike(like),
                TopoFabricNode.ip.ilike(like),
                TopoFabricNode.managed_ne_id.ilike(like),
                TopoFabricNode.ume_ne_id.ilike(like),
            )
        )
    role_v = str(role or "").strip().lower()
    if role_v:
        q = q.filter(TopoFabricNode.role == role_v)
    region_v = str(region_folder_id or "").strip()
    if region_v:
        q = q.filter(TopoFabricNode.region_folder_id == region_v)
    um = str(unmatched or "").strip().lower()
    if um == "role":
        q = q.filter(or_(TopoFabricNode.role == "", TopoFabricNode.role == "unknown"))
    elif um == "region":
        q = q.filter(
            or_(TopoFabricNode.region_folder_id.is_(None), TopoFabricNode.region_folder_id == "")
        )
    elif um == "any":
        q = q.filter(
            or_(
                TopoFabricNode.role == "",
                TopoFabricNode.role == "unknown",
                TopoFabricNode.region_folder_id.is_(None),
                TopoFabricNode.region_folder_id == "",
            )
        )
    ls = str(link_status or "").strip().lower()
    if ls == "orphaned":
        q = q.filter(
            or_(TopoFabricNode.managed_ne_id.is_(None), TopoFabricNode.managed_ne_id == ""),
            or_(TopoFabricNode.ume_ne_id.is_(None), TopoFabricNode.ume_ne_id == ""),
        )
    elif ls == "linked":
        q = q.filter(
            or_(
                and_(TopoFabricNode.managed_ne_id.isnot(None), TopoFabricNode.managed_ne_id != ""),
                and_(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != ""),
            )
        )
    elif ls == "managed":
        q = q.filter(
            and_(TopoFabricNode.managed_ne_id.isnot(None), TopoFabricNode.managed_ne_id != ""),
            or_(TopoFabricNode.ume_ne_id.is_(None), TopoFabricNode.ume_ne_id == ""),
        )
    elif ls == "ume":
        q = q.filter(
            and_(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != ""),
            or_(TopoFabricNode.managed_ne_id.is_(None), TopoFabricNode.managed_ne_id == ""),
        )
    elif ls == "both":
        q = q.filter(
            and_(TopoFabricNode.managed_ne_id.isnot(None), TopoFabricNode.managed_ne_id != ""),
            and_(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != ""),
        )
    total = int(q.count())
    rows = (
        q.order_by(TopoFabricNode.name.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = enrich_fabric_node_dicts(db, [_node_out(n).model_dump() for n in rows])
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


def list_fabric_edges(
    db: Session,
    *,
    node_id: str = "",
    layer: str = "physical",
    status: str = "",
    source: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = PAGE_DEFAULT,
) -> dict[str, Any]:
    page = max(1, int(page or 1))
    page_size = max(1, min(PAGE_MAX, int(page_size or PAGE_DEFAULT)))
    q = db.query(TopoFabricEdge)
    layer_v = str(layer or "physical").strip() or "physical"
    q = q.filter(TopoFabricEdge.layer == layer_v)
    nid = str(node_id or "").strip()
    if nid:
        q = q.filter(or_(TopoFabricEdge.a_node_id == nid, TopoFabricEdge.b_node_id == nid))
    st = str(status or "").strip().lower()
    if st in _EDGE_STATUS_MISSING_COMPAT:
        q = q.filter(TopoFabricEdge.status.in_(list(_EDGE_STATUS_MISSING_COMPAT)))
    elif st:
        q = q.filter(TopoFabricEdge.status == st)
    src = str(source or "").strip().lower()
    if src:
        if src == "stale":
            src = "lldp"
        q = q.filter(TopoFabricEdge.source == src)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        matched_ids = [
            r.id
            for r in db.query(TopoFabricNode.id)
            .filter(or_(TopoFabricNode.name.ilike(like), TopoFabricNode.ip.ilike(like)))
            .limit(2000)
            .all()
        ]
        if not matched_ids:
            return {"total": 0, "page": page, "page_size": page_size, "items": []}
        q = q.filter(
            or_(
                TopoFabricEdge.a_node_id.in_(matched_ids),
                TopoFabricEdge.b_node_id.in_(matched_ids),
            )
        )
    total = int(q.count())
    rows = (
        q.order_by(TopoFabricEdge.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    node_map = _nodes_by_ids(db, {e.a_node_id for e in rows} | {e.b_node_id for e in rows})
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_edge_out(e, nodes_by_id=node_map).model_dump() for e in rows],
    }


def get_fabric_neighborhood(
    db: Session, node_id: str, *, depth: int = 1, layer: str = "physical"
) -> FabricNeighborhoodOut:
    center = str(node_id or "").strip()
    if not center or db.get(TopoFabricNode, center) is None:
        raise HTTPException(status_code=404, detail="fabric_node_not_found")
    depth = max(1, min(3, int(depth or 1)))
    layer_v = str(layer or "physical").strip() or "physical"
    seen_nodes = {center}
    frontier = {center}
    edges: dict[str, TopoFabricEdge] = {}
    for _ in range(depth):
        if not frontier:
            break
        batch = (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.layer == layer_v,
                or_(
                    TopoFabricEdge.a_node_id.in_(list(frontier)),
                    TopoFabricEdge.b_node_id.in_(list(frontier)),
                ),
            )
            .limit(VIEW_GRAPH_EDGE_HARD_CAP)
            .all()
        )
        next_frontier: set[str] = set()
        for e in batch:
            edges[e.id] = e
            for nid in (e.a_node_id, e.b_node_id):
                if nid not in seen_nodes:
                    next_frontier.add(nid)
                    seen_nodes.add(nid)
        frontier = next_frontier
    nodes = db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(list(seen_nodes))).all()
    return FabricNeighborhoodOut(
        center_node_id=center,
        depth=depth,
        nodes=[_node_out(n) for n in nodes],
        edges=[_edge_out(e) for e in edges.values()],
    )


def upsert_fabric_edge(
    db: Session,
    *,
    a_node_id: str,
    b_node_id: str,
    a_port: str,
    b_port: str,
    source: str = "lldp",
    layer: str = "physical",
    now: datetime | None = None,
) -> tuple[TopoFabricEdge, str]:
    """Return (edge, action) where action is added|updated|kept_manual."""
    now = now or _utcnow()
    a, b, ap, bp = _normalize_endpoints(a_node_id, b_node_id, a_port, b_port)
    if a == b:
        raise HTTPException(status_code=400, detail="edge_self_loop")
    layer_v = str(layer or "physical").strip() or "physical"
    src = str(source or "lldp").strip().lower() or "lldp"
    if src == "stale":
        src = "lldp"
    if src not in {"lldp", "manual"}:
        raise HTTPException(status_code=400, detail="invalid_edge_source")
    row = (
        db.query(TopoFabricEdge)
        .filter(
            TopoFabricEdge.layer == layer_v,
            TopoFabricEdge.a_node_id == a,
            TopoFabricEdge.b_node_id == b,
            TopoFabricEdge.a_port == ap,
            TopoFabricEdge.b_port == bp,
        )
        .one_or_none()
    )
    if row is None:
        try:
            with db.begin_nested():
                row = TopoFabricEdge(
                    id=uuid4().hex,
                    layer=layer_v,
                    a_node_id=a,
                    b_node_id=b,
                    a_port=ap,
                    b_port=bp,
                    source=src,
                    status="active",
                    attrs={},
                    discovered_at=now if src == "lldp" else None,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                db.flush()
                return row, "added"
        except IntegrityError:
            row = (
                db.query(TopoFabricEdge)
                .filter(
                    TopoFabricEdge.layer == layer_v,
                    TopoFabricEdge.a_node_id == a,
                    TopoFabricEdge.b_node_id == b,
                    TopoFabricEdge.a_port == ap,
                    TopoFabricEdge.b_port == bp,
                )
                .one_or_none()
            )
            if row is None:
                raise
    if (row.source or "") == "manual" and src == "lldp":
        return row, "kept_manual"
    row.source = src
    row.status = "active"
    row.attrs = _clear_miss_attrs(_edge_attrs(row))
    if src == "lldp":
        row.discovered_at = row.discovered_at or now
    row.last_seen_at = now
    row.updated_at = now
    return row, "updated"


def _absorb_fabric_node(db: Session, canon: TopoFabricNode, dupe: TopoFabricNode) -> None:
    """Retarget edges/view placements from dupe onto canon, then delete dupe."""
    if canon is None or dupe is None or canon.id == dupe.id:
        return
    if db.get(TopoFabricNode, dupe.id) is None:
        return
    _retarget_fabric_edges(db, from_id=dupe.id, to_id=canon.id)
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.fabric_node_id == dupe.id).all()
    for vn in vnodes:
        exists = (
            db.query(TopoViewNode)
            .filter(
                TopoViewNode.view_id == vn.view_id,
                TopoViewNode.fabric_node_id == canon.id,
            )
            .one_or_none()
        )
        if exists is not None:
            db.delete(vn)
        else:
            vn.fabric_node_id = canon.id
            vn.updated_at = _utcnow()
    db.delete(dupe)


def _prefer_fabric_canon(
    db: Session, a: TopoFabricNode, b: TopoFabricNode
) -> tuple[TopoFabricNode, TopoFabricNode]:
    """Return (canon, dupe) preferring higher inventory score, then older row."""
    sa = _fabric_match_score(db, a)
    sb = _fabric_match_score(db, b)
    if sa != sb:
        return (a, b) if sa > sb else (b, a)
    ta = a.created_at or a.updated_at
    tb = b.created_at or b.updated_at
    if ta and tb and ta != tb:
        return (a, b) if ta <= tb else (b, a)
    return (a, b) if a.id <= b.id else (b, a)


def _mark_replaced_port_peers(
    db: Session,
    *,
    self_id: str,
    local_port: str,
    peer_id: str,
    new_edge_id: str,
    layer: str = "physical",
    now: datetime | None = None,
) -> list[str]:
    """Same local port now peers with a different NE → mark old edges missing (cutover).

    If the previous peer is the same hostname (duplicate fabric rows for one device),
    absorb the weaker node instead of marking the link missing.

    Returns ids of edges touched by this replacement (skip re-bump in same job).
    """
    now = now or _utcnow()
    lp = normalize_ifname(local_port)
    if not self_id or not peer_id or not lp:
        return []
    layer_v = str(layer or "physical").strip() or "physical"
    new_peer = db.get(TopoFabricNode, peer_id)
    new_name = _norm_host(new_peer.name if new_peer is not None else "")
    candidates = (
        db.query(TopoFabricEdge)
        .filter(
            TopoFabricEdge.layer == layer_v,
            TopoFabricEdge.id != new_edge_id,
            TopoFabricEdge.source != "manual",
            or_(TopoFabricEdge.a_node_id == self_id, TopoFabricEdge.b_node_id == self_id),
        )
        .all()
    )
    handled: list[str] = []
    for e in candidates:
        if e.a_node_id == self_id:
            e_local, e_peer = e.a_port or "", e.b_node_id
        else:
            e_local, e_peer = e.b_port or "", e.a_node_id
        if normalize_ifname(e_local) != lp:
            continue
        if e_peer == peer_id:
            continue
        old_peer = db.get(TopoFabricNode, e_peer)
        old_name = _norm_host(old_peer.name if old_peer is not None else "")
        # Same System Name under two fabric nodes → collapse, keep one link.
        if (
            new_peer is not None
            and old_peer is not None
            and new_name
            and old_name
            and new_name == old_name
        ):
            canon, dupe = _prefer_fabric_canon(db, new_peer, old_peer)
            _absorb_fabric_node(db, canon, dupe)
            # Survivor edge on this port should stay active (retarget may have merged).
            survivor = (
                db.query(TopoFabricEdge)
                .filter(
                    TopoFabricEdge.layer == layer_v,
                    or_(
                        and_(
                            TopoFabricEdge.a_node_id == self_id,
                            TopoFabricEdge.b_node_id == canon.id,
                        ),
                        and_(
                            TopoFabricEdge.b_node_id == self_id,
                            TopoFabricEdge.a_node_id == canon.id,
                        ),
                    ),
                )
                .all()
            )
            for se in survivor:
                se_local = se.a_port if se.a_node_id == self_id else se.b_port
                if normalize_ifname(se_local or "") != lp:
                    continue
                se.status = "active"
                se.attrs = _clear_miss_attrs(_edge_attrs(se))
                se.last_seen_at = now
                se.updated_at = now
                handled.append(se.id)
            handled.append(e.id)
            continue
        _set_edge_missing(e, now, replaced_by_edge_id=new_edge_id)
        handled.append(e.id)
    return handled


def _apply_missing_and_purge(
    db: Session,
    *,
    scanned_ok: set[str],
    touched_edge_ids: set[str],
    now: datetime | None = None,
) -> tuple[int, int]:
    """Rule A: endpoint scanned OK but edge absent → missing; purge after N cycles.

    Returns (newly_marked_missing, purged).
    """
    now = now or _utcnow()
    if not scanned_ok:
        return 0, 0
    edges = (
        db.query(TopoFabricEdge)
        .filter(
            TopoFabricEdge.layer == "physical",
            TopoFabricEdge.source != "manual",
            or_(
                TopoFabricEdge.a_node_id.in_(list(scanned_ok)),
                TopoFabricEdge.b_node_id.in_(list(scanned_ok)),
            ),
        )
        .all()
    )
    newly_marked = 0
    purged = 0
    for e in edges:
        if e.id in touched_edge_ids:
            continue
        if e.a_node_id not in scanned_ok and e.b_node_id not in scanned_ok:
            continue
        if _set_edge_missing(e, now):
            newly_marked += 1
        if _purge_edge_if_due(db, e):
            purged += 1
    return newly_marked, purged


# ---------------------------------------------------------------------------
# LLDP discovery → fabric
# ---------------------------------------------------------------------------


def _is_inventory_node(n: TopoFabricNode) -> bool:
    return bool(str(n.managed_ne_id or "").strip() or str(n.ume_ne_id or "").strip())


def _managed_source(db: Session, ne_id: str | None) -> str:
    mid = str(ne_id or "").strip()
    if not mid:
        return ""
    ne = db.get(ManagedNE, mid)
    if ne is None:
        return ""
    return str(ne.source or "").strip().lower()


def _ne_inventory_score(ne: ManagedNE) -> int:
    """Prefer real inventory over LLDP placeholders; never prefer WebCRT twins."""
    src = str(ne.source or "").strip().lower()
    if src == WEBCRT_NE_SOURCE:
        return 0
    if src == LLDP_DISCOVERED_NE_SOURCE:
        return 1
    return 2


def _fabric_match_score(db: Session, n: TopoFabricNode) -> int:
    """Higher = prefer when collapsing LLDP hits / duplicate IPs.

    WebCRT quick-connect intentionally allows duplicate IPs as separate ManagedNE
    rows; those must lose to real inventory NEs with the same address.
    LLDP placeholders (SSH shell, empty creds) rank above WebCRT, below real NEs.
    """
    if str(n.ume_ne_id or "").strip():
        return 3
    mid = str(n.managed_ne_id or "").strip()
    if not mid:
        return 0
    src = _managed_source(db, mid)
    if src == WEBCRT_NE_SOURCE:
        return 1
    if src == LLDP_DISCOVERED_NE_SOURCE:
        return 2
    return 4


def _pick_managed_ne(
    db: Session, *, ip: str = "", name_key: str = ""
) -> ManagedNE | None:
    """Pick inventory NE. Name matching uses hostname key (not LLDP mgmt IP)."""
    rows: list[ManagedNE] = []
    if name_key:
        key = _norm_host(name_key) or str(name_key or "").strip().lower()
        if key:
            candidates = (
                db.query(ManagedNE)
                .filter(
                    or_(
                        func.lower(ManagedNE.name) == key,
                        func.lower(ManagedNE.name).like(f"{key}.%"),
                    )
                )
                .all()
            )
            rows = [ne for ne in candidates if _norm_host(ne.name or "") == key]
    elif ip:
        # Kept for non-LLDP callers; LLDP peer match must not use this path.
        rows = db.query(ManagedNE).filter(ManagedNE.ip_address == ip).all()
    if not rows:
        return None
    rows.sort(key=_ne_inventory_score, reverse=True)
    best = rows[0]
    # Only-WebCRT IP collision must not become a topology peer — treat as unmatched
    # so discover can create an LLDP placeholder instead.
    if _ne_inventory_score(best) == 0:
        return None
    return best


def ensure_lldp_discovered_managed_ne(
    db: Session,
    *,
    remote_name: str = "",
    remote_ip: str = "",
    placeholder_by_name: dict[str, ManagedNE] | None = None,
) -> ManagedNE:
    """SSH placeholder ManagedNE for an LLDP neighbor not in inventory.

    Intentionally empty IP / username / password — operator fills them later.
    LLDP management IP (if any) is kept in ``source_ref`` / remark only.
    """
    display = (str(remote_name or "").strip() or str(remote_ip or "").strip() or "unknown")[:256]
    name_key = _norm_host(display)
    ip_hint = str(remote_ip or "").strip()[:128]
    now = _utcnow()

    cache = placeholder_by_name
    if cache is None:
        cache = {}
        for ne in (
            db.query(ManagedNE)
            .filter(ManagedNE.source == LLDP_DISCOVERED_NE_SOURCE)
            .all()
        ):
            nk = _norm_host(ne.name or "")
            if nk and nk not in cache:
                cache[nk] = ne

    if name_key and name_key in cache:
        ne = cache[name_key]
        if ip_hint and not str(ne.source_ref or "").strip():
            ne.source_ref = ip_hint
            ne.updated_at = now
        return ne

    row = ManagedNE(
        id=uuid4().hex,
        name=display,
        vendor="Other",
        device_type="generic",
        ip_address="",
        port=22,
        protocol="ssh",
        username="",
        password_enc="",
        enable_secret_enc="",
        connect_status="unknown",
        tags="",
        remark=(f"LLDP discovered" + (f"; seen_mgmt_ip={ip_hint}" if ip_hint else ""))[:1024],
        source=LLDP_DISCOVERED_NE_SOURCE,
        source_ref=ip_hint,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    if name_key:
        cache[name_key] = row
    if placeholder_by_name is not None and name_key:
        placeholder_by_name[name_key] = row
    return row


class _FabricPeerIndex:
    """In-memory name index for one discover target (avoids O(nodes) per neighbor).

    Identity is System Name / Device ID only. LLDP Management Address is often a
    physical-interface IP and must not be used to pick the peer NE.
    """

    def __init__(self, db: Session, self_id: str) -> None:
        self.db = db
        self.self_id = self_id
        self.by_name: dict[str, list[TopoFabricNode]] = {}
        self.placeholder_by_name: dict[str, ManagedNE] = {}
        for n in db.query(TopoFabricNode).filter(TopoFabricNode.id != self_id).all():
            nk = _norm_host(n.name or "")
            if nk:
                self.by_name.setdefault(nk, []).append(n)
        for ne in (
            db.query(ManagedNE).filter(ManagedNE.source == LLDP_DISCOVERED_NE_SOURCE).all()
        ):
            nk = _norm_host(ne.name or "")
            if nk and nk not in self.placeholder_by_name:
                self.placeholder_by_name[nk] = ne

    def _best(self, matched: list[TopoFabricNode]) -> TopoFabricNode:
        # Prefer real inventory; ties → older fabric row (stable across rediscovers).
        matched.sort(
            key=lambda n: (
                -_fabric_match_score(self.db, n),
                n.created_at.timestamp() if n.created_at else 0.0,
                n.id,
            )
        )
        return matched[0]

    def match(self, hit: NeighborHit) -> TopoFabricNode | None:
        name_key = _norm_host(hit.remote_name)
        if not name_key:
            return None

        matched = list(self.by_name.get(name_key) or [])
        if matched:
            return self._best(matched)

        ne = _pick_managed_ne(self.db, name_key=name_key)
        if ne is not None:
            node = ensure_fabric_node_for_managed(self.db, ne)
            self._remember(node)
            return node
        return None

    def _remember(self, node: TopoFabricNode) -> None:
        if not node or node.id == self.self_id:
            return
        nk = _norm_host(node.name or "")
        if nk:
            bucket = self.by_name.setdefault(nk, [])
            if node not in bucket:
                bucket.append(node)

    def ensure_placeholder(self, *, remote_name: str, remote_ip: str) -> TopoFabricNode:
        placeholder = ensure_lldp_discovered_managed_ne(
            self.db,
            remote_name=remote_name,
            remote_ip=remote_ip,
            placeholder_by_name=self.placeholder_by_name,
        )
        peer = ensure_fabric_node_for_managed(self.db, placeholder)
        self._remember(peer)
        return peer


def _match_hit_to_fabric_node(
    db: Session, hit: NeighborHit, *, self_id: str
) -> TopoFabricNode | None:
    return _FabricPeerIndex(db, self_id).match(hit)


def _retarget_fabric_edges(db: Session, *, from_id: str, to_id: str) -> None:
    """Move edges from from_id onto to_id; drop duplicates / self-loops."""
    if not from_id or not to_id or from_id == to_id:
        return
    edges = (
        db.query(TopoFabricEdge)
        .filter(or_(TopoFabricEdge.a_node_id == from_id, TopoFabricEdge.b_node_id == from_id))
        .all()
    )
    for e in edges:
        a = to_id if e.a_node_id == from_id else e.a_node_id
        b = to_id if e.b_node_id == from_id else e.b_node_id
        if a == b:
            db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.fabric_edge_id == e.id).delete(
                synchronize_session=False
            )
            db.delete(e)
            continue
        na, nb, ap, bp = _normalize_endpoints(a, b, e.a_port or "", e.b_port or "")
        clash = (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.id != e.id,
                TopoFabricEdge.layer == (e.layer or "physical"),
                TopoFabricEdge.a_node_id == na,
                TopoFabricEdge.b_node_id == nb,
                TopoFabricEdge.a_port == ap,
                TopoFabricEdge.b_port == bp,
            )
            .one_or_none()
        )
        if clash is not None:
            # Keep the surviving edge fresher.
            if (e.last_seen_at or e.updated_at) and (
                not clash.last_seen_at
                or (e.last_seen_at and clash.last_seen_at and e.last_seen_at > clash.last_seen_at)
            ):
                clash.source = e.source or clash.source
                clash.status = e.status or clash.status
                clash.last_seen_at = e.last_seen_at or clash.last_seen_at
                clash.discovered_at = e.discovered_at or clash.discovered_at
                clash.updated_at = _utcnow()
            db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.fabric_edge_id == e.id).delete(
                synchronize_session=False
            )
            db.delete(e)
            continue
        e.a_node_id = na
        e.b_node_id = nb
        e.a_port = ap
        e.b_port = bp
        e.updated_at = _utcnow()


def merge_duplicate_fabric_nodes(db: Session) -> dict[str, int]:
    """Collapse duplicate fabric nodes (same managed/ume/name/ip) onto inventory canonicals."""
    nodes = db.query(TopoFabricNode).order_by(TopoFabricNode.created_at.asc()).all()
    merged = 0
    placeholders_removed = 0

    # 1) Same managed_ne_id / ume_ne_id (constraint may be missing on old DBs).
    by_managed: dict[str, list[TopoFabricNode]] = {}
    by_ume: dict[str, list[TopoFabricNode]] = {}
    for n in nodes:
        mid = str(n.managed_ne_id or "").strip()
        uid = str(n.ume_ne_id or "").strip()
        if mid:
            by_managed.setdefault(mid, []).append(n)
        if uid:
            by_ume.setdefault(uid, []).append(n)

    def _absorb(canon: TopoFabricNode, dupes: list[TopoFabricNode]) -> None:
        nonlocal merged
        for d in dupes:
            if d.id == canon.id:
                continue
            if db.get(TopoFabricNode, d.id) is None:
                continue
            _absorb_fabric_node(db, canon, d)
            merged += 1

    seen_absorb: set[str] = set()
    for group in list(by_managed.values()) + list(by_ume.values()):
        alive = [n for n in group if n.id not in seen_absorb and db.get(TopoFabricNode, n.id) is not None]
        if len(alive) < 2:
            continue
        canon = next((n for n in alive if _is_inventory_node(n)), alive[0])
        _absorb(canon, alive)
        for n in alive:
            seen_absorb.add(n.id)

    # 2) Orphans (no inventory ids) that collide with inventory node by name/ip.
    db.flush()
    nodes = db.query(TopoFabricNode).all()
    inventory = [n for n in nodes if _is_inventory_node(n)]
    orphans = [n for n in nodes if not _is_inventory_node(n)]
    inv_by_name: dict[str, TopoFabricNode] = {}
    inv_by_ip: dict[str, TopoFabricNode] = {}
    for n in sorted(inventory, key=lambda x: _fabric_match_score(db, x), reverse=True):
        nk = _norm_host(n.name or "")
        if nk and nk not in inv_by_name:
            inv_by_name[nk] = n
        ip = str(n.ip or "").strip()
        if ip and ip not in inv_by_ip:
            inv_by_ip[ip] = n
    for o in orphans:
        canon = None
        ip = str(o.ip or "").strip()
        nk = _norm_host(o.name or "")
        if ip and ip in inv_by_ip:
            canon = inv_by_ip[ip]
        elif nk and nk in inv_by_name:
            canon = inv_by_name[nk]
        if canon is None:
            continue
        _absorb(canon, [o])

    # 2b) LLDP placeholders (score=2) → real inventory (score>=3) by hostname / seen mgmt IP.
    # Placeholders have managed_ne_id so they are NOT orphans; absorb + drop empty ManagedNE.
    db.flush()
    nodes = db.query(TopoFabricNode).all()
    reals = [n for n in nodes if _fabric_match_score(db, n) >= 3]
    placeholders = [n for n in nodes if _fabric_match_score(db, n) == 2]
    real_by_name: dict[str, TopoFabricNode] = {}
    real_by_ip: dict[str, TopoFabricNode] = {}
    for n in sorted(reals, key=lambda x: _fabric_match_score(db, x), reverse=True):
        nk = _norm_host(n.name or "")
        if nk and nk not in real_by_name:
            real_by_name[nk] = n
        ip = str(n.ip or "").strip()
        if ip and ip not in real_by_ip:
            real_by_ip[ip] = n
    for p in placeholders:
        if db.get(TopoFabricNode, p.id) is None:
            continue
        canon = None
        nk = _norm_host(p.name or "")
        ip = str(p.ip or "").strip()
        seen_ip = ""
        mid = str(p.managed_ne_id or "").strip()
        ph_ne = db.get(ManagedNE, mid) if mid else None
        if ph_ne is not None:
            seen_ip = str(ph_ne.source_ref or "").strip()
        if ip and ip in real_by_ip:
            canon = real_by_ip[ip]
        elif seen_ip and seen_ip in real_by_ip:
            canon = real_by_ip[seen_ip]
        elif nk and nk in real_by_name:
            canon = real_by_name[nk]
        if canon is None or canon.id == p.id:
            continue
        _absorb(canon, [p])
        db.flush()
        # Drop placeholder ManagedNE if nothing else references it.
        if ph_ne is not None and str(ph_ne.source or "").strip().lower() == LLDP_DISCOVERED_NE_SOURCE:
            still = (
                db.query(TopoFabricNode)
                .filter(TopoFabricNode.managed_ne_id == ph_ne.id)
                .count()
            )
            if still == 0:
                db.delete(ph_ne)
                placeholders_removed += 1

    # 3) WebCRT session hosts sharing an IP with a real inventory fabric node.
    db.flush()
    nodes = db.query(TopoFabricNode).all()
    by_ip: dict[str, list[TopoFabricNode]] = {}
    for n in nodes:
        ip = str(n.ip or "").strip()
        if ip:
            by_ip.setdefault(ip, []).append(n)
    for group in by_ip.values():
        if len(group) < 2:
            continue
        real = [n for n in group if _fabric_match_score(db, n) >= 3]
        webcrtish = [n for n in group if _fabric_match_score(db, n) == 1]
        if not real or not webcrtish:
            continue
        canon = max(real, key=lambda n: _fabric_match_score(db, n))
        _absorb(canon, webcrtish)

    if merged or placeholders_removed:
        db.commit()
        refresh_fabric_stats(db)
    return {"merged": merged, "placeholders_removed": placeholders_removed}


