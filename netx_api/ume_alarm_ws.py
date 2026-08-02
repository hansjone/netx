"""UME alarm WebSocket consumer facade."""
from __future__ import annotations

from . import ume_alarm_ws_state as _ws_state
from .ume_alarm_ws_consumer import (
    run_alarm_ws_consumer_loop,
    shutdown_ws_consumer,
    start_ume_alarm_ws_consumer,
)
from .ume_alarm_ws_log import (
    append_ws_log,
    begin_startup_alarm_sync_gate,
    complete_startup_alarm_sync_gate,
    get_ws_logs,
    is_startup_alarm_sync_pending,
    is_ume_subscription_missing_error,
    parse_subscription_id_from_already_exists_error,
)
from .ume_alarm_ws_subscription import (
    _clear_active_subscription,
    _set_active_subscription,
    _set_ws_connection_state,
    cancel_alarm_subscription_manual,
    clear_local_alarm_subscription_manual,
    clear_ume_subscription_lost_flag,
    establish_alarm_subscription_manual,
    get_active_subscription,
    get_alarms_coordination_status,
    get_current_alarms_mode,
    get_subscription_status,
    get_ws_connection_status,
    is_ume_subscription_lost,
    is_wss_active_for_current_alarms,
    load_persisted_subscription,
    mark_ume_subscription_lost,
    process_alarm_notification,
    request_ws_reconnect,
)

# Shared mutable objects (same identity as state module) for tests/diagnostics.
_WS_LOG_LOCK = _ws_state._WS_LOG_LOCK
_WS_LOG_ENTRIES = _ws_state._WS_LOG_ENTRIES
_LOG_DEDUP_LOCK = _ws_state._LOG_DEDUP_LOCK
_LOG_DEDUP_CACHE = _ws_state._LOG_DEDUP_CACHE
_ws_connection_lock = _ws_state._ws_connection_lock


def __getattr__(name: str):
    """Proxy remaining state attributes (e.g. connection state strings)."""
    if hasattr(_ws_state, name):
        return getattr(_ws_state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "_LOG_DEDUP_CACHE",
    "_LOG_DEDUP_LOCK",
    "_WS_LOG_ENTRIES",
    "_WS_LOG_LOCK",
    "_clear_active_subscription",
    "_set_active_subscription",
    "_set_ws_connection_state",
    "_ws_connection_lock",
    "append_ws_log",
    "begin_startup_alarm_sync_gate",
    "cancel_alarm_subscription_manual",
    "clear_local_alarm_subscription_manual",
    "clear_ume_subscription_lost_flag",
    "complete_startup_alarm_sync_gate",
    "establish_alarm_subscription_manual",
    "get_active_subscription",
    "get_alarms_coordination_status",
    "get_current_alarms_mode",
    "get_subscription_status",
    "get_ws_connection_status",
    "get_ws_logs",
    "is_startup_alarm_sync_pending",
    "is_ume_subscription_lost",
    "is_ume_subscription_missing_error",
    "is_wss_active_for_current_alarms",
    "load_persisted_subscription",
    "mark_ume_subscription_lost",
    "parse_subscription_id_from_already_exists_error",
    "process_alarm_notification",
    "request_ws_reconnect",
    "run_alarm_ws_consumer_loop",
    "shutdown_ws_consumer",
    "start_ume_alarm_ws_consumer",
]
