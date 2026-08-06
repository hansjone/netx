"""UME key-alert rules / monitor / keyword helpers."""
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

@router.get("/v1/ume/key-alert-rules")
def ume_list_key_alert_rules(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str = Query(default=""),
    enabled: str | None = Query(default=None),
    match_type: str | None = Query(default=None),
) -> dict[str, Any]:
    from sqlalchemy import func, or_

    q = db.query(UmeKeyAlertRule)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            or_(
                UmeKeyAlertRule.notification_id.ilike(like),
                UmeKeyAlertRule.match_value.ilike(like),
                UmeKeyAlertRule.label.ilike(like),
            )
        )
    if enabled is not None:
        en = str(enabled).strip().lower()
        if en in {"1", "true", "yes", "on"}:
            q = q.filter(UmeKeyAlertRule.enabled == 1)
        elif en in {"0", "false", "no", "off"}:
            q = q.filter(UmeKeyAlertRule.enabled == 0)
    if match_type:
        mt = normalize_match_type(str(match_type))
        q = q.filter(UmeKeyAlertRule.match_type == mt)

    total = int(q.count())
    rows = (
        q.order_by(UmeKeyAlertRule.notification_id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    stat_rows = (
        db.query(
            UmeKeyAlertForwardLog.rule_key,
            func.count(UmeKeyAlertForwardLog.id).label("attempts"),
            func.sum(UmeKeyAlertForwardLog.oclaw_ok).label("published_ok"),
            func.max(UmeKeyAlertForwardLog.forwarded_at).label("last_forwarded_at"),
        )
        .filter(UmeKeyAlertForwardLog.rule_key != "")
        .group_by(UmeKeyAlertForwardLog.rule_key)
        .all()
    )
    stat_map = {
        str(rk or ""): {
            "attempts": int(attempts or 0),
            "published_ok": int(published_ok or 0),
            "last_forwarded_at": (_ensure_utc(last_at) or datetime.now(timezone.utc)).isoformat() if last_at else "",
        }
        for rk, attempts, published_ok, last_at in stat_rows
        if str(rk or "").strip()
    }
    items = [
        {
            "notification_id": str(row.notification_id or ""),
            "match_type": rule_match_type(row),
            "match_value": rule_match_value(row),
            "enabled": bool(int(row.enabled or 0)),
            "label": str(row.label or ""),
            "ne_types": rule_ne_types(row),
            "created_at": (_ensure_utc(row.created_at) or datetime.now(timezone.utc)).isoformat(),
            "updated_at": (_ensure_utc(row.updated_at) or datetime.now(timezone.utc)).isoformat(),
            "forward_stats": stat_map.get(str(row.notification_id or ""), {
                "attempts": 0,
                "published_ok": 0,
                "last_forwarded_at": "",
            }),
        }
        for row in rows
    ]
    fwd = forwarder_status()
    return {"items": items, "total": total, "page": page, "page_size": page_size, "forwarder": fwd}


@router.get("/v1/ume/key-alert-monitor")
def ume_key_alert_monitor(
    db: Session = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    keyword: str = Query(default=""),
    enabled: str | None = Query(default=None),
    match_type: str | None = Query(default=None),
) -> dict[str, Any]:
    base = ume_list_key_alert_rules(
        db=db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        enabled=enabled,
        match_type=match_type,
    )
    return {
        "ok": True,
        "rules": base.get("items") or [],
        "total": int(base.get("total") or 0),
        "page": int(base.get("page") or page),
        "page_size": int(base.get("page_size") or page_size),
        "config": get_key_alert_monitor_config(db),
        "forwarder": base.get("forwarder") or forwarder_status(),
    }


@router.patch("/v1/ume/key-alert-monitor/config")
def ume_update_key_alert_monitor_config(payload: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    if "forward_on_clear" not in payload:
        raise HTTPException(status_code=400, detail="forward_on_clear_required")
    config = set_key_alert_monitor_config(db, forward_on_clear=bool(payload.get("forward_on_clear")))
    return {"ok": True, "config": config}


@router.post("/v1/ume/key-alert-rules")
def ume_upsert_key_alert_rule(payload: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    match_type = normalize_match_type(str(payload.get("match_type") or "notification_id"))
    match_value = str(payload.get("match_value") or payload.get("notification_id") or "").strip()
    if not match_value:
        raise HTTPException(status_code=400, detail="match_value_required")
    label = str(payload.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="label_required")
    enabled = 1 if bool(payload.get("enabled", True)) else 0
    ne_types_list = parse_rule_ne_types_payload(payload.get("ne_types"))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        storage_key = rule_storage_key(match_type=match_type, value=match_value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = db.get(UmeKeyAlertRule, storage_key)
    if row is None:
        row = UmeKeyAlertRule(notification_id=storage_key, created_at=now, updated_at=now)
        db.add(row)
    row.match_type = match_type
    row.match_value = match_value
    row.enabled = enabled
    row.label = label
    row.ne_types = serialize_rule_ne_types(ne_types_list)
    row.updated_at = now
    saved = {
        "notification_id": storage_key,
        "match_type": match_type,
        "match_value": match_value,
        "enabled": bool(enabled),
        "label": label,
        "ne_types": ne_types_list,
    }
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        msg = str(exc).lower()
        if "match_type" in msg or "match_value" in msg or "ne_types" in msg or "undefinedcolumn" in msg:
            raise HTTPException(
                status_code=503,
                detail="key_alert_schema_outdated: restart netx API to apply database migration",
            ) from exc
        raise
    invalidate_key_alert_rule_cache()
    return {"ok": True, "item": saved}


@router.patch("/v1/ume/key-alert-rules/{rule_key:path}")
def ume_patch_key_alert_rule(rule_key: str, payload: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    key = str(rule_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="rule_key_required")
    row = db.get(UmeKeyAlertRule, key)
    if row is None:
        raise HTTPException(status_code=404, detail="rule_not_found")
    has_enabled = "enabled" in payload
    has_ne_types = "ne_types" in payload
    if not has_enabled and not has_ne_types:
        raise HTTPException(status_code=400, detail="patch_fields_required")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if has_enabled:
        row.enabled = 1 if bool(payload.get("enabled")) else 0
    if has_ne_types:
        row.ne_types = serialize_rule_ne_types(parse_rule_ne_types_payload(payload.get("ne_types")))
    row.updated_at = now
    db.commit()
    invalidate_key_alert_rule_cache()
    return {
        "ok": True,
        "item": {
            "notification_id": key,
            "match_type": rule_match_type(row),
            "match_value": rule_match_value(row),
            "enabled": bool(int(row.enabled or 0)),
            "label": str(row.label or ""),
            "ne_types": rule_ne_types(row),
        },
    }


@router.delete("/v1/ume/key-alert-rules/{rule_key:path}")
def ume_delete_key_alert_rule(rule_key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    key = str(rule_key or "").strip()
    row = db.get(UmeKeyAlertRule, key)
    if row is None:
        raise HTTPException(status_code=404, detail="rule_not_found")
    db.delete(row)
    db.commit()
    invalidate_key_alert_rule_cache()
    return {"ok": True, "deleted": key}


@router.get("/v1/ume/alarm-keywords")
def ume_list_alarm_keywords(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import func

    rows = (
        db.query(
            UmeAlarmCurrent.native_probable_cause,
            func.count(UmeAlarmCurrent.alarm_key).label("cnt"),
        )
        .filter(UmeAlarmCurrent.native_probable_cause != "")
        .group_by(UmeAlarmCurrent.native_probable_cause)
        .order_by(func.count(UmeAlarmCurrent.alarm_key).desc(), UmeAlarmCurrent.native_probable_cause.asc())
        .limit(limit)
        .all()
    )
    items = [
        {
            "keyword": str(cause or ""),
            "alarm_count": int(cnt or 0),
        }
        for cause, cnt in rows
        if str(cause or "").strip()
    ]
    return {"items": items, "total": len(items)}


@router.get("/v1/ume/notification-ids")
def ume_list_notification_ids(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from sqlalchemy import func

    rows = (
        db.query(
            UmeAlarmCurrent.notification_id,
            func.max(UmeAlarmCurrent.native_probable_cause).label("cause_sample"),
        )
        .filter(UmeAlarmCurrent.notification_id != "")
        .group_by(UmeAlarmCurrent.notification_id)
        .order_by(UmeAlarmCurrent.notification_id.asc())
        .limit(limit)
        .all()
    )
    items = [
        {
            "notification_id": str(nid or ""),
            "native_probable_cause_sample": str(cause or ""),
        }
        for nid, cause in rows
        if str(nid or "").strip()
    ]
    return {"items": items, "total": len(items), "forwarder": forwarder_status()}


