from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import logging
import re
import ssl
import threading
import time
from typing import Any, Callable

from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .ume_alarm_subscription_store import (
    DEFAULT_SUBSCRIPTION_KEY,
    clear_subscription,
    load_subscription,
    save_subscription,
)
from .ume_client import UMEClient
from .ume_sync_service import (
    _alarm_key,
    apply_alarm_to_current,
    extract_alarm_from_notification,
    normalize_yang_alarm,
    _utc_now_naive,
)

_WS_ALARM_ACTION_LABEL: dict[str, str] = {
    "inserted": "上报(新增)",
    "updated": "上报(更新)",
    "deleted": "清除",
    "skipped": "忽略",
}

_ws_log = logging.getLogger("netx.ume.alarm_ws")

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

# Blocks WSS connect until startup REST sync of current alarms completes (see main.on_startup).
# Default closed (clear): WSS must not write until main opens the gate after REST snapshot.
_STARTUP_ALARM_SYNC_GATE = threading.Event()


def begin_startup_alarm_sync_gate() -> None:
    _STARTUP_ALARM_SYNC_GATE.clear()


def complete_startup_alarm_sync_gate() -> None:
    _STARTUP_ALARM_SYNC_GATE.set()


def is_startup_alarm_sync_pending() -> bool:
    return not _STARTUP_ALARM_SYNC_GATE.is_set()

_ORPHAN_SUB_ID_RE = re.compile(r"id:([0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{12})")

_SUBSCRIPTION_MISSING_MARKERS: tuple[str, ...] = (
    "subscription not exist",
    "subscription not found",
    "not found or overtime",
    "please establish again",
    "status-code: 404",
    "status-reason: subscription not found",
    "non-101 status: 503",
)


def is_ume_subscription_missing_error(message: str) -> bool:
    """True when UME reports the notification subscription is gone or expired."""
    low = str(message or "").lower()
    return any(marker in low for marker in _SUBSCRIPTION_MISSING_MARKERS)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trim_log_dedup_cache() -> None:
    if len(_LOG_DEDUP_CACHE) <= _LOG_DEDUP_MAX_KEYS:
        return
    # Drop oldest half when over capacity (abnormal keys should not grow forever).
    ranked = sorted(_LOG_DEDUP_CACHE.items(), key=lambda item: item[1][0])
    drop_n = max(1, len(ranked) // 2)
    for key, _ in ranked[:drop_n]:
        _LOG_DEDUP_CACHE.pop(key, None)


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
        with _LOG_DEDUP_LOCK:
            prev = _LOG_DEDUP_CACHE.get(key)
            if prev is not None:
                last_ts, prev_suppressed = prev
                if now - last_ts < max(1.0, float(dedup_cooldown_s)):
                    _LOG_DEDUP_CACHE[key] = (last_ts, prev_suppressed + 1)
                    return
                suppressed = prev_suppressed
            _LOG_DEDUP_CACHE[key] = (now, 0)
            _trim_log_dedup_cache()

    if suppressed > 0:
        msg = f"{msg} (此前相同日志重复 {suppressed} 次)"

    entry = {
        "ts": _utc_now_iso(),
        "level": level_norm,
        "message": msg[:500],
        "subscription_id": sub_id,
    }
    with _WS_LOG_LOCK:
        _WS_LOG_ENTRIES.append(entry)
    log_fn = _ws_log.info
    if entry["level"] == "warning":
        log_fn = _ws_log.warning
    elif entry["level"] == "error":
        log_fn = _ws_log.error
    log_fn("%s%s", entry["message"], f" sub={entry['subscription_id']}" if entry["subscription_id"] else "")


def get_ws_logs(*, limit: int | None = None) -> list[dict[str, Any]]:
    cap = int(limit if limit is not None else _WS_LOG_MAX_RETURN)
    cap = max(1, min(cap, _WS_LOG_MAX_RETURN))
    with _WS_LOG_LOCK:
        items = list(_WS_LOG_ENTRIES)
    if len(items) <= cap:
        return items
    return items[-cap:]


def parse_subscription_id_from_already_exists_error(message: str) -> str:
    """Extract subscription id from UME 400 'topic subscription already exist, id:...'."""
    m = _ORPHAN_SUB_ID_RE.search(str(message or ""))
    return m.group(1).strip() if m else ""

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


def mark_ume_subscription_lost(reason: str) -> None:
    global _ume_subscription_lost, _ume_subscription_lost_reason
    detail = str(reason or "").strip()[:500]
    with _ume_lost_lock:
        if _ume_subscription_lost and detail == _ume_subscription_lost_reason:
            return
        _ume_subscription_lost = True
        _ume_subscription_lost_reason = detail
    append_ws_log(
        f"UME 侧订阅已丢失: {detail[:200]}",
        level="warning",
        dedup_cooldown_s=_LOOP_STATUS_COOLDOWN_S,
    )
    request_ws_reconnect()


def clear_ume_subscription_lost_flag() -> None:
    global _ume_subscription_lost, _ume_subscription_lost_reason
    with _ume_lost_lock:
        _ume_subscription_lost = False
        _ume_subscription_lost_reason = ""


def is_ume_subscription_lost() -> bool:
    with _ume_lost_lock:
        return bool(_ume_subscription_lost)


def _server_subscription_lost_fields() -> dict[str, Any]:
    with _ume_lost_lock:
        lost = bool(_ume_subscription_lost)
        reason = str(_ume_subscription_lost_reason or "")
    return {
        "server_subscription_lost": lost,
        "server_subscription_lost_reason": reason,
    }


def _set_ws_connection_state(state: str, *, detail: str = "") -> None:
    global _ws_connection_state, _ws_connection_detail
    with _ws_connection_lock:
        _ws_connection_state = str(state or "").strip() or "unknown"
        _ws_connection_detail = str(detail or "").strip()[:240]


def get_ws_connection_status() -> dict[str, Any]:
    with _ws_connection_lock:
        state = str(_ws_connection_state or "init")
        detail = str(_ws_connection_detail or "")
    return {
        "state": state,
        "label": _WS_CONNECTION_LABELS.get(state, state),
        "detail": detail,
    }


def _notify_ws_connection(
    state: str,
    *,
    detail: str = "",
    subscription_id: str = "",
    on_status: Callable[[str], None] | None = None,
    log_level: str = "info",
    log_cooldown_s: float = _WS_NOTIFY_COOLDOWN_S,
) -> None:
    global _WS_NOTIFY_LAST_KEY, _WS_NOTIFY_LAST_TS
    _set_ws_connection_state(state, detail=detail)
    label = _WS_CONNECTION_LABELS.get(state, state)
    log_msg = str(detail or label).strip()
    notify_key = f"{state}\0{log_msg}\0{subscription_id}"
    now = time.monotonic()
    should_log = True
    with _WS_NOTIFY_LOCK:
        if notify_key == _WS_NOTIFY_LAST_KEY and now - _WS_NOTIFY_LAST_TS < max(1.0, float(log_cooldown_s)):
            should_log = False
        else:
            _WS_NOTIFY_LAST_KEY = notify_key
            _WS_NOTIFY_LAST_TS = now
    if should_log:
        append_ws_log(
            log_msg,
            level=log_level,
            subscription_id=subscription_id,
            dedup=False,
        )
    if on_status is not None:
        on_status(label)


def _parse_ws_message(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def process_alarm_notification(db: Session, payload: dict[str, Any]) -> tuple[str, bool]:
    alarm = extract_alarm_from_notification(payload)
    if alarm is None:
        return "skipped", False
    return apply_alarm_to_current(db, alarm, touch_ts=_utc_now_naive())


def _delete_subscription_on_ume(client: UMEClient, subscription_id: str) -> None:
    sub_id = str(subscription_id or "").strip()
    if not sub_id:
        return
    client.delete_alarm_subscription(sub_id)


def _wait_for_shared_token(client: UMEClient, *, timeout_s: float = 300.0) -> bool:
    deadline = time.time() + max(5.0, float(timeout_s))
    while time.time() < deadline:
        client._sync_token_from_store()
        if client.has_valid_token():
            return True
        time.sleep(2.0)
    return False


def _set_active_subscription(subscription_id: str, wss_uri: str, *, topic: str = "ALARM") -> None:
    global _subscription_id, _subscription_uri, _subscription_topic
    with _subscription_lock:
        _subscription_id = str(subscription_id or "").strip()
        _subscription_uri = str(wss_uri or "").strip()
        _subscription_topic = str(topic or "ALARM").strip() or "ALARM"


def _clear_active_subscription() -> None:
    global _subscription_id, _subscription_uri, _subscription_topic
    with _subscription_lock:
        _subscription_id = ""
        _subscription_uri = ""
        _subscription_topic = "ALARM"


def get_active_subscription() -> tuple[str, str]:
    with _subscription_lock:
        return str(_subscription_id or ""), str(_subscription_uri or "")


def load_persisted_subscription() -> bool:
    """Load subscription from DB into memory (call on process startup)."""
    db = SessionLocal()
    try:
        loaded = load_subscription(db)
        if loaded is None:
            _clear_active_subscription()
            return False
        sub_id, uri, topic = loaded
        _set_active_subscription(sub_id, uri, topic=topic)
        append_ws_log(f"loaded persisted subscription id={sub_id}", subscription_id=sub_id)
        return True
    finally:
        db.close()


def get_subscription_status() -> dict[str, Any]:
    with _subscription_lock:
        sub_id = str(_subscription_id or "")
        uri = str(_subscription_uri or "")
        topic = str(_subscription_topic or "ALARM")
    return {
        "active": bool(sub_id and uri),
        "subscription_id": sub_id,
        "wss_uri": uri,
        "topic": topic,
        **_server_subscription_lost_fields(),
    }


def is_wss_active_for_current_alarms() -> bool:
    """True when WSS subscription is active and should own ume_alarms_current updates."""
    if not bool(getattr(settings, "ume_alarm_ws_enabled", True)):
        return False
    if is_ume_subscription_lost():
        return False
    return bool(get_subscription_status().get("active"))


def get_current_alarms_mode() -> str:
    return "wss" if is_wss_active_for_current_alarms() else "rest"


def get_alarms_coordination_status() -> dict[str, Any]:
    wss_active = is_wss_active_for_current_alarms()
    skip_when_ws = bool(getattr(settings, "ume_sync_alarms_current_skip_when_ws", True))
    return {
        "current_alarms_mode": get_current_alarms_mode(),
        "wss_active_for_current_alarms": wss_active,
        "scheduled_sync_skipped": bool(wss_active and skip_when_ws),
    }


def clear_local_alarm_subscription_manual(db: Session) -> dict[str, Any]:
    """Drop persisted/in-memory subscription without calling UME delete."""
    clear_subscription(db, cache_key=DEFAULT_SUBSCRIPTION_KEY)
    db.commit()
    _clear_active_subscription()
    clear_ume_subscription_lost_flag()
    request_ws_reconnect()
    append_ws_log("cleared local subscription record (UME delete skipped)")
    return get_subscription_status()


def request_ws_reconnect() -> None:
    _ws_wake_event.set()


def establish_alarm_subscription_manual(
    client: UMEClient,
    db: Session,
    *,
    force_reestablish: bool = False,
) -> dict[str, Any]:
    """Manually establish ALARM subscription (UME API + persist). Does not open WSS."""
    if force_reestablish or is_ume_subscription_lost():
        clear_local_alarm_subscription_manual(db)

    existing = load_subscription(db)
    if existing is not None:
        sub_id, uri, topic = existing
        _set_active_subscription(sub_id, uri, topic=topic)
        request_ws_reconnect()
        append_ws_log(f"establish skipped: already in DB id={sub_id}", subscription_id=sub_id)
        st = get_subscription_status()
        return {**st, "already_exists": True}

    mem_id, mem_uri = get_active_subscription()
    if mem_id and mem_uri:
        request_ws_reconnect()
        append_ws_log(f"establish skipped: in-memory id={mem_id}", subscription_id=mem_id)
        st = get_subscription_status()
        return {**st, "already_exists": True}

    if not _wait_for_shared_token(client, timeout_s=120.0):
        raise RuntimeError("ume_ws_no_valid_token:wait_timeout")
    client._sync_token_from_store()
    if not client.has_valid_token():
        raise RuntimeError("ume_ws_no_valid_token")

    topic = str(getattr(settings, "ume_notification_topic", "ALARM") or "ALARM").strip() or "ALARM"
    try:
        sub_id, uri = client.establish_alarm_subscription(topic=topic)
    except RuntimeError as exc:
        orphan_id = parse_subscription_id_from_already_exists_error(str(exc))
        if not orphan_id:
            raise
        append_ws_log(
            f"establish: orphan on UME id={orphan_id}, deleting then retry",
            level="warning",
            subscription_id=orphan_id,
        )
        try:
            _delete_subscription_on_ume(client, orphan_id)
        except RuntimeError as del_exc:
            raise RuntimeError(
                f"ume_orphan_subscription_delete_failed:id={orphan_id}:{str(del_exc)[:180]}"
            ) from del_exc
        sub_id, uri = client.establish_alarm_subscription(topic=topic)
    save_subscription(db, subscription_id=sub_id, wss_uri=uri, topic=topic)
    db.commit()

    global _active_client
    with _shutdown_lock:
        _active_client = client
    _set_active_subscription(sub_id, uri, topic=topic)
    clear_ume_subscription_lost_flag()
    request_ws_reconnect()
    append_ws_log(f"manual establish subscription id={sub_id}", subscription_id=sub_id)
    return {**get_subscription_status(), "already_exists": False}


def cancel_alarm_subscription_manual(
    client: UMEClient,
    db: Session,
    *,
    force_clear_local: bool = False,
) -> dict[str, Any]:
    """Manually cancel subscription (UME delete + clear DB + drop WSS)."""
    loaded = load_subscription(db)
    sub_id, _uri = get_active_subscription()
    if not sub_id and loaded is not None:
        sub_id = str(loaded[0] or "")
    ume_already_missing = False
    if sub_id:
        client.refresh_if_needed()
        try:
            _delete_subscription_on_ume(client, sub_id)
        except RuntimeError as exc:
            if is_ume_subscription_missing_error(str(exc)):
                ume_already_missing = True
                mark_ume_subscription_lost(str(exc))
                if not force_clear_local:
                    return {
                        **get_subscription_status(),
                        "ok": False,
                        "ume_already_missing": True,
                        "needs_local_cleanup": True,
                        "message": (
                            "UME 侧告警订阅已不存在或已过期（服务器订阅已丢失）。"
                            "请确认是否清除本地订阅记录，然后重新建立订阅。"
                        ),
                    }
            else:
                raise

    clear_subscription(db, cache_key=DEFAULT_SUBSCRIPTION_KEY)
    db.commit()
    _clear_active_subscription()
    clear_ume_subscription_lost_flag()
    request_ws_reconnect()
    append_ws_log(
        f"manual cancel subscription id={sub_id or '(none)'}",
        subscription_id=sub_id,
    )
    st = get_subscription_status()
    return {
        **st,
        "ok": True,
        "cleared_local": True,
        "ume_already_missing": ume_already_missing,
    }


def _run_ws_session(
    client: UMEClient,
    *,
    wss_uri: str,
    subscription_id: str,
    on_status: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    import websocket

    headers = client.ws_auth_headers()
    header_list = [f"{k}: {v}" for k, v in headers.items() if str(v or "").strip()]

    sslopt: dict[str, Any] | None = None
    if wss_uri.lower().startswith("wss://"):
        if client.verify_tls:
            sslopt = {"cert_reqs": ssl.CERT_REQUIRED}
        else:
            sslopt = {"cert_reqs": ssl.CERT_NONE}

    closed = threading.Event()

    def _on_message(_ws: Any, message: str) -> None:
        payload = _parse_ws_message(message)
        if payload is None:
            append_ws_log(
                "message ignored: invalid JSON",
                level="warning",
                subscription_id=subscription_id,
                dedup_cooldown_s=_WS_IGNORE_COOLDOWN_S,
            )
            return
        db = SessionLocal()
        try:
            alarm = extract_alarm_from_notification(payload)
            if alarm is None:
                append_ws_log(
                    "收包忽略: 非告警通知",
                    level="warning",
                    subscription_id=subscription_id,
                    dedup_cooldown_s=_WS_IGNORE_COOLDOWN_S,
                )
                return
            norm = normalize_yang_alarm(alarm) or {}
            alarm_key = _alarm_key(norm) or "?"
            action, changed = apply_alarm_to_current(db, alarm, touch_ts=_utc_now_naive())
            if changed:
                db.commit()
            else:
                db.rollback()
            if not changed:
                return
            label = _WS_ALARM_ACTION_LABEL.get(action, action)
            status_msg = f"{label} key={alarm_key}"
            append_ws_log(status_msg, subscription_id=subscription_id, dedup=False)
            try:
                from .key_alert_forward import maybe_forward_key_alert

                maybe_forward_key_alert(db, norm=norm, alarm_key=alarm_key, action=action)
            except Exception as fwd_exc:
                append_ws_log(
                    f"key alert forward failed: {str(fwd_exc)[:160]}",
                    level="error",
                    subscription_id=subscription_id,
                )
        except Exception as exc:
            db.rollback()
            append_ws_log(f"alarm apply failed: {str(exc)[:200]}", level="error", subscription_id=subscription_id)
        finally:
            db.close()

    def _on_error(_ws: Any, error: Any) -> None:
        err_text = str(error)
        detail = f"ws error: {err_text[:200]}"
        if is_ume_subscription_missing_error(err_text):
            mark_ume_subscription_lost(err_text)
            _notify_ws_connection(
                "subscription_lost",
                detail=detail,
                subscription_id=subscription_id,
                on_status=on_status,
                log_level="warning",
            )
        else:
            _notify_ws_connection(
                "error",
                detail=detail,
                subscription_id=subscription_id,
                on_status=on_status,
                log_level="error",
            )

    def _on_close(_ws: Any, close_status_code: Any, close_msg: Any) -> None:
        detail = f"ws closed code={close_status_code} msg={str(close_msg or '')[:120]}"
        _notify_ws_connection("disconnected", detail=detail, subscription_id=subscription_id, on_status=on_status)
        closed.set()

    def _on_open(_ws: Any) -> None:
        _notify_ws_connection(
            "connected",
            detail=f"ws connected uri={wss_uri[:120]}",
            subscription_id=subscription_id,
            on_status=on_status,
        )

    ws_app = websocket.WebSocketApp(
        wss_uri,
        header=header_list,
        on_open=_on_open,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
    )

    kwargs: dict[str, Any] = {"ping_interval": 30, "ping_timeout": 20}
    if sslopt is not None:
        kwargs["sslopt"] = sslopt

    thread = threading.Thread(
        target=lambda: ws_app.run_forever(**kwargs),
        name=f"ume-alarm-ws-{subscription_id[:8]}",
        daemon=True,
    )
    thread.start()

    while thread.is_alive() and not closed.is_set():
        if stop_event is not None and stop_event.is_set():
            try:
                ws_app.close()
            except Exception:
                pass
            break
        sub_id_now, _ = get_active_subscription()
        if not sub_id_now or sub_id_now != subscription_id:
            try:
                ws_app.close()
            except Exception:
                pass
            break
        if _ws_wake_event.is_set():
            try:
                ws_app.close()
            except Exception:
                pass
            break
        time.sleep(0.5)

    if thread.is_alive():
        thread.join(timeout=5.0)
    _ws_wake_event.clear()


def run_alarm_ws_consumer_loop(
    client: UMEClient,
    *,
    on_status: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
    is_paused: Callable[[], bool] | None = None,
) -> None:
    """
    Connect WSS only when a persisted/manual subscription exists.
    Never auto-establish subscription; reconnect same uri after disconnect.
    """
    backoff_s = 2.0
    max_backoff_s = 120.0

    def _loop_status(
        state: str,
        *,
        detail: str = "",
        subscription_id: str = "",
        log_level: str = "info",
    ) -> None:
        _notify_ws_connection(
            state,
            detail=detail,
            subscription_id=subscription_id,
            on_status=on_status,
            log_level=log_level,
            log_cooldown_s=_LOOP_STATUS_COOLDOWN_S,
        )

    while stop_event is None or not stop_event.is_set():
        if is_paused is not None and is_paused():
            _loop_status("paused", log_level="warning")
            time.sleep(1.0)
            continue

        if not _STARTUP_ALARM_SYNC_GATE.is_set():
            _loop_status("waiting_startup_alarm_sync", log_level="info")
            _STARTUP_ALARM_SYNC_GATE.wait(timeout=1.0)
            continue

        if is_ume_subscription_lost():
            with _ume_lost_lock:
                lost_detail = str(_ume_subscription_lost_reason or "")
            _loop_status(
                "subscription_lost",
                detail=lost_detail or "UME subscription missing on server",
                log_level="warning",
            )
            _ws_wake_event.wait(timeout=5.0)
            _ws_wake_event.clear()
            continue

        subscription_id, wss_uri = get_active_subscription()
        if not subscription_id or not wss_uri:
            _loop_status("no_subscription")
            _ws_wake_event.wait(timeout=2.0)
            _ws_wake_event.clear()
            continue

        try:
            if not _wait_for_shared_token(client, timeout_s=120.0):
                _loop_status("waiting_token", log_level="warning", subscription_id=subscription_id)
                time.sleep(2.0)
                continue

            _loop_status("connecting", detail=f"connecting {wss_uri[:160]}", subscription_id=subscription_id)
            _run_ws_session(
                client,
                wss_uri=wss_uri,
                subscription_id=subscription_id,
                on_status=on_status,
                stop_event=stop_event,
            )
            backoff_s = 2.0
        except RuntimeError as exc:
            if "ume_ws_no_valid_token" in str(exc):
                _loop_status("waiting_token", log_level="warning", subscription_id=subscription_id)
                time.sleep(2.0)
                continue
            _loop_status("error", detail=f"session error: {str(exc)[:200]}", subscription_id=subscription_id, log_level="error")
        except Exception as exc:
            _loop_status("error", detail=f"session error: {str(exc)[:200]}", subscription_id=subscription_id, log_level="error")

        sub_id_after, uri_after = get_active_subscription()
        if stop_event is not None and stop_event.is_set():
            break
        if not sub_id_after or not uri_after:
            _loop_status("no_subscription")
            continue

        _loop_status("reconnecting", detail=f"reconnect in {int(backoff_s)}s", subscription_id=sub_id_after)
        slept = 0.0
        while slept < backoff_s:
            if stop_event is not None and stop_event.is_set():
                return
            if _ws_wake_event.is_set():
                _ws_wake_event.clear()
                break
            time.sleep(0.5)
            slept += 0.5
        backoff_s = min(max_backoff_s, backoff_s * 2.0)


def shutdown_ws_consumer() -> None:
    """Process exit: close WSS only; subscription remains on UME until user cancels."""
    request_ws_reconnect()
    _ws_wake_event.set()


def start_ume_alarm_ws_consumer(
    client: UMEClient,
    *,
    on_status: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
    is_paused: Callable[[], bool] | None = None,
) -> threading.Thread:
    ev = stop_event if stop_event is not None else threading.Event()

    def _target() -> None:
        if not bool(getattr(settings, "ume_alarm_ws_enabled", True)):
            return
        if not str(client.base_url or "").strip():
            append_ws_log("consumer disabled: no UME base URL", level="warning")
            if on_status is not None:
                on_status("disabled:no_base_url")
            return
        append_ws_log("ws consumer thread started")
        global _active_client
        with _shutdown_lock:
            _active_client = client
        run_alarm_ws_consumer_loop(
            client,
            on_status=on_status,
            stop_event=ev,
            is_paused=is_paused,
        )

    thread = threading.Thread(target=_target, name="ume-alarm-ws-consumer", daemon=True)
    thread.start()
    return thread
