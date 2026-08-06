"""UME sync jobs and runtime pause/resume."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .db import get_db
from .models import UmeSyncJob
from .oclaw_alarm_forwarder import request_forwarder_reconnect
from .runtime_task_messages import (
    RT_RESUMED,
    RT_RESUMED_OCLAW_WSS_RECONNECT,
    RT_RESUMED_SYNC_SOON,
    RT_RESUMED_WSS_RECONNECT,
)
from .ume_alarm_ws import (
    get_subscription_status,
    is_wss_active_for_current_alarms,
    request_ws_reconnect,
)
from .ume_support import (
    UME_KNOWN_RUNTIME_TASKS,
    _clear_force_resume_hints,
    _ensure_utc,
    _list_runtime_tasks,
    _request_force_sync_after_resume,
    _runtime_pause_task,
    _runtime_resume_task,
    _set_runtime_task,
    _ume_client,
)
from .ume_sync_service import sync_alarms_current, sync_alarms_history_full, sync_inventory_full, sync_topology_full

_log = logging.getLogger("netx.ume.router")
router = APIRouter(tags=["ume"])

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
        if "topology" in domain_set:
            job = sync_topology_full(db, client, trigger_mode=trigger_mode)
            out["jobs"].append(
                {
                    "domain": "topology",
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
    inv = cur = topo_n = topo_l = 0
    try:
        inv = max(0, int(obj.get("deleted_inventory_ne") or 0))
    except Exception:
        pass
    try:
        cur = max(0, int(obj.get("deleted_stale_current_alarms") or 0))
    except Exception:
        pass
    try:
        topo_n = max(0, int(obj.get("deleted_topo_nodes") or 0))
    except Exception:
        pass
    try:
        topo_l = max(0, int(obj.get("deleted_topo_links") or 0))
    except Exception:
        pass
    return int(inv + cur + topo_n + topo_l)


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


