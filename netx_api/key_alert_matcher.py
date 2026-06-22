from __future__ import annotations

import threading
import time
from typing import Any

from sqlalchemy.orm import Session

from .models import UmeKeyAlertRule
from .ume_sync_service import _is_alarm_cleared, notification_id_from_norm

_RULE_CACHE_LOCK = threading.Lock()
_RULE_CACHE: dict[str, UmeKeyAlertRule] = {}
_RULE_CACHE_LOADED_AT = 0.0
_RULE_CACHE_TTL_S = 30.0


def invalidate_key_alert_rule_cache() -> None:
    global _RULE_CACHE_LOADED_AT
    with _RULE_CACHE_LOCK:
        _RULE_CACHE.clear()
        _RULE_CACHE_LOADED_AT = 0.0


def _load_enabled_rules(db: Session) -> dict[str, UmeKeyAlertRule]:
    global _RULE_CACHE_LOADED_AT
    now = time.time()
    with _RULE_CACHE_LOCK:
        if _RULE_CACHE and (now - _RULE_CACHE_LOADED_AT) < _RULE_CACHE_TTL_S:
            return dict(_RULE_CACHE)
    rows = (
        db.query(UmeKeyAlertRule)
        .filter(UmeKeyAlertRule.enabled == 1)
        .all()
    )
    loaded = {str(row.notification_id or "").strip(): row for row in rows if str(row.notification_id or "").strip()}
    with _RULE_CACHE_LOCK:
        _RULE_CACHE.clear()
        _RULE_CACHE.update(loaded)
        _RULE_CACHE_LOADED_AT = now
    return dict(loaded)


def match_key_alert_rule(
    db: Session,
    *,
    norm: dict[str, Any],
    action: str,
) -> UmeKeyAlertRule | None:
    notification_id = notification_id_from_norm(norm)
    if not notification_id:
        return None
    rules = _load_enabled_rules(db)
    rule = rules.get(notification_id)
    if rule is None:
        return None
    act = str(action or "").strip().lower()
    if act in {"inserted", "updated"}:
        if _is_alarm_cleared(norm):
            return None
        return rule
    if act == "deleted":
        if int(getattr(rule, "forward_on_clear", 0) or 0) == 1:
            return rule
    return None
