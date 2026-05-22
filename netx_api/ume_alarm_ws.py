from __future__ import annotations

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
from .ume_sync_service import apply_alarm_to_current, extract_alarm_from_notification, _utc_now_naive

_ws_log = logging.getLogger("netx.ume.alarm_ws")

_ORPHAN_SUB_ID_RE = re.compile(r"id:([0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{12})")


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
        _ws_log.info("loaded persisted alarm subscription id=%s", sub_id)
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
        _ws_log.info("establish skipped: subscription already exists id=%s", sub_id)
        st = get_subscription_status()
        return {**st, "already_exists": True}

    mem_id, mem_uri = get_active_subscription()
    if mem_id and mem_uri:
        request_ws_reconnect()
        _ws_log.info("establish skipped: in-memory subscription id=%s", mem_id)
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
        _ws_log.warning("establish: UME reports existing subscription id=%s, deleting then retry", orphan_id)
        _delete_subscription_on_ume(client, orphan_id)
        sub_id, uri = client.establish_alarm_subscription(topic=topic)
    save_subscription(db, subscription_id=sub_id, wss_uri=uri, topic=topic)
    db.commit()

    global _active_client
    with _shutdown_lock:
        _active_client = client
    _set_active_subscription(sub_id, uri, topic=topic)
    request_ws_reconnect()
    _ws_log.info("manual establish alarm subscription id=%s", sub_id)
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
    _ws_log.info("manual cancel alarm subscription id=%s", sub_id)
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
            return
        db = SessionLocal()
        try:
            action, changed = process_alarm_notification(db, payload)
            if changed:
                db.commit()
            else:
                db.rollback()
            if on_status is not None:
                on_status(f"last={action}")
        except Exception as exc:
            db.rollback()
            _ws_log.exception("ws alarm apply failed: %s", exc)
            if on_status is not None:
                on_status(f"apply_error:{str(exc)[:120]}")
        finally:
            db.close()

    def _on_error(_ws: Any, error: Any) -> None:
        _ws_log.warning("ws error subscription=%s: %s", subscription_id, error)
        if on_status is not None:
            on_status(f"ws_error:{str(error)[:120]}")

    def _on_close(_ws: Any, close_status_code: Any, close_msg: Any) -> None:
        _ws_log.info("ws closed subscription=%s code=%s msg=%s", subscription_id, close_status_code, close_msg)
        closed.set()

    def _on_open(_ws: Any) -> None:
        _ws_log.info("ws connected subscription=%s", subscription_id)
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

    while stop_event is None or not stop_event.is_set():
        if is_paused is not None and is_paused():
            if on_status is not None:
                on_status("paused")
            time.sleep(1.0)
            continue

        subscription_id, wss_uri = get_active_subscription()
        if not subscription_id or not wss_uri:
            if on_status is not None:
                on_status("no_subscription")
            _ws_wake_event.wait(timeout=2.0)
            _ws_wake_event.clear()
            continue

        try:
            if not _wait_for_shared_token(client, timeout_s=120.0):
                if on_status is not None:
                    on_status("waiting_token")
                time.sleep(2.0)
                continue

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
                if on_status is not None:
                    on_status("waiting_token")
                time.sleep(2.0)
                continue
            _ws_log.exception("alarm ws session failed: %s", exc)
            if on_status is not None:
                on_status(f"error:{str(exc)[:120]}")
        except Exception as exc:
            _ws_log.exception("alarm ws session failed: %s", exc)
            if on_status is not None:
                on_status(f"error:{str(exc)[:120]}")

        sub_id_after, uri_after = get_active_subscription()
        if stop_event is not None and stop_event.is_set():
            break
        if not sub_id_after or not uri_after:
            if on_status is not None:
                on_status("no_subscription")
            continue

        if on_status is not None:
            on_status(f"reconnect_ws_in_{int(backoff_s)}s")
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
            if on_status is not None:
                on_status("disabled:no_base_url")
            return
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
