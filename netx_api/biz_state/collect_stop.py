"""Stop an in-flight / queued biz_state collect batch.

Queued batches are cancelled in DB immediately. Running lanes poll
``is_stop_requested`` between commands (DB-backed for dedicated workers)
and process-local holders are force-closed when stop is requested in the
same process.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from ..db import SessionLocal
from ..models import BizStateBatch, BizStateTask
from ..ne_session_factory import close_netmiko_connection
from ..timeutil import utcnow_naive

_log = logging.getLogger("netx.biz_state.collect_stop")

STOP_REQUEST_TOKEN = "stop_requested"
STOP_USER_MESSAGE = "stopped_by_user"

_lock = threading.Lock()
_stop_batches: set[str] = set()
_holders: dict[str, list[dict[str, Any]]] = {}
# batch_id → (monotonic_ts, stop_requested)
_db_cache: dict[str, tuple[float, bool]] = {}
_DB_CACHE_TTL_SEC = 1.5


def register_lane_holder(batch_id: str, holder: dict[str, Any]) -> None:
    bid = str(batch_id or "").strip()
    if not bid or holder is None:
        return
    with _lock:
        _holders.setdefault(bid, []).append(holder)


def unregister_lane_holder(batch_id: str, holder: dict[str, Any]) -> None:
    bid = str(batch_id or "").strip()
    if not bid:
        return
    with _lock:
        lst = _holders.get(bid) or []
        try:
            lst.remove(holder)
        except ValueError:
            pass
        if not lst:
            _holders.pop(bid, None)


def mark_stop_requested(batch_id: str) -> None:
    bid = str(batch_id or "").strip()
    if not bid:
        return
    with _lock:
        _stop_batches.add(bid)
        _db_cache[bid] = (time.monotonic(), True)


def clear_stop_requested(batch_id: str) -> None:
    bid = str(batch_id or "").strip()
    if not bid:
        return
    with _lock:
        _stop_batches.discard(bid)
        _db_cache.pop(bid, None)
        _holders.pop(bid, None)


def is_stop_requested(batch_id: str) -> bool:
    """True if user asked to stop this batch (memory or DB)."""
    bid = str(batch_id or "").strip()
    if not bid:
        return False
    with _lock:
        if bid in _stop_batches:
            return True
        hit = _db_cache.get(bid)
        now = time.monotonic()
        if hit and (now - hit[0]) < _DB_CACHE_TTL_SEC:
            return bool(hit[1])

    stopped = _read_stop_from_db(bid)
    with _lock:
        _db_cache[bid] = (time.monotonic(), stopped)
        if stopped:
            _stop_batches.add(bid)
    return stopped


def _read_stop_from_db(batch_id: str) -> bool:
    db = SessionLocal()
    try:
        batch = db.get(BizStateBatch, batch_id)
        if not batch:
            return False
        st = str(batch.status or "")
        if st in ("cancelled", "success", "partial", "failed"):
            # Already terminal — treat as stop so lanes exit quickly.
            return st == "cancelled" or STOP_REQUEST_TOKEN in str(batch.message or "")
        msg = str(batch.message or "")
        return msg.startswith(STOP_REQUEST_TOKEN) or msg == STOP_USER_MESSAGE
    except Exception:
        _log.exception("read stop flag failed batch=%s", batch_id)
        return False
    finally:
        db.close()


def _force_close_holders(batch_id: str) -> int:
    bid = str(batch_id or "").strip()
    with _lock:
        holders = list(_holders.get(bid) or [])
    n = 0
    for holder in holders:
        try:
            holder["stop_requested"] = True
            holder["timed_out"] = True
            conn = holder.get("conn")
            if conn is not None:
                close_netmiko_connection(conn)
                n += 1
        except Exception:
            _log.exception("force-close on stop failed batch=%s", bid)
    return n


def request_stop_collect(task_id: str) -> dict[str, Any]:
    """Cancel queued batches and signal running collect for this task to abort."""
    tid = str(task_id or "").strip()
    if not tid:
        return {"ok": False, "reason": "missing_task_id"}

    db = SessionLocal()
    cancelled_ids: list[str] = []
    signaled_ids: list[str] = []
    closed = 0
    try:
        task = db.get(BizStateTask, tid)
        if not task:
            return {"ok": False, "reason": "task_not_found", "task_id": tid}

        active = (
            db.query(BizStateBatch)
            .filter(
                BizStateBatch.task_id == tid,
                BizStateBatch.status.in_(("queued", "running")),
            )
            .all()
        )
        if not active:
            # Nothing to stop — clear sticky collect_running if orphaned.
            if bool(task.collect_running):
                task.collect_running = False
                if hasattr(task, "collect_queued_at"):
                    task.collect_queued_at = None
                task.updated_at = utcnow_naive()
                db.commit()
            return {
                "ok": True,
                "task_id": tid,
                "stopped": False,
                "reason": "not_collecting",
                "cancelled_batches": [],
                "signaled_batches": [],
            }

        now = utcnow_naive()
        still_running = False
        for batch in active:
            bid = str(batch.id)
            mark_stop_requested(bid)
            if str(batch.status or "") == "queued":
                batch.status = "cancelled"
                batch.message = STOP_USER_MESSAGE
                batch.ended_at = now
                cancelled_ids.append(bid)
            else:
                batch.message = STOP_REQUEST_TOKEN
                signaled_ids.append(bid)
                still_running = True
            closed += _force_close_holders(bid)

        if not still_running:
            task.collect_running = False
            if hasattr(task, "collect_queued_at"):
                task.collect_queued_at = None
            task.last_collect_ended_at = now
            task.last_error = STOP_USER_MESSAGE
            task.updated_at = now

        db.commit()
        _log.info(
            "stop collect task=%s cancelled=%s signaled=%s closed_conns=%s",
            tid,
            cancelled_ids,
            signaled_ids,
            closed,
        )
        return {
            "ok": True,
            "task_id": tid,
            "stopped": True,
            "cancelled_batches": cancelled_ids,
            "signaled_batches": signaled_ids,
            "closed_connections": closed,
        }
    except Exception:
        _log.exception("request_stop_collect failed task=%s", tid)
        try:
            db.rollback()
        except Exception:
            pass
        return {"ok": False, "reason": "stop_error", "task_id": tid}
    finally:
        db.close()
