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

# Middleware-tagged HTTP wrappers that duplicate semantic business audits.
_MIDDLEWARE_NOISE_ACTIONS = frozenset(
    {
        "audit.list",
        "webcrt.get",
        "webcrt.post",
        "webcrt.put",
        "webcrt.patch",
        "webcrt.delete",
        "users.get",
        "api_tokens.get",
        # Legacy auto-gate noise (no longer written; hide historical rows).
        "auth.unauthorized",
        "auth.password_change_required",
        "auth.forbidden_scope",
    }
)

# Frontend/session polls under /v1/auth/* — middleware tags them auth.{tail}.
# Keep failures (expired session etc.); drop successful chatter.
_AUTH_READ_NOISE_ACTIONS = frozenset(
    {
        "auth.me",
        "auth.sessions",
        "auth.refresh",
    }
)

_ALWAYS_KEEP_PREFIXES = (
    "auth.",
    "users.",
    "api_tokens.",
    "ne.",
    "port_traffic.",
    "config_sync.",
)


def _sample_ok() -> bool:
    """When sample_n > 1, keep 1/N of leftover generic events."""
    global _counter
    n = int(getattr(settings, "audit_sample_n", 1) or 1)
    if n <= 1:
        return True
    _counter += 1
    return (_counter % n) == 0


def audit_should_persist(
    *,
    action: str,
    method: str = "",
    status_code: int = 0,
    path: str = "",
) -> bool:
    """Decide whether a candidate audit event is worth writing.

    Policy:
    - Keep intentional ops: auth.login/logout/login_failed, users/tokens/NE/port_traffic/
      config_sync, semantic webcrt session/command.
    - Drop anonymous 401 / auto-gate noise (unauthorized, forbidden_scope, …).
    - Drop successful ``auth.me`` / ``auth.sessions`` / ``auth.refresh`` polls.
    - Drop successful GET/HEAD/OPTIONS ``http.*``; keep mutating http.* and failures.
    - Drop middleware ``webcrt.{method}`` / ``audit.list``.
    """
    del path  # reserved for future path allow/deny lists
    act = str(action or "")
    method_u = str(method or "").upper()
    code = int(status_code or 0)

    if act in _MIDDLEWARE_NOISE_ACTIONS:
        return False

    # /v1/auth/me and /v1/auth/sessions are polled constantly by the UI.
    if act in _AUTH_READ_NOISE_ACTIONS and code < 400:
        return False

    if act.startswith("webcrt.session_") or act == "webcrt.command":
        return True

    if any(act.startswith(p) for p in _ALWAYS_KEEP_PREFIXES):
        return True

    if code >= 400:
        return True

    if act.startswith("http."):
        if method_u in ("GET", "HEAD", "OPTIONS") or act in ("http.get", "http.head", "http.options"):
            return False
        if method_u in ("POST", "PUT", "PATCH", "DELETE") or act in (
            "http.post",
            "http.put",
            "http.patch",
            "http.delete",
        ):
            return True
        return _sample_ok()

    # e.g. ume.token.* — keep writes, drop successful reads
    if act.startswith("ume."):
        if method_u in ("GET", "HEAD", "OPTIONS"):
            return False
        return True

    return _sample_ok()


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
    if not audit_should_persist(action=act, method=method, status_code=status_code, path=path):
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
