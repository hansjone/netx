"""Background scheduler for biz_state collection."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .cli_budget import clamp_cli_workers
from .config import settings
from .db import SessionLocal
from .models import BizStateTask
from .biz_state.collect_runner import dispatch_collect

_log = logging.getLogger("netx.biz_state.scheduler")
_stop = threading.Event()
_thread: threading.Thread | None = None
_dispatch_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_last_tick_mono: float = 0.0
_last_purge_mono: float = 0.0
_PURGE_INTERVAL_SEC = 3600.0


def _utcnow() -> datetime:
    return datetime.utcnow()


def _maybe_purge_retention() -> None:
    """Hourly sweep so paused/HF tasks still get retention cleanup."""
    import time as _time

    global _last_purge_mono
    now = _time.monotonic()
    if _last_purge_mono and (now - _last_purge_mono) < _PURGE_INTERVAL_SEC:
        return
    _last_purge_mono = now
    db = SessionLocal()
    try:
        from .biz_state.retention import purge_all_tasks

        info = purge_all_tasks(db)
        if info.get("dropped"):
            _log.info(
                "biz_state periodic retention dropped=%s tasks=%s",
                info.get("dropped"),
                len(info.get("tasks") or []),
            )
    except Exception:
        _log.exception("biz_state periodic retention failed")
    finally:
        db.close()


def _dispatch_pool_get() -> ThreadPoolExecutor:
    global _dispatch_pool
    with _pool_lock:
        if _dispatch_pool is None:
            workers = clamp_cli_workers(
                int(getattr(settings, "biz_state_dispatch_workers", 2) or 2),
            )
            _dispatch_pool = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="biz-dispatch"
            )
        return _dispatch_pool


def shutdown_biz_state_dispatch_pool(*, wait: bool = False) -> None:
    global _dispatch_pool
    with _pool_lock:
        if _dispatch_pool is not None:
            try:
                _dispatch_pool.shutdown(wait=wait, cancel_futures=True)
            except TypeError:
                _dispatch_pool.shutdown(wait=wait)
            _dispatch_pool = None


def try_dispatch_due_tasks() -> int:
    import time as _time

    global _last_tick_mono
    _last_tick_mono = _time.monotonic()

    db = SessionLocal()
    try:
        tasks = (
            db.query(BizStateTask)
            .filter(
                BizStateTask.status == "running",
                BizStateTask.collect_running.is_(False),
            )
            .all()
        )
        due_ids: list[str] = []
        now = _utcnow()
        for task in tasks:
            interval = max(60, int(task.interval_sec or 300))
            ended = task.last_collect_ended_at
            if ended is None:
                due_ids.append(str(task.id))
                continue
            if (now - ended).total_seconds() >= interval:
                due_ids.append(str(task.id))
    finally:
        db.close()

    if not due_ids:
        return 0
    pool = _dispatch_pool_get()
    for tid in due_ids:
        try:
            pool.submit(dispatch_collect, tid)
        except Exception:
            _log.exception("biz_state submit failed task=%s", tid)
    return len(due_ids)


def _loop() -> None:
    tick = max(5, int(getattr(settings, "biz_state_scheduler_tick_sec", 15) or 15))
    while not _stop.wait(tick):
        if not bool(getattr(settings, "biz_state_scheduler_enabled", True)):
            continue
        try:
            try_dispatch_due_tasks()
        except Exception:
            _log.exception("biz_state scheduler tick failed")
        try:
            _maybe_purge_retention()
        except Exception:
            _log.exception("biz_state retention tick failed")


def start_biz_state_scheduler() -> None:
    global _thread
    if not bool(getattr(settings, "biz_state_scheduler_enabled", True)):
        _log.info("biz_state scheduler disabled")
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="biz-state-scheduler", daemon=True)
    _thread.start()
    _log.info("biz_state scheduler started")


def stop_biz_state_scheduler() -> None:
    _stop.set()
    shutdown_biz_state_dispatch_pool(wait=False)
    global _thread
    t = _thread
    _thread = None
    if t and t.is_alive():
        t.join(timeout=2.0)
    _log.info("biz_state scheduler stopped")


def biz_state_scheduler_status() -> dict:
    import time as _time

    alive = bool(_thread and _thread.is_alive())
    age = None
    if _last_tick_mono:
        age = max(0.0, _time.monotonic() - _last_tick_mono)
    return {
        "running": alive,
        "enabled": bool(getattr(settings, "biz_state_scheduler_enabled", True)),
        "last_tick_age_sec": age,
    }
