"""UME sync service facade (inventory + alarms + topology)."""
from __future__ import annotations

from .config import settings
from .ume_alarm_apply import (
    _alarm_key,
    _derive_ne_id_from_alarm,
    _is_alarm_cleared,
    apply_alarm_to_current,
    extract_alarm_from_notification,
    normalize_yang_alarm,
    notification_id_from_norm,
)
from .ume_sync_common import _pick, _s, _utc_now_naive
from .ume_sync_pull import sync_alarms_current, sync_alarms_history_full, sync_inventory_full
from .ume_sync_topology import sync_topology_full

__all__ = [
    "_alarm_key",
    "_derive_ne_id_from_alarm",
    "_is_alarm_cleared",
    "_pick",
    "_s",
    "_utc_now_naive",
    "apply_alarm_to_current",
    "extract_alarm_from_notification",
    "normalize_yang_alarm",
    "notification_id_from_norm",
    "settings",
    "sync_alarms_current",
    "sync_alarms_history_full",
    "sync_inventory_full",
    "sync_topology_full",
]
