from __future__ import annotations

import os
import socket
from time import sleep, time
from datetime import datetime

from sqlalchemy import text as sql_text

from .config import settings
from .db import SessionLocal
from .models import UmeTokenCache


def build_cache_key() -> str:
    base = str(settings.ume_base_url or "").strip().rstrip("/")
    user = str(settings.ume_username or "").strip()
    return f"{base}|{user}" if base or user else "default"

def build_owner_id() -> str:
    host = ""
    try:
        host = socket.gethostname()
    except Exception:
        host = "host"
    pid = os.getpid()
    return f"{host}:{pid}"


def load_shared_token(cache_key: str | None = None) -> tuple[str, float] | None:
    key = str(cache_key or build_cache_key())
    with SessionLocal() as db:
        row = db.get(UmeTokenCache, key)
        if not row:
            return None
        token = str(row.token or "").strip()
        if not token:
            return None
        return token, float(int(row.expires_at_epoch_s or 0))

def try_acquire_refresh_lock(
    *,
    cache_key: str | None = None,
    owner_id: str | None = None,
    lock_ttl_s: int = 30,
) -> bool:
    key = str(cache_key or build_cache_key())
    owner = str(owner_id or build_owner_id())
    now = int(time())
    ttl = max(5, int(lock_ttl_s or 30))
    lock_until = now + ttl
    with SessionLocal() as db:
        db.execute(
            sql_text(
                """
UPDATE ume_token_cache
SET lock_owner = :owner,
    lock_expires_at_epoch_s = :lock_until,
    updated_at = updated_at
WHERE cache_key = :key
  AND (
    lock_expires_at_epoch_s IS NULL
    OR lock_expires_at_epoch_s < :now
    OR lock_owner = :owner
  )
"""
            ),
            {"owner": owner, "lock_until": lock_until, "key": key, "now": now},
        )
        if db.get(UmeTokenCache, key) is None:
            row = UmeTokenCache(cache_key=key)
            db.add(row)
            db.commit()
        row = db.get(UmeTokenCache, key)
        if not row:
            return False
        ok = (str(row.lock_owner or "") == owner) and int(row.lock_expires_at_epoch_s or 0) >= now
        if ok:
            db.commit()
        return bool(ok)


def release_refresh_lock(*, cache_key: str | None = None, owner_id: str | None = None) -> None:
    key = str(cache_key or build_cache_key())
    owner = str(owner_id or build_owner_id())
    with SessionLocal() as db:
        db.execute(
            sql_text(
                """
UPDATE ume_token_cache
SET lock_owner = '',
    lock_expires_at_epoch_s = 0
WHERE cache_key = :key AND lock_owner = :owner
"""
            ),
            {"key": key, "owner": owner},
        )
        db.commit()


def wait_for_token_update(
    *,
    cache_key: str | None = None,
    min_expires_at_epoch_s: float = 0.0,
    timeout_s: float = 10.0,
    poll_interval_s: float = 0.4,
) -> tuple[str, float] | None:
    key = str(cache_key or build_cache_key())
    deadline = time() + max(0.1, float(timeout_s or 10.0))
    poll = max(0.1, float(poll_interval_s or 0.4))
    while time() < deadline:
        cur = load_shared_token(key)
        if cur:
            token, exp = cur
            if float(exp) > float(min_expires_at_epoch_s) and str(token or "").strip():
                return token, float(exp)
        sleep(poll)
    return None


def save_shared_token(token: str, expires_at_epoch_s: float, cache_key: str | None = None) -> None:
    key = str(cache_key or build_cache_key())
    with SessionLocal() as db:
        row = db.get(UmeTokenCache, key)
        if row is None:
            row = UmeTokenCache(cache_key=key)
            db.add(row)
        row.token = str(token or "")
        row.expires_at_epoch_s = int(expires_at_epoch_s or 0)
        row.updated_at = datetime.utcnow()
        db.commit()


def clear_shared_token(cache_key: str | None = None) -> None:
    key = str(cache_key or build_cache_key())
    with SessionLocal() as db:
        row = db.get(UmeTokenCache, key)
        if row is None:
            return
        row.token = ""
        row.expires_at_epoch_s = 0
        row.updated_at = datetime.utcnow()
        db.commit()
