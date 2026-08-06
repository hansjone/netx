"""PostgreSQL database size for workbench (used bytes only)."""

from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy import text

from .config import settings

_CACHE_LOCK = threading.Lock()
_CACHED: dict[str, Any] | None = None
_CACHED_AT = 0.0
_CACHE_TTL_SEC = 30.0


def _open_session():
    """Indirection so tests can patch without importing the full engine."""
    from .db import SessionLocal

    return SessionLocal()


def _collect_uncached() -> dict[str, Any]:
    url = str(settings.database_url or "")
    out: dict[str, Any] = {
        "used_bytes": 0,
        "source": "none",
    }
    if url.startswith("sqlite"):
        out["source"] = "sqlite"
        out["error"] = "not_applicable"
        return out

    try:
        with _open_session() as db:
            row = db.execute(
                text(
                    "SELECT current_database() AS db_name, "
                    "pg_database_size(current_database())::bigint AS used_bytes"
                )
            ).mappings().one()
            out["db_name"] = str(row["db_name"] or "")
            out["used_bytes"] = int(row["used_bytes"] or 0)
            out["source"] = "pg_database_size"
    except Exception as exc:  # noqa: BLE001
        out["source"] = "error"
        out["error"] = str(exc)[:200]
    return out


def collect_db_storage_metrics() -> dict[str, Any]:
    """Return PG used-size dict; never raises. Cached ~30s."""
    global _CACHED, _CACHED_AT
    now = time.monotonic()
    with _CACHE_LOCK:
        if _CACHED is not None and (now - _CACHED_AT) < _CACHE_TTL_SEC:
            return dict(_CACHED)
    out = _collect_uncached()
    with _CACHE_LOCK:
        _CACHED = dict(out)
        _CACHED_AT = time.monotonic()
    return out
