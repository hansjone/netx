"""Process-local mutable state for UME alarm WSS consumer."""
from __future__ import annotations

from collections import deque
import threading
from typing import Any

from .ume_client import UMEClient

_WS_ALARM_ACTION_LABEL: dict[str, str] = {
    "inserted": "上报(新增)",
    "updated": "上报(更新)",
    "deleted": "清除",
    "skipped": "忽略",
}

_WS_LOG_LOCK = threading.Lock()
_WS_LOG_ENTRIES: deque[dict[str, Any]] = deque(maxlen=200)
_WS_LOG_MAX_RETURN = 100

_LOG_DEDUP_LOCK = threading.Lock()
_LOG_DEDUP_CACHE: dict[str, tuple[float, int]] = {}
_LOG_DEDUP_MAX_KEYS = 256
_WS_NOTIFY_LOCK = threading.Lock()
_WS_NOTIFY_LAST_KEY = ""
_WS_NOTIFY_LAST_TS = 0.0
_WS_NOTIFY_COOLDOWN_S = 45.0
_LOOP_STATUS_COOLDOWN_S = 60.0
_WS_IGNORE_COOLDOWN_S = 60.0

_STARTUP_ALARM_SYNC_GATE = threading.Event()

_shutdown_lock = threading.Lock()
_subscription_lock = threading.Lock()
_ws_wake_event = threading.Event()
_ws_connection_lock = threading.Lock()

_subscription_id: str = ""
_subscription_uri: str = ""
_subscription_topic: str = "ALARM"
_active_client: UMEClient | None = None
_ws_connection_state: str = "init"
_ws_connection_detail: str = ""
_ume_lost_lock = threading.Lock()
_ume_subscription_lost = False
_ume_subscription_lost_reason: str = ""

_WS_CONNECTION_LABELS: dict[str, str] = {
    "init": "初始化",
    "connected": "已连接",
    "connecting": "连接中",
    "disconnected": "已断开",
    "no_subscription": "无订阅",
    "waiting_token": "等待 token",
    "paused": "已暂停",
    "error": "连接异常",
    "reconnecting": "重连等待",
    "subscription_lost": "UME订阅已丢失",
}
