"""Background scheduler for periodic config sync."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from uuid import uuid4

from .config import settings
from .config_sync_runner import dispatch_cycle
from .config_sync_service import (
    ensure_policy,
    expand_targets,
    has_running_cycle,
    next_due_at,
)
from .db import SessionLocal
from .models import ConfigSyncCycle, ConfigSyncTask

_log = logging.getLogger("netx.config_sync.scheduler")
_stop = threading.Event()
_thread: threading.Thread | None = None
_BOOT_MONO = time.monotonic()


def _utcnow() -> datetime:
    return datetime.utcnow()


def startup_grace_remaining_sec() -> float:
    grace = max(0, int(settings.config_sync_startup_grace_sec or 0))
    elapsed = time.monotonic() - _BOOT_MONO
    return max(0.0, float(grace) - elapsed)


def in_startup_grace() -> bool:
    return startup_grace_remaining_sec() > 0


def startup_grace_until() -> datetime | None:
    rem = startup_grace_remaining_sec()
    if rem <= 0:
        return None
    return _utcnow() + timedelta(seconds=rem)


def try_start_scheduled_cycle() -> str | None:
    """Create and dispatch a scheduled cycle if policy is due. Returns cycle id or None.

    Never starts while another cycle is active (running/pending/paused), including
    a cycle being resumed after crash. Startup grace only delays *new* scheduled runs.
    """
    if in_startup_grace():
        return None
    db = SessionLocal()
    try:
        policy = ensure_policy(db)
        if not policy.enabled:
            return None
        active = has_running_cycle(db)
        if active:
            _log.debug(
                "config_sync schedule skip: active cycle=%s status=%s",
                active.id,
                active.status,
            )
            return None
        due = next_due_at(db, policy)
        if due is not None and due > _utcnow():
            return None
        targets = expand_targets(db, policy)
        if not targets:
            _log.info("config_sync schedule skip: no targets")
            return None
        concurrency = max(1, min(32, int(policy.concurrency or 5)))
        cycle = ConfigSyncCycle(
            id=uuid4().hex,
            trigger_mode="schedule",
            status="running",
            concurrency=concurrency,
            planned_count=len(targets),
            started_at=_utcnow(),
            created_at=_utcnow(),
        )
        db.add(cycle)
        db.flush()
        for t in targets:
            db.add(
                ConfigSyncTask(
                    id=uuid4().hex,
                    cycle_id=cycle.id,
                    source=t["source"],
                    target_id=t["id"],
                    ne_name=t.get("ne_name") or "",
                    ne_ip=t.get("ne_ip") or "",
                    vendor=t.get("vendor") or "",
                    status="pending",
                )
            )
        db.commit()
        cycle_id = str(cycle.id)
    except Exception:
        db.rollback()
        _log.exception("config_sync schedule create failed")
        return None
    finally:
        db.close()

    dispatch_cycle(cycle_id)
    _log.info("config_sync scheduled cycle started id=%s", cycle_id)
    return cycle_id


def _loop() -> None:
    tick = max(15, int(settings.config_sync_scheduler_tick_sec or 60))
    grace = max(0, int(settings.config_sync_startup_grace_sec or 0))
    _log.info("config_sync scheduler started tick=%ss startup_grace=%ss", tick, grace)
    while not _stop.is_set():
        try:
            if bool(settings.config_sync_scheduler_enabled):
                try_start_scheduled_cycle()
        except Exception:
            _log.exception("config_sync scheduler tick failed")
        _stop.wait(tick)
    _log.info("config_sync scheduler stopped")


def start_config_sync_scheduler() -> None:
    global _thread
    if not bool(settings.config_sync_scheduler_enabled):
        _log.info("config_sync scheduler disabled by settings")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="config-sync-scheduler", daemon=True)
    _thread.start()
    _log.info("started thread %s alive=%s", _thread.name, _thread.is_alive())


def stop_config_sync_scheduler() -> None:
    _stop.set()
