"""UME token + alarm subscription routes."""
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
    _UME_RUNTIME_TASKS,
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


