"""UME REST routes (token, sync, inventory, alarms, key-alert, runtime)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
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

@router.get("/v1/ume/token/status")
def ume_token_status() -> dict[str, Any]:
    client = _ume_client()
    st = client.token_status()
    return {"ok": True, **st}


@router.post("/v1/ume/token/refresh")
def ume_token_refresh() -> dict[str, Any]:
    client = _ume_client()
    try:
        before = client.token_status()
        token = client.refresh_if_needed()
        after = client.token_status()
        return {
            "ok": True,
            "token": token,
            "changed": bool(before.get("token_preview") != after.get("token_preview")),
            **after,
        }
    except Exception as exc:
        msg = str(exc)[:240]
        return {"ok": False, "error_kind": _ume_error_kind(msg), "error": msg}


@router.post("/v1/ume/token/disconnect")
def ume_token_disconnect() -> dict[str, Any]:
    client = _ume_client()
    ok = bool(client.logout_token())
    st = client.token_status()
    return {"ok": ok, **st}


@router.get("/v1/ume/alarm-subscription/status")
def ume_alarm_subscription_status(limit: int = 80) -> dict[str, Any]:
    st = get_subscription_status()
    ws_task = _UME_RUNTIME_TASKS.get("alarms_current_ws_consumer") or {}
    log_limit = max(10, min(int(limit or 80), 100))
    return {
        "ok": True,
        **st,
        **get_alarms_coordination_status(),
        "ws_connection": get_ws_connection_status(),
        "ws_consumer_status": str(ws_task.get("status") or ""),
        "ws_consumer_last_error": str(ws_task.get("last_error") or ""),
        "ws_consumer_last_run_at": ws_task.get("last_run_at"),
        "ws_logs": get_ws_logs(limit=log_limit),
    }


@router.post("/v1/ume/alarm-subscription/establish")
def ume_alarm_subscription_establish(
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    client = _ume_client()
    body = payload or {}
    force_reestablish = bool(body.get("force_reestablish"))
    try:
        st = establish_alarm_subscription_manual(client, db, force_reestablish=force_reestablish)
        return {"ok": True, "created": not bool(st.get("already_exists")), **st}
    except Exception as exc:
        msg = str(exc)[:240]
        raise HTTPException(status_code=502, detail=msg) from exc


@router.post("/v1/ume/alarm-subscription/cancel")
def ume_alarm_subscription_cancel(
    payload: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    client = _ume_client()
    body = payload or {}
    force_clear_local = bool(body.get("force_clear_local"))
    try:
        st = cancel_alarm_subscription_manual(client, db, force_clear_local=force_clear_local)
        if st.get("needs_local_cleanup"):
            return st
        return {"ok": True, **st}
    except Exception as exc:
        msg = str(exc)[:240]
        raise HTTPException(status_code=502, detail=msg) from exc


@router.post("/v1/ume/alarm-subscription/clear-local")
def ume_alarm_subscription_clear_local(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        st = clear_local_alarm_subscription_manual(db)
        return {"ok": True, "cleared_local": True, **st}
    except Exception as exc:
        msg = str(exc)[:240]
        raise HTTPException(status_code=502, detail=msg) from exc


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


@router.post("/v1/ume/sync")
def ume_sync(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    body = payload or {}
    domains = body.get("domains")
    if not isinstance(domains, list) or not domains:
        domains = ["inventory", "alarms_current", "alarms_history"]
    domain_set = {str(x).strip().lower() for x in domains if str(x).strip()}
    trigger_mode = str(body.get("trigger_mode") or "manual").strip().lower()
    if trigger_mode not in {"manual", "schedule"}:
        trigger_mode = "manual"

    client = _ume_client()
    out: dict[str, Any] = {"ok": True, "jobs": []}
    try:
        if "inventory" in domain_set:
            job = sync_inventory_full(db, client, trigger_mode=trigger_mode)
            out["jobs"].append(
                {
                    "domain": "inventory",
                    "status": job.status,
                    "pulled_count": int(job.pulled_count or 0),
                    "inserted_count": int(job.inserted_count or 0),
                    "updated_count": int(job.updated_count or 0),
                    "error_message": str(job.error_message or ""),
                }
            )
        if "alarms" in domain_set or "alarms_current" in domain_set:
            paused_ws_for_sync = False
            if is_wss_active_for_current_alarms() and trigger_mode == "manual":
                _runtime_pause_task("alarms_current_ws_consumer")
                request_ws_reconnect()
                paused_ws_for_sync = True
            try:
                job, batch = sync_alarms_current(
                    db,
                    client,
                    trigger_mode=trigger_mode,
                    wss_active=is_wss_active_for_current_alarms(),
                )
            finally:
                if paused_ws_for_sync:
                    _runtime_resume_task("alarms_current_ws_consumer")
                    request_ws_reconnect()
            out["jobs"].append(
                {
                    "domain": "alarms_current",
                    "status": job.status,
                    "batch_id": str(batch.batch_id),
                    "pulled_count": int(job.pulled_count or 0),
                    "inserted_count": int(job.inserted_count or 0),
                    "updated_count": int(job.updated_count or 0),
                    "error_message": str(job.error_message or ""),
                }
            )
        if "alarms_history" in domain_set:
            job, batch = sync_alarms_history_full(db, client, trigger_mode=trigger_mode)
            out["jobs"].append(
                {
                    "domain": "alarms_history",
                    "status": job.status,
                    "batch_id": str(batch.batch_id),
                    "pulled_count": int(job.pulled_count or 0),
                    "inserted_count": int(job.inserted_count or 0),
                    "updated_count": int(job.updated_count or 0),
                    "error_message": str(job.error_message or ""),
                }
            )
    except Exception as exc:
        out["ok"] = False
        out["error"] = str(exc)[:240]
    return out


def _ume_sync_job_deleted_count(row: UmeSyncJob) -> int:
    """Single reconcile delete count: inventory uses deleted_inventory_ne; current alarms uses deleted_stale_current_alarms."""
    raw = str(getattr(row, "details_json", "") or "").strip()
    if not raw:
        return 0
    try:
        obj = json.loads(raw)
    except Exception:
        return 0
    if not isinstance(obj, dict):
        return 0
    inv = cur = 0
    try:
        inv = max(0, int(obj.get("deleted_inventory_ne") or 0))
    except Exception:
        pass
    try:
        cur = max(0, int(obj.get("deleted_stale_current_alarms") or 0))
    except Exception:
        pass
    return int(inv + cur)


@router.get("/v1/ume/sync/status")
def ume_sync_status(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    q = db.query(UmeSyncJob)
    total = int(q.count())
    rows = (
        q.order_by(UmeSyncJob.id.desc())
        .offset((int(page) - 1) * int(page_size))
        .limit(int(page_size))
        .all()
    )
    items = []
    latest_by_domain: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = {
            "id": int(r.id),
            "domain": str(r.domain or ""),
            "status": str(r.status or ""),
            "trigger_mode": str(r.trigger_mode or ""),
            "pulled_count": int(r.pulled_count or 0),
            "inserted_count": int(r.inserted_count or 0),
            "updated_count": int(r.updated_count or 0),
            "deleted": int(_ume_sync_job_deleted_count(r)),
            "error_message": str(r.error_message or ""),
            "started_at": (_ensure_utc(r.started_at) or datetime.now(timezone.utc)).isoformat(),
            "ended_at": (_ensure_utc(r.ended_at).isoformat() if r.ended_at else None),
        }
        items.append(item)
        if item["domain"] and item["domain"] not in latest_by_domain:
            latest_by_domain[item["domain"]] = item
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
        "latest_by_domain": latest_by_domain,
        "runtime_tasks": _list_runtime_tasks(),
        "alarm_subscription": get_subscription_status(),
    }


@router.post("/v1/ume/runtime/tasks/{task}/pause")
def ume_runtime_task_pause(task: str) -> dict[str, Any]:
    tid = str(task or "").strip()
    if tid not in UME_KNOWN_RUNTIME_TASKS:
        raise HTTPException(status_code=404, detail="unknown_runtime_task")
    _runtime_pause_task(tid)
    if tid in ("alarms_current_auto_sync", "inventory_auto_sync"):
        _clear_force_resume_hints(tid)
    if tid == "alarms_current_ws_consumer":
        request_ws_reconnect()
    if tid == "oclaw_alarm_forwarder":
        request_forwarder_reconnect()
    _set_runtime_task(tid, status="paused", last_error="")
    return {"ok": True, "task": tid, "runtime_tasks": _list_runtime_tasks()}


@router.post("/v1/ume/runtime/tasks/{task}/resume")
def ume_runtime_task_resume(task: str) -> dict[str, Any]:
    tid = str(task or "").strip()
    if tid not in UME_KNOWN_RUNTIME_TASKS:
        raise HTTPException(status_code=404, detail="unknown_runtime_task")
    _runtime_resume_task(tid)
    if tid in ("alarms_current_auto_sync", "inventory_auto_sync"):
        _request_force_sync_after_resume(tid)
        resume_hint = RT_RESUMED_SYNC_SOON
    elif tid == "alarms_current_ws_consumer":
        request_ws_reconnect()
        resume_hint = RT_RESUMED_WSS_RECONNECT
    elif tid == "oclaw_alarm_forwarder":
        request_forwarder_reconnect()
        resume_hint = RT_RESUMED_OCLAW_WSS_RECONNECT
    else:
        resume_hint = RT_RESUMED
    _set_runtime_task(tid, status="running", last_error=resume_hint)
    return {"ok": True, "task": tid, "runtime_tasks": _list_runtime_tasks()}


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


@router.get("/v1/ume/alarms")
def ume_list_alarms(
    severity: str | None = Query(default=None),
    is_cleared: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    host_name: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmCurrent.perceived_severity == str(severity).strip())
    if is_cleared and str(is_cleared).strip():
        stmt = stmt.filter(UmeAlarmCurrent.is_cleared == str(is_cleared).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmCurrent.ne_id == str(ne_id).strip())
    hn = str(host_name or "").strip()
    if hn:
        stmt = stmt.filter(
            UmeAlarmCurrent.host_name.contains(hn) | UmeInventoryNE.host_name.contains(hn)
        )
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmCurrent.alarm_key.contains(kw)
            | UmeAlarmCurrent.object_name.contains(kw)
            | UmeAlarmCurrent.native_probable_cause.contains(kw)
            | UmeAlarmCurrent.notification_id.contains(kw)
            | UmeAlarmCurrent.host_name.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
            | UmeInventoryNE.host_name.contains(kw)
        )
    total = int(stmt.count())
    rows = (
        stmt.order_by(
            UmeAlarmCurrent.time_created.desc(),
            UmeAlarmCurrent.last_seen_at.desc(),
            UmeAlarmCurrent.alarm_key.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        {
            "alarm_key": str(alarm.alarm_key or ""),
            "ne_id": str(alarm.ne_id or ""),
            "ne_name": str((ne.ne_name if ne else "") or ""),
            "user_label": str((ne.user_label if ne else "") or ""),
            "host_name": _ume_alarm_host_name(alarm, ne),
            "ne_type": str((ne.ne_type if ne else "") or ""),
            "object_name": str(alarm.object_name or ""),
            "event_type": str(alarm.event_type or ""),
            "native_probable_cause": str(alarm.native_probable_cause or ""),
            "notification_id": str(alarm.notification_id or ""),
            "perceived_severity": str(alarm.perceived_severity or ""),
            "is_cleared": str(alarm.is_cleared or ""),
            "time_created": str(alarm.time_created or ""),
            "last_seen_at": (_ensure_utc(alarm.last_seen_at) or datetime.now(timezone.utc)).isoformat(),
        }
        for alarm, ne in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/v1/ume/alarms/fields")
def ume_alarms_fields() -> dict[str, Any]:
    """List all queryable field names for UME raw alarm query."""
    alarm_cols = [str(c.name) for c in UmeAlarmCurrent.__table__.columns]  # type: ignore[attr-defined]
    ne_cols = [str(c.name) for c in UmeInventoryNE.__table__.columns]  # type: ignore[attr-defined]
    selectable_fields = [f"alarm_{x}" for x in alarm_cols] + [f"ne_{x}" for x in ne_cols] + ["ne_exists"]
    order_by_allowed = ["last_seen_at", "time_created", "perceived_severity", "event_type", "ne_id"]
    return {
        "alarm_fields": alarm_cols,
        "ne_fields": ne_cols,
        "selectable_fields": selectable_fields,
        "order_by_allowed": order_by_allowed,
    }


def _serialize_ume_alarm_raw_row(
    alarm: UmeAlarmCurrent, ne: UmeInventoryNE | None, selected_fields: set[str] | None = None
) -> dict[str, Any]:
    selected = selected_fields or set()
    use_all = len(selected) == 0
    out: dict[str, Any] = {}
    for c in UmeAlarmCurrent.__table__.columns:  # type: ignore[attr-defined]
        name = str(c.name)
        v = getattr(alarm, name, None)
        key = f"alarm_{name}"
        if not use_all and key not in selected:
            continue
        if hasattr(v, "isoformat"):
            try:
                if isinstance(v, datetime):
                    out[key] = (_ensure_utc(v) or v).isoformat()
                else:
                    out[key] = v.isoformat()
                continue
            except Exception:
                pass
        out[key] = v
    if ne is None:
        if use_all or "ne_exists" in selected:
            out["ne_exists"] = False
        return out
    if use_all or "ne_exists" in selected:
        out["ne_exists"] = True
    for c in UmeInventoryNE.__table__.columns:  # type: ignore[attr-defined]
        name = str(c.name)
        v = getattr(ne, name, None)
        key = f"ne_{name}"
        if not use_all and key not in selected:
            continue
        if hasattr(v, "isoformat"):
            try:
                if isinstance(v, datetime):
                    out[key] = (_ensure_utc(v) or v).isoformat()
                else:
                    out[key] = v.isoformat()
                continue
            except Exception:
                pass
        out[key] = v
    return out


def _extract_ume_raw_group_field(alarm: UmeAlarmCurrent, ne: UmeInventoryNE | None, field: str) -> str:
    key = str(field or "").strip()
    if not key:
        return ""
    if key.startswith("alarm_"):
        attr = key[len("alarm_") :]
        return str(getattr(alarm, attr, "") or "")
    if key.startswith("ne_"):
        attr = key[len("ne_") :]
        if key == "ne_exists":
            return "1" if ne is not None else "0"
        if key == "ne_host_name":
            hn = str(getattr(alarm, "host_name", "") or "").strip()
            if hn:
                return hn
        if ne is None:
            return ""
        return str(getattr(ne, attr, "") or "")
    return ""


@router.get("/v1/ume/alarms/raw")
def ume_alarms_raw(
    severity: str | None = Query(default=None),
    is_cleared: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    time_from: str | None = Query(default=None),
    time_to: str | None = Query(default=None),
    order_by: str = Query(default="last_seen_at"),
    order: str = Query(default="desc"),
    select_fields: str | None = Query(default=None, description="comma-separated alarm_*/ne_* fields"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmCurrent.perceived_severity == str(severity).strip())
    if is_cleared and str(is_cleared).strip():
        stmt = stmt.filter(UmeAlarmCurrent.is_cleared == str(is_cleared).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmCurrent.ne_id == str(ne_id).strip())
    if event_type and str(event_type).strip():
        stmt = stmt.filter(UmeAlarmCurrent.event_type.contains(str(event_type).strip()))
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmCurrent.alarm_key.contains(kw)
            | UmeAlarmCurrent.object_name.contains(kw)
            | UmeAlarmCurrent.native_probable_cause.contains(kw)
            | UmeAlarmCurrent.event_type.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
        )
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    if dt_from:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))

    allowed_order_by = {
        "last_seen_at": UmeAlarmCurrent.last_seen_at,
        "time_created": UmeAlarmCurrent.time_created,
        "perceived_severity": UmeAlarmCurrent.perceived_severity,
        "event_type": UmeAlarmCurrent.event_type,
        "ne_id": UmeAlarmCurrent.ne_id,
    }
    col = allowed_order_by.get(str(order_by or "").strip(), UmeAlarmCurrent.last_seen_at)
    if str(order or "").strip().lower() == "asc":
        stmt = stmt.order_by(col.asc())
    else:
        stmt = stmt.order_by(col.desc())

    selected_fields: set[str] = set()
    fields_meta = ume_alarms_fields()
    selectable_fields = set(str(x) for x in (fields_meta.get("selectable_fields") or []))
    order_by_allowed = [str(x) for x in (fields_meta.get("order_by_allowed") or [])]
    if select_fields and str(select_fields).strip():
        selected_fields = {x.strip() for x in str(select_fields).split(",") if x.strip()}
        invalid = [x for x in selected_fields if x not in selectable_fields]
        if invalid:
            raise HTTPException(status_code=400, detail=f"invalid_select_fields:{','.join(sorted(invalid)[:20])}")

    total = int(stmt.count())
    rows = stmt.offset((int(page) - 1) * int(page_size)).limit(int(page_size)).all()
    return {
        "total": total,
        "page": int(page),
        "page_size": int(page_size),
        "select_fields": sorted(selected_fields) if selected_fields else [],
        "meta": {
            "available_fields": sorted(selectable_fields),
            "order_by_allowed": order_by_allowed,
            "time_filter_field": "last_seen_at",
        },
        "items": [_serialize_ume_alarm_raw_row(alarm, ne, selected_fields) for alarm, ne in rows],
    }


@router.get("/v1/ume/alarms/aggregate/raw")
def ume_alarms_aggregate_raw(
    group_by: str = Query(default="alarm_perceived_severity"),
    group_by2: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    is_cleared: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    time_from: str | None = Query(default=None),
    time_to: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    fields_meta = ume_alarms_fields()
    selectable_fields = set(str(x) for x in (fields_meta.get("selectable_fields") or []))
    g1 = str(group_by or "").strip()
    g2 = str(group_by2 or "").strip()
    if g1 not in selectable_fields:
        raise HTTPException(status_code=400, detail=f"invalid_group_by:{g1}")
    if g2 and g2 not in selectable_fields:
        raise HTTPException(status_code=400, detail=f"invalid_group_by2:{g2}")

    stmt = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmCurrent.perceived_severity == str(severity).strip())
    if is_cleared and str(is_cleared).strip():
        stmt = stmt.filter(UmeAlarmCurrent.is_cleared == str(is_cleared).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmCurrent.ne_id == str(ne_id).strip())
    if event_type and str(event_type).strip():
        stmt = stmt.filter(UmeAlarmCurrent.event_type.contains(str(event_type).strip()))
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmCurrent.alarm_key.contains(kw)
            | UmeAlarmCurrent.object_name.contains(kw)
            | UmeAlarmCurrent.native_probable_cause.contains(kw)
            | UmeAlarmCurrent.event_type.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
        )
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    if dt_from:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))

    rows = stmt.order_by(UmeAlarmCurrent.last_seen_at.desc()).all()
    counts: dict[tuple[str, str], int] = {}
    for alarm, ne in rows:
        k1 = _extract_ume_raw_group_field(alarm, ne, g1)
        k2 = _extract_ume_raw_group_field(alarm, ne, g2) if g2 else ""
        kk = (k1, k2)
        counts[kk] = int(counts.get(kk, 0)) + 1
    buckets = sorted(counts.items(), key=lambda x: x[1], reverse=True)[: int(limit)]
    return {
        "total": len(rows),
        "group_by": g1,
        "group_by2": g2 or None,
        "meta": {
            "available_fields": sorted(selectable_fields),
            "group_by_allowed": sorted(selectable_fields),
            "applied_filters": {
                "severity": str(severity or "").strip() or None,
                "is_cleared": str(is_cleared or "").strip() or None,
                "ne_id": str(ne_id or "").strip() or None,
                "event_type": str(event_type or "").strip() or None,
                "keyword": str(keyword or "").strip() or None,
                "time_from": str(time_from or "").strip() or None,
                "time_to": str(time_to or "").strip() or None,
            },
            "time_filter_field": "last_seen_at",
            "limit": int(limit),
        },
        "buckets": [
            {"key": k1, "key2": (k2 if g2 else None), "count": int(v)}
            for (k1, k2), v in buckets
        ],
    }


@router.get("/v1/ume/alarms/aggregate")
def ume_alarms_aggregate(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_ne = _aggregate_rows(rows, lambda x: _ume_alarm_ne_group_key(x[0], x[1]))
    return {"total": len(rows), "by_severity": by_severity, "by_ne": by_ne}


@router.get("/v1/ume/diagnostics")
def ume_diagnostics(
    lang: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_alarm_code = _aggregate_rows(rows, lambda x: x[0].event_type)[:10]
    by_ne = _aggregate_rows(rows, lambda x: _ume_alarm_ne_group_key(x[0], x[1]))[:10]

    lang_norm = _normalize_netx_lang(lang)
    proto_counts: dict[str, int] = {}
    for alarm, ne in rows:
        blob = " | ".join(
            [
                str(alarm.event_type or ""),
                str(alarm.native_probable_cause or ""),
                str(alarm.object_name or ""),
                str(ne.ne_name if ne else ""),
                str(ne.user_label if ne else ""),
                str(ne.ip_address if ne else ""),
            ]
        )
        bucket = _protocol_bucket_label(blob, lang=lang_norm)
        proto_counts[bucket] = int(proto_counts.get(bucket, 0)) + 1
    protocol_summary = sorted(proto_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "source": "ume_alarms_current",
        "total_alarms": len(rows),
        "severity_summary": [{"key": k, "count": v} for k, v in by_severity],
        "top_alarm_codes": [{"key": k, "count": v} for k, v in by_alarm_code],
        "top_ne": [{"key": k, "count": v} for k, v in by_ne],
        "protocol_summary": [{"key": k, "count": v} for k, v in protocol_summary],
    }


@router.get("/v1/ume/alarms/history")
def ume_list_alarms_history(
    severity: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    time_from: str | None = Query(default=None),
    time_to: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeAlarmHistory, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmHistory.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmHistory.perceived_severity == str(severity).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmHistory.ne_id == str(ne_id).strip())
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmHistory.alarm_key.contains(kw)
            | UmeAlarmHistory.object_name.contains(kw)
            | UmeAlarmHistory.native_probable_cause.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
        )
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    if dt_from:
        stmt = stmt.filter(UmeAlarmHistory.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        stmt = stmt.filter(UmeAlarmHistory.last_seen_at <= dt_to.replace(tzinfo=None))
    total = int(stmt.count())
    rows = stmt.order_by(UmeAlarmHistory.last_seen_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "alarm_key": str(alarm.alarm_key or ""),
            "ne_id": str(alarm.ne_id or ""),
            "ne_name": str((ne.ne_name if ne else "") or ""),
            "user_label": str((ne.user_label if ne else "") or ""),
            "object_name": str(alarm.object_name or ""),
            "event_type": str(alarm.event_type or ""),
            "native_probable_cause": str(alarm.native_probable_cause or ""),
            "perceived_severity": str(alarm.perceived_severity or ""),
            "is_cleared": str(alarm.is_cleared or ""),
            "time_created": str(alarm.time_created or ""),
            "last_seen_at": (_ensure_utc(alarm.last_seen_at) or datetime.now(timezone.utc)).isoformat(),
        }
        for alarm, ne in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/v1/ume/alarms/history/aggregate")
def ume_alarms_history_aggregate(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(UmeAlarmHistory, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmHistory.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_ne = _aggregate_rows(rows, lambda x: (x[1].user_label if x[1] else "") or (x[1].ne_name if x[1] else "") or x[0].ne_id)
    by_date = _aggregate_rows(rows, lambda x: str(x[0].time_created or "")[:10])
    return {"total": len(rows), "by_severity": by_severity, "by_ne": by_ne, "by_date": by_date}


