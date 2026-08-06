"""UME shared client, runtime task state, and sync helpers (used by router + startup)."""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import UmeAlarmCurrent, UmeInventoryNE, UmeSyncJob
from .timeutil import utcnow_naive
from .runtime_task_messages import (
    RT_ALARMS_SYNC_IN_PROGRESS_SKIP,
    RT_KEEPALIVE_FAILED,
    RT_OCLAW_FWD_DISABLED,
    RT_PULLING_ALARMS_CURRENT,
    RT_PULLING_INVENTORY,
    RT_RESUMED,
    RT_RESUMED_OCLAW_WSS_RECONNECT,
    RT_RESUMED_SYNC_SOON,
    RT_RESUMED_WSS_RECONNECT,
    RT_STARTUP_ALARM_SYNC_BEFORE_WS,
    RT_STARTUP_GATE_WAITING,
    RT_UME_WS_DISABLED_NO_BASE_URL,
    RT_WSS_ACTIVE_SKIP_REST,
)
from .oclaw_alarm_forwarder import is_forwarder_enabled
from .ume_alarm_ws import (
    begin_startup_alarm_sync_gate,
    complete_startup_alarm_sync_gate,
    is_startup_alarm_sync_pending,
    is_wss_active_for_current_alarms,
)
from .ume_client import UMEClient
from .ume_sync_service import sync_alarms_current, sync_inventory_full, sync_topology_full
from .ume_token_store import (
    clear_shared_token,
    load_shared_token,
    release_refresh_lock,
    save_shared_token,
    try_acquire_refresh_lock,
    wait_for_token_update,
)

_schedule_log = logging.getLogger("netx.ume.schedule")
_BOOT_MONO = time.monotonic()

_UME_CLIENT_SINGLETON = UMEClient(
    token_loader=lambda: load_shared_token(),
    token_saver=lambda token, exp: save_shared_token(token, exp),
    token_clearer=lambda: clear_shared_token(),
    lock_acquirer=lambda: try_acquire_refresh_lock(),
    lock_releaser=lambda: release_refresh_lock(),
    token_waiter=lambda min_exp: wait_for_token_update(min_expires_at_epoch_s=float(min_exp)),
)

_UME_RUNTIME_TASKS: dict[str, dict[str, Any]] = {
    "token_keepalive": {"task": "token_keepalive", "status": "init", "last_run_at": None, "last_error": ""},
    "alarms_current_auto_sync": {"task": "alarms_current_auto_sync", "status": "init", "last_run_at": None, "last_error": ""},
    "alarms_current_ws_consumer": {"task": "alarms_current_ws_consumer", "status": "init", "last_run_at": None, "last_error": ""},
    "oclaw_alarm_forwarder": {"task": "oclaw_alarm_forwarder", "status": "init", "last_run_at": None, "last_error": ""},
    "inventory_auto_sync": {"task": "inventory_auto_sync", "status": "init", "last_run_at": None, "last_error": ""},
    "topology_auto_sync": {"task": "topology_auto_sync", "status": "init", "last_run_at": None, "last_error": ""},
}
_UME_WS_STOP_EVENT: threading.Event | None = None
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
            return None, "disabled"
        interval_s = int(getattr(settings, "ume_keepalive_interval_s", 600) or 600)
        eff = max(30, min(interval_s, 3600))
        return eff, _format_runtime_interval_label(eff)
    if task_id == "alarms_current_auto_sync":
        if not bool(getattr(settings, "ume_sync_alarms_current_enabled", True)):
            return None, "disabled"
        interval_s = int(getattr(settings, "ume_sync_alarms_current_interval_s", 18000) or 18000)
        eff = max(30, min(interval_s, 86400))
        return eff, _format_runtime_interval_label(eff)
    if task_id == "alarms_current_ws_consumer":
        if not bool(getattr(settings, "ume_alarm_ws_enabled", True)):
            return None, "disabled"
        return None, "realtime"
    if task_id == "oclaw_alarm_forwarder":
        if not is_forwarder_enabled():
            return None, "disabled"
        return None, "realtime"
    if task_id == "inventory_auto_sync":
        if not bool(getattr(settings, "ume_sync_inventory_auto_enabled", True)):
            return None, "disabled"
        hours = int(getattr(settings, "ume_sync_inventory_every_hours", 48) or 48)
        hours = max(1, min(hours, 168))
        eff = int(hours * 3600)
        return eff, _format_runtime_interval_label(eff)
    if task_id == "topology_auto_sync":
        if not bool(getattr(settings, "ume_sync_topology_auto_enabled", True)):
            return None, "disabled"
        hours = int(getattr(settings, "ume_sync_topology_every_hours", 24) or 24)
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
        now_naive = utcnow_naive()
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


def _needs_startup_alarm_sync_before_ws() -> bool:
    ume_url = str(getattr(settings, "ume_base_url", "") or "").strip()
    return bool(
        getattr(settings, "ume_startup_sync_alarms_before_ws", True)
        and getattr(settings, "ume_alarm_ws_enabled", True)
        and getattr(settings, "ume_sync_alarms_current_enabled", True)
        and ume_url
    )


def _startup_alarm_pull_delay_s() -> int:
    return max(0, min(3600, int(getattr(settings, "ume_startup_alarm_sync_delay_s", 60) or 60)))


def _wait_until_startup_alarm_pull_allowed(label: str) -> None:
    delay_s = _startup_alarm_pull_delay_s()
    if delay_s <= 0:
        return
    remaining = float(delay_s) - (time.monotonic() - _BOOT_MONO)
    if remaining <= 0:
        return
    _schedule_log.info("%s: defer alarm pull %.0fs after process start", label, remaining)
    time.sleep(remaining)


def _run_startup_alarm_sync_before_ws() -> None:
    """REST-sync current alarms once on boot; WSS gate must already be closed in on_startup."""
    if not _needs_startup_alarm_sync_before_ws():
        complete_startup_alarm_sync_gate()
        return

    _wait_until_startup_alarm_pull_allowed("startup_alarm_sync")
    try:
        _schedule_log.info("startup: REST current-alarm snapshot (WSS blocked until finished)")
        _set_runtime_task(
            "alarms_current_auto_sync",
            status="running",
            last_run_at=datetime.now(timezone.utc),
            last_error=RT_STARTUP_ALARM_SYNC_BEFORE_WS,
        )
        db = SessionLocal()
        try:
            client = _ume_client()
            sync_alarms_current(db, client, trigger_mode="schedule", wss_active=False)
            _schedule_log.info("startup: current alarms sync completed, WSS may connect")
            _set_runtime_task(
                "alarms_current_auto_sync",
                status="running",
                last_run_at=datetime.now(timezone.utc),
                last_error="",
            )
        finally:
            db.close()
    except RuntimeError as exc:
        if str(exc) != "alarms_current_sync_busy":
            raise
        _schedule_log.warning("startup: skip REST before WSS — sync already in progress")
    except Exception as exc:
        _schedule_log.exception("startup: current alarms sync before WSS failed: %s", exc)
        _set_runtime_task(
            "alarms_current_auto_sync",
            status="error",
            last_run_at=datetime.now(timezone.utc),
            last_error=str(exc)[:240],
        )
    finally:
        complete_startup_alarm_sync_gate()


def _consume_force_sync_skip(task_id: str) -> bool:
    """Return True once if resume/kick asked to skip the next debounce wait."""
    with _UME_DEBOUNCE_MUTEX:
        if task_id in _UME_SYNC_SKIP_DEBOUNCE:
            _UME_SYNC_SKIP_DEBOUNCE.discard(task_id)
            return True
    return False


def _has_force_sync_skip(task_id: str) -> bool:
    with _UME_DEBOUNCE_MUTEX:
        return task_id in _UME_SYNC_SKIP_DEBOUNCE


def _sleep_or_until_paused(task_id: str, total_s: float) -> bool:
    """Sleep up to total_s; honor pause; wake early on resume.

    Returns True when the wait was interrupted for a forced sync (resume/kick).
    """
    if _has_force_sync_skip(task_id):
        return True
    deadline = time.time() + max(0.0, float(total_s))
    ev = _debounce_wake_event(task_id)
    # Do not clear a wake that resume already set between skip-check and sleep.
    while time.time() < deadline:
        if _has_force_sync_skip(task_id):
            return True
        if _runtime_is_paused(task_id):
            # Pause must still be interruptible so resume is not stuck up to 1s+interval.
            if ev.wait(timeout=1.0):
                ev.clear()
                if _has_force_sync_skip(task_id) or not _runtime_is_paused(task_id):
                    return True
            continue
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        timeout = min(2.0, remaining)
        if ev.wait(timeout=timeout):
            ev.clear()
            _schedule_log.info("%s: debounce wait interrupted (resume)", task_id)
            return True
    if ev.is_set():
        ev.clear()
        return _has_force_sync_skip(task_id)
    return False


def _last_finished_job_ended_at(db: Session, domain: str) -> datetime | None:
    """Anchor for the sync interval clock: last *real* job completion.

    Uses the latest ``ended_at`` among done/failed rows, skipping startup/reaper
    cleanup (``stale_running_*``). So: resume → sync now → after that job
    finishes, the next wait is a full interval from *that* ``ended_at``.
    """
    rows = (
        db.query(UmeSyncJob)
        .filter(
            UmeSyncJob.domain == domain,
            UmeSyncJob.ended_at.isnot(None),
            UmeSyncJob.status.in_(("done", "failed")),
        )
        .order_by(UmeSyncJob.ended_at.desc())
        .limit(50)
        .all()
    )
    for row in rows:
        err = str(getattr(row, "error_message", "") or "")
        if "stale_running" in err:
            continue
        if row.ended_at is None:
            continue
        return _ensure_utc(row.ended_at)
    return None


def _seconds_since_last_finished_job(db: Session, domain: str) -> float | None:
    """Seconds since latest real finished job. None if none."""
    end = _last_finished_job_ended_at(db, domain)
    if end is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - end).total_seconds())


def _refresh_runtime_task_idle(task_id: str, domain: str, *, last_error: str | None = None) -> None:
    """Mark scheduled sync task idle; last_run_at = last real finished job time."""
    with _UME_RUNTIME_LOCK:
        prev_error = str((_UME_RUNTIME_TASKS.get(task_id) or {}).get("last_error") or "")
    db = SessionLocal()
    try:
        ended = _last_finished_job_ended_at(db, domain)
    finally:
        db.close()
    _set_runtime_task(
        task_id,
        status="idle",
        last_run_at=ended,
        last_error=prev_error if last_error is None else last_error,
    )


def _maybe_wait_for_sync_interval(
    *,
    task_id: str,
    domain: str,
    interval_s: int,
    label: str,
) -> None:
    """Wait until ``interval_s`` since last real finished job, unless resume/kick.

    Flow for topology (and inventory):
    - Resume/kick → skip wait → sync immediately.
    - After that sync ends (job ``ended_at`` written) → next loop waits a full
      interval from that completion.
    - Process restart does not invent a new anchor from stale cleanup rows.
    """
    if _consume_force_sync_skip(task_id):
        _schedule_log.info("%s: debounce skipped (resume/kick) — sync now", label)
        return
    db = SessionLocal()
    try:
        elapsed = _seconds_since_last_finished_job(db, domain)
    finally:
        db.close()
    _refresh_runtime_task_idle(task_id, domain)
    if _consume_force_sync_skip(task_id):
        _schedule_log.info("%s: debounce skipped after idle refresh (resume/kick) — sync now", label)
        return
    if elapsed is None:
        # No real sync yet (only stale cleanup or empty history): wait one full
        # interval so API restart does not pull topology immediately.
        if domain == "topology":
            _schedule_log.info(
                "%s: no prior real sync for %s, wait full %ss",
                label,
                domain,
                interval_s,
            )
            if _sleep_or_until_paused(task_id, float(interval_s)):
                _consume_force_sync_skip(task_id)
                _schedule_log.info("%s: wait interrupted — sync now", label)
            return
        _schedule_log.info("%s: no prior finished job for %s, sync now", label, domain)
        return
    if elapsed >= float(interval_s):
        _schedule_log.info(
            "%s: last real sync %.0fs ago (>= %ss), sync now",
            label,
            elapsed,
            interval_s,
        )
        return
    wait_s = float(interval_s) - elapsed
    _schedule_log.info(
        "%s: last real sync %.0fs ago, wait %.0fs (next run = last ended_at + interval)",
        label,
        elapsed,
        wait_s,
    )
    if _sleep_or_until_paused(task_id, wait_s):
        _consume_force_sync_skip(task_id)
        _schedule_log.info("%s: wait interrupted — sync now", label)


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


def _ume_alarm_host_name(
    alarm: UmeAlarmCurrent | UmeAlarmHistory,
    ne: UmeInventoryNE | None = None,
) -> str:
    hn = str(getattr(alarm, "host_name", "") or "").strip()
    if hn:
        return hn
    if ne is not None:
        return str(getattr(ne, "host_name", "") or "").strip()
    return ""


def _ume_alarm_ne_group_key(
    alarm: UmeAlarmCurrent | UmeAlarmHistory,
    ne: UmeInventoryNE | None,
) -> str:
    return (
        _ume_alarm_host_name(alarm, ne)
        or (str(ne.user_label if ne else "") or "").strip()
        or (str(ne.ne_name if ne else "") or "").strip()
        or str(alarm.ne_id or "").strip()
        or "unknown"
    )


_PROTOCOL_BUCKET_ZH: dict[str, str] = {
    "IP/MPLS": "IP/MPLS",
    "ETH": "ETH",
    "OTN/Optical": "OTN/光",
    "Clock": "时钟",
    "Power": "电源",
    "Other": "其他",
}


def _classify_protocol_bucket(text: str) -> str:
    """Canonical English protocol/technology bucket id."""
    t = (text or "").upper()
    if any(x in t for x in ("BGP", "OSPF", "ISIS", "LDP", "MPLS", "L3VPN", "VPN")):
        return "IP/MPLS"
    if any(x in t for x in ("ETH", "GE", "10GE", "25GE", "40GE", "100GE", "XGE")):
        return "ETH"
    if any(x in t for x in ("OTN", "ODU", "OCH", "OMS", "OSC", "DWDM", "WDM", "ROADM")):
        return "OTN/Optical"
    if any(x in t for x in ("CLOCK", "SYNC", "PTP", "1588", "BITS", "TOD")):
        return "Clock"
    if any(x in t for x in ("PWR", "POWER", "PSU", "BAT", "BATT")):
        return "Power"
    return "Other"


def _protocol_bucket_label(text: str, *, lang: str = "zh") -> str:
    key = _classify_protocol_bucket(text)
    if str(lang or "").strip().lower().startswith("en"):
        return key
    return _PROTOCOL_BUCKET_ZH.get(key, key)


def _normalize_netx_lang(lang: str | None) -> str:
    return "en" if str(lang or "").strip().lower().startswith("en") else "zh"


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


