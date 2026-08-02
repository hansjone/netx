"""Fabric topology + views + LLDP discovery (final model, no CDP)."""

from __future__ import annotations

import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cli_resolve import get_default_profile, infer_device_type_vendor
from .config import settings
from .db import SessionLocal
from .device_types import LLDP_DISCOVERED_NE_SOURCE, WEBCRT_NE_SOURCE
from .models import (
    LldpCollectPolicy,
    ManagedNE,
    TopoDiscoverJob,
    TopoDiscoverJobItem,
    TopoFabricEdge,
    TopoFabricNode,
    TopoFabricStats,
    TopoFolder,
    TopoView,
    TopoViewEdgeStyle,
    TopoViewNode,
    UmeInventoryNE,
)
from .topology_membership import (
    VIEW_KIND_CUSTOM,
    VIEW_KIND_PHYSICAL,
    has_hard_scope,
    merge_filter_with_membership,
    normalize_view_kind,
    normalize_view_role,
    parse_membership,
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
    TopologyFolderCreate,
    TopologyFolderOut,
    TopologyFolderUpdate,
    TopologyTreeFolderOut,
    TopologyTreeOut,
    TopologyTreeViewOut,
    TopologyViewCreate,
    TopologyViewGraphOut,
    TopologyViewOut,
    TopologyViewUpdate,
    ViewEdgeOut,
    ViewEdgeStylePatch,
    ViewNodeIn,
    ViewNodeOut,
    ViewNodesAdd,
    ViewPopulateOut,
    ViewPopulateRequest,
    ViewPositionsPatch,
)

ROOT_FOLDER_NAME = "Network"
PHYSICAL_VIEW_NAME = "Physical topology"
# Legacy system region name (no longer auto-created; stripped on bootstrap when empty).
_LEGACY_UNASSIGNED_NAME = "Unassigned"

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


# Postgres advisory-lock namespaces for fabric ensure (avoid cross-feature collisions).
_ADV_NS_FABRIC_MANAGED = 710001
_ADV_NS_FABRIC_UME = 710002
_DISCOVER_DEADLOCK_RETRIES = 4


def _is_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind is not None and str(bind.dialect.name).lower() == "postgresql"


def _advisory_xact_lock(db: Session, namespace: int, key: str) -> None:
    """Serialize concurrent creates for the same unique key (Postgres only)."""
    k = str(key or "").strip()
    if not k or not _is_postgres(db):
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, hashtext(:key))"),
        {"ns": int(namespace), "key": k},
    )


def _is_deadlock_error(exc: BaseException) -> bool:
    """True for Postgres 40P01 / SQLite 'database is locked' style races."""
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        pgcode = getattr(cur, "pgcode", None) or getattr(cur, "sqlstate", None)
        if str(pgcode or "") == "40P01":
            return True
        msg = str(cur).lower()
        if "deadlock" in msg or "40p01" in msg:
            return True
        orig = getattr(cur, "orig", None)
        if isinstance(orig, BaseException) and id(orig) not in seen:
            cur = orig
            continue
        cur = cur.__cause__ or cur.__context__  # type: ignore[assignment]
    return False


def _sleep_deadlock_backoff(attempt: int) -> None:
    # attempt is 0-based; jitter avoids thundering herd across workers.
    base = 0.05 * (2**attempt)
    time.sleep(base + random.uniform(0.0, 0.05))


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
# Folders (tree) + Views (leaf canvases)
# ---------------------------------------------------------------------------


def _folder_out(f: TopoFolder) -> TopologyFolderOut:
    return TopologyFolderOut(
        id=f.id,
        parent_id=str(f.parent_id or ""),
        kind=str(f.kind or "region"),
        name=f.name or "",
        sort_order=int(f.sort_order or 0),
        is_system=bool(f.is_system),
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


def _view_out(v: TopoView, *, node_count: int = 0) -> TopologyViewOut:
    return TopologyViewOut(
        id=v.id,
        name=v.name,
        remark=v.remark or "",
        folder_id=str(v.folder_id or ""),
        kind=normalize_view_kind(getattr(v, "kind", None)),
        role=normalize_view_role(v.role),
        sort_order=int(v.sort_order or 0),
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


def _get_folder_or_404(db: Session, folder_id: str) -> TopoFolder:
    fid = str(folder_id or "").strip()
    row = db.get(TopoFolder, fid) if fid else None
    if row is None:
        raise HTTPException(status_code=404, detail="topology_folder_not_found")
    return row


def ensure_region_physical_view(db: Session, folder_id: str, *, commit: bool = True) -> TopoView:
    """Ensure a site has exactly one default physical topology map."""
    fid = str(folder_id or "").strip()
    folder = _get_folder_or_404(db, fid)
    if str(folder.kind or "") == "root":
        raise HTTPException(status_code=400, detail="view_must_hang_under_region")
    existing = (
        db.query(TopoView)
        .filter(TopoView.folder_id == folder.id, TopoView.kind == VIEW_KIND_PHYSICAL)
        .order_by(TopoView.sort_order.asc(), TopoView.created_at.asc())
        .first()
    )
    if existing is not None:
        return existing
    now = _utcnow()
    role = "core"
    row = TopoView(
        id=uuid4().hex,
        folder_id=folder.id,
        parent_view_id=None,
        kind=VIEW_KIND_PHYSICAL,
        role=role,
        name=PHYSICAL_VIEW_NAME,
        remark="",
        sort_order=0,
        filter=merge_filter_with_membership({}, role=role, kind=VIEW_KIND_PHYSICAL),
        viewport={},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def bootstrap_topology_tree(db: Session) -> dict[str, str]:
    """Ensure hidden system root; flatten legacy nesting; ensure physical map per site."""
    now = _utcnow()
    root = (
        db.query(TopoFolder)
        .filter(TopoFolder.kind == "root")
        .order_by(TopoFolder.created_at.asc())
        .first()
    )
    if root is None:
        root = TopoFolder(
            id=uuid4().hex,
            parent_id=None,
            kind="root",
            name=ROOT_FOLDER_NAME,
            sort_order=0,
            is_system=True,
            created_at=now,
            updated_at=now,
        )
        db.add(root)
        db.flush()

    # Drop legacy auto-created Unassigned region when empty; otherwise demote to normal region.
    legacy = (
        db.query(TopoFolder)
        .filter(
            TopoFolder.kind == "region",
            TopoFolder.name == _LEGACY_UNASSIGNED_NAME,
        )
        .all()
    )
    for folder in legacy:
        view_cnt = db.query(TopoView).filter(TopoView.folder_id == folder.id).count()
        if view_cnt == 0:
            db.delete(folder)
        elif bool(folder.is_system):
            folder.is_system = False
            folder.updated_at = now

    # Flatten nesting + normalize kind for all views.
    for v in db.query(TopoView).all():
        changed = False
        if v.parent_view_id:
            v.parent_view_id = None
            changed = True
        kind = normalize_view_kind(getattr(v, "kind", None))
        if str(getattr(v, "kind", "") or "") != kind:
            v.kind = kind
            changed = True
        if not str(v.role or "").strip():
            v.role = "core"
            changed = True
        filt = dict(v.filter or {})
        if "membership" not in filt:
            v.filter = merge_filter_with_membership(
                filt, role=normalize_view_role(v.role), kind=kind
            )
            changed = True
        if changed:
            v.updated_at = now

    # Ensure every region has a physical map.
    regions = db.query(TopoFolder).filter(TopoFolder.kind == "region").all()
    for region in regions:
        ensure_region_physical_view(db, region.id, commit=False)

    db.commit()
    return {"root_id": root.id}


def create_folder(db: Session, body: TopologyFolderCreate) -> TopologyFolderOut:
    bootstrap_topology_tree(db)
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    kind = str(body.kind or "region").strip().lower()
    if kind != "region":
        raise HTTPException(status_code=400, detail="folder_kind_must_be_region")
    root = db.query(TopoFolder).filter(TopoFolder.kind == "root").first()
    if root is None:
        raise HTTPException(status_code=500, detail="topology_root_missing")
    parent_id = str(body.parent_id or "").strip() or root.id
    parent = _get_folder_or_404(db, parent_id)
    if str(parent.kind or "") != "root":
        raise HTTPException(status_code=400, detail="region_must_hang_under_root")
    now = _utcnow()
    row = TopoFolder(
        id=uuid4().hex,
        parent_id=root.id,
        kind="region",
        name=name[:256],
        sort_order=int(body.sort_order or 0),
        is_system=False,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    ensure_region_physical_view(db, row.id, commit=False)
    db.commit()
    db.refresh(row)
    return _folder_out(row)


def update_folder(db: Session, folder_id: str, body: TopologyFolderUpdate) -> TopologyFolderOut:
    row = _get_folder_or_404(db, folder_id)
    if str(row.kind or "") == "root":
        if body.parent_id is not None:
            raise HTTPException(status_code=400, detail="cannot_reparent_root")
    if body.name is not None:
        name = str(body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name_required")
        if bool(row.is_system) and str(row.kind or "") == "root":
            row.name = name[:256]
        elif bool(row.is_system):
            raise HTTPException(status_code=400, detail="cannot_rename_system_folder")
        else:
            row.name = name[:256]
    if body.sort_order is not None:
        row.sort_order = int(body.sort_order)
    if body.parent_id is not None and str(row.kind or "") == "region":
        parent = _get_folder_or_404(db, body.parent_id)
        if str(parent.kind or "") != "root":
            raise HTTPException(status_code=400, detail="region_must_hang_under_root")
        row.parent_id = parent.id
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _folder_out(row)


def delete_folder(db: Session, folder_id: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a region and cascade-delete its maps.

    Every region has a default physical map, so folder delete must purge views
    itself (cannot call ``delete_view``, which recreates physical).
    ``force`` is accepted for API compatibility; cascade always runs.
    """
    row = _get_folder_or_404(db, folder_id)
    if str(row.kind or "") == "root" or bool(row.is_system):
        raise HTTPException(status_code=400, detail="cannot_delete_system_folder")
    _ = force
    views = db.query(TopoView).filter(TopoView.folder_id == row.id).all()
    for v in views:
        db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.view_id == v.id).delete(
            synchronize_session=False
        )
        db.query(TopoViewNode).filter(TopoViewNode.view_id == v.id).delete(
            synchronize_session=False
        )
        db.delete(v)
    db.flush()
    db.delete(row)
    db.commit()
    return {"ok": True, "folder_id": folder_id, "deleted": True}


def get_topology_tree(db: Session) -> TopologyTreeOut:
    bootstrap_topology_tree(db)
    folders = db.query(TopoFolder).order_by(TopoFolder.sort_order.asc(), TopoFolder.name.asc()).all()
    views = db.query(TopoView).order_by(TopoView.sort_order.asc(), TopoView.name.asc()).all()
    nc_map: dict[str, int] = {}
    for vid, cnt in (
        db.query(TopoViewNode.view_id, func.count(TopoViewNode.id))
        .group_by(TopoViewNode.view_id)
        .all()
    ):
        nc_map[str(vid)] = int(cnt or 0)

    by_parent: dict[str, list[TopoFolder]] = {}
    root: TopoFolder | None = None
    for f in folders:
        if str(f.kind or "") == "root":
            root = f
            continue
        pid = str(f.parent_id or "")
        by_parent.setdefault(pid, []).append(f)

    views_by_folder: dict[str, list[TopoView]] = {}
    for v in views:
        views_by_folder.setdefault(str(v.folder_id or ""), []).append(v)

    def _flat_views(folder_views: list[TopoView]) -> list[TopologyTreeViewOut]:
        # physical first, then custom; stable by sort_order/name.
        ordered = sorted(
            folder_views,
            key=lambda x: (
                0 if normalize_view_kind(getattr(x, "kind", None)) == VIEW_KIND_PHYSICAL else 1,
                int(x.sort_order or 0),
                x.name or "",
                x.id,
            ),
        )
        return [
            TopologyTreeViewOut(
                id=v.id,
                name=v.name or "",
                kind=normalize_view_kind(getattr(v, "kind", None)),
                role=normalize_view_role(v.role),
                sort_order=int(v.sort_order or 0),
                node_count=nc_map.get(v.id, 0),
                updated_at=v.updated_at,
            )
            for v in ordered
        ]

    def _build(folder: TopoFolder) -> TopologyTreeFolderOut:
        kids = [_build(c) for c in by_parent.get(folder.id, [])]
        return TopologyTreeFolderOut(
            id=folder.id,
            parent_id=str(folder.parent_id or ""),
            kind=str(folder.kind or "region"),
            name=folder.name or "",
            sort_order=int(folder.sort_order or 0),
            is_system=bool(folder.is_system),
            views=_flat_views(views_by_folder.get(folder.id, [])),
            children=kids,
        )

    if root is None:
        return TopologyTreeOut(root=None)
    return TopologyTreeOut(root=_build(root))


def list_views(db: Session) -> dict[str, Any]:
    bootstrap_topology_tree(db)
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
    role = normalize_view_role(body.role)
    kind = normalize_view_kind(body.kind)
    folder_id = str(body.folder_id or "").strip()
    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id_required")
    folder = _get_folder_or_404(db, folder_id)
    if str(folder.kind or "") == "root":
        raise HTTPException(status_code=400, detail="view_must_hang_under_region")
    if kind == VIEW_KIND_PHYSICAL:
        existing = (
            db.query(TopoView)
            .filter(TopoView.folder_id == folder.id, TopoView.kind == VIEW_KIND_PHYSICAL)
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=400, detail="region_already_has_physical_view")
    filt = merge_filter_with_membership(dict(body.filter or {}), role=role, kind=kind)
    now = _utcnow()
    row = TopoView(
        id=uuid4().hex,
        folder_id=folder_id,
        parent_view_id=None,
        kind=kind,
        role=role,
        name=name[:256],
        remark=str(body.remark or "")[:1024],
        sort_order=int(body.sort_order or 0),
        filter=filt,
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
    if body.role is not None:
        row.role = normalize_view_role(body.role)
    if body.sort_order is not None:
        row.sort_order = int(body.sort_order)
    if body.folder_id is not None:
        fid = str(body.folder_id or "").strip()
        folder = _get_folder_or_404(db, fid)
        if str(folder.kind or "") == "root":
            raise HTTPException(status_code=400, detail="view_must_hang_under_region")
        row.folder_id = folder.id
    if body.kind is not None:
        new_kind = normalize_view_kind(body.kind)
        if new_kind == VIEW_KIND_PHYSICAL and normalize_view_kind(row.kind) != VIEW_KIND_PHYSICAL:
            clash = (
                db.query(TopoView)
                .filter(
                    TopoView.folder_id == row.folder_id,
                    TopoView.kind == VIEW_KIND_PHYSICAL,
                    TopoView.id != row.id,
                )
                .first()
            )
            if clash is not None:
                raise HTTPException(status_code=400, detail="region_already_has_physical_view")
        row.kind = new_kind
    row.parent_view_id = None
    if body.filter is not None:
        row.filter = merge_filter_with_membership(
            dict(body.filter or {}),
            role=normalize_view_role(row.role),
            kind=normalize_view_kind(row.kind),
        )
    if body.viewport is not None:
        row.viewport = dict(body.viewport or {})
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    nc = db.query(TopoViewNode).filter(TopoViewNode.view_id == row.id).count()
    return _view_out(row, node_count=nc)


def delete_view(db: Session, view_id: str, *, force: bool = False) -> dict[str, Any]:
    row = _get_view_or_404(db, view_id)
    folder_id = str(row.folder_id or "")
    is_physical = normalize_view_kind(row.kind) == VIEW_KIND_PHYSICAL
    if is_physical and not force:
        raise HTTPException(status_code=400, detail="cannot_delete_physical_view")
    db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.view_id == row.id).delete(
        synchronize_session=False
    )
    db.query(TopoViewNode).filter(TopoViewNode.view_id == row.id).delete(synchronize_session=False)
    db.delete(row)
    db.flush()
    if folder_id and is_physical:
        ensure_region_physical_view(db, folder_id, commit=False)
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
    outside = _outside_peers_for_view(db, view, member_ids=set(fids), layer=layer)
    return TopologyViewGraphOut(
        view=_view_out(view, node_count=len(nodes_out)),
        nodes=nodes_out,
        edges=edges_out,
        truncated=truncated,
        truncate_reason=reason,
        outside_peers=outside,
    )


def _membership_for_view(view: TopoView) -> dict[str, Any]:
    return parse_membership(dict(view.filter or {}), role=normalize_view_role(view.role))


def _fabric_in_hard_scope(db: Session, fn: TopoFabricNode, mem: dict[str, Any]) -> bool:
    """If hard scope filters are set, node must match ALL set dimensions (AND)."""
    if not has_hard_scope(mem):
        return True
    mid = str(fn.managed_ne_id or "").strip()
    allowed_mids = set(mem.get("managed_ne_ids") or [])
    if allowed_mids and mid not in allowed_mids:
        return False
    vendors = {str(x).lower() for x in (mem.get("vendors") or [])}
    if vendors and str(fn.vendor or "").strip().lower() not in vendors:
        return False
    dtypes = {str(x).lower() for x in (mem.get("device_types") or [])}
    if dtypes and str(fn.device_type or "").strip().lower() not in dtypes:
        return False
    keyword = str(mem.get("keyword") or "").strip().lower()
    if keyword:
        blob = f"{fn.name or ''} {fn.ip or ''}".lower()
        if keyword not in blob:
            return False
    tags_any = {str(x).lower() for x in (mem.get("tags_any") or [])}
    if tags_any:
        ne = db.get(ManagedNE, mid) if mid else None
        tag_blob = str(getattr(ne, "tags", "") or "").lower() if ne else ""
        if not any(t in tag_blob for t in tags_any):
            return False
    return True


def _outside_peers_for_view(
    db: Session,
    view: TopoView,
    *,
    member_ids: set[str],
    layer: str,
    limit: int = 50,
) -> list[dict[str, str]]:
    if not member_ids:
        return []
    edges = (
        db.query(TopoFabricEdge)
        .filter(
            TopoFabricEdge.layer == layer,
            or_(
                TopoFabricEdge.a_node_id.in_(list(member_ids)),
                TopoFabricEdge.b_node_id.in_(list(member_ids)),
            ),
        )
        .limit(5000)
        .all()
    )
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for e in edges:
        for peer_id, local_id in ((e.b_node_id, e.a_node_id), (e.a_node_id, e.b_node_id)):
            if local_id not in member_ids or peer_id in member_ids:
                continue
            if peer_id in seen:
                continue
            seen.add(peer_id)
            fn = db.get(TopoFabricNode, peer_id)
            out.append(
                {
                    "fabric_node_id": peer_id,
                    "name": (fn.name if fn else "") or "",
                    "ip": (fn.ip if fn else "") or "",
                    "via_node_id": local_id,
                }
            )
            if len(out) >= limit:
                return out
    return out


def _place_fabric_ids_on_view(
    db: Session,
    view: TopoView,
    fabric_ids: list[str],
    *,
    existing: set[str],
) -> int:
    now = _utcnow()
    added = 0
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    max_x = max((float(vn.x or 0) for vn in vnodes), default=40.0)
    base_x = max_x + 200.0
    cols = max(1, int(len(fabric_ids) ** 0.5) or 1)
    for i, fid in enumerate(fabric_ids):
        if fid in existing or db.get(TopoFabricNode, fid) is None:
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
        existing.add(fid)
        added += 1
    if added:
        view.updated_at = now
    return added


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
    mem = _membership_for_view(view)
    max_nodes = int(mem.get("max_nodes") or 300)
    now = _utcnow()
    existing = {
        vn.fabric_node_id
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    if len(existing) >= max_nodes:
        raise HTTPException(status_code=400, detail="membership_max_nodes")
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
    # `existing` already includes ids in added_ids; cap new placements.
    original_count = len(existing) - len(added_ids)
    room = max(0, max_nodes - original_count)
    if len(added_ids) > room:
        added_ids = added_ids[:room]
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


def _neighbor_ids(
    db: Session, *, seed_ids: set[str], layer: str, hops: int
) -> set[str]:
    frontier = set(seed_ids)
    found: set[str] = set()
    for _ in range(max(0, hops)):
        if not frontier:
            break
        rows = (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.layer == layer,
                or_(
                    TopoFabricEdge.a_node_id.in_(list(frontier)),
                    TopoFabricEdge.b_node_id.in_(list(frontier)),
                ),
            )
            .all()
        )
        nxt: set[str] = set()
        for e in rows:
            for a, b in ((e.a_node_id, e.b_node_id), (e.b_node_id, e.a_node_id)):
                if a in frontier and b not in seed_ids and b not in found:
                    nxt.add(b)
        found |= nxt
        frontier = nxt
    return found


def project_fabric_neighbors_to_view(db: Session, view_id: str) -> TopologyViewGraphOut:
    """Add in-scope fabric neighbors onto the leaf view (bounded by membership)."""
    merge_duplicate_fabric_nodes(db)
    view = _get_view_or_404(db, view_id)
    mem = _membership_for_view(view)
    if bool(mem.get("frozen")):
        return get_view_graph(db, view.id)

    max_nodes = int(mem.get("max_nodes") or 300)
    hops = int(mem.get("expand_hops") or 1)
    filt = dict(view.filter or {})
    layer = str(filt.get("layer") or "physical").strip() or "physical"

    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    # Drop placements pointing at missing fabric rows only (keep LLDP placeholders).
    orphan_vns = [vn for vn in vnodes if db.get(TopoFabricNode, vn.fabric_node_id) is None]
    if orphan_vns:
        for vn in orphan_vns:
            db.delete(vn)
        view.updated_at = _utcnow()
        db.commit()
        vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()

    existing = {vn.fabric_node_id for vn in vnodes}
    if not existing:
        return get_view_graph(db, view.id)
    if len(existing) >= max_nodes:
        g = get_view_graph(db, view.id)
        g.truncated = True
        g.truncate_reason = g.truncate_reason or "membership_cap"
        return g

    peer_ids = _neighbor_ids(db, seed_ids=existing, layer=layer, hops=hops)
    to_add: list[str] = []
    for peer in sorted(peer_ids):
        if peer in existing:
            continue
        fn = db.get(TopoFabricNode, peer)
        if fn is None or not _is_inventory_node(fn):
            continue
        if _fabric_match_score(db, fn) < 2:
            continue
        if not _fabric_in_hard_scope(db, fn, mem):
            continue
        to_add.append(peer)
        if len(existing) + len(to_add) >= max_nodes:
            break

    truncated = len(peer_ids) > len(to_add)
    if to_add:
        _place_fabric_ids_on_view(db, view, to_add, existing=existing)
        db.commit()
    g = get_view_graph(db, view.id)
    if truncated:
        g.truncated = True
        g.truncate_reason = g.truncate_reason or "membership_cap"
    return g


def populate_view(db: Session, view_id: str, body: ViewPopulateRequest) -> ViewPopulateOut:
    """Resolve membership candidates and optionally place them on the leaf view."""
    view = _get_view_or_404(db, view_id)
    role = normalize_view_role(view.role)
    if body.membership is not None:
        filt = merge_filter_with_membership(
            dict(view.filter or {}), role=role, membership=parse_membership(
                {"membership": body.membership}, role=role
            )
        )
        if not body.dry_run:
            view.filter = filt
    mem = parse_membership(dict(view.filter or {}), role=role)
    max_nodes = int(mem.get("max_nodes") or 300)
    hops = int(mem.get("expand_hops") or 1)
    layer = str((view.filter or {}).get("layer") or "physical").strip() or "physical"

    existing = {
        vn.fabric_node_id
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    seeds = set(mem.get("seed_fabric_node_ids") or []) | set(existing)

    # Seed from managed_ne_ids
    for mid in mem.get("managed_ne_ids") or []:
        ne = db.get(ManagedNE, mid)
        if ne is None:
            continue
        fn = ensure_fabric_node_for_managed(db, ne)
        seeds.add(fn.id)

    # Hard-scope scan when filters present
    candidates: set[str] = set(seeds)
    if has_hard_scope(mem):
        for fn in db.query(TopoFabricNode).all():
            if not _is_inventory_node(fn):
                continue
            if _fabric_in_hard_scope(db, fn, mem):
                candidates.add(fn.id)

    if hops > 0 and seeds:
        for peer in _neighbor_ids(db, seed_ids=seeds, layer=layer, hops=hops):
            fn = db.get(TopoFabricNode, peer)
            if fn is None or not _is_inventory_node(fn):
                continue
            if has_hard_scope(mem) and not _fabric_in_hard_scope(db, fn, mem):
                continue
            if _fabric_match_score(db, fn) < 2:
                continue
            candidates.add(peer)

    ordered = sorted(candidates)
    truncated = len(ordered) > max_nodes
    ordered = ordered[:max_nodes]
    would_add = [fid for fid in ordered if fid not in existing]

    outside = _outside_peers_for_view(db, view, member_ids=set(ordered), layer=layer)
    if body.dry_run:
        return ViewPopulateOut(
            view_id=view.id,
            dry_run=True,
            candidate_count=len(candidates),
            would_add=len(would_add),
            added=0,
            max_nodes=max_nodes,
            truncated=truncated,
            outside_peers=outside,
            graph=None,
        )

    added = _place_fabric_ids_on_view(db, view, would_add, existing=existing)
    if body.freeze_after:
        mem["frozen"] = True
        view.filter = merge_filter_with_membership(dict(view.filter or {}), role=role, membership=mem)
    view.updated_at = _utcnow()
    db.commit()
    g = get_view_graph(db, view.id)
    if truncated:
        g.truncated = True
        g.truncate_reason = g.truncate_reason or "membership_cap"
    return ViewPopulateOut(
        view_id=view.id,
        dry_run=False,
        candidate_count=len(candidates),
        would_add=len(would_add),
        added=added,
        max_nodes=max_nodes,
        truncated=truncated,
        outside_peers=g.outside_peers,
        graph=g,
    )


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
        edges_missing=int(job.edges_stale or 0),
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


def _ume_target_dict(db: Session, uid: str, default_profile: Any) -> dict[str, str] | None:
    ume = db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == uid).one_or_none()
    if ume is None:
        return None
    if default_profile is not None:
        dtype, vendor = infer_device_type_vendor(str(ume.ne_type or ""), default_profile)
    else:
        dtype, vendor = "zte_zxros", (ume.vendor or "ZTE")
    name = (ume.host_name or ume.ne_name or ume.user_label or ume.ip_address or uid).strip()
    return {
        "ne_id": uid,
        "ume_ne_id": uid,
        "ne_name": name,
        "ne_ip": ume.ip_address or "",
        "vendor": vendor or (ume.vendor or "ZTE"),
        "device_type": dtype or "zte_zxros",
    }


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

    managed_ids = [str(x).strip() for x in (body.managed_ne_ids or []) if str(x).strip()]
    ume_ids = [str(x).strip() for x in (body.ume_ne_ids or []) if str(x).strip()]
    if managed_ids or ume_ids:
        seen: set[str] = set()
        for mid in managed_ids:
            if mid in seen:
                continue
            ne = db.get(ManagedNE, mid)
            if ne is None:
                continue
            seen.add(mid)
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
        for uid in ume_ids:
            key = f"ume:{uid}"
            if key in seen:
                continue
            row = _ume_target_dict(db, uid, default_profile)
            if row is None:
                continue
            seen.add(key)
            targets.append(row)
        if not targets:
            raise HTTPException(status_code=400, detail="ne_ids_required")
        return targets

    # Legacy mixed ne_ids: prefer ManagedNE, leftover treated as UME.
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
        row = _ume_target_dict(db, uid, default_profile)
        if row is not None:
            targets.append(row)
    return targets


def prune_discover_jobs(db: Session, *, keep: int = 30) -> int:
    """Delete finished discover jobs beyond ``keep`` (newest kept). Open jobs always retained."""
    keep = max(0, min(200, int(keep)))
    finished = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["done", "failed"]))
        .order_by(TopoDiscoverJob.created_at.desc())
        .all()
    )
    to_drop = finished if keep == 0 else finished[keep:]
    if not to_drop:
        return 0
    dropped = 0
    for job in to_drop:
        db.query(TopoDiscoverJobItem).filter(TopoDiscoverJobItem.job_id == job.id).delete(
            synchronize_session=False
        )
        db.delete(job)
        dropped += 1
    if dropped:
        db.commit()
    return dropped


def _discover_one_target(
    target: dict[str, str],
    *,
    auto_add_unmatched: bool,
) -> dict[str, Any]:
    """Run LLDP for one NE in a fresh DB session.

    Keep the write txn short: resolve self fabric → commit → SSH → apply peers/edges
    (with deadlock retries). Holding inserts across SSH was a major deadlock source.
    """
    base = {
        "ne_id": target.get("ne_id") or "",
        "ume_ne_id": target.get("ume_ne_id") or "",
        "fabric_node_id": "",
        "ne_name": target.get("ne_name") or "",
        "ne_ip": target.get("ne_ip") or "",
    }
    db = SessionLocal()
    try:
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
            return {**base, "ok": False, "error": "fabric_node_resolve_failed"}

        fabric_node_id = fabric_node.id
        base["fabric_node_id"] = fabric_node_id
        # Release unique-index locks before slow SSH.
        db.commit()

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
            return {
                **base,
                "ok": False,
                "command": cmd,
                "error": str(exc.detail or "exec_failed")[:500],
            }
        if not exec_out.get("ok"):
            return {
                **base,
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
        stub_flag = bool(is_stub and raw.strip() and not hits)

        apply_out = _apply_discover_hits(
            db,
            fabric_node_id=fabric_node_id,
            hits=hits,
            auto_add_unmatched=auto_add_unmatched,
        )
        if not apply_out.get("ok"):
            return {
                **base,
                "ok": False,
                "command": cmd,
                "parser_key": pkey,
                "parser_stub": stub_flag,
                "error": str(apply_out.get("error") or "apply_failed")[:500],
                "raw_preview": _raw_preview(raw),
            }

        return {
            **base,
            "ok": True,
            "command": cmd,
            "neighbors": len(hits),
            "edges_added": int(apply_out.get("edges_added") or 0),
            "edges_updated": int(apply_out.get("edges_updated") or 0),
            "unmatched_count": int(apply_out.get("unmatched_count") or 0),
            "unmatched": list(apply_out.get("unmatched") or []),
            "parser_key": pkey,
            "parser_stub": stub_flag,
            "error": "parser_stub" if stub_flag else "",
            "raw_preview": _raw_preview(raw),
            "touched_edge_ids": list(apply_out.get("touched_edge_ids") or []),
            "replaced_edge_ids": list(apply_out.get("replaced_edge_ids") or []),
            "scanned_node_id": fabric_node_id,
        }
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return {**base, "ok": False, "error": str(exc)[:500]}
    finally:
        db.close()


def _apply_discover_hits(
    db: Session,
    *,
    fabric_node_id: str,
    hits: list[NeighborHit],
    auto_add_unmatched: bool,
) -> dict[str, Any]:
    """Write peer fabric nodes + edges; retry on Postgres deadlocks."""
    last_err = ""
    for attempt in range(_DISCOVER_DEADLOCK_RETRIES):
        try:
            now = _utcnow()
            fabric_node = db.get(TopoFabricNode, fabric_node_id)
            if fabric_node is None:
                return {"ok": False, "error": "fabric_node_missing"}

            added = 0
            updated = 0
            unmatched: list[dict[str, str]] = []
            touched: list[str] = []
            replaced: list[str] = []
            peer_index = _FabricPeerIndex(db, fabric_node.id)
            for hit in hits:
                peer = peer_index.match(hit)
                if peer is None:
                    if auto_add_unmatched and (hit.remote_name or hit.remote_ip):
                        peer = peer_index.ensure_placeholder(
                            remote_name=(hit.remote_name or "").strip(),
                            remote_ip=(hit.remote_ip or "").strip(),
                        )
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
            return {
                "ok": True,
                "edges_added": added,
                "edges_updated": updated,
                "unmatched_count": len(unmatched),
                "unmatched": unmatched[:40],
                "touched_edge_ids": touched,
                "replaced_edge_ids": replaced,
            }
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            last_err = str(exc)[:500]
            if _is_deadlock_error(exc) and attempt + 1 < _DISCOVER_DEADLOCK_RETRIES:
                _sleep_deadlock_backoff(attempt)
                continue
            return {"ok": False, "error": last_err}
    return {"ok": False, "error": last_err or "apply_failed"}


def _preensure_discover_targets(db: Session, targets: list[dict[str, str]]) -> None:
    """Create fabric rows for scan targets before parallel workers start."""
    for target in targets:
        managed = db.get(ManagedNE, target["ne_id"]) if target.get("ne_id") else None
        if managed is not None:
            ensure_fabric_node_for_managed(db, managed)
            continue
        if not target.get("ume_ne_id"):
            continue
        ume = (
            db.query(UmeInventoryNE)
            .filter(UmeInventoryNE.ne_id == target["ume_ne_id"])
            .one_or_none()
        )
        if ume is not None:
            ensure_fabric_node_for_ume(
                db,
                ume,
                device_type=target.get("device_type") or "",
                vendor=target.get("vendor") or "",
            )
    db.commit()


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

        # Reduce cross-worker races on self nodes before concurrent SSH/apply.
        try:
            _preensure_discover_targets(db, targets)
        except Exception:  # noqa: BLE001
            db.rollback()

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
        try:
            from .lldp_collect_service import DEFAULT_HISTORY_KEEP, ensure_policy

            keep = int(getattr(ensure_policy(db), "history_keep", DEFAULT_HISTORY_KEEP) or 0)
            prune_discover_jobs(db, keep=keep)
        except Exception:  # noqa: BLE001
            pass
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


def reclaim_stale_discover_jobs(
    db: Session,
    *,
    force_all_open: bool = False,
    now: datetime | None = None,
) -> int:
    """Mark orphaned / hung discover jobs as failed so scheduling can proceed.

    - ``force_all_open``: process restart — all pending/running rows are dead.
    - Otherwise: pending older than pending_stale_sec, or running with stale updated_at.
    """
    now = now or _utcnow()
    run_sec = max(60, int(getattr(settings, "lldp_collect_stale_run_sec", 7200) or 7200))
    pend_sec = max(30, int(getattr(settings, "lldp_collect_pending_stale_sec", 300) or 300))
    open_jobs = (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running"]))
        .all()
    )
    if not open_jobs:
        return 0
    closed = 0
    for job in open_jobs:
        status = str(job.status or "")
        if force_all_open:
            reason = "stale_running_reset_on_startup"
        elif status == "pending":
            created = job.created_at or job.updated_at or now
            if created > now - timedelta(seconds=pend_sec):
                continue
            reason = "pending_stale_timeout"
        else:
            touched = job.updated_at or job.started_at or job.created_at or now
            if touched > now - timedelta(seconds=run_sec):
                continue
            reason = "running_stale_timeout"
        job.status = "failed"
        job.ended_at = now
        job.updated_at = now
        msg = str(job.error or "").strip()
        job.error = (msg + ("; " if msg else "") + reason)[:1024]
        closed += 1
        with _JOB_LOCK:
            _RUNNING_JOBS.discard(job.id)
    if closed:
        db.commit()
    return closed


def start_discover_job(
    db: Session,
    body: FabricDiscoverRequest,
    *,
    trigger_mode: str = "manual",
) -> FabricDiscoverJobOut:
    reclaim_stale_discover_jobs(db)
    # Serialize multi-worker starts via singleton policy row lock (PG/SQLite FOR UPDATE).
    pol = db.get(LldpCollectPolicy, 1)
    if pol is None:
        pol = LldpCollectPolicy(
            id=1,
            enabled=False,
            interval_days=1,
            interval_hours=24,
            concurrency=4,
            scope_mode="all",
            selected_targets=[],
            auto_add_unmatched=True,
            history_keep=30,
            updated_at=_utcnow(),
        )
        db.add(pol)
        db.commit()
    db.query(LldpCollectPolicy).filter(LldpCollectPolicy.id == 1).with_for_update().one()
    if (
        db.query(TopoDiscoverJob)
        .filter(TopoDiscoverJob.status.in_(["pending", "running"]))
        .first()
        is not None
    ):
        raise HTTPException(status_code=409, detail="lldp_collect_already_running")
    scope = str(body.scope or "ne_ids").strip().lower() or "ne_ids"
    if scope not in {"all_inventory", "ne_ids"}:
        raise HTTPException(status_code=400, detail="invalid_scope")
    trig = str(trigger_mode or getattr(body, "trigger_mode", None) or "manual").strip().lower() or "manual"
    if trig not in {"manual", "schedule", "topology"}:
        trig = "manual"
    now = _utcnow()
    # Persist explicit source lists when present; keep legacy ne_ids for older clients.
    stored_ids = list(body.ne_ids or [])
    if body.managed_ne_ids or body.ume_ne_ids:
        stored_ids = [
            *(f"managed:{x}" for x in (body.managed_ne_ids or []) if str(x).strip()),
            *(f"ume:{x}" for x in (body.ume_ne_ids or []) if str(x).strip()),
        ]
    job = TopoDiscoverJob(
        id=uuid4().hex,
        scope=scope,
        trigger_mode=trig,
        ne_ids_json=stored_ids,
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
