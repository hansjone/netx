"""Fabric edge upsert, missing/purge lifecycle, absorb, and merge."""
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


from .topology_fabric_nodes import (
    _edge_out,
    _node_out,
    _nodes_by_ids,
    _normalize_endpoints,
    refresh_fabric_stats,
)
from .topology_fabric_peers import _fabric_match_score, _is_inventory_node

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
) -> tuple[TopoFabricEdge | None, str]:
    """Return (edge, action) where action is added|updated|kept_manual|skipped_self_loop.

    Self-loops are skipped (``(None, \"skipped_self_loop\")``) so LLDP discovery can
    ignore a device advertising itself without aborting the rest of the scan.
    Manual edge APIs should treat that action as a client error.
    """
    now = now or _utcnow()
    a, b, ap, bp = _normalize_endpoints(a_node_id, b_node_id, a_port, b_port)
    if a == b:
        return None, "skipped_self_loop"
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



