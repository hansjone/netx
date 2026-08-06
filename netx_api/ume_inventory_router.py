"""UME inventory NE list/detail."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .key_alert_config import (
    get_key_alert_monitor_config,
    invalidate_key_alert_config_cache,
    set_key_alert_monitor_config,
)
from .key_alert_matcher import (
    invalidate_key_alert_rule_cache,
    normalize_match_type,
    parse_rule_ne_types_payload,
    rule_match_type,
    rule_match_value,
    rule_ne_types,
    rule_storage_key,
    serialize_rule_ne_types,
)
from .models import (
    UmeAlarmCurrent,
    UmeAlarmHistory,
    UmeInventoryNE,
    UmeKeyAlertForwardLog,
    UmeKeyAlertRule,
    UmeSyncJob,
)
from .oclaw_alarm_forwarder import (
    forwarder_status,
    request_forwarder_reconnect,
)
from .ume_alarm_ws import (
    cancel_alarm_subscription_manual,
    clear_local_alarm_subscription_manual,
    establish_alarm_subscription_manual,
    get_alarms_coordination_status,
    get_subscription_status,
    get_ws_connection_status,
    get_ws_logs,
    request_ws_reconnect,
)
from .ume_support import (
    UME_KNOWN_RUNTIME_TASKS,
    _aggregate_rows,
    _ensure_utc,
    _list_runtime_tasks,
    _request_force_sync_after_resume,
    _runtime_pause_task,
    _runtime_resume_task,
    _ume_alarm_host_name,
    _ume_alarm_ne_group_key,
    _ume_client,
    _ume_error_kind,
    _clear_force_resume_hints,
)
from .ume_sync_service import sync_alarms_current, sync_alarms_history_full, sync_inventory_full
from .ume_token_store import clear_shared_token

_log = logging.getLogger("netx.ume.router")
router = APIRouter(tags=["ume"])

@router.get("/v1/ume/inventory/ne-types")
def ume_list_inventory_ne_types(
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import func

    rows = (
        db.query(
            UmeInventoryNE.ne_type,
            func.count(UmeInventoryNE.ne_id).label("ne_count"),
        )
        .filter(UmeInventoryNE.ne_type != "")
        .group_by(UmeInventoryNE.ne_type)
        .order_by(func.count(UmeInventoryNE.ne_id).desc(), UmeInventoryNE.ne_type.asc())
        .limit(limit)
        .all()
    )
    items = [{"ne_type": str(ne_type or ""), "ne_count": int(ne_count or 0)} for ne_type, ne_count in rows if str(ne_type or "").strip()]
    return {"items": items, "total": len(items)}


@router.get("/v1/ume/inventory/ne")
def ume_list_ne(
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeInventoryNE)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        stmt = stmt.filter(
            UmeInventoryNE.ne_id.ilike(like)
            | UmeInventoryNE.ne_name.ilike(like)
            | UmeInventoryNE.user_label.ilike(like)
            | UmeInventoryNE.ip_address.ilike(like)
            | UmeInventoryNE.host_name.ilike(like)
        )
    total = int(stmt.count())
    rows = stmt.order_by(UmeInventoryNE.ne_id.asc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "ne_id": str(x.ne_id or ""),
            "ne_name": str(x.ne_name or ""),
            "user_label": str(x.user_label or ""),
            "ip_address": str(x.ip_address or ""),
            "ipv6_address": str(x.ipv6_address or ""),
            "ne_type": str(x.ne_type or ""),
            "device_level": str(x.device_level or ""),
            "host_name": str(x.host_name or ""),
            "location": str(x.location or ""),
            "hardware_version": str(x.hardware_version or ""),
            "loopback": str(x.loopback or ""),
            "consistent_state": str(x.consistent_state or ""),
            "interface_version": str(x.interface_version or ""),
            "mac": str(x.mac or ""),
            "admin_status": str(x.admin_status or ""),
            "address_type": str(x.address_type or ""),
            "connection_status": str(x.connection_status or ""),
            "maintain_status": str(x.maintain_status or ""),
            "net_mask": str(x.net_mask or ""),
            "create_time": str(x.create_time or ""),
            "creator": str(x.creator or ""),
            "last_seen_at": (_ensure_utc(x.last_seen_at) or datetime.now(timezone.utc)).isoformat(),
        }
        for x in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/v1/ume/inventory/ne/{ne_id}")
def ume_get_ne(ne_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = db.get(UmeInventoryNE, ne_id)
    if not row:
        raise HTTPException(status_code=404, detail="ume_ne_not_found")
    return {
        "ne_id": str(row.ne_id or ""),
        "ne_name": str(row.ne_name or ""),
        "user_label": str(row.user_label or ""),
        "ip_address": str(row.ip_address or ""),
        "ipv6_address": str(row.ipv6_address or ""),
        "ne_type": str(row.ne_type or ""),
        "device_level": str(row.device_level or ""),
        "host_name": str(row.host_name or ""),
        "location": str(row.location or ""),
        "hardware_version": str(row.hardware_version or ""),
        "loopback": str(row.loopback or ""),
        "consistent_state": str(row.consistent_state or ""),
        "interface_version": str(row.interface_version or ""),
        "mac": str(row.mac or ""),
        "admin_status": str(row.admin_status or ""),
        "address_type": str(row.address_type or ""),
        "connection_status": str(row.connection_status or ""),
        "maintain_status": str(row.maintain_status or ""),
        "net_mask": str(row.net_mask or ""),
        "create_time": str(row.create_time or ""),
        "creator": str(row.creator or ""),
        "vendor": str(row.vendor or ""),
        "source_type": str(row.source_type or ""),
        "first_seen_at": (_ensure_utc(row.first_seen_at) or datetime.now(timezone.utc)).isoformat(),
        "last_seen_at": (_ensure_utc(row.last_seen_at) or datetime.now(timezone.utc)).isoformat(),
        "raw_json": str(row.raw_json or "{}"),
    }


