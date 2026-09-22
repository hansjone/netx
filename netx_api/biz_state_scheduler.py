"""Background scheduler for biz_state collection (enqueue + claim loop)."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from .cli_budget import clamp_cli_workers
from .config import settings
from .db import SessionLocal
from .models import BizStateTask
from .biz_state.claim import claim_queued_batches, enqueue_collect, max_concurrent_tasks, open_slots
from .biz_state.collect_runner import execute_claimed_batch

_log = logging.getLogger("netx.biz_state.scheduler")
_stop = threading.Event()
_thread: threading.Thread | None = None
_dispatch_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_last_tick_mono: float = 0.0
_last_purge_mono: float = 0.0
_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()
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
            n = clamp_cli_workers(
                int(
                    getattr(settings, "biz_state_worker_collect_threads", None)
                    or getattr(settings, "biz_state_dispatch_workers", 8)
                    or 8
                ),
            )
            n = max(1, min(n, max_concurrent_tasks()))
            _dispatch_pool = ThreadPoolExecutor(
                max_workers=n, thread_name_prefix="biz-collect"
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
    try:
        from .biz_state.persist_pool import shutdown_persist_pool

        shutdown_persist_pool(wait=wait)
    except Exception:
        _log.exception("shutdown persist pool failed")


def _sync_cutover_hf_windows() -> None:
    """Pause/resume cutover_hf tasks according to migration project hf_start/hf_end."""
    db = SessionLocal()
    try:
        from .biz_migration.service import (
            PURPOSE_CUTOVER_HF,
            _all_hf_task_ids,
            _hf_window_status,
        )
        from .biz_state import service as biz_svc
        from .models import BizMigrationProject

        projects = db.query(BizMigrationProject).all()
        for proj in projects:
            want = _hf_window_status(proj)
            tids = _all_hf_task_ids(proj, "old") + _all_hf_task_ids(proj, "new")
            for tid in tids:
                task = db.get(BizStateTask, tid)
                if not task:
                    continue
                purpose = str(getattr(task, "purpose", None) or "").strip()
                if purpose and purpose != PURPOSE_CUTOVER_HF:
                    continue
                if want == "paused" and task.status == "running":
                    try:
                        patch = {"status": "paused"}
                        if purpose != PURPOSE_CUTOVER_HF:
                            patch["purpose"] = PURPOSE_CUTOVER_HF
                        biz_svc.update_task(db, tid, patch)
                    except Exception:
                        _log.exception("pause hf window failed task=%s", tid)
                elif want == "running" and task.status == "paused":
                    try:
                        patch = {"status": "running"}
                        if purpose != PURPOSE_CUTOVER_HF:
                            patch["purpose"] = PURPOSE_CUTOVER_HF
                        biz_svc.update_task(db, tid, patch)
                    except Exception:
                        _log.exception("resume hf window failed task=%s", tid)
    except Exception:
        _log.exception("cutover hf window sync failed")
    finally:
        db.close()


def _enqueue_due_tasks() -> int:
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

    n = 0
    for tid in due_ids:
        try:
            r = enqueue_collect(tid, manual=False)
            if r.get("queued"):
                n += 1
        except Exception:
            _log.exception("biz_state enqueue failed task=%s", tid)
    return n


def _run_claimed(job: dict[str, Any]) -> None:
    bid = str(job.get("batch_id") or "")
    try:
        execute_claimed_batch(
            batch_id=bid,
            task_id=str(job.get("task_id") or ""),
            source=str(job.get("source") or ""),
            ne_id=str(job.get("ne_id") or ""),
            vendor=str(job.get("vendor") or ""),
            device_type=str(job.get("device_type") or ""),
        )
    finally:
        with _in_flight_lock:
            _in_flight.discard(bid)


def _claim_and_dispatch() -> int:
    slots = open_slots()
    with _in_flight_lock:
        local_busy = len(_in_flight)
    # Don't over-submit beyond local pool either.
    pool = _dispatch_pool_get()
    local_cap = getattr(pool, "_max_workers", 8) or 8
    want = min(slots, max(0, int(local_cap) - local_busy))
    if want <= 0:
        return 0
    jobs = claim_queued_batches(want)
    if not jobs:
        return 0
    submitted = 0
    for job in jobs:
        bid = str(job.get("batch_id") or "")
        if not bid:
            continue
        with _in_flight_lock:
            if bid in _in_flight:
                continue
            _in_flight.add(bid)
        try:
            pool.submit(_run_claimed, job)
            submitted += 1
        except Exception:
            with _in_flight_lock:
                _in_flight.discard(bid)
            _log.exception("biz_state submit claimed batch failed batch=%s", bid)
            # Compensate: claimed batch must not stay running forever.
            try:
                from .biz_state.collect_runner import _fail_batch_status, _finish_task

                _fail_batch_status(bid, "RuntimeError: submit_claimed_batch_failed")
                _finish_task(
                    str(job.get("task_id") or ""),
                    error="RuntimeError: submit_claimed_batch_failed",
                )
            except Exception:
                _log.exception("biz_state compensate after submit fail batch=%s", bid)
    return submitted


def try_dispatch_due_tasks() -> int:
    """Enqueue due tasks, then claim+run up to concurrency ceiling."""
    import time as _time

    global _last_tick_mono
    _last_tick_mono = _time.monotonic()

    try:
        _sync_cutover_hf_windows()
    except Exception:
        _log.exception("hf window sync tick failed")

    enq = 0
    try:
        enq = _enqueue_due_tasks()
    except Exception:
        _log.exception("biz_state enqueue tick failed")

    try:
        from .biz_state.claim import reclaim_stale_queued

        reclaim_stale_queued(max_age_sec=3600)
    except Exception:
        _log.exception("biz_state stale queued reclaim failed")

    claimed = 0
    try:
        claimed = _claim_and_dispatch()
    except Exception:
        _log.exception("biz_state claim tick failed")

    return enq + claimed


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
    _log.info(
        "biz_state scheduler started max_concurrent=%s",
        max_concurrent_tasks(),
    )


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
    with _in_flight_lock:
        inflight = len(_in_flight)
    return {
        "running": alive,
        "enabled": bool(getattr(settings, "biz_state_scheduler_enabled", True)),
        "last_tick_age_sec": age,
        "in_flight_batches": inflight,
        "max_concurrent_tasks": max_concurrent_tasks(),
        "dedicated_workers": bool(
            getattr(settings, "biz_state_dedicated_workers", True)
        ),
    }
