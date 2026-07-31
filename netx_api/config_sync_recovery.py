"""Startup recovery for interrupted config-sync cycles."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .config_sync_runner import dispatch_cycle
from .config_sync_service import finalize_cycle, sync_cycle_progress
from .models import ConfigSyncCycle, ConfigSyncTask

_log = logging.getLogger("netx.config_sync.recovery")

_ACTIVE = ("running", "paused", "pending")


def _close_cycle(db: Session, cycle: ConfigSyncCycle, *, reason: str) -> None:
    """Fail in-flight work and mark cycle terminal so it cannot block forever."""
    cycle_id = str(cycle.id)
    now = datetime.utcnow()
    for task in (
        db.query(ConfigSyncTask)
        .filter(
            ConfigSyncTask.cycle_id == cycle_id,
            ConfigSyncTask.status.in_(("running", "pending")),
        )
        .all()
    ):
        was_running = str(task.status) == "running"
        task.status = "fail" if was_running else "cancelled"
        task.message = reason
        task.ended_at = now
    db.commit()
    sync_cycle_progress(db, cycle_id)
    db.refresh(cycle)
    cycle.status = "fail"
    cycle.error_message = reason
    cycle.ended_at = now
    db.commit()


def recover_config_sync_on_startup(db: Session) -> int:
    """
    Resume at most one interrupted cycle after process restart.

    Rules:
    - Only one active cycle (running/pending/paused) may exist; older actives are closed.
    - Orphan ``running`` tasks are re-queued to ``pending`` and continued (crash 续跑).
    - ``paused`` cycles stay paused (no auto dispatch) but still occupy the single-flight slot.
    - New scheduled cycles remain blocked while this active cycle exists.
    """
    cycles = (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status.in_(_ACTIVE))
        .all()
    )
    cycles = sorted(cycles, key=lambda c: c.created_at or datetime.min)
    if not cycles:
        return 0

    # Single-flight hygiene: keep newest, close older interrupted cycles.
    primary = cycles[-1]
    for stale in cycles[:-1]:
        _log.warning(
            "config_sync recovery closing older active cycle=%s (keep=%s)",
            stale.id,
            primary.id,
        )
        _close_cycle(db, stale, reason="superseded_active_cycle")

    cycle_id = str(primary.id)
    orphans = (
        db.query(ConfigSyncTask)
        .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "running")
        .all()
    )
    for task in orphans:
        task.status = "pending"
        task.message = "requeued_after_restart"
        task.started_at = None
        task.ended_at = None
    if orphans:
        db.commit()
        _log.info("config_sync recovery cycle=%s requeued_orphans=%s", cycle_id, len(orphans))

    sync_cycle_progress(db, cycle_id)
    db.refresh(primary)

    if str(primary.status) == "paused":
        _log.info("config_sync recovery cycle=%s stays paused (blocks new cycles)", cycle_id)
        return 0

    pending = (
        db.query(ConfigSyncTask)
        .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "pending")
        .all()
    )
    if not pending:
        finalize_cycle(db, cycle_id)
        _log.info("config_sync recovery cycle=%s finalized (no pending)", cycle_id)
        return 0

    if str(primary.status) == "pending":
        primary.status = "running"
        if not primary.started_at:
            primary.started_at = datetime.utcnow()
        db.commit()

    task_ids = [str(t.id) for t in pending]
    n = dispatch_cycle(cycle_id)
    _log.info("config_sync recovery resumed cycle=%s pending=%s dispatched=%s", cycle_id, len(task_ids), n)
    return n
