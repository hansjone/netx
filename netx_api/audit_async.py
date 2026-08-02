"""Async / sampled audit writes to reduce DB pressure on every request."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from .config import settings
from .db import SessionLocal

_log = logging.getLogger("netx.audit.async")

_q: queue.SimpleQueue[dict[str, Any]] | None = None
_worker: threading.Thread | None = None
_lock = threading.Lock()
_counter = 0


def _sample_ok() -> bool:
    """When sample_n > 1, keep 1/N of http.* audits; always keep auth/security actions."""
    global _counter
    n = int(getattr(settings, "audit_sample_n", 1) or 1)
    if n <= 1:
        return True
    _counter += 1
    return (_counter % n) == 0


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


def _ensure_worker() -> queue.SimpleQueue:
    global _q, _worker
    with _lock:
        if _q is None:
            _q = queue.SimpleQueue()
            _worker = threading.Thread(target=_worker_loop, name="netx-audit-writer", daemon=True)
            _worker.start()
        return _q


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
    act = str(action or "")
    # Always persist auth / security-relevant events.
    if act.startswith("auth.") or act.startswith("users.") or act.startswith("api_tokens.") or act.startswith("webcrt."):
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
        _ensure_worker().put(payload)
    except Exception:
        _log.exception("audit enqueue failed; falling back to sync")
        from .auth_service import write_audit

        db = SessionLocal()
        try:
            write_audit(db, **payload)
        finally:
            db.close()
