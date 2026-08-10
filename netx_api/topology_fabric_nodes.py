"""Fabric node serialization, ensure, list, stats, and neighborhood."""
from __future__ import annotations

import re
from collections import deque
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
        world_x=float(n.world_x) if n.world_x is not None else None,
        world_y=float(n.world_y) if n.world_y is not None else None,
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


def find_fabric_paths(
    db: Session,
    *,
    from_ume_ne_id: str = "",
    from_managed_ne_id: str = "",
    to_ume_ne_id: str = "",
    to_managed_ne_id: str = "",
    max_paths: int = 3,
    max_hops: int = 6,
    layer: str = "physical",
) -> dict[str, Any]:
    """Find up to max_paths simple paths between two fabric nodes.

    Accepts ume_ne_id (from UME alarms) or managed_ne_id (from managed NE) — resolved
    to fabric_node_id internally so agents can use alarm ne_id directly.
    """
    from_uid = str(from_ume_ne_id or "").strip()
    from_mid = str(from_managed_ne_id or "").strip()
    to_uid = str(to_ume_ne_id or "").strip()
    to_mid = str(to_managed_ne_id or "").strip()
    if bool(from_uid) == bool(from_mid):
        raise HTTPException(400, detail="exactly_one_of_from_ume_ne_id_or_from_managed_ne_id_required")
    if bool(to_uid) == bool(to_mid):
        raise HTTPException(400, detail="exactly_one_of_to_ume_ne_id_or_to_managed_ne_id_required")

    def _resolve(uid: str, mid: str) -> str:
        q = db.query(TopoFabricNode)
        if uid:
            q = q.filter(TopoFabricNode.ume_ne_id == uid)
        else:
            q = q.filter(TopoFabricNode.managed_ne_id == mid)
        row = q.first()
        if not row:
            raise HTTPException(404, detail="fabric_node_not_found_for_ne_id")
        return row.id

    from_id = _resolve(from_uid, from_mid)
    to_id = _resolve(to_uid, to_mid)
    if from_id == to_id:
        raise HTTPException(400, detail="from_and_to_are_same_node")

    max_paths = max(1, min(10, int(max_paths or 3)))
    max_hops = max(1, min(12, int(max_hops or 6)))
    layer_v = str(layer or "physical").strip() or "physical"

    # Lazy adjacency: only fetch edges for nodes the BFS actually expands
    # (avoids loading the entire fabric layer on large graphs).
    adj_cache: dict[str, list[tuple[str, str]]] = {}
    adj_loaded: set[str] = set()
    edge_map: dict[str, TopoFabricEdge] = {}

    def _ensure_adj(node_ids: set[str]) -> None:
        missing = [n for n in node_ids if n not in adj_loaded]
        if not missing:
            return
        batch = (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.layer == layer_v,
                or_(
                    TopoFabricEdge.a_node_id.in_(missing),
                    TopoFabricEdge.b_node_id.in_(missing),
                ),
            )
            .all()
        )
        for nid in missing:
            adj_cache[nid] = []
            adj_loaded.add(nid)
        for e in batch:
            edge_map[e.id] = e
            if e.a_node_id in missing:
                adj_cache[e.a_node_id].append((e.b_node_id, e.id))
            if e.b_node_id in missing:
                adj_cache[e.b_node_id].append((e.a_node_id, e.id))

    # BFS for simple paths so shorter hops are found first; cap expansions on dense graphs.
    _EXPLORE_CAP = 5000
    found: list[list[str]] = []
    queue: deque[tuple[str, list[str], set[str]]] = deque([(from_id, [], {from_id})])
    explored = 0
    while queue and len(found) < max_paths and explored < _EXPLORE_CAP:
        node, edge_path, visited = queue.popleft()
        if len(edge_path) >= max_hops:
            continue
        _ensure_adj({node})
        for nbr, eid in adj_cache.get(node, []):
            if nbr in visited:
                continue
            explored += 1
            new_path = edge_path + [eid]
            if nbr == to_id:
                found.append(new_path)
                if len(found) >= max_paths:
                    break
                continue
            queue.append((nbr, new_path, visited | {nbr}))

    node_ids = {from_id, to_id}
    for p in found:
        for eid in p:
            e = edge_map.get(eid)
            if e:
                node_ids.add(e.a_node_id)
                node_ids.add(e.b_node_id)
    node_map = _nodes_by_ids(db, node_ids)

    def _path_nodes(edge_ids: list[str]) -> list[dict]:
        ids = [from_id]
        cur = from_id
        for eid in edge_ids:
            e = edge_map.get(eid)
            if not e:
                break
            nxt = e.b_node_id if e.a_node_id == cur else e.a_node_id
            ids.append(nxt)
            cur = nxt
        return [_node_out(node_map[nid]).model_dump() for nid in ids if nid in node_map]

    return {
        "from_node_id": from_id,
        "to_node_id": to_id,
        "layer": layer_v,
        "path_count": len(found),
        "paths": [
            {
                "hops": len(p),
                "nodes": _path_nodes(p),
                "edges": [_edge_out(edge_map[eid], nodes_by_id=node_map).model_dump() for eid in p if eid in edge_map],
            }
            for p in found
        ],
    }


