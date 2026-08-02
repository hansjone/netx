"""Fabric inventory matching, LLDP peer index, and discovered NE ensure."""
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


from .topology_fabric_nodes import ensure_fabric_node_for_managed

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


