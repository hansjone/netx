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

_ORPHAN_SUB_ID_RE = re.compile(r"id:([0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{12})")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_ws_log(
    message: str,
    *,
    level: str = "info",
    subscription_id: str = "",
) -> None:
    """Ring buffer of recent WSS events for UI (newest last)."""
    entry = {
        "ts": _utc_now_iso(),
        "level": str(level or "info").strip().lower() or "info",
        "message": str(message or "").strip()[:500],
        "subscription_id": str(subscription_id or "").strip(),
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

_subscription_id: str = ""
_subscription_uri: str = ""
_subscription_topic: str = "ALARM"
_active_client: UMEClient | None = None


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
    }


def request_ws_reconnect() -> None:
    _ws_wake_event.set()


def establish_alarm_subscription_manual(client: UMEClient, db: Session) -> dict[str, Any]:
    """Manually establish ALARM subscription (UME API + persist). Does not open WSS."""
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
    request_ws_reconnect()
    append_ws_log(f"manual establish subscription id={sub_id}", subscription_id=sub_id)
    return {**get_subscription_status(), "already_exists": False}


def cancel_alarm_subscription_manual(client: UMEClient, db: Session) -> dict[str, Any]:
    """Manually cancel subscription (UME delete + clear DB + drop WSS)."""
    loaded = load_subscription(db)
    sub_id, _uri = get_active_subscription()
    if not sub_id and loaded is not None:
        sub_id = str(loaded[0] or "")
    if sub_id:
        client.refresh_if_needed()
        _delete_subscription_on_ume(client, sub_id)

    clear_subscription(db, cache_key=DEFAULT_SUBSCRIPTION_KEY)
    db.commit()
    _clear_active_subscription()
    request_ws_reconnect()
    append_ws_log(f"manual cancel subscription id={sub_id or '(none)'}", subscription_id=sub_id)
    return get_subscription_status()


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
            append_ws_log("message ignored: invalid JSON", level="warning", subscription_id=subscription_id)
            return
        db = SessionLocal()
        try:
            alarm = extract_alarm_from_notification(payload)
            if alarm is None:
                append_ws_log("收包忽略: 非告警通知", level="warning", subscription_id=subscription_id)
                return
            norm = normalize_yang_alarm(alarm) or {}
            alarm_key = _alarm_key(norm) or "?"
            action, changed = apply_alarm_to_current(db, alarm, touch_ts=_utc_now_naive())
            if changed:
                db.commit()
            else:
                db.rollback()
            label = _WS_ALARM_ACTION_LABEL.get(action, action)
            status_msg = f"{label} key={alarm_key}" + ("" if changed else " (无变更)")
            append_ws_log(status_msg, subscription_id=subscription_id)
            if on_status is not None:
                on_status(f"last={action}")
        except Exception as exc:
            db.rollback()
            append_ws_log(f"alarm apply failed: {str(exc)[:200]}", level="error", subscription_id=subscription_id)
            if on_status is not None:
                on_status(f"apply_error:{str(exc)[:120]}")
        finally:
            db.close()

    def _on_error(_ws: Any, error: Any) -> None:
        append_ws_log(f"ws error: {str(error)[:200]}", level="error", subscription_id=subscription_id)
        if on_status is not None:
            on_status(f"ws_error:{str(error)[:120]}")

    def _on_close(_ws: Any, close_status_code: Any, close_msg: Any) -> None:
        append_ws_log(
            f"ws closed code={close_status_code} msg={str(close_msg or '')[:120]}",
            subscription_id=subscription_id,
        )
        closed.set()

    def _on_open(_ws: Any) -> None:
        append_ws_log(f"ws connected uri={wss_uri[:120]}", subscription_id=subscription_id)
        if on_status is not None:
            on_status("connected")

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

    def _status(msg: str, *, level: str = "info", sub_id: str = "") -> None:
        append_ws_log(msg, level=level, subscription_id=sub_id)
        if on_status is not None:
            on_status(msg)

    while stop_event is None or not stop_event.is_set():
        if is_paused is not None and is_paused():
            _status("paused", level="warning")
            time.sleep(1.0)
            continue

        subscription_id, wss_uri = get_active_subscription()
        if not subscription_id or not wss_uri:
            _status("no_subscription")
            _ws_wake_event.wait(timeout=2.0)
            _ws_wake_event.clear()
            continue

        try:
            if not _wait_for_shared_token(client, timeout_s=120.0):
                _status("waiting_token", level="warning", sub_id=subscription_id)
                time.sleep(2.0)
                continue

            append_ws_log(f"connecting {wss_uri[:160]}", subscription_id=subscription_id)
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
                _status("waiting_token", level="warning", sub_id=subscription_id)
                time.sleep(2.0)
                continue
            _status(f"session error: {str(exc)[:200]}", level="error", sub_id=subscription_id)
        except Exception as exc:
            _status(f"session error: {str(exc)[:200]}", level="error", sub_id=subscription_id)

        sub_id_after, uri_after = get_active_subscription()
        if stop_event is not None and stop_event.is_set():
            break
        if not sub_id_after or not uri_after:
            _status("no_subscription")
            continue

        _status(f"reconnect in {int(backoff_s)}s", sub_id=sub_id_after)
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
