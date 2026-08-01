"""Startup recovery for interrupted port traffic collect rounds."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from .models import PortTrafficDevice

_log = logging.getLogger("netx.port_traffic.recovery")


def recover_port_traffic_on_startup(db: Session) -> int:
    """Clear stuck collect_running flags so scheduler can resume."""
    now = datetime.utcnow()
    rows = db.query(PortTrafficDevice).filter(PortTrafficDevice.collect_running.is_(True)).all()
    n = 0
    for device in rows:
        device.collect_running = False
        if not device.last_collect_ended_at:
            device.last_collect_ended_at = now
        if not device.last_error:
            device.last_error = "requeued_after_restart"
        device.updated_at = now
        n += 1
    if n:
        db.commit()
        _log.info("port_traffic recovery cleared collect_running on %s device(s)", n)
    return n
