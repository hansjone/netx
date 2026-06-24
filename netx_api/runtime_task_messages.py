"""Stable runtime-task status codes for frontend i18n (rt:* / ws:* / fwd:*)."""

RT_STARTUP_ALARM_SYNC_BEFORE_WS = "rt:startup_alarm_sync_before_ws"
RT_STARTUP_GATE_WAITING = "rt:startup_gate_waiting"
RT_WSS_ACTIVE_SKIP_REST = "rt:wss_active_skip_rest"
RT_PULLING_ALARMS_CURRENT = "rt:pulling_alarms_current"
RT_ALARMS_SYNC_IN_PROGRESS_SKIP = "rt:alarms_sync_in_progress_skip"
RT_PULLING_INVENTORY = "rt:pulling_inventory"
RT_UME_WS_DISABLED_NO_BASE_URL = "rt:ume_ws_disabled_no_base_url"
RT_OCLAW_FWD_DISABLED = "rt:oclaw_fwd_disabled"
RT_RESUMED_SYNC_SOON = "rt:resumed_sync_soon"
RT_RESUMED_WSS_RECONNECT = "rt:resumed_wss_reconnect"
RT_RESUMED_OCLAW_WSS_RECONNECT = "rt:resumed_oclaw_wss_reconnect"
RT_RESUMED = "rt:resumed"
RT_KEEPALIVE_FAILED = "rt:keepalive_failed"


def ws_state_code(state: str) -> str:
    return f"ws:{str(state or '').strip()}"


def fwd_state_code(state: str) -> str:
    return f"fwd:{str(state or '').strip()}"
