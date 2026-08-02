"""Background scheduler for port traffic collection + retention purge."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .cli_budget import clamp_cli_workers
from .config import settings
from .db import SessionLocal
from .models import PortTrafficDevice
from .port_traffic_runner import dispatch_collect
from .port_traffic_service import purge_expired_samples

_log = logging.getLogger("netx.port_traffic.scheduler")
_stop = threading.Event()
_thread: threading.Thread | None = None
_purge_thread: threading.Thread | None = None
_dispatch_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_last_tick_mono: float = 0.0
_last_purge_mono: float = 0.0


def _utcnow() -> datetime:
    return datetime.utcnow()


def _dispatch_pool_get() -> ThreadPoolExecutor:
    global _dispatch_pool
    with _pool_lock:
        if _dispatch_pool is None:
            workers = clamp_cli_workers(
                int(getattr(settings, "port_traffic_dispatch_workers", 3) or 3),
            )
            _dispatch_pool = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="pt-dispatch"
            )
        return _dispatch_pool


def shutdown_port_traffic_dispatch_pool(*, wait: bool = False) -> None:
    global _dispatch_pool
    with _pool_lock:
        if _dispatch_pool is not None:
            try:
                _dispatch_pool.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                _dispatch_pool.shutdown(wait=wait)
            _dispatch_pool = None


def try_dispatch_due_devices() -> int:
    """Claim due devices and sample them on a bounded pool (non-blocking submit)."""
    import time as _time

    global _last_tick_mono
    _last_tick_mono = _time.monotonic()

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

    if not due_ids:
        return 0

    pool = _dispatch_pool_get()
    started = 0
    for did in due_ids:
        try:
            pool.submit(_run_device_collect, did)
            started += 1
        except Exception:
            _log.exception("port_traffic dispatch submit failed device=%s", did)
    return started


def _run_device_collect(device_id: str) -> None:
    try:
        n = dispatch_collect(device_id)
        if n:
            _log.info("port_traffic collect finished device=%s targets=%s", device_id, n)
    except Exception:
        _log.exception("port_traffic dispatch failed device=%s", device_id)


def try_dispatch_due_tasks() -> int:
    return try_dispatch_due_devices()


def _purge_once() -> None:
    import time as _time

    global _last_purge_mono
    db = SessionLocal()
    try:
        purge_expired_samples(db)
        _last_purge_mono = _time.monotonic()
    except Exception:
        _log.exception("port_traffic purge failed")
    finally:
        db.close()


def _loop() -> None:
    tick = max(5, int(settings.port_traffic_scheduler_tick_sec or 15))
    _log.info("port_traffic scheduler started tick=%ss", tick)
    while not _stop.is_set():
        try:
            if bool(settings.port_traffic_scheduler_enabled):
                try_dispatch_due_devices()
        except Exception:
            _log.exception("port_traffic scheduler tick failed")
        _stop.wait(tick)
    _log.info("port_traffic scheduler stopped")


def _purge_loop() -> None:
    # Independent of collect latency — every ~5 minutes.
    interval = 300
    _log.info("port_traffic purge loop started interval=%ss", interval)
    while not _stop.is_set():
        try:
            if bool(settings.port_traffic_scheduler_enabled):
                _purge_once()
        except Exception:
            _log.exception("port_traffic purge loop failed")
        _stop.wait(interval)
    _log.info("port_traffic purge loop stopped")


def start_port_traffic_scheduler() -> None:
    global _thread, _purge_thread
    if not bool(settings.port_traffic_scheduler_enabled):
        _log.info("port_traffic scheduler disabled by settings")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="port-traffic-scheduler", daemon=True)
    _thread.start()
    if not (_purge_thread and _purge_thread.is_alive()):
        _purge_thread = threading.Thread(target=_purge_loop, name="port-traffic-purge", daemon=True)
        _purge_thread.start()


def stop_port_traffic_scheduler() -> None:
    _stop.set()
    shutdown_port_traffic_dispatch_pool(wait=False)


def port_traffic_scheduler_status() -> dict:
    import time as _time

    now = _time.monotonic()
    return {
        "last_tick_age_sec": (now - _last_tick_mono) if _last_tick_mono else None,
        "last_purge_age_sec": (now - _last_purge_mono) if _last_purge_mono else None,
        "running": bool(_thread and _thread.is_alive()),
    }
