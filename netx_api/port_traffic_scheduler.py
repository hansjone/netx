"""Background scheduler for port traffic collection + retention purge."""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from .config import settings
from .db import SessionLocal
from .models import PortTrafficDevice
from .port_traffic_runner import dispatch_collect
from .port_traffic_service import purge_expired_samples

_log = logging.getLogger("netx.port_traffic.scheduler")
_stop = threading.Event()
_thread: threading.Thread | None = None
_purge_counter = 0


def _utcnow() -> datetime:
    return datetime.utcnow()


def try_dispatch_due_devices() -> int:
    """Dispatch collect rounds for due running devices. Returns number started."""
    db = SessionLocal()
    try:
        devices = (
            db.query(PortTrafficDevice)
            .filter(
                PortTrafficDevice.status == "running",
                PortTrafficDevice.collect_running.is_(False),
            )
            .all()
        )
        due_ids: list[str] = []
        now = _utcnow()
        for device in devices:
            interval = max(15, int(device.interval_sec or 60))
            ended = device.last_collect_ended_at
            if ended is None:
                due_ids.append(str(device.id))
                continue
            if (now - ended).total_seconds() >= interval:
                due_ids.append(str(device.id))
    finally:
        db.close()

    started = 0
    for did in due_ids:
        try:
            n = dispatch_collect(did)
            if n:
                started += 1
                _log.info("port_traffic collect started device=%s targets=%s", did, n)
        except Exception:
            _log.exception("port_traffic dispatch failed device=%s", did)
    return started


def try_dispatch_due_tasks() -> int:
    return try_dispatch_due_devices()


def _loop() -> None:
    global _purge_counter
    tick = max(5, int(settings.port_traffic_scheduler_tick_sec or 15))
    _log.info("port_traffic scheduler started tick=%ss", tick)
    while not _stop.is_set():
        try:
            if bool(settings.port_traffic_scheduler_enabled):
                try_dispatch_due_devices()
                _purge_counter += 1
                if _purge_counter >= 20:
                    _purge_counter = 0
                    db = SessionLocal()
                    try:
                        purge_expired_samples(db)
                    finally:
                        db.close()
        except Exception:
            _log.exception("port_traffic scheduler tick failed")
        _stop.wait(tick)
    _log.info("port_traffic scheduler stopped")


def start_port_traffic_scheduler() -> None:
    global _thread
    if not bool(settings.port_traffic_scheduler_enabled):
        _log.info("port_traffic scheduler disabled by settings")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="port-traffic-scheduler", daemon=True)
    _thread.start()


def stop_port_traffic_scheduler() -> None:
    _stop.set()
