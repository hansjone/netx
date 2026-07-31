"""Startup recovery for interrupted port traffic collect rounds."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .models import PortTrafficTask

_log = logging.getLogger("netx.port_traffic.recovery")


def recover_port_traffic_on_startup(db: Session) -> int:
    """Clear stuck collect_running flags so scheduler can resume."""
    now = datetime.utcnow()
    rows = db.query(PortTrafficTask).filter(PortTrafficTask.collect_running.is_(True)).all()
    n = 0
    for task in rows:
        task.collect_running = False
        if not task.last_collect_ended_at:
            task.last_collect_ended_at = now
        if not task.last_error:
            task.last_error = "requeued_after_restart"
        task.updated_at = now
        n += 1
    if n:
        db.commit()
        _log.info("port_traffic recovery cleared collect_running on %s task(s)", n)
    return n
