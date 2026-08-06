"""Login brute-force protection: Redis when configured, else in-process."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import settings

_log = logging.getLogger("netx.auth.rate")

_lock = threading.Lock()


@dataclass
class _Bucket:
    window_start: float
    failures: int
    locked_until: float = 0.0


_buckets: dict[str, _Bucket] = {}
_redis_client: Any | None = None
_redis_failed = False


def _key(username: str, client_ip: str) -> str:
    return f"{str(client_ip or '').strip().lower()}|{str(username or '').strip().lower()}"


def _limits() -> tuple[int, int, int]:
    max_fail = max(3, int(getattr(settings, "auth_login_max_failures", 10) or 10))
    window = max(60, int(getattr(settings, "auth_login_window_sec", 300) or 300))
    lockout = max(60, int(getattr(settings, "auth_login_lockout_sec", 900) or 900))
    return max_fail, window, lockout


def _get_redis() -> Any | None:
    global _redis_client, _redis_failed
    url = str(getattr(settings, "auth_redis_url", "") or "").strip()
    if not url:
        return None
    if _redis_failed:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis  # type: ignore

        client = redis.Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.5)
        client.ping()
        _redis_client = client
        _log.info("auth login rate-limit using Redis")
        return _redis_client
    except Exception:
        _redis_failed = True
        _log.warning("auth Redis unavailable; falling back to in-process rate-limit", exc_info=True)
        return None


def login_lock_remaining(username: str, client_ip: str) -> float:
    """Seconds remaining on lockout, or 0 if not locked."""
    k = _key(username, client_ip)
    r = _get_redis()
    if r is not None:
        try:
            ttl = r.ttl(f"netx:auth:lock:{k}")
            if isinstance(ttl, int) and ttl > 0:
                return float(ttl)
            return 0.0
        except Exception:
            _log.debug("redis lock_remaining failed", exc_info=True)
    now = time.time()
    with _lock:
        row = _buckets.get(k)
        if row is None:
            return 0.0
        return max(0.0, float(row.locked_until) - now)


def register_login_failure(username: str, client_ip: str) -> float:
    """Record a failed login. Returns lock remaining seconds (0 if not yet locked)."""
    max_fail, window, lockout = _limits()
    k = _key(username, client_ip)
    r = _get_redis()
    if r is not None:
        try:
            lock_key = f"netx:auth:lock:{k}"
            fail_key = f"netx:auth:fail:{k}"
            existing = r.ttl(lock_key)
            if isinstance(existing, int) and existing > 0:
                return float(existing)
            count = int(r.incr(fail_key))
            if count == 1:
                r.expire(fail_key, window)
            if count >= max_fail:
                r.setex(lock_key, lockout, "1")
                r.delete(fail_key)
                return float(lockout)
            return 0.0
        except Exception:
            _log.debug("redis register_failure failed; using memory", exc_info=True)

    now = time.time()
    with _lock:
        row = _buckets.get(k)
        if row is None or (now - row.window_start) > window:
            row = _Bucket(window_start=now, failures=0)
            _buckets[k] = row
        if row.locked_until > now:
            return float(row.locked_until - now)
        row.failures += 1
        if row.failures >= max_fail:
            row.locked_until = now + lockout
            return float(lockout)
        return 0.0


def clear_login_failures(username: str, client_ip: str) -> None:
    k = _key(username, client_ip)
    r = _get_redis()
    if r is not None:
        try:
            r.delete(f"netx:auth:fail:{k}", f"netx:auth:lock:{k}")
        except Exception:
            _log.debug("redis clear_failures failed", exc_info=True)
    with _lock:
        _buckets.pop(k, None)


def reset_login_rate_limit_for_tests() -> None:
    global _redis_client, _redis_failed
    with _lock:
        _buckets.clear()
    _redis_client = None
    _redis_failed = False
