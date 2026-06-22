from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import UmeKeyAlertMonitorConfig

_CONFIG_LOCK = threading.Lock()
_FORWARD_ON_CLEAR_CACHE: bool | None = None
_CONFIG_CACHE_LOADED_AT = 0.0
_CONFIG_CACHE_TTL_S = 10.0
_CONFIG_ROW_ID = 1


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def invalidate_key_alert_config_cache() -> None:
    global _CONFIG_CACHE_LOADED_AT, _FORWARD_ON_CLEAR_CACHE
    with _CONFIG_LOCK:
        _FORWARD_ON_CLEAR_CACHE = None
        _CONFIG_CACHE_LOADED_AT = 0.0


def _get_or_create_config_row(db: Session) -> UmeKeyAlertMonitorConfig:
    row = db.get(UmeKeyAlertMonitorConfig, _CONFIG_ROW_ID)
    if row is None:
        now = _utc_now_naive()
        row = UmeKeyAlertMonitorConfig(id=_CONFIG_ROW_ID, forward_on_clear=0, updated_at=now)
        db.add(row)
        db.flush()
    return row


def is_forward_on_clear_enabled(db: Session) -> bool:
    global _CONFIG_CACHE_LOADED_AT, _FORWARD_ON_CLEAR_CACHE
    now = time.time()
    with _CONFIG_LOCK:
        if _FORWARD_ON_CLEAR_CACHE is not None and (now - _CONFIG_CACHE_LOADED_AT) < _CONFIG_CACHE_TTL_S:
            return _FORWARD_ON_CLEAR_CACHE
    row = _get_or_create_config_row(db)
    enabled = bool(int(row.forward_on_clear or 0))
    with _CONFIG_LOCK:
        _FORWARD_ON_CLEAR_CACHE = enabled
        _CONFIG_CACHE_LOADED_AT = now
    return enabled


def get_key_alert_monitor_config(db: Session) -> dict[str, bool]:
    return {"forward_on_clear": is_forward_on_clear_enabled(db)}


def set_key_alert_monitor_config(db: Session, *, forward_on_clear: bool) -> dict[str, bool]:
    row = _get_or_create_config_row(db)
    row.forward_on_clear = 1 if forward_on_clear else 0
    row.updated_at = _utc_now_naive()
    db.commit()
    invalidate_key_alert_config_cache()
    return {"forward_on_clear": bool(forward_on_clear)}
