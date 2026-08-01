"""Fabric topology + views + LLDP discovery (final model, no CDP)."""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cli_resolve import get_default_profile, infer_device_type_vendor
from .db import SessionLocal
from .device_types import LLDP_DISCOVERED_NE_SOURCE, WEBCRT_NE_SOURCE
from .models import (
    ManagedNE,
    TopoDiscoverJob,
    TopoDiscoverJobItem,
    TopoFabricEdge,
    TopoFabricNode,
    TopoFabricStats,
    TopoView,
    TopoViewEdgeStyle,
    TopoViewNode,
    UmeInventoryNE,
)
from .ne_exec import execute_managed_ne_commands
from .topology_lldp import (
    NeighborHit,
    normalize_ifname,
    parse_neighbor_output,
    parser_meta,
    pick_neighbor_command,
)
from .topology_schemas import (
    FabricDiscoverJobItemOut,
    FabricDiscoverJobOut,
    FabricDiscoverRequest,
    FabricDiscoverUnmatched,
    FabricEdgeOut,
    FabricNeighborhoodOut,
    FabricNodeOut,
    FabricSummaryOut,
    TopologyViewCreate,
    TopologyViewGraphOut,
    TopologyViewOut,
    TopologyViewUpdate,
    ViewEdgeOut,
    ViewEdgeStylePatch,
    ViewNodeIn,
    ViewNodeOut,
    ViewNodesAdd,
    ViewPositionsPatch,
)

PAGE_DEFAULT = 100
PAGE_MAX = 2000
VIEW_GRAPH_NODE_HARD_CAP = 2000
VIEW_GRAPH_EDGE_HARD_CAP = 5000
_RAW_PREVIEW_MAX = 12_000
_JOB_LOCK = threading.Lock()
_RUNNING_JOBS: set[str] = set()

# Fabric link lifecycle: absent once → missing; still absent for N cycles → purge.
_EDGE_STATUS_MISSING = "missing"
_EDGE_STATUS_MISSING_COMPAT = frozenset({"missing", "stale"})
_MISS_PURGE_AFTER_CYCLES = 4


def _normalize_edge_status(status: str) -> str:
    s = str(status or "").strip().lower() or "active"
    if s in _EDGE_STATUS_MISSING_COMPAT:
        return _EDGE_STATUS_MISSING
    return s


def _edge_attrs(e: TopoFabricEdge) -> dict[str, Any]:
    return dict(e.attrs or {})


def _clear_miss_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    out = dict(attrs or {})
    out.pop("miss_count", None)
    out.pop("first_missing_at", None)
    out.pop("replaced_by_edge_id", None)
    return out


def _set_edge_missing(
    e: TopoFabricEdge,
    now: datetime,
    *,
    replaced_by_edge_id: str = "",
) -> bool:
    """Mark edge missing and bump miss_count. Returns True if newly became missing."""
    prev = _normalize_edge_status(e.status or "")
    attrs = _edge_attrs(e)
    miss_count = int(attrs.get("miss_count") or 0) + 1
    attrs["miss_count"] = miss_count
    if not attrs.get("first_missing_at"):
        attrs["first_missing_at"] = now.isoformat(timespec="seconds")
    if replaced_by_edge_id:
        attrs["replaced_by_edge_id"] = str(replaced_by_edge_id)
    e.attrs = attrs
    e.status = _EDGE_STATUS_MISSING
    # Keep observational source; never leave source stuck on legacy "stale".
    if str(e.source or "").strip().lower() in {"", "stale"}:
        e.source = "lldp"
    e.updated_at = now
    return prev != _EDGE_STATUS_MISSING


def _purge_edge_if_due(db: Session, e: TopoFabricEdge) -> bool:
    """Physically delete missing edge after enough consecutive miss cycles."""
    attrs = _edge_attrs(e)
    if int(attrs.get("miss_count") or 0) < _MISS_PURGE_AFTER_CYCLES:
        return False
    if _normalize_edge_status(e.status or "") != _EDGE_STATUS_MISSING:
        return False
    if str(e.source or "").strip().lower() == "manual":
        return False
    db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.fabric_edge_id == e.id).delete(
        synchronize_session=False
    )
    db.delete(e)
    return True


def _utcnow() -> datetime:
    return datetime.utcnow()


def _norm_host(s: str) -> str:
    t = str(s or "").strip().lower().split(".")[0]
    return t.rstrip(".,;:")


def _empty_to_none(s: str | None) -> str | None:
    v = str(s or "").strip()
    return v or None


# ---------------------------------------------------------------------------
# Fabric nodes / edges helpers
# ---------------------------------------------------------------------------


def _node_out(n: TopoFabricNode) -> FabricNodeOut:
    return FabricNodeOut(
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


def _edge_out(e: TopoFabricEdge) -> FabricEdgeOut:
    src = str(e.source or "lldp").strip().lower() or "lldp"
    if src == "stale":
        src = "lldp"
    return FabricEdgeOut(
        id=e.id,
        layer=e.layer or "physical",
        a_node_id=e.a_node_id,
        b_node_id=e.b_node_id,
        a_port=e.a_port or "",
        b_port=e.b_port or "",
        source=src,
        status=_normalize_edge_status(e.status or "active"),
        attrs=dict(e.attrs or {}),
        discovered_at=e.discovered_at,
        last_seen_at=e.last_seen_at,
    )


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
        last_discover_at=row.last_discover_at,
        updated_at=row.updated_at,
    )


def list_fabric_nodes(
    db: Session,
    *,
    keyword: str = "",
    page: int = 1,
    page_size: int = PAGE_DEFAULT,
) -> dict[str, Any]:
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
    total = int(q.count())
    rows = (
        q.order_by(TopoFabricNode.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_node_out(n).model_dump() for n in rows],
    }


def list_fabric_edges(
    db: Session,
    *,
    node_id: str = "",
    layer: str = "physical",
    status: str = "",
    source: str = "",
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
    if st:
        q = q.filter(TopoFabricEdge.status == st)
    src = str(source or "").strip().lower()
    if src:
        q = q.filter(TopoFabricEdge.source == src)
    total = int(q.count())
    rows = (
        q.order_by(TopoFabricEdge.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_edge_out(e).model_dump() for e in rows],
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
        return row, "added"
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

    Returns ids of edges touched by this replacement (skip re-bump in same job).
    """
    now = now or _utcnow()
    lp = normalize_ifname(local_port)
    if not self_id or not peer_id or not lp:
        return []
    layer_v = str(layer or "physical").strip() or "physical"
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
# Views
# ---------------------------------------------------------------------------


def _view_out(v: TopoView, *, node_count: int = 0) -> TopologyViewOut:
    return TopologyViewOut(
        id=v.id,
        name=v.name,
        remark=v.remark or "",
        filter=dict(v.filter or {}),
        viewport=dict(v.viewport or {}),
        node_count=node_count,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


def _get_view_or_404(db: Session, view_id: str) -> TopoView:
    vid = str(view_id or "").strip()
    row = db.get(TopoView, vid) if vid else None
    if row is None:
        raise HTTPException(status_code=404, detail="topology_view_not_found")
    return row


def list_views(db: Session) -> dict[str, Any]:
    rows = db.query(TopoView).order_by(TopoView.updated_at.desc()).all()
    items = []
    for v in rows:
        nc = db.query(TopoViewNode).filter(TopoViewNode.view_id == v.id).count()
        items.append(_view_out(v, node_count=nc).model_dump())
    return {"total": len(items), "items": items}


def create_view(db: Session, body: TopologyViewCreate) -> TopologyViewOut:
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    now = _utcnow()
    row = TopoView(
        id=uuid4().hex,
        name=name[:256],
        remark=str(body.remark or "")[:1024],
        filter=dict(body.filter or {}),
        viewport={},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _view_out(row)


def update_view(db: Session, view_id: str, body: TopologyViewUpdate) -> TopologyViewOut:
    row = _get_view_or_404(db, view_id)
    if body.name is not None:
        name = str(body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name_required")
        row.name = name[:256]
    if body.remark is not None:
        row.remark = str(body.remark or "")[:1024]
    if body.filter is not None:
        row.filter = dict(body.filter or {})
    if body.viewport is not None:
        row.viewport = dict(body.viewport or {})
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    nc = db.query(TopoViewNode).filter(TopoViewNode.view_id == row.id).count()
    return _view_out(row, node_count=nc)


def delete_view(db: Session, view_id: str) -> dict[str, Any]:
    row = _get_view_or_404(db, view_id)
    db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.view_id == row.id).delete(
        synchronize_session=False
    )
    db.query(TopoViewNode).filter(TopoViewNode.view_id == row.id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True, "view_id": view_id, "deleted": True}


def _connect_status_for_node(db: Session, n: TopoFabricNode) -> str:
    if n.managed_ne_id:
        ne = db.get(ManagedNE, n.managed_ne_id)
        if ne is not None:
            return ne.connect_status or ""
    if n.ume_ne_id:
        ume = (
            db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == n.ume_ne_id).one_or_none()
        )
        if ume is not None:
            return ume.connection_status or ""
    return ""


def get_view_graph(db: Session, view_id: str) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    truncated = False
    reason = ""
    if len(vnodes) > VIEW_GRAPH_NODE_HARD_CAP:
        vnodes = vnodes[:VIEW_GRAPH_NODE_HARD_CAP]
        truncated = True
        reason = "too_many_view_nodes"
    fids = [vn.fabric_node_id for vn in vnodes]
    fabric_nodes = {
        n.id: n for n in db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(fids)).all()
    } if fids else {}
    filt = dict(view.filter or {})
    layer = str(filt.get("layer") or "physical").strip() or "physical"
    status = str(filt.get("status") or "").strip().lower()
    nodes_out: list[ViewNodeOut] = []
    for vn in vnodes:
        fn = fabric_nodes.get(vn.fabric_node_id)
        label = (vn.label or "").strip()
        if not label and fn is not None:
            label = (fn.name or fn.ip or vn.fabric_node_id)[:256]
        nodes_out.append(
            ViewNodeOut(
                fabric_node_id=vn.fabric_node_id,
                managed_ne_id=(fn.managed_ne_id if fn else "") or "",
                ume_ne_id=(fn.ume_ne_id if fn else "") or "",
                label=label,
                x=float(vn.x or 0),
                y=float(vn.y or 0),
                locked=bool(vn.locked),
                name=(fn.name if fn else "") or "",
                ip=(fn.ip if fn else "") or "",
                vendor=(fn.vendor if fn else "") or "",
                device_type=(fn.device_type if fn else "") or "",
                connect_status=_connect_status_for_node(db, fn) if fn else "",
            )
        )
    edges_out: list[ViewEdgeOut] = []
    if fids:
        q = db.query(TopoFabricEdge).filter(
            TopoFabricEdge.layer == layer,
            TopoFabricEdge.a_node_id.in_(fids),
            TopoFabricEdge.b_node_id.in_(fids),
        )
        if status:
            st_norm = _normalize_edge_status(status)
            if st_norm == _EDGE_STATUS_MISSING:
                q = q.filter(TopoFabricEdge.status.in_(list(_EDGE_STATUS_MISSING_COMPAT)))
            else:
                q = q.filter(TopoFabricEdge.status == st_norm)
        edges = q.limit(VIEW_GRAPH_EDGE_HARD_CAP + 1).all()
        if len(edges) > VIEW_GRAPH_EDGE_HARD_CAP:
            edges = edges[:VIEW_GRAPH_EDGE_HARD_CAP]
            truncated = True
            reason = reason or "too_many_edges"
        styles = {
            s.fabric_edge_id: s
            for s in db.query(TopoViewEdgeStyle)
            .filter(
                TopoViewEdgeStyle.view_id == view.id,
                TopoViewEdgeStyle.fabric_edge_id.in_([e.id for e in edges]),
            )
            .all()
        }
        for e in edges:
            st = styles.get(e.id)
            src = str(e.source or "lldp").strip().lower() or "lldp"
            if src == "stale":
                src = "lldp"
            edges_out.append(
                ViewEdgeOut(
                    id=e.id,
                    a_node_id=e.a_node_id,
                    b_node_id=e.b_node_id,
                    a_port=e.a_port or "",
                    b_port=e.b_port or "",
                    source=src,
                    status=_normalize_edge_status(e.status or "active"),
                    layer=e.layer or "physical",
                    stroke_color=(st.stroke_color if st else "") or "",
                    stroke_width=int(st.stroke_width if st else 0) or 0,
                    line_style=(st.line_style if st else "") or "",
                    discovered_at=e.discovered_at,
                )
            )
    return TopologyViewGraphOut(
        view=_view_out(view, node_count=len(nodes_out)),
        nodes=nodes_out,
        edges=edges_out,
        truncated=truncated,
        truncate_reason=reason,
    )


def patch_view_positions(
    db: Session, view_id: str, body: ViewPositionsPatch
) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    now = _utcnow()
    positions = list(body.positions or [])
    if len(positions) > VIEW_GRAPH_NODE_HARD_CAP:
        raise HTTPException(status_code=400, detail="too_many_positions")
    existing = {
        vn.fabric_node_id: vn
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    for p in positions:
        fid = str(p.fabric_node_id or "").strip()
        if not fid:
            continue
        if db.get(TopoFabricNode, fid) is None:
            raise HTTPException(status_code=400, detail=f"fabric_node_not_found:{fid}")
        row = existing.get(fid)
        if row is None:
            row = TopoViewNode(
                id=uuid4().hex,
                view_id=view.id,
                fabric_node_id=fid,
                x=float(p.x or 0),
                y=float(p.y or 0),
                label=str(p.label or "")[:256],
                locked=bool(p.locked),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            existing[fid] = row
        else:
            if row.locked and not p.locked:
                # allow unlock + move when explicitly unlocked in patch
                pass
            if row.locked and bool(p.locked):
                continue
            row.x = float(p.x or 0)
            row.y = float(p.y or 0)
            if p.label is not None:
                row.label = str(p.label or "")[:256]
            row.locked = bool(p.locked)
            row.updated_at = now
    view.updated_at = now
    db.commit()
    return get_view_graph(db, view.id)


def add_nodes_to_view(db: Session, view_id: str, body: ViewNodesAdd) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    now = _utcnow()
    existing = {
        vn.fabric_node_id
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    added_ids: list[str] = []
    for mid in body.managed_ne_ids or []:
        mid_s = str(mid or "").strip()
        if not mid_s:
            continue
        ne = db.get(ManagedNE, mid_s)
        if ne is None:
            continue
        fn = ensure_fabric_node_for_managed(db, ne)
        if fn.id not in existing:
            added_ids.append(fn.id)
            existing.add(fn.id)
    default_profile = get_default_profile(db)
    for uid in body.ume_ne_ids or []:
        uid_s = str(uid or "").strip()
        if not uid_s:
            continue
        ume = db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == uid_s).one_or_none()
        if ume is None:
            continue
        if default_profile is not None:
            dtype, vendor = infer_device_type_vendor(str(ume.ne_type or ""), default_profile)
        else:
            dtype, vendor = "zte_zxros", (ume.vendor or "ZTE")
        fn = ensure_fabric_node_for_ume(db, ume, device_type=dtype, vendor=vendor)
        if fn.id not in existing:
            added_ids.append(fn.id)
            existing.add(fn.id)
    for fid in body.fabric_node_ids or []:
        fid_s = str(fid or "").strip()
        if not fid_s or fid_s in existing:
            continue
        if db.get(TopoFabricNode, fid_s) is None:
            continue
        added_ids.append(fid_s)
        existing.add(fid_s)
    cols = max(1, int(len(added_ids) ** 0.5) or 1)
    for i, fid in enumerate(added_ids):
        x = (i % cols) * 180.0 + 40.0
        y = (i // cols) * 120.0 + 40.0
        db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=view.id,
                fabric_node_id=fid,
                x=x,
                y=y,
                label="",
                locked=False,
                created_at=now,
                updated_at=now,
            )
        )
    view.updated_at = now
    db.commit()
    return get_view_graph(db, view.id)


def project_fabric_neighbors_to_view(db: Session, view_id: str) -> TopologyViewGraphOut:
    """Add fabric neighbors of current view nodes onto the view so edges can render."""
    # Collapse duplicate fabric nodes first (fixes R1/r1 + twin R2 after raced discovers).
    merge_duplicate_fabric_nodes(db)
    view = _get_view_or_404(db, view_id)
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()

    # Drop view placements that still point at LLDP orphans (no inventory link).
    orphan_vns = []
    for vn in vnodes:
        fn = db.get(TopoFabricNode, vn.fabric_node_id)
        if fn is None or not _is_inventory_node(fn):
            orphan_vns.append(vn)
    if orphan_vns:
        for vn in orphan_vns:
            db.delete(vn)
        view.updated_at = _utcnow()
        db.commit()
        vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()

    existing = {vn.fabric_node_id for vn in vnodes}
    if not existing:
        return get_view_graph(db, view.id)

    filt = dict(view.filter or {})
    layer = str(filt.get("layer") or "physical").strip() or "physical"
    peer_ids: set[str] = set()
    for fid in existing:
        rows = (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.layer == layer,
                or_(TopoFabricEdge.a_node_id == fid, TopoFabricEdge.b_node_id == fid),
            )
            .all()
        )
        for edge in rows:
            peer = edge.b_node_id if edge.a_node_id == fid else edge.a_node_id
            if not peer or peer in existing:
                continue
            fn = db.get(TopoFabricNode, peer)
            # Project real inventory + LLDP placeholders; skip WebCRT twins / orphans.
            if fn is None or not _is_inventory_node(fn):
                continue
            if _fabric_match_score(db, fn) < 2:
                continue
            peer_ids.add(peer)

    if not peer_ids:
        return get_view_graph(db, view.id)

    now = _utcnow()
    added = sorted(peer_ids)
    cols = max(1, int(len(added) ** 0.5) or 1)
    max_x = max((float(vn.x or 0) for vn in vnodes), default=40.0)
    base_x = max_x + 200.0
    for i, fid in enumerate(added):
        if db.get(TopoFabricNode, fid) is None:
            continue
        x = base_x + (i % cols) * 180.0
        y = 40.0 + (i // cols) * 120.0
        db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=view.id,
                fabric_node_id=fid,
                x=x,
                y=y,
                label="",
                locked=False,
                created_at=now,
                updated_at=now,
            )
        )
    view.updated_at = now
    db.commit()
    return get_view_graph(db, view.id)


def remove_view_nodes(db: Session, view_id: str, fabric_node_ids: list[str]) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    ids = [str(x).strip() for x in (fabric_node_ids or []) if str(x).strip()]
    if ids:
        db.query(TopoViewNode).filter(
            TopoViewNode.view_id == view.id, TopoViewNode.fabric_node_id.in_(ids)
        ).delete(synchronize_session=False)
        view.updated_at = _utcnow()
        db.commit()
    return get_view_graph(db, view.id)


_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_LINE_STYLES = {"", "solid", "dashed", "dotted"}


def patch_view_edge_style(
    db: Session, view_id: str, body: ViewEdgeStylePatch
) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    eid = str(body.fabric_edge_id or "").strip()
    if not eid or db.get(TopoFabricEdge, eid) is None:
        raise HTTPException(status_code=404, detail="fabric_edge_not_found")
    color = str(body.stroke_color or "").strip()
    if color and not _HEX_COLOR_RE.match(color):
        raise HTTPException(status_code=400, detail="invalid_stroke_color")
    width = int(body.stroke_width or 0)
    if width < 0 or width > 12:
        raise HTTPException(status_code=400, detail="invalid_stroke_width")
    style = str(body.line_style or "").strip().lower()
    if style not in _LINE_STYLES:
        raise HTTPException(status_code=400, detail="invalid_line_style")
    now = _utcnow()
    row = (
        db.query(TopoViewEdgeStyle)
        .filter(TopoViewEdgeStyle.view_id == view.id, TopoViewEdgeStyle.fabric_edge_id == eid)
        .one_or_none()
    )
    if row is None:
        row = TopoViewEdgeStyle(
            id=uuid4().hex,
            view_id=view.id,
            fabric_edge_id=eid,
            stroke_color=color,
            stroke_width=width,
            line_style=style,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.stroke_color = color
        row.stroke_width = width
        row.line_style = style
        row.updated_at = now
    view.updated_at = now
    db.commit()
    return get_view_graph(db, view.id)


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
    rows: list[ManagedNE] = []
    if ip:
        rows = db.query(ManagedNE).filter(ManagedNE.ip_address == ip).all()
    elif name_key:
        rows = (
            db.query(ManagedNE).filter(func.lower(ManagedNE.name) == name_key).all()
        )
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
    db: Session, *, remote_name: str = "", remote_ip: str = ""
) -> ManagedNE:
    """SSH placeholder ManagedNE for an LLDP neighbor not in inventory.

    Intentionally empty IP / username / password — operator fills them later.
    LLDP management IP (if any) is kept in ``source_ref`` / remark only.
    """
    display = (str(remote_name or "").strip() or str(remote_ip or "").strip() or "unknown")[:256]
    name_key = _norm_host(display)
    ip_hint = str(remote_ip or "").strip()[:128]
    now = _utcnow()

    # Reuse existing LLDP placeholder by normalized hostname.
    if name_key:
        for ne in (
            db.query(ManagedNE)
            .filter(ManagedNE.source == LLDP_DISCOVERED_NE_SOURCE)
            .all()
        ):
            if _norm_host(ne.name or "") == name_key:
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
    return row


def _match_hit_to_fabric_node(
    db: Session, hit: NeighborHit, *, self_id: str
) -> TopoFabricNode | None:
    name_key = _norm_host(hit.remote_name)
    ip_key = str(hit.remote_ip or "").strip()
    candidates = db.query(TopoFabricNode).filter(TopoFabricNode.id != self_id).all()

    matched: list[TopoFabricNode] = []
    for n in candidates:
        names = {_norm_host(n.name or "")}
        ips = {str(n.ip or "").strip()}
        if ip_key and ip_key in ips:
            matched.append(n)
            continue
        if name_key and name_key in names:
            matched.append(n)
    if matched:
        matched.sort(key=lambda n: _fabric_match_score(db, n), reverse=True)
        return matched[0]

    # Inventory not yet in fabric (or missed due to concurrent insert).
    if ip_key:
        ne = _pick_managed_ne(db, ip=ip_key)
        if ne is not None:
            return ensure_fabric_node_for_managed(db, ne)
        ume = db.query(UmeInventoryNE).filter(UmeInventoryNE.ip_address == ip_key).first()
        if ume is not None:
            return ensure_fabric_node_for_ume(db, ume)
    if name_key:
        ne = _pick_managed_ne(db, name_key=name_key)
        if ne is not None:
            return ensure_fabric_node_for_managed(db, ne)
    return None


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
            _retarget_fabric_edges(db, from_id=d.id, to_id=canon.id)
            # View placements: keep canon if present, else retarget; drop duplicate placements.
            vnodes = db.query(TopoViewNode).filter(TopoViewNode.fabric_node_id == d.id).all()
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
            db.delete(d)
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

    if merged:
        db.commit()
        refresh_fabric_stats(db)
    return {"merged": merged}


def _raw_preview(raw: str, *, limit: int = _RAW_PREVIEW_MAX) -> str:
    text = str(raw or "")
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated preview {limit}/{len(text)} chars]"


def _job_out(db: Session, job: TopoDiscoverJob, *, include_items: bool = True) -> FabricDiscoverJobOut:
    items_out: list[FabricDiscoverJobItemOut] = []
    if include_items:
        items = (
            db.query(TopoDiscoverJobItem)
            .filter(TopoDiscoverJobItem.job_id == job.id)
            .order_by(TopoDiscoverJobItem.created_at.asc())
            .all()
        )
        for it in items:
            unmatched = [
                FabricDiscoverUnmatched.model_validate(x) for x in (it.unmatched_json or [])[:40]
            ]
            items_out.append(
                FabricDiscoverJobItemOut(
                    id=it.id,
                    job_id=it.job_id,
                    ne_id=it.ne_id or "",
                    ume_ne_id=it.ume_ne_id or "",
                    fabric_node_id=it.fabric_node_id or "",
                    ne_name=it.ne_name or "",
                    ne_ip=it.ne_ip or "",
                    ok=bool(it.ok),
                    command=it.command or "",
                    neighbors=int(it.neighbors or 0),
                    edges_added=int(it.edges_added or 0),
                    edges_updated=int(it.edges_updated or 0),
                    unmatched_count=int(it.unmatched_count or 0),
                    unmatched=unmatched,
                    parser_key=it.parser_key or "",
                    parser_stub=bool(it.parser_stub),
                    error=it.error or "",
                    raw_preview=it.raw_preview or "",
                )
            )
    return FabricDiscoverJobOut(
        id=job.id,
        scope=job.scope,
        trigger_mode=str(getattr(job, "trigger_mode", None) or "manual"),
        status=job.status,
        total=int(job.total or 0),
        done=int(job.done or 0),
        edges_added=int(job.edges_added or 0),
        edges_updated=int(job.edges_updated or 0),
        edges_stale=int(job.edges_stale or 0),
        error=job.error or "",
        started_at=job.started_at,
        ended_at=job.ended_at,
        items=items_out,
    )


def get_discover_job(db: Session, job_id: str) -> FabricDiscoverJobOut:
    job = db.get(TopoDiscoverJob, str(job_id or "").strip())
    if job is None:
        raise HTTPException(status_code=404, detail="discover_job_not_found")
    return _job_out(db, job)


def _resolve_scan_targets(
    db: Session, body: FabricDiscoverRequest
) -> list[dict[str, str]]:
    scope = str(body.scope or "ne_ids").strip().lower() or "ne_ids"
    default_profile = get_default_profile(db)
    targets: list[dict[str, str]] = []
    if scope == "all_inventory":
        for ne in db.query(ManagedNE).all():
            targets.append(
                {
                    "ne_id": ne.id,
                    "ume_ne_id": "",
                    "ne_name": ne.name or "",
                    "ne_ip": ne.ip_address or "",
                    "vendor": ne.vendor or "",
                    "device_type": ne.device_type or "",
                }
            )
        return targets
    filter_ids = {str(x).strip() for x in (body.ne_ids or []) if str(x).strip()}
    if not filter_ids:
        raise HTTPException(status_code=400, detail="ne_ids_required")
    for mid in list(filter_ids):
        ne = db.get(ManagedNE, mid)
        if ne is not None:
            targets.append(
                {
                    "ne_id": ne.id,
                    "ume_ne_id": "",
                    "ne_name": ne.name or "",
                    "ne_ip": ne.ip_address or "",
                    "vendor": ne.vendor or "",
                    "device_type": ne.device_type or "",
                }
            )
            filter_ids.discard(mid)
    for uid in list(filter_ids):
        ume = db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == uid).one_or_none()
        if ume is None:
            continue
        if default_profile is not None:
            dtype, vendor = infer_device_type_vendor(str(ume.ne_type or ""), default_profile)
        else:
            dtype, vendor = "zte_zxros", (ume.vendor or "ZTE")
        name = (ume.host_name or ume.ne_name or ume.user_label or ume.ip_address or uid).strip()
        targets.append(
            {
                "ne_id": uid,
                "ume_ne_id": uid,
                "ne_name": name,
                "ne_ip": ume.ip_address or "",
                "vendor": vendor or (ume.vendor or "ZTE"),
                "device_type": dtype or "zte_zxros",
            }
        )
    return targets


def _discover_one_target(
    target: dict[str, str],
    *,
    auto_add_unmatched: bool,
) -> dict[str, Any]:
    """Run LLDP for one NE in a fresh DB session."""
    db = SessionLocal()
    try:
        now = _utcnow()
        if target["ume_ne_id"] and not target["ne_id"]:
            pass
        fabric_node: TopoFabricNode | None = None
        managed = db.get(ManagedNE, target["ne_id"]) if target.get("ne_id") else None
        if managed is not None:
            fabric_node = ensure_fabric_node_for_managed(db, managed)
        elif target.get("ume_ne_id"):
            ume = (
                db.query(UmeInventoryNE)
                .filter(UmeInventoryNE.ne_id == target["ume_ne_id"])
                .one_or_none()
            )
            if ume is not None:
                fabric_node = ensure_fabric_node_for_ume(
                    db,
                    ume,
                    device_type=target.get("device_type") or "",
                    vendor=target.get("vendor") or "",
                )
        if fabric_node is None:
            return {
                "ne_id": target["ne_id"],
                "ume_ne_id": target.get("ume_ne_id") or "",
                "fabric_node_id": "",
                "ne_name": target.get("ne_name") or "",
                "ne_ip": target.get("ne_ip") or "",
                "ok": False,
                "error": "fabric_node_resolve_failed",
            }

        cmd, _proto = pick_neighbor_command(
            vendor=target.get("vendor") or "",
            device_type=target.get("device_type") or "",
        )
        exec_kwargs: dict[str, Any] = {"read_timeout_sec": 60}
        if target.get("ume_ne_id") and not db.get(ManagedNE, target["ne_id"]):
            exec_kwargs["ume_ne_id"] = target["ume_ne_id"]
        else:
            exec_kwargs["ne_id"] = target["ne_id"]
        try:
            exec_out = execute_managed_ne_commands(db, [cmd], **exec_kwargs)
        except HTTPException as exc:
            db.commit()
            return {
                "ne_id": target["ne_id"],
                "ume_ne_id": target.get("ume_ne_id") or "",
                "fabric_node_id": fabric_node.id,
                "ne_name": target.get("ne_name") or "",
                "ne_ip": target.get("ne_ip") or "",
                "ok": False,
                "command": cmd,
                "error": str(exc.detail or "exec_failed")[:500],
            }
        if not exec_out.get("ok"):
            db.commit()
            return {
                "ne_id": target["ne_id"],
                "ume_ne_id": target.get("ume_ne_id") or "",
                "fabric_node_id": fabric_node.id,
                "ne_name": target.get("ne_name") or "",
                "ne_ip": target.get("ne_ip") or "",
                "ok": False,
                "command": cmd,
                "error": str(exec_out.get("detail") or exec_out.get("error") or "exec_failed")[:500],
            }

        raw = str(exec_out.get("output") or "")
        pkey, is_stub = parser_meta(
            vendor=target.get("vendor") or "", device_type=target.get("device_type") or ""
        )
        hits = parse_neighbor_output(
            raw,
            protocol="lldp",
            vendor=target.get("vendor") or "",
            device_type=target.get("device_type") or "",
        )
        added = 0
        updated = 0
        unmatched: list[dict[str, str]] = []
        touched: list[str] = []
        replaced: list[str] = []
        for hit in hits:
            peer = _match_hit_to_fabric_node(db, hit, self_id=fabric_node.id)
            if peer is None:
                if auto_add_unmatched and (hit.remote_name or hit.remote_ip):
                    # Not in inventory → SSH placeholder ManagedNE (empty IP/creds).
                    placeholder = ensure_lldp_discovered_managed_ne(
                        db,
                        remote_name=(hit.remote_name or "").strip(),
                        remote_ip=(hit.remote_ip or "").strip(),
                    )
                    peer = ensure_fabric_node_for_managed(db, placeholder)
                    peer.attrs = dict(peer.attrs or {})
                    peer.attrs["from_lldp_unmatched"] = True
                    peer.last_seen_at = now
                    peer.updated_at = now
                else:
                    unmatched.append(
                        {
                            "remote_name": (hit.remote_name or "").strip()[:256],
                            "remote_ip": (hit.remote_ip or "").strip()[:128],
                            "local_port": (hit.local_port or "").strip()[:128],
                            "remote_port": (hit.remote_port or "").strip()[:128],
                        }
                    )
                    continue
            edge, action = upsert_fabric_edge(
                db,
                a_node_id=fabric_node.id,
                b_node_id=peer.id,
                a_port=(hit.local_port or ""),
                b_port=(hit.remote_port or ""),
                source="lldp",
                now=now,
            )
            touched.append(edge.id)
            # Same local port, different peer → immediate missing (cutover).
            replaced.extend(
                _mark_replaced_port_peers(
                    db,
                    self_id=fabric_node.id,
                    local_port=(hit.local_port or ""),
                    peer_id=peer.id,
                    new_edge_id=edge.id,
                    now=now,
                )
            )
            if action == "added":
                added += 1
            elif action == "updated":
                updated += 1
        fabric_node.last_seen_at = now
        fabric_node.updated_at = now
        db.commit()
        stub_flag = bool(is_stub and raw.strip() and not hits)
        return {
            "ne_id": target["ne_id"],
            "ume_ne_id": target.get("ume_ne_id") or "",
            "fabric_node_id": fabric_node.id,
            "ne_name": target.get("ne_name") or "",
            "ne_ip": target.get("ne_ip") or "",
            "ok": True,
            "command": cmd,
            "neighbors": len(hits),
            "edges_added": added,
            "edges_updated": updated,
            "unmatched_count": len(unmatched),
            "unmatched": unmatched[:40],
            "parser_key": pkey,
            "parser_stub": stub_flag,
            "error": "parser_stub" if stub_flag else "",
            "raw_preview": _raw_preview(raw),
            "touched_edge_ids": touched,
            "replaced_edge_ids": replaced,
            "scanned_node_id": fabric_node.id,
        }
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {
            "ne_id": target.get("ne_id") or "",
            "ume_ne_id": target.get("ume_ne_id") or "",
            "fabric_node_id": "",
            "ne_name": target.get("ne_name") or "",
            "ne_ip": target.get("ne_ip") or "",
            "ok": False,
            "error": str(exc)[:500],
        }
    finally:
        db.close()


def _run_discover_job(job_id: str, body: FabricDiscoverRequest) -> None:
    db = SessionLocal()
    try:
        job = db.get(TopoDiscoverJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = _utcnow()
        job.updated_at = job.started_at
        try:
            targets = _resolve_scan_targets(db, body)
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail or "resolve_failed")[:1024]
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            db.commit()
            return
        job.total = len(targets)
        db.commit()

        concurrency = max(1, min(32, int(body.concurrency or 4)))
        added = 0
        updated = 0
        stale = 0
        scanned_ok: set[str] = set()
        touched_edges: set[str] = set()

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = {
                pool.submit(
                    _discover_one_target, t, auto_add_unmatched=bool(body.auto_add_unmatched)
                ): t
                for t in targets
            }
            for fut in as_completed(futs):
                result = fut.result()
                item = TopoDiscoverJobItem(
                    id=uuid4().hex,
                    job_id=job_id,
                    ne_id=str(result.get("ne_id") or ""),
                    ume_ne_id=str(result.get("ume_ne_id") or ""),
                    fabric_node_id=str(result.get("fabric_node_id") or ""),
                    ne_name=str(result.get("ne_name") or "")[:256],
                    ne_ip=str(result.get("ne_ip") or "")[:128],
                    ok=bool(result.get("ok")),
                    command=str(result.get("command") or "")[:256],
                    neighbors=int(result.get("neighbors") or 0),
                    edges_added=int(result.get("edges_added") or 0),
                    edges_updated=int(result.get("edges_updated") or 0),
                    unmatched_count=int(result.get("unmatched_count") or 0),
                    unmatched_json=list(result.get("unmatched") or []),
                    parser_key=str(result.get("parser_key") or "")[:64],
                    parser_stub=bool(result.get("parser_stub")),
                    error=str(result.get("error") or "")[:1024],
                    raw_preview=str(result.get("raw_preview") or ""),
                    created_at=_utcnow(),
                )
                db.add(item)
                added += int(result.get("edges_added") or 0)
                updated += int(result.get("edges_updated") or 0)
                if result.get("ok") and result.get("scanned_node_id"):
                    scanned_ok.add(str(result["scanned_node_id"]))
                for eid in result.get("touched_edge_ids") or []:
                    touched_edges.add(str(eid))
                # Cutover edges already marked missing — skip same-job miss bump.
                for eid in result.get("replaced_edge_ids") or []:
                    touched_edges.add(str(eid))
                job.done = int(job.done or 0) + 1
                job.edges_added = added
                job.edges_updated = updated
                job.updated_at = _utcnow()
                db.commit()

        # Absent on a successfully scanned endpoint → missing; purge after N cycles.
        if scanned_ok:
            newly_missing, purged = _apply_missing_and_purge(
                db,
                scanned_ok=scanned_ok,
                touched_edge_ids=touched_edges,
            )
            stale = newly_missing + purged
            job.edges_stale = stale
            db.commit()

        stats = db.get(TopoFabricStats, "global")
        if stats is None:
            stats = TopoFabricStats(id="global")
            db.add(stats)
        stats.last_discover_at = _utcnow()
        db.commit()
        merge_duplicate_fabric_nodes(db)
        refresh_fabric_stats(db)

        job = db.get(TopoDiscoverJob, job_id)
        if job is not None:
            job.status = "done"
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            job.edges_added = added
            job.edges_updated = updated
            job.edges_stale = stale
            db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        job = db.get(TopoDiscoverJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:1024]
            job.ended_at = _utcnow()
            job.updated_at = job.ended_at
            db.commit()
    finally:
        db.close()
        with _JOB_LOCK:
            _RUNNING_JOBS.discard(job_id)


def start_discover_job(
    db: Session,
    body: FabricDiscoverRequest,
    *,
    trigger_mode: str = "manual",
) -> FabricDiscoverJobOut:
    scope = str(body.scope or "ne_ids").strip().lower() or "ne_ids"
    if scope not in {"all_inventory", "ne_ids"}:
        raise HTTPException(status_code=400, detail="invalid_scope")
    trig = str(trigger_mode or getattr(body, "trigger_mode", None) or "manual").strip().lower() or "manual"
    if trig not in {"manual", "schedule", "topology"}:
        trig = "manual"
    now = _utcnow()
    job = TopoDiscoverJob(
        id=uuid4().hex,
        scope=scope,
        trigger_mode=trig,
        ne_ids_json=list(body.ne_ids or []),
        status="pending",
        total=0,
        done=0,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    with _JOB_LOCK:
        _RUNNING_JOBS.add(job.id)
    thread = threading.Thread(
        target=_run_discover_job,
        args=(job.id, body),
        name=f"topo-discover-{job.id[:8]}",
        daemon=True,
    )
    thread.start()
    return _job_out(db, job, include_items=False)
