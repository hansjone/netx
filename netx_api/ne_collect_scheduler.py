"""Background scheduler for periodic NE batch collect."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from .collection_policy import ensure_policy, next_due_at
from .collection_service import create_and_start_from_policy, has_active_collection_job
from .config import settings
from .db import SessionLocal
from .ne_collect_runner import dispatch_collection_runs

_log = logging.getLogger("netx.ne_collect.scheduler")
_stop = threading.Event()
_thread: threading.Thread | None = None
_BOOT_MONO = time.monotonic()
_last_tick_mono: float = 0.0


def _utcnow() -> datetime:
    # Match NeCollectionJob timestamps (collection_service uses datetime.now()).
    return datetime.now()


def startup_grace_remaining_sec() -> float:
    grace = max(0, int(getattr(settings, "ne_collect_startup_grace_sec", 3600) or 0))
    elapsed = time.monotonic() - _BOOT_MONO
    return max(0.0, float(grace) - elapsed)


def in_startup_grace() -> bool:
    return startup_grace_remaining_sec() > 0


def try_start_scheduled_collect() -> str | None:
    if not bool(getattr(settings, "ne_collect_scheduler_enabled", True)):
        return None
    if in_startup_grace():
        return None
    db = SessionLocal()
    try:
        policy = ensure_policy(db)
        if not policy.enabled:
            return None
        if has_active_collection_job(db) is not None:
            return None
        due = next_due_at(db, policy)
        if due is not None and due > _utcnow():
            return None
        out, payload = create_and_start_from_policy(db, trigger_mode="schedule")
        job_id = str(out.id)
        dispatch_collection_runs(payload["job_id"], payload["run_ids"], payload["commands"])
        _log.info("ne_collect scheduled job started id=%s", job_id)
        return job_id or None
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        detail = getattr(exc, "detail", None)
        if detail in {
            "collection_job_running",
            "commands_empty",
            "no_eligible_ne",
            "commands_required_for_schedule",
            "no_selected_targets",
        }:
            _log.info("ne_collect schedule skip: %s", detail)
            return None
        _log.exception("ne_collect schedule start failed")
        return None
    finally:
        db.close()


def _loop() -> None:
    global _last_tick_mono
    tick = max(15, int(getattr(settings, "ne_collect_scheduler_tick_sec", 60) or 60))
    grace = max(0, int(getattr(settings, "ne_collect_startup_grace_sec", 3600) or 0))
    _log.info("ne_collect scheduler started tick=%ss startup_grace=%ss", tick, grace)
    while not _stop.is_set():
        try:
            _last_tick_mono = time.monotonic()
            try_start_scheduled_collect()
        except Exception:
            _log.exception("ne_collect scheduler tick failed")
        _stop.wait(tick)
    _log.info("ne_collect scheduler stopped")


def start_ne_collect_scheduler() -> None:
    global _thread
    if not bool(getattr(settings, "ne_collect_scheduler_enabled", True)):
        _log.info("ne_collect scheduler disabled by settings")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ne-collect-scheduler", daemon=True)
    _thread.start()


def stop_ne_collect_scheduler() -> None:
    _stop.set()


def ne_collect_scheduler_status() -> dict:
    now = time.monotonic()
    return {
        "running": bool(_thread is not None and _thread.is_alive()),
        "last_tick_age_sec": (now - _last_tick_mono) if _last_tick_mono else None,
        "startup_grace_remaining_sec": round(startup_grace_remaining_sec(), 1),
    }
