"""Startup recovery for interrupted config-sync cycles."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from .config_sync_runner import dispatch_cycle
from .config_sync_service import finalize_cycle, sync_cycle_progress
from .models import ConfigSyncCycle, ConfigSyncTask
from datetime import datetime

_log = logging.getLogger("netx.config_sync.recovery")


def recover_config_sync_on_startup(db: Session) -> int:
    """
    Mark orphaned running tasks as fail(orphan_recovered), then resume pending
    tasks for cycles still marked running/paused.
    """
    cycles = (
        db.query(ConfigSyncCycle)
        .filter(ConfigSyncCycle.status.in_(("running", "paused", "pending")))
        .all()
    )
    resumed = 0
    for cycle in cycles:
        cycle_id = str(cycle.id)
        orphans = (
            db.query(ConfigSyncTask)
            .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "running")
            .all()
        )
        for task in orphans:
            task.status = "fail"
            task.message = "orphan_recovered"
            task.ended_at = datetime.utcnow()
        if orphans:
            db.commit()
            _log.info("config_sync recovery cycle=%s orphaned_tasks=%s", cycle_id, len(orphans))

        sync_cycle_progress(db, cycle_id)
        db.refresh(cycle)

        if str(cycle.status) == "paused":
            continue

        pending = (
            db.query(ConfigSyncTask)
            .filter(ConfigSyncTask.cycle_id == cycle_id, ConfigSyncTask.status == "pending")
            .count()
        )
        if pending <= 0:
            if str(cycle.status) in ("running", "pending"):
                finalize_cycle(db, cycle_id)
            continue

        if str(cycle.status) == "pending":
            cycle.status = "running"
            if not cycle.started_at:
                cycle.started_at = datetime.utcnow()
            db.commit()

        n = dispatch_cycle(cycle_id)
        resumed += n
        _log.info("config_sync recovery resumed cycle=%s pending=%s", cycle_id, n)
    return resumed
