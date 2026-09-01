"""Async / sampled audit writes to reduce DB pressure on every request."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from .config import settings
from .db import SessionLocal

_log = logging.getLogger("netx.audit.async")

_q: queue.Queue[dict[str, Any] | None] | None = None
_worker: threading.Thread | None = None
_lock = threading.Lock()
_counter = 0
_dropped = 0


def _sample_ok() -> bool:
    """When sample_n > 1, keep 1/N of http.* audits; always keep auth/security actions."""
    global _counter
    n = int(getattr(settings, "audit_sample_n", 1) or 1)
    if n <= 1:
        return True
    _counter += 1
    return (_counter % n) == 0


def audit_queue_status() -> dict[str, int]:
    q = _q
    return {
        "depth": int(q.qsize()) if q is not None else 0,
        "dropped": int(_dropped),
        "maxsize": max(100, int(getattr(settings, "audit_queue_max", 5000) or 5000)),
    }


def _worker_loop() -> None:
    assert _q is not None
    while True:
        item = _q.get()
        if item is None:
            break
        try:
            from .auth_service import write_audit

            db = SessionLocal()
            try:
                write_audit(db, **item)
            finally:
                db.close()
        except Exception:
            _log.exception("async audit write failed")


def _ensure_worker() -> queue.Queue:
    global _q, _worker
    with _lock:
        if _q is None:
            maxsize = max(100, int(getattr(settings, "audit_queue_max", 5000) or 5000))
            _q = queue.Queue(maxsize=maxsize)
            _worker = threading.Thread(target=_worker_loop, name="netx-audit-writer", daemon=True)
            _worker.start()
        return _q


def shutdown_audit_worker(*, timeout_sec: float = 2.0) -> None:
    global _q, _worker
    with _lock:
        q = _q
        worker = _worker
        if q is None:
            return
        try:
            q.put_nowait(None)
        except queue.Full:
            pass
    if worker is not None:
        worker.join(timeout=max(0.1, float(timeout_sec)))
    with _lock:
        _q = None
        _worker = None


def enqueue_audit(
    *,
    action: str,
    actor_user_id: str = "",
    actor_username: str = "",
    method: str = "",
    path: str = "",
    status_code: int = 0,
    client_ip: str = "",
    user_agent: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    global _dropped
    act = str(action or "")
    # Always persist auth / security / device-op events.
    if (
        act.startswith("auth.")
        or act.startswith("users.")
        or act.startswith("api_tokens.")
        or act.startswith("webcrt.")
        or act.startswith("ne.")
    ):
        pass
    elif act.startswith("http.") and not _sample_ok():
        return
    payload = {
        "action": act,
        "actor_user_id": actor_user_id,
        "actor_username": actor_username,
        "method": method,
        "path": path,
        "status_code": status_code,
        "client_ip": client_ip,
        "user_agent": user_agent,
        "detail": detail or {},
    }
    if not bool(getattr(settings, "audit_async", True)):
        from .auth_service import write_audit

        db = SessionLocal()
        try:
            write_audit(db, **payload)
        finally:
            db.close()
        return
    try:
        q = _ensure_worker()
        try:
            q.put_nowait(payload)
        except queue.Full:
            # Drop oldest then retry once to keep newest events.
            try:
                q.get_nowait()
                _dropped += 1
            except queue.Empty:
                pass
            try:
                q.put_nowait(payload)
            except queue.Full:
                _dropped += 1
                _log.warning("audit queue full; dropped event action=%s", act)
    except Exception:
        _log.exception("audit enqueue failed; falling back to sync")
        from .auth_service import write_audit

        db = SessionLocal()
        try:
            write_audit(db, **payload)
        finally:
            db.close()
