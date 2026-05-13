from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timezone
from io import StringIO
import time
import re
import threading

_schedule_log = logging.getLogger("netx.ume.schedule")
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from typing import Any
import uvicorn

from .ap_client import analyze_with_oclaw, health_with_oclaw
from .config import settings
from .db import Base, SessionLocal, engine
from .importer import aggregate_alarms, import_alarm_excel, query_alarms
from .models import (
    AiAnalyzeHistory,
    AlarmBatch,
    AlarmNorm,
    ImportErrorRow,
    UmeAlarmCurrent,
    UmeAlarmHistory,
    UmeInventoryNE,
    UmeSyncJob,
)
from .models import ImportJob
from .parser_config import load_parser_config
from .ume_client import UMEClient
from .ume_sync_service import sync_alarms_current, sync_alarms_history_full, sync_inventory_full
from .ume_token_store import (
    clear_shared_token,
    load_shared_token,
    release_refresh_lock,
    save_shared_token,
    try_acquire_refresh_lock,
    wait_for_token_update,
)
from .schemas import (
    AlarmAggregateBucket,
    AlarmAggregateResponse,
    AiAnalyzeHistoryItem,
    AiAnalyzeHistoryResponse,
    AlarmItem,
    AlarmQueryResponse,
    BatchSummary,
    ImportJobItem,
    ImportJobListResponse,
)

app = FastAPI(title="netx ops tool", version="0.1.0")
parser_cfg = load_parser_config()
_UME_CLIENT_SINGLETON = UMEClient(
    token_loader=lambda: load_shared_token(),
    token_saver=lambda token, exp: save_shared_token(token, exp),
    token_clearer=lambda: clear_shared_token(),
    lock_acquirer=lambda: try_acquire_refresh_lock(),
    lock_releaser=lambda: release_refresh_lock(),
    token_waiter=lambda min_exp: wait_for_token_update(min_expires_at_epoch_s=float(min_exp)),
)

_SQL_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|call|copy|vacuum|analyze)\b",
    flags=re.IGNORECASE,
)
_UME_RUNTIME_TASKS: dict[str, dict[str, Any]] = {
    "token_keepalive": {"task": "token_keepalive", "status": "init", "last_run_at": None, "last_error": ""},
    "alarms_current_auto_sync": {"task": "alarms_current_auto_sync", "status": "init", "last_run_at": None, "last_error": ""},
    "inventory_auto_sync": {"task": "inventory_auto_sync", "status": "init", "last_run_at": None, "last_error": ""},
}
_UME_RUNTIME_PAUSED: dict[str, bool] = {}
UME_KNOWN_RUNTIME_TASKS: tuple[str, ...] = tuple(_UME_RUNTIME_TASKS.keys())
_UME_RUNTIME_LOCK = threading.Lock()
# Debounce skip / wake for scheduled sync threads (resume should not wait full interval).
_UME_DEBOUNCE_MUTEX = threading.Lock()
_UME_SYNC_SKIP_DEBOUNCE: set[str] = set()
_UME_DEBOUNCE_WAKE: dict[str, threading.Event] = {}


def _debounce_wake_event(task_id: str) -> threading.Event:
    with _UME_DEBOUNCE_MUTEX:
        ev = _UME_DEBOUNCE_WAKE.get(task_id)
        if ev is None:
            ev = threading.Event()
            _UME_DEBOUNCE_WAKE[task_id] = ev
        return ev


def _request_force_sync_after_resume(task_id: str) -> None:
    """Skip next debounce wait and interrupt an in-progress debounce sleep (UI 开始)."""
    with _UME_DEBOUNCE_MUTEX:
        _UME_SYNC_SKIP_DEBOUNCE.add(task_id)
    try:
        _debounce_wake_event(task_id).set()
    except Exception:
        pass


def _clear_force_resume_hints(task_id: str) -> None:
    """Pause: drop pending skip/wake so state is predictable."""
    with _UME_DEBOUNCE_MUTEX:
        _UME_SYNC_SKIP_DEBOUNCE.discard(task_id)
    try:
        _debounce_wake_event(task_id).clear()
    except Exception:
        pass


def _reset_debounce_wakeup() -> None:
    with _UME_DEBOUNCE_MUTEX:
        _UME_SYNC_SKIP_DEBOUNCE.clear()
        for ev in _UME_DEBOUNCE_WAKE.values():
            try:
                ev.clear()
            except Exception:
                pass


def _set_runtime_task(task: str, *, status: str, last_run_at: datetime | None = None, last_error: str = "") -> None:
    with _UME_RUNTIME_LOCK:
        item = _UME_RUNTIME_TASKS.get(task, {"task": task, "status": "init", "last_run_at": None, "last_error": ""})
        item["status"] = str(status or "unknown")
        if last_run_at is not None:
            item["last_run_at"] = last_run_at
        item["last_error"] = str(last_error or "")
        _UME_RUNTIME_TASKS[task] = item


def _runtime_is_paused(task: str) -> bool:
    with _UME_RUNTIME_LOCK:
        return bool(_UME_RUNTIME_PAUSED.get(str(task or "").strip()))


def _runtime_pause_task(task: str) -> None:
    tid = str(task or "").strip()
    with _UME_RUNTIME_LOCK:
        if tid not in _UME_RUNTIME_TASKS:
            raise KeyError(tid)
        _UME_RUNTIME_PAUSED[tid] = True


def _runtime_resume_task(task: str) -> None:
    tid = str(task or "").strip()
    with _UME_RUNTIME_LOCK:
        _UME_RUNTIME_PAUSED[tid] = False


def _format_runtime_interval_label(seconds: int) -> str:
    s = max(1, int(seconds))
    if s >= 3600 and s % 3600 == 0:
        h = s // 3600
        return f"{h} h"
    if s >= 60 and s % 60 == 0:
        m = s // 60
        return f"{m} min"
    return f"{s}s"


def _runtime_task_interval_fields(task_id: str) -> tuple[int | None, str]:
    """Effective loop interval as configured at process start (matches startup clamps)."""
    if task_id == "token_keepalive":
        if not bool(getattr(settings, "ume_keepalive_enabled", True)):
            return None, "未启用"
        interval_s = int(getattr(settings, "ume_keepalive_interval_s", 600) or 600)
        eff = max(30, min(interval_s, 3600))
        return eff, _format_runtime_interval_label(eff)
    if task_id == "alarms_current_auto_sync":
        if not bool(getattr(settings, "ume_sync_alarms_current_enabled", True)):
            return None, "未启用"
        interval_s = int(getattr(settings, "ume_sync_alarms_current_interval_s", 300) or 300)
        eff = max(30, min(interval_s, 86400))
        return eff, _format_runtime_interval_label(eff)
    if task_id == "inventory_auto_sync":
        if not bool(getattr(settings, "ume_sync_inventory_auto_enabled", True)):
            return None, "未启用"
        hours = int(getattr(settings, "ume_sync_inventory_every_hours", 48) or 48)
        hours = max(1, min(hours, 168))
        eff = int(hours * 3600)
        return eff, _format_runtime_interval_label(eff)
    return None, "—"


def _list_runtime_tasks() -> list[dict[str, Any]]:
    with _UME_RUNTIME_LOCK:
        out: list[dict[str, Any]] = []
        for v in _UME_RUNTIME_TASKS.values():
            task_id = str(v.get("task") or "")
            paused = bool(_UME_RUNTIME_PAUSED.get(task_id))
            eff_status = "paused" if paused else str(v.get("status") or "unknown")
            ts = _ensure_utc(v.get("last_run_at")) if isinstance(v.get("last_run_at"), datetime) else None
            interval_s, interval_label = _runtime_task_interval_fields(task_id)
            out.append(
                {
                    "task": task_id,
                    "status": eff_status,
                    "paused": paused,
                    "last_run_at": ts.isoformat() if ts else None,
                    "last_error": str(v.get("last_error") or ""),
                    "interval_s": interval_s,
                    "interval_label": interval_label,
                }
            )
        return out


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # All timestamps are stored as UTC in DB (naive). Treat naive as UTC.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc)
    except Exception:
        return dt


def _reset_runtime_pause_flags() -> None:
    """Ensure no task is stuck paused in memory after process boot (pause is not persisted)."""
    with _UME_RUNTIME_LOCK:
        for tid in UME_KNOWN_RUNTIME_TASKS:
            _UME_RUNTIME_PAUSED[tid] = False
    _reset_debounce_wakeup()


def _fail_stale_running_sync_jobs_on_startup() -> None:
    """Orphan running rows (crashed mid-sync) confuse scheduling; close them so interval uses real ended_at."""
    db = SessionLocal()
    try:
        rows = (
            db.query(UmeSyncJob)
            .filter(UmeSyncJob.status == "running", UmeSyncJob.ended_at.is_(None))
            .all()
        )
        if not rows:
            return
        now_naive = datetime.utcnow()
        for row in rows:
            row.status = "failed"
            row.ended_at = now_naive
            msg = str(row.error_message or "").strip()
            suffix = "stale_running_reset_on_startup"
            row.error_message = (msg + ("; " if msg else "") + suffix)[:1024]
        db.commit()
        _schedule_log.warning("startup: closed %s orphaned running ume_sync_jobs", len(rows))
    except Exception:
        _schedule_log.exception("startup: stale sync job cleanup failed")
    finally:
        db.close()


def _sleep_or_until_paused(task_id: str, total_s: float) -> None:
    """Sleep up to total_s wall seconds; honor pause; wake early on resume (debounce interrupt)."""
    deadline = time.time() + max(0.0, float(total_s))
    ev = _debounce_wake_event(task_id)
    ev.clear()
    while time.time() < deadline:
        if _runtime_is_paused(task_id):
            time.sleep(1)
            continue
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        timeout = min(2.0, remaining)
        if ev.wait(timeout=timeout):
            ev.clear()
            _schedule_log.info("%s: debounce wait interrupted (resume)", task_id)
            with _UME_DEBOUNCE_MUTEX:
                _UME_SYNC_SKIP_DEBOUNCE.discard(task_id)
            return
    if ev.is_set():
        ev.clear()


def _seconds_since_last_finished_job(db: Session, domain: str) -> float | None:
    """Seconds since latest job with ended_at for domain (done or failed). None if none."""
    row = (
        db.query(UmeSyncJob)
        .filter(
            UmeSyncJob.domain == domain,
            UmeSyncJob.ended_at.isnot(None),
        )
        .order_by(UmeSyncJob.ended_at.desc())
        .limit(1)
        .first()
    )
    if not row or row.ended_at is None:
        return None
    end = _ensure_utc(row.ended_at)
    if end is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - end).total_seconds())


def _maybe_wait_for_sync_interval(
    *,
    task_id: str,
    domain: str,
    interval_s: int,
    label: str,
) -> None:
    """Sleep until interval elapsed since last finished job (ended_at), if any."""
    with _UME_DEBOUNCE_MUTEX:
        if task_id in _UME_SYNC_SKIP_DEBOUNCE:
            _UME_SYNC_SKIP_DEBOUNCE.discard(task_id)
            _schedule_log.info("%s: debounce skipped (resume/kick)", label)
            return
    db = SessionLocal()
    try:
        elapsed = _seconds_since_last_finished_job(db, domain)
    finally:
        db.close()
    if elapsed is None:
        _schedule_log.info("%s: no prior finished job for %s, sync now", label, domain)
        return
    if elapsed >= float(interval_s):
        _schedule_log.info("%s: last finished %.0fs ago (>= %ss), sync now", label, elapsed, interval_s)
        return
    wait_s = float(interval_s) - elapsed
    _schedule_log.info("%s: last finished %.0fs ago, wait %.0fs before sync", label, elapsed, wait_s)
    # Do not write debounce/wait text to last_error — it is not an error and would
    # overwrite the cleared state right after a successful sync on the next loop tick.
    _sleep_or_until_paused(task_id, wait_s)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _parse_time(text: str | None) -> datetime | None:
    s = str(text or "").strip()
    if not s:
        return None
    s2 = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s2)
        return _ensure_utc(dt)
    except Exception:
        return None


def _aggregate_rows(items: list[Any], key_fn) -> list[dict[str, Any]]:
    bucket: dict[str, int] = {}
    for item in items:
        key = str(key_fn(item) or "").strip()
        if not key:
            key = "unknown"
        bucket[key] = int(bucket.get(key, 0)) + 1
    return [{"key": k, "count": v} for k, v in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)]


def _ume_client() -> UMEClient:
    return _UME_CLIENT_SINGLETON


def _ume_error_kind(err: str) -> str:
    low = str(err or "").lower()
    if "401" in low or "403" in low or "password" in low or "auth" in low:
        return "auth_failed"
    if "timeout" in low:
        return "timeout"
    if "tls" in low or "certificate" in low or "ssl" in low:
        return "tls_failed"
    if "connect" in low or "name or service not known" in low:
        return "connect_failed"
    if "handshake" in low:
        return "handshake_failed"
    return "other"


@app.post("/v1/sql/query")
def sql_query(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)) -> dict:
    """
    Read-only SQL query endpoint for AI power users.

    Safety constraints:
    - SELECT only, single statement (no ';')
    - forbid DDL/DML keywords
    - enforce max rows (server-side LIMIT wrapper)
    - require batch_id param and require SQL contains ':batch_id'
    """
    payload = payload or {}
    sql = str(payload.get("sql") or "").strip()
    batch_id = str(payload.get("batch_id") or "").strip()
    limit = int(payload.get("limit") or 200)
    limit = max(1, min(limit, 2000))
    if not sql:
        raise HTTPException(status_code=400, detail="sql_required")
    if ";" in sql:
        raise HTTPException(status_code=400, detail="single_statement_only")
    low = sql.lower().lstrip()
    if not low.startswith("select"):
        raise HTTPException(status_code=400, detail="select_only")
    if _SQL_FORBIDDEN_RE.search(sql):
        raise HTTPException(status_code=400, detail="forbidden_keyword")
    if not batch_id:
        raise HTTPException(status_code=400, detail="batch_id_required")
    if ":batch_id" not in sql:
        raise HTTPException(status_code=400, detail="batch_id_param_required(:batch_id)")
    wrapped = f"select * from ({sql}) as q limit {limit}"
    try:
        res = db.execute(sql_text(wrapped), {"batch_id": batch_id})
        cols = list(res.keys())
        raw_rows = res.fetchall()
        rows: list[list[Any]] = []
        for r in raw_rows:
            out_row: list[Any] = []
            for v in list(r):
                if isinstance(v, datetime):
                    out_row.append(((_ensure_utc(v) or v).isoformat().replace("+00:00", "Z")))
                else:
                    out_row.append(v)
            rows.append(out_row)
        return {"ok": True, "columns": cols, "rows": rows, "limit": limit}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"sql_failed:{str(exc)[:240]}") from exc


@app.post("/v1/sql/ume_query")
def sql_ume_query(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)) -> dict:
    """
    Read-only SQL query endpoint for UME current alarms/inventory.

    Safety constraints:
    - SELECT only, single statement (no ';')
    - forbid DDL/DML keywords
    - enforce max rows (server-side LIMIT wrapper)
    - only allow FROM/JOIN on ume_alarms_current and ume_inventory_ne
    """
    payload = payload or {}
    sql = str(payload.get("sql") or "").strip()
    limit = int(payload.get("limit") or 200)
    limit = max(1, min(limit, 2000))
    statement_timeout_ms = int(payload.get("statement_timeout_ms") or 0)
    statement_timeout_ms = max(0, min(statement_timeout_ms, 30000))
    if not sql:
        raise HTTPException(status_code=400, detail="sql_required")
    if ";" in sql:
        raise HTTPException(status_code=400, detail="single_statement_only")
    low = sql.lower().lstrip()
    if not low.startswith("select"):
        raise HTTPException(status_code=400, detail="select_only")
    if _SQL_FORBIDDEN_RE.search(sql):
        raise HTTPException(status_code=400, detail="forbidden_keyword")

    allowed_tables = {"ume_alarms_current", "ume_inventory_ne"}
    refs = re.findall(r"\b(?:from|join)\s+([a-zA-Z0-9_\"\.]+)", sql, flags=re.IGNORECASE)
    for ref in refs:
        normalized = str(ref).strip().strip('"')
        if "." in normalized:
            normalized = normalized.split(".")[-1]
        if normalized.lower() not in allowed_tables:
            raise HTTPException(status_code=400, detail=f"ume_table_not_allowed:{normalized}")

    wrapped = f"select * from ({sql}) as q limit {limit}"
    try:
        if statement_timeout_ms > 0:
            try:
                if str(getattr(getattr(db, "bind", None), "dialect", None).name).lower().startswith("postgres"):
                    db.execute(sql_text("SET LOCAL statement_timeout = :ms"), {"ms": int(statement_timeout_ms)})
            except Exception:
                pass
        res = db.execute(sql_text(wrapped))
        cols = list(res.keys())
        raw_rows = res.fetchall()
        rows: list[list[Any]] = []
        for r in raw_rows:
            out_row: list[Any] = []
            for v in list(r):
                if isinstance(v, datetime):
                    out_row.append(((_ensure_utc(v) or v).isoformat().replace("+00:00", "Z")))
                else:
                    out_row.append(v)
            rows.append(out_row)
        return {"ok": True, "columns": cols, "rows": rows, "limit": limit}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"sql_failed:{str(exc)[:240]}") from exc


def _configure_ume_diag_logging() -> None:
    """Emit netx.ume.* INFO to stderr so background scripts/.run/*.log and consoles show scheduler lines."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("netx.ume.schedule", "netx.ume.sync"):
        lg = logging.getLogger(name)
        if lg.handlers:
            continue
        h = logging.StreamHandler()
        h.setFormatter(fmt)
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False


@app.on_event("startup")
def on_startup() -> None:
    _configure_ume_diag_logging()
    Base.metadata.create_all(bind=engine)
    _reset_runtime_pause_flags()
    _fail_stale_running_sync_jobs_on_startup()
    # Best-effort schema evolution for new columns (no migrations framework).
    # Safe for Postgres (IF NOT EXISTS); ignored on failure.
    try:
        with engine.begin() as conn:
            # Removed from ORM: drop legacy holder table if present (was optional nested UME data).
            conn.exec_driver_sql("DROP TABLE IF EXISTS ume_inventory_equipment_holder")
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS relevancy VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS l3vpn_peer_ne VARCHAR(256) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS service VARCHAR(256) DEFAULT ''")
            conn.exec_driver_sql(
                "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS affected_client_service_number INTEGER DEFAULT 0"
            )
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS intermittence_count INTEGER DEFAULT 0")
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS me_level VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_token_cache ADD COLUMN IF NOT EXISTS lock_owner VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_token_cache ADD COLUMN IF NOT EXISTS lock_expires_at_epoch_s INTEGER DEFAULT 0")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS device_level VARCHAR(64) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS host_name VARCHAR(256) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS location VARCHAR(512) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS ipv6_address VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql(
                "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS hardware_version VARCHAR(128) DEFAULT ''"
            )
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS loopback VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql(
                "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS consistent_state VARCHAR(64) DEFAULT ''"
            )
            conn.exec_driver_sql(
                "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS interface_version VARCHAR(128) DEFAULT ''"
            )
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS mac VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS admin_status VARCHAR(64) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS address_type VARCHAR(64) DEFAULT ''")
            conn.exec_driver_sql(
                "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS connection_status VARCHAR(64) DEFAULT ''"
            )
            conn.exec_driver_sql(
                "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS maintain_status VARCHAR(64) DEFAULT ''"
            )
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS net_mask VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS create_time VARCHAR(64) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS creator VARCHAR(128) DEFAULT ''")
            # Allow long UME alarm fields; avoid StringDataRightTruncation on large payloads.
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN alarm_key TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN object_name TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN event_type TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN native_probable_cause TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN perceived_severity TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN is_cleared TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN time_created TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current ALTER COLUMN root_cause_alarm_indication TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN alarm_key TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN object_name TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN event_type TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN native_probable_cause TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN perceived_severity TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN is_cleared TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN time_created TYPE TEXT")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history ALTER COLUMN root_cause_alarm_indication TYPE TEXT")
            # Simplify alarm tables: display fields come from runtime join with inventory table.
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current DROP COLUMN IF EXISTS ne_name")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_current DROP COLUMN IF EXISTS user_label")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history DROP COLUMN IF EXISTS ne_name")
            conn.exec_driver_sql("ALTER TABLE ume_alarms_history DROP COLUMN IF EXISTS user_label")
            conn.exec_driver_sql("COMMENT ON TABLE ume_inventory_ne IS '网元对象详细信息'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.ne_id IS '网元uuid'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.ne_name IS '资源名称'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.ne_type IS '网元类型'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.user_label IS '用户标签'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.address_type IS '管理地址类型(1:IPv4,2:IPv6)'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.ip_address IS '网元IPv4地址'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.net_mask IS '管理IPv4掩码(点分十进制)'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.ipv6_address IS 'IPv6地址'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.admin_status IS '管理状态(0-离线,1-在线)'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.connection_status IS '连接状态(0-断链,1-正常)'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.consistent_state IS '数据一致性状态(1一致,2不一致,3冲突)'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.maintain_status IS '工程状态(0普通,1调测,2新建)'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.vendor IS '网元提供商'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.interface_version IS '网元接口版本号'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.hardware_version IS '硬件版本'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.mac IS '设备机架MAC地址'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.loopback IS '业务环回IP(IPv4)'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.device_level IS '网元层次'")
            conn.exec_driver_sql("COMMENT ON COLUMN ume_inventory_ne.host_name IS '主机名称'")
    except Exception:
        pass
    try:
        if bool(getattr(settings, "ume_keepalive_enabled", True)):
            interval_keepalive_s = int(getattr(settings, "ume_keepalive_interval_s", 600) or 600)
            interval_keepalive_s = max(30, min(interval_keepalive_s, 3600))
            renew_before_s = int(getattr(settings, "ume_keepalive_renew_before_s", 900) or 900)
            renew_before_s = max(30, min(renew_before_s, 86400))

            def _keepalive_loop() -> None:
                # Best-effort keepalive: if token exists, periodically handshake to extend TTL.
                while True:
                    try:
                        if _runtime_is_paused("token_keepalive"):
                            time.sleep(1)
                            continue
                        client = _ume_client()
                        st = client.token_status()
                        expires_in = int(st.get("expires_in_s") or 0)
                        # Renew when missing/invalid TTL (0) or nearing expiry — previously 0 skipped renew forever.
                        if bool(st.get("has_token")) and (expires_in <= 0 or expires_in < renew_before_s):
                            client.renew_token()
                        _set_runtime_task("token_keepalive", status="running", last_run_at=datetime.now(timezone.utc), last_error="")
                    except Exception:
                        _set_runtime_task("token_keepalive", status="error", last_run_at=datetime.now(timezone.utc), last_error="keepalive_failed")
                    time.sleep(interval_keepalive_s)

            t = threading.Thread(target=_keepalive_loop, name="ume-token-keepalive", daemon=True)
            t.start()
    except Exception:
        pass
    try:
        if bool(getattr(settings, "ume_sync_alarms_current_enabled", True)):
            alarms_interval_s = int(getattr(settings, "ume_sync_alarms_current_interval_s", 300) or 300)
            alarms_interval_s = max(30, min(alarms_interval_s, 86400))

            def _alarms_current_sync_loop() -> None:
                while True:
                    try:
                        _schedule_log.info(
                            "alarms_current_auto_sync: loop tick paused=%s",
                            _runtime_is_paused("alarms_current_auto_sync"),
                        )
                        if _runtime_is_paused("alarms_current_auto_sync"):
                            time.sleep(1)
                            continue
                        _maybe_wait_for_sync_interval(
                            task_id="alarms_current_auto_sync",
                            domain="alarms_current",
                            interval_s=alarms_interval_s,
                            label="alarms_current_auto_sync",
                        )
                        _schedule_log.info(
                            "alarms_current_auto_sync: iteration start (interval=%ss)",
                            alarms_interval_s,
                        )
                        _set_runtime_task(
                            "alarms_current_auto_sync",
                            status="running",
                            last_run_at=datetime.now(timezone.utc),
                            last_error="正在拉取 UME 当前告警…",
                        )
                        db = SessionLocal()
                        try:
                            client = _ume_client()
                            sync_alarms_current(db, client, trigger_mode="schedule")
                            _schedule_log.info("alarms_current_auto_sync: sync finished ok")
                            _set_runtime_task(
                                "alarms_current_auto_sync",
                                status="running",
                                last_run_at=datetime.now(timezone.utc),
                                last_error="",
                            )
                        finally:
                            db.close()
                    except Exception as exc:
                        _schedule_log.exception("alarms_current_auto_sync: sync failed: %s", exc)
                        _set_runtime_task(
                            "alarms_current_auto_sync",
                            status="error",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=str(exc)[:240],
                        )

            t2 = threading.Thread(target=_alarms_current_sync_loop, name="ume-alarms-current-sync", daemon=True)
            t2.start()
            _schedule_log.info("started thread %s alive=%s", t2.name, t2.is_alive())
            if not t2.is_alive():
                _schedule_log.error("ume-alarms-current-sync thread exited immediately (check uncaught errors above)")
    except Exception:
        pass
    try:
        if bool(getattr(settings, "ume_sync_inventory_auto_enabled", True)):
            hours = int(getattr(settings, "ume_sync_inventory_every_hours", 48) or 48)
            hours = max(1, min(hours, 168))
            inventory_interval_s = int(hours * 3600)

            def _inventory_auto_sync_loop() -> None:
                while True:
                    try:
                        _schedule_log.info(
                            "inventory_auto_sync: loop tick paused=%s",
                            _runtime_is_paused("inventory_auto_sync"),
                        )
                        if _runtime_is_paused("inventory_auto_sync"):
                            time.sleep(1)
                            continue
                        _maybe_wait_for_sync_interval(
                            task_id="inventory_auto_sync",
                            domain="inventory",
                            interval_s=inventory_interval_s,
                            label="inventory_auto_sync",
                        )
                        _schedule_log.info(
                            "inventory_auto_sync: iteration start (interval=%ss)",
                            inventory_interval_s,
                        )
                        _set_runtime_task(
                            "inventory_auto_sync",
                            status="running",
                            last_run_at=datetime.now(timezone.utc),
                            last_error="正在拉取 UME 网元清单…",
                        )
                        db = SessionLocal()
                        try:
                            client = _ume_client()
                            sync_inventory_full(db, client, trigger_mode="schedule")
                            _schedule_log.info("inventory_auto_sync: sync finished ok")
                            _set_runtime_task(
                                "inventory_auto_sync",
                                status="running",
                                last_run_at=datetime.now(timezone.utc),
                                last_error="",
                            )
                        finally:
                            db.close()
                    except Exception as exc:
                        _schedule_log.exception("inventory_auto_sync: sync failed: %s", exc)
                        _set_runtime_task(
                            "inventory_auto_sync",
                            status="error",
                            last_run_at=datetime.now(timezone.utc),
                            last_error=str(exc)[:240],
                        )

            t3 = threading.Thread(target=_inventory_auto_sync_loop, name="ume-inventory-auto-sync", daemon=True)
            t3.start()
            _schedule_log.info("started thread %s alive=%s", t3.name, t3.is_alive())
            if not t3.is_alive():
                _schedule_log.error("ume-inventory-auto-sync thread exited immediately (check uncaught errors above)")
    except Exception:
        pass


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/ume/token/status")
def ume_token_status() -> dict[str, Any]:
    client = _ume_client()
    st = client.token_status()
    return {"ok": True, **st}


@app.post("/v1/ume/token/refresh")
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


@app.post("/v1/ume/token/disconnect")
def ume_token_disconnect() -> dict[str, Any]:
    client = _ume_client()
    ok = bool(client.logout_token())
    st = client.token_status()
    return {"ok": ok, **st}


@app.post("/v1/ume/sync")
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
            job, batch = sync_alarms_current(db, client, trigger_mode=trigger_mode)
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


@app.get("/v1/ume/sync/status")
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
    }


@app.post("/v1/ume/runtime/tasks/{task}/pause")
def ume_runtime_task_pause(task: str) -> dict[str, Any]:
    tid = str(task or "").strip()
    if tid not in UME_KNOWN_RUNTIME_TASKS:
        raise HTTPException(status_code=404, detail="unknown_runtime_task")
    _runtime_pause_task(tid)
    if tid in ("alarms_current_auto_sync", "inventory_auto_sync"):
        _clear_force_resume_hints(tid)
    _set_runtime_task(tid, status="paused", last_error="")
    return {"ok": True, "task": tid, "runtime_tasks": _list_runtime_tasks()}


@app.post("/v1/ume/runtime/tasks/{task}/resume")
def ume_runtime_task_resume(task: str) -> dict[str, Any]:
    tid = str(task or "").strip()
    if tid not in UME_KNOWN_RUNTIME_TASKS:
        raise HTTPException(status_code=404, detail="unknown_runtime_task")
    _runtime_resume_task(tid)
    if tid in ("alarms_current_auto_sync", "inventory_auto_sync"):
        _request_force_sync_after_resume(tid)
    _set_runtime_task(tid, status="running", last_error="已恢复：将跳过本轮周期等待并尽快同步")
    return {"ok": True, "task": tid, "runtime_tasks": _list_runtime_tasks()}


@app.get("/v1/ume/inventory/ne")
def ume_list_ne(
    keyword: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeInventoryNE)
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeInventoryNE.ne_id.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
            | UmeInventoryNE.host_name.contains(kw)
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


@app.get("/v1/ume/inventory/ne/{ne_id}")
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


@app.get("/v1/ume/alarms")
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
        stmt = stmt.filter(UmeInventoryNE.host_name.contains(hn))
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmCurrent.alarm_key.contains(kw)
            | UmeAlarmCurrent.object_name.contains(kw)
            | UmeAlarmCurrent.native_probable_cause.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
            | UmeInventoryNE.host_name.contains(kw)
        )
    total = int(stmt.count())
    rows = stmt.order_by(UmeAlarmCurrent.last_seen_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "alarm_key": str(alarm.alarm_key or ""),
            "ne_id": str(alarm.ne_id or ""),
            "ne_name": str((ne.ne_name if ne else "") or ""),
            "user_label": str((ne.user_label if ne else "") or ""),
            "host_name": str((ne.host_name if ne else "") or ""),
            "ne_type": str((ne.ne_type if ne else "") or ""),
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


@app.get("/v1/ume/alarms/fields")
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
        if ne is None:
            return ""
        return str(getattr(ne, attr, "") or "")
    return ""


@app.get("/v1/ume/alarms/raw")
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


@app.get("/v1/ume/alarms/aggregate/raw")
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


@app.get("/v1/ume/alarms/aggregate")
def ume_alarms_aggregate(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_ne = _aggregate_rows(rows, lambda x: (x[1].user_label if x[1] else "") or (x[1].ne_name if x[1] else "") or x[0].ne_id)
    return {"total": len(rows), "by_severity": by_severity, "by_ne": by_ne}


@app.get("/v1/ume/diagnostics")
def ume_diagnostics(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_alarm_code = _aggregate_rows(rows, lambda x: x[0].event_type)[:10]
    by_ne = _aggregate_rows(rows, lambda x: (x[1].user_label if x[1] else "") or (x[1].ne_name if x[1] else "") or x[0].ne_id)[:10]

    def _protocol_bucket(text: str) -> str:
        t = (text or "").upper()
        if any(x in t for x in ("BGP", "OSPF", "ISIS", "LDP", "MPLS", "L3VPN", "VPN")):
            return "IP/MPLS"
        if any(x in t for x in ("ETH", "GE", "10GE", "25GE", "40GE", "100GE", "XGE")):
            return "ETH"
        if any(x in t for x in ("OTN", "ODU", "OCH", "OMS", "OSC", "DWDM", "WDM", "ROADM")):
            return "OTN/光"
        if any(x in t for x in ("CLOCK", "SYNC", "PTP", "1588", "BITS", "TOD")):
            return "时钟"
        if any(x in t for x in ("PWR", "POWER", "PSU", "BAT", "BATT")):
            return "电源"
        return "其他"

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
        bucket = _protocol_bucket(blob)
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


@app.get("/v1/ume/alarms/history")
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


@app.get("/v1/ume/alarms/history/aggregate")
def ume_alarms_history_aggregate(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(UmeAlarmHistory, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmHistory.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_ne = _aggregate_rows(rows, lambda x: (x[1].user_label if x[1] else "") or (x[1].ne_name if x[1] else "") or x[0].ne_id)
    by_date = _aggregate_rows(rows, lambda x: str(x[0].time_created or "")[:10])
    return {"total": len(rows), "by_severity": by_severity, "by_ne": by_ne, "by_date": by_date}


@app.get("/v1/integrations/status")
def integrations_status(db: Session = Depends(get_db)) -> dict:
    # netx api is up if this handler executes; still verify DB + oclaw bridge separately.
    netx_api = {"status": "up"}

    db_status: dict = {"status": "unknown"}
    try:
        t0 = time.monotonic()
        db.execute(sql_text("select 1"))
        db_status = {"status": "up", "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        db_status = {"status": "down", "error": str(exc)[:240]}

    oclaw_status: dict = {"status": "unknown"}
    try:
        t0 = time.monotonic()
        data = health_with_oclaw()
        oclaw_status = {
            "status": "up",
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "http_status": int(data.get("status_code") or 200),
            "detail": data.get("data") or {},
        }
    except Exception as exc:
        msg = str(exc)
        http_status = None
        kind = "unknown"
        if " 401 " in msg or "401" in msg:
            kind = "auth"
            http_status = 401
        elif " 404 " in msg or "404" in msg:
            kind = "not_found"
            http_status = 404
        elif "timeout" in msg.lower():
            kind = "timeout"
        elif "connect" in msg.lower():
            kind = "connect"
        else:
            kind = "other"
        oclaw_status = {"status": "down", "error_kind": kind, "http_status": http_status, "error": msg[:240]}

    return {"netx_api": netx_api, "db": db_status, "oclaw_bridge": oclaw_status}


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "mode": "api_only",
        "message": "netx UI is served by Vite frontend only",
        "frontend_url": settings.frontend_url,
        "api_health": "/health",
        "api_status": "/v1/integrations/status",
    }


@app.post("/v1/alarms/import", response_model=BatchSummary)
async def import_alarms(file: UploadFile = File(...), db: Session = Depends(get_db)) -> BatchSummary:
    filename = str(file.filename or "alarm.xlsx")
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="only_excel_supported_in_phase1")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")
    batch = import_alarm_excel(db, filename=filename, content=content, parser=parser_cfg)

    try:
        job = ImportJob(
            kind="alarms",
            file_name=filename,
            batch_id=str(batch.batch_id),
            ok=1,
            summary=f"success={int(batch.success_rows)} failed={int(batch.failed_rows)}",
        )
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
    return BatchSummary(
        batch_id=str(batch.batch_id),
        total_rows=int(batch.total_rows or 0),
        success_rows=int(batch.success_rows or 0),
        failed_rows=int(batch.failed_rows or 0),
        status=str(batch.status or ""),
        created_at=_ensure_utc(batch.created_at) or datetime.now(timezone.utc),
    )


@app.post("/v1/logs/import")
async def import_logs(file: UploadFile = File(...)) -> dict:
    # Placeholder for Phase 2: logs parsing + storage + query.
    filename = str(file.filename or "logs.zip")
    if not filename:
        raise HTTPException(status_code=400, detail="filename_required")
    raise HTTPException(status_code=501, detail="logs_import_not_implemented")


@app.get("/v1/jobs", response_model=ImportJobListResponse)
def list_jobs(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> ImportJobListResponse:
    rows = db.query(ImportJob).order_by(ImportJob.created_at.desc()).limit(limit).all()
    items = [
        ImportJobItem(
            id=int(x.id),
            kind=str(x.kind),
            file_name=str(x.file_name or ""),
            batch_id=str(x.batch_id) if x.batch_id else None,
            ok=bool(int(x.ok or 0)),
            summary=str(x.summary or ""),
            created_at=_ensure_utc(x.created_at) or datetime.now(timezone.utc),
        )
        for x in rows
    ]
    return ImportJobListResponse(items=items)


@app.get("/v1/batches")
def list_batches(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
    rows = db.query(AlarmBatch).order_by(AlarmBatch.created_at.desc()).limit(limit).all()
    return {
        "items": [
            {
                "batch_id": x.batch_id,
                "source_file": x.source_file,
                "status": x.status,
                "total_rows": x.total_rows,
                "success_rows": x.success_rows,
                "failed_rows": x.failed_rows,
                "created_at": (_ensure_utc(x.created_at) or datetime.now(timezone.utc)).isoformat(),
            }
            for x in rows
        ]
    }


@app.get("/v1/batches/{batch_id}/errors.csv")
def download_batch_errors(batch_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(ImportErrorRow)
        .filter(ImportErrorRow.batch_id == batch_id)
        .order_by(ImportErrorRow.id.asc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="batch_or_errors_not_found")
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["row_no", "reason", "raw_json"])
    for r in rows:
        writer.writerow([r.row_no, r.reason, r.raw_json])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="batch_{batch_id}_errors.csv"'},
    )


@app.delete("/v1/batches/{batch_id}")
def delete_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = db.get(AlarmBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    try:
        alarms_deleted = int(
            db.query(AlarmNorm).filter(AlarmNorm.batch_id == batch_id).delete(synchronize_session=False)
        )
        errors_deleted = int(
            db.query(ImportErrorRow).filter(ImportErrorRow.batch_id == batch_id).delete(synchronize_session=False)
        )
        jobs_deleted = int(
            db.query(ImportJob).filter(ImportJob.batch_id == batch_id).delete(synchronize_session=False)
        )
        db.delete(batch)
        db.commit()
        return {
            "ok": True,
            "batch_id": batch_id,
            "deleted": {
                "batch": 1,
                "alarms": alarms_deleted,
                "errors": errors_deleted,
                "jobs": jobs_deleted,
            },
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"delete_batch_failed: {exc}") from exc


@app.delete("/v1/batches")
def delete_all_batches(db: Session = Depends(get_db)) -> dict:
    try:
        alarms_deleted = int(db.query(AlarmNorm).delete(synchronize_session=False))
        errors_deleted = int(db.query(ImportErrorRow).delete(synchronize_session=False))
        jobs_deleted = int(db.query(ImportJob).delete(synchronize_session=False))
        batches_deleted = int(db.query(AlarmBatch).delete(synchronize_session=False))
        db.commit()
        return {
            "ok": True,
            "deleted": {
                "batches": batches_deleted,
                "alarms": alarms_deleted,
                "errors": errors_deleted,
                "jobs": jobs_deleted,
            },
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"delete_all_batches_failed: {exc}") from exc


@app.get("/v1/diagnostics")
def diagnostics(
    batch_id: str = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    sev_rows = aggregate_alarms(db, group_by="severity_norm", batch_id=batch_id)
    code_rows = aggregate_alarms(db, group_by="alarm_code", batch_id=batch_id)[:10]
    ne_rows = aggregate_alarms(db, group_by="ne_name", batch_id=batch_id)[:10]
    total = sum(count for _, count in sev_rows)

    def _protocol_bucket(text: str) -> str:
        t = (text or "").upper()
        # IP/MPLS control plane
        if any(x in t for x in ("BGP", "OSPF", "ISIS", "LDP", "MPLS", "L3VPN", "VPN")):
            return "IP/MPLS"
        # Ethernet / packet
        if any(x in t for x in ("ETH", "GE", "10GE", "25GE", "40GE", "100GE", "XGE")):
            return "ETH"
        # OTN / optical
        if any(x in t for x in ("OTN", "ODU", "OCH", "OMS", "OSC", "DWDM", "WDM", "ROADM")):
            return "OTN/光"
        # Timing / clock
        if any(x in t for x in ("CLOCK", "SYNC", "PTP", "1588", "BITS", "TOD")):
            return "时钟"
        # Power
        if any(x in t for x in ("PWR", "POWER", "PSU", "BAT", "BATT")):
            return "电源"
        return "其他"

    proto_counts: dict[str, int] = {}
    for name, desc, code, raw in (
        db.query(AlarmNorm.alarm_name, AlarmNorm.description, AlarmNorm.alarm_code, AlarmNorm.raw_json)
        .filter(AlarmNorm.batch_id == batch_id)
        .all()
    ):
        blob = " | ".join([str(code or ""), str(name or ""), str(desc or ""), str(raw or "")])
        k = _protocol_bucket(blob)
        proto_counts[k] = int(proto_counts.get(k, 0)) + 1
    protocol_summary = sorted(proto_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "batch_id": batch_id,
        "total_alarms": int(total),
        "severity_summary": [{"key": k, "count": v} for k, v in sev_rows],
        "top_alarm_codes": [{"key": k, "count": v} for k, v in code_rows],
        "top_ne": [{"key": k, "count": v} for k, v in ne_rows],
        "protocol_summary": [{"key": k, "count": v} for k, v in protocol_summary],
    }


@app.post("/v1/ap/analyze")
def ap_analyze(payload: dict, db: Session = Depends(get_db)) -> dict:
    batch_id = str(payload.get("batch_id") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not batch_id or not question:
        raise HTTPException(status_code=400, detail="batch_id_and_question_required")
    diag = diagnostics(batch_id=batch_id, db=db)
    analysis_request_id = str(payload.get("analysis_request_id") or "").strip()
    filters_obj = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    req = {
        "analysis_request_id": analysis_request_id,
        "question": question,
        "dataset_ref": {
            "batch_id": batch_id,
            "filters": filters_obj or {},
        },
        "context": {
            "severity_summary": diag["severity_summary"],
            "top_alarm_codes": diag["top_alarm_codes"],
            "top_ne": diag["top_ne"],
            "protocol_summary": diag.get("protocol_summary", []),
            "findings": diag.get("findings", []),
        },
        "constraints": payload.get("constraints") or {"language": "zh-CN", "max_points": 6},
        "interaction_mode": "expert",
        "specialist": "ops",
    }
    ok = False
    err = ""
    oclaw_resp: dict[str, Any] | None = None
    try:
        oclaw_resp = analyze_with_oclaw(req)
        ok = bool(oclaw_resp.get("ok")) if isinstance(oclaw_resp, dict) else False
    except Exception as exc:
        err = str(exc)
    # Persist Q&A history (best-effort; never block response).
    try:
        answer = ""
        if isinstance(oclaw_resp, dict):
            answer = str(oclaw_resp.get("answer") or "").strip()
        row = AiAnalyzeHistory(
            analysis_request_id=analysis_request_id,
            batch_id=batch_id,
            question=question,
            filters_json=json.dumps(filters_obj or {}, ensure_ascii=False),
            ok=1 if ok else 0,
            answer=answer,
            error=err,
            evidence_json=json.dumps(diag or {}, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
    if not ok:
        return {
            "ok": False,
            "error": err or "oclaw_bridge_unavailable",
            "fallback_diagnostics": diag,
            "batch_id": batch_id,
            "question": question,
        }
    return {"ok": True, "batch_id": batch_id, "question": question, "diagnostics": diag, "oclaw": oclaw_resp}


@app.get("/v1/ap/history", response_model=AiAnalyzeHistoryResponse)
def ap_history(
    batch_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AiAnalyzeHistoryResponse:
    q = db.query(AiAnalyzeHistory)
    if batch_id and str(batch_id).strip():
        q = q.filter(AiAnalyzeHistory.batch_id == str(batch_id).strip())
    total = int(q.count())
    rows = (
        q.order_by(AiAnalyzeHistory.id.desc())
        .offset((int(page) - 1) * int(page_size))
        .limit(int(page_size))
        .all()
    )
    items: list[AiAnalyzeHistoryItem] = []
    for r in rows:
        try:
            filters = json.loads(str(r.filters_json or "{}"))
        except Exception:
            filters = {}
        items.append(
            AiAnalyzeHistoryItem(
                id=int(r.id),
                analysis_request_id=str(r.analysis_request_id or ""),
                batch_id=str(r.batch_id or ""),
                question=str(r.question or ""),
                filters=filters if isinstance(filters, dict) else {},
                ok=bool(int(r.ok or 0) == 1),
                answer=str(r.answer or ""),
                error=str(r.error or ""),
                created_at=_ensure_utc(r.created_at) or datetime.now(timezone.utc),
            )
        )
    return AiAnalyzeHistoryResponse(total=total, page=page, page_size=page_size, items=items)


@app.get("/v1/alarms", response_model=AlarmQueryResponse)
def list_alarms(
    batch_id: str | None = Query(default=None),
    alarm_code: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    ne_name: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> AlarmQueryResponse:
    total, rows = query_alarms(
        db,
        batch_id=batch_id,
        alarm_code=alarm_code,
        severity=severity,
        ne_name=ne_name,
        page=page,
        page_size=page_size,
    )
    items = [
        AlarmItem(
            id=x.id,
            batch_id=x.batch_id,
            row_no=x.row_no,
            alarm_time=_ensure_utc(x.alarm_time) or datetime.now(timezone.utc),
            severity_norm=x.severity_norm,
            severity_raw=x.severity_raw,
            ne_name=x.ne_name,
            alarm_code=x.alarm_code,
            description=x.description,
            ack_state=x.ack_state,
        )
        for x in rows
    ]
    return AlarmQueryResponse(total=total, page=page, page_size=page_size, items=items)


@app.get("/v1/alarms/fields")
def alarms_fields() -> dict:
    """List all columns in alarms_norm for power querying."""
    cols = []
    try:
        cols = [str(c.name) for c in AlarmNorm.__table__.columns]  # type: ignore[attr-defined]
    except Exception:
        cols = []
    return {"items": cols}


def _serialize_alarm_row(row: AlarmNorm) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in AlarmNorm.__table__.columns:  # type: ignore[attr-defined]
        name = str(c.name)
        v = getattr(row, name, None)
        if hasattr(v, "isoformat"):
            try:
                if isinstance(v, datetime):
                    out[name] = (_ensure_utc(v) or v).isoformat()
                else:
                    out[name] = v.isoformat()  # datetime/date
                continue
            except Exception:
                pass
        out[name] = v
    return out


@app.get("/v1/alarms/raw")
def alarms_raw(
    batch_id: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    alarm_code: str | None = Query(default=None),
    ne_name: str | None = Query(default=None),
    q: str | None = Query(default=None, description="free text contains on alarm_code/ne_name/description/service"),
    order_by: str = Query(default="alarm_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """
    Power query: return **all columns** for alarms_norm rows.

    Safety constraints:
    - batch_id is required (avoid unbounded scans)
    - order_by is whitelisted
    - page_size capped
    """
    bid = str(batch_id or "").strip()
    if not bid:
        raise HTTPException(status_code=400, detail="batch_id_required")
    stmt = db.query(AlarmNorm).filter(AlarmNorm.batch_id == bid)
    if severity and str(severity).strip():
        stmt = stmt.filter(AlarmNorm.severity_norm == str(severity).strip())
    if alarm_code and str(alarm_code).strip():
        stmt = stmt.filter(AlarmNorm.alarm_code.contains(str(alarm_code).strip()))
    if ne_name and str(ne_name).strip():
        stmt = stmt.filter(AlarmNorm.ne_name.contains(str(ne_name).strip()))
    if q and str(q).strip():
        qw = str(q).strip()
        stmt = stmt.filter(
            (AlarmNorm.alarm_code.contains(qw))
            | (AlarmNorm.ne_name.contains(qw))
            | (AlarmNorm.description.contains(qw))
            | (AlarmNorm.service.contains(qw))
        )
    allowed_order_by = {
        "id": AlarmNorm.id,
        "alarm_time": AlarmNorm.alarm_time,
        "severity_norm": AlarmNorm.severity_norm,
        "ne_name": AlarmNorm.ne_name,
        "alarm_code": AlarmNorm.alarm_code,
    }
    col = allowed_order_by.get(str(order_by or "").strip(), AlarmNorm.alarm_time)
    if str(order or "").strip().lower() == "asc":
        stmt = stmt.order_by(col.asc())
    else:
        stmt = stmt.order_by(col.desc())
    total = int(stmt.count())
    rows = (
        stmt.offset((int(page) - 1) * int(page_size))
        .limit(int(page_size))
        .all()
    )
    return {
        "total": total,
        "page": int(page),
        "page_size": int(page_size),
        "items": [_serialize_alarm_row(r) for r in rows],
    }


@app.get("/v1/alarms/aggregate", response_model=AlarmAggregateResponse)
def alarms_aggregate(
    group_by: str = Query(default="severity_norm"),
    batch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AlarmAggregateResponse:
    try:
        rows = aggregate_alarms(db, group_by=group_by, batch_id=batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AlarmAggregateResponse(
        group_by=group_by,
        buckets=[AlarmAggregateBucket(key=k, count=v) for k, v in rows],
    )


@app.get("/v1/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = db.get(AlarmBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    errors = (
        db.query(ImportErrorRow)
        .filter(ImportErrorRow.batch_id == batch_id)
        .order_by(ImportErrorRow.id.asc())
        .limit(20)
        .all()
    )
    return {
        "batch": BatchSummary.model_validate(batch, from_attributes=True).model_dump(),
        "errors_preview": [
            {"row_no": e.row_no, "reason": e.reason, "raw_json": e.raw_json}
            for e in errors
        ],
    }


if __name__ == "__main__":
    uvicorn.run("netx_api.main:app", host=settings.host, port=settings.port, reload=False)
