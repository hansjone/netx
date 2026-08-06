"""UME alarm WSS log buffer, startup gate, and subscription-error helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import re
import time
from typing import Any

from . import ume_alarm_ws_state as ws_state

_ws_log = logging.getLogger("netx.ume.alarm_ws")

_ORPHAN_SUB_ID_RE = re.compile(
    r"id:([0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{12})"
)

_SUBSCRIPTION_MISSING_MARKERS: tuple[str, ...] = (
    "subscription not exist",
    "subscription not found",
    "not found or overtime",
    "please establish again",
    "status-code: 404",
    "status-reason: subscription not found",
    "non-101 status: 503",
    "non-101 status: 404",
    "handshake status 404",
    "handshake status 503",
)


def is_ume_subscription_missing_error(message: str) -> bool:
    """True when UME reports the notification subscription is gone or expired."""
    low = str(message or "").lower()
    return any(marker in low for marker in _SUBSCRIPTION_MISSING_MARKERS)


def begin_startup_alarm_sync_gate() -> None:
    ws_state._STARTUP_ALARM_SYNC_GATE.clear()


def complete_startup_alarm_sync_gate() -> None:
    ws_state._STARTUP_ALARM_SYNC_GATE.set()


def is_startup_alarm_sync_pending() -> bool:
    return not ws_state._STARTUP_ALARM_SYNC_GATE.is_set()
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_log_dedup_cache() -> None:
    if len(ws_state._LOG_DEDUP_CACHE) <= ws_state._LOG_DEDUP_MAX_KEYS:
        return
    # Drop oldest half when over capacity (abnormal keys should not grow forever).
    ranked = sorted(ws_state._LOG_DEDUP_CACHE.items(), key=lambda item: item[1][0])
    drop_n = max(1, len(ranked) // 2)
    for key, _ in ranked[:drop_n]:
        ws_state._LOG_DEDUP_CACHE.pop(key, None)


def append_ws_log(
    message: str,
    *,
    level: str = "info",
    subscription_id: str = "",
    dedup: bool = True,
    dedup_cooldown_s: float = 30.0,
) -> None:
    """Ring buffer of recent WSS events for UI (newest last).

    When dedup=True, identical (level, subscription_id, message) within cooldown
    are suppressed; the next log after cooldown includes a repeat count suffix.
    """
    level_norm = str(level or "info").strip().lower() or "info"
    msg = str(message or "").strip()
    sub_id = str(subscription_id or "").strip()
    if not msg:
        return

    suppressed = 0
    if dedup:
        key = f"{level_norm}\0{sub_id}\0{msg}"
        now = time.monotonic()
        with ws_state._LOG_DEDUP_LOCK:
            prev = ws_state._LOG_DEDUP_CACHE.get(key)
            if prev is not None:
                last_ts, prev_suppressed = prev
                if now - last_ts < max(1.0, float(dedup_cooldown_s)):
                    ws_state._LOG_DEDUP_CACHE[key] = (last_ts, prev_suppressed + 1)
                    return
                suppressed = prev_suppressed
            ws_state._LOG_DEDUP_CACHE[key] = (now, 0)
            _trim_log_dedup_cache()

    if suppressed > 0:
        msg = f"{msg} (此前相同日志重复 {suppressed} 次)"

    entry = {
        "ts": _utc_now_iso(),
        "level": level_norm,
        "message": msg[:500],
        "subscription_id": sub_id,
    }
    with ws_state._WS_LOG_LOCK:
        ws_state._WS_LOG_ENTRIES.append(entry)
    log_fn = _ws_log.info
    if entry["level"] == "warning":
        log_fn = _ws_log.warning
    elif entry["level"] == "error":
        log_fn = _ws_log.error
    log_fn("%s%s", entry["message"], f" sub={entry['subscription_id']}" if entry["subscription_id"] else "")


def get_ws_logs(*, limit: int | None = None) -> list[dict[str, Any]]:
    cap = int(limit if limit is not None else ws_state._WS_LOG_MAX_RETURN)
    cap = max(1, min(cap, ws_state._WS_LOG_MAX_RETURN))
    with ws_state._WS_LOG_LOCK:
        items = list(ws_state._WS_LOG_ENTRIES)
    if len(items) <= cap:
        return items
    return items[-cap:]


def parse_subscription_id_from_already_exists_error(message: str) -> str:
    """Extract subscription id from UME 400 'topic subscription already exist, id:...'."""
    m = _ORPHAN_SUB_ID_RE.search(str(message or ""))
    return m.group(1).strip() if m else ""

