"""UME alarm WSS consumer session and background loop."""
from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from typing import Any, Callable

from . import ume_alarm_ws_state as ws_state
from .config import settings
from .db import SessionLocal
from .ume_alarm_ws_log import (
    append_ws_log,
    is_startup_alarm_sync_pending,
    is_ume_subscription_missing_error,
)
from .ume_alarm_ws_subscription import (
    _notify_ws_connection,
    _parse_ws_message,
    _wait_for_shared_token,
    clear_ume_subscription_lost_flag,
    get_active_subscription,
    is_ume_subscription_lost,
    mark_ume_subscription_lost,
    process_alarm_notification,
)
from .ume_client import UMEClient
from .ume_sync_service import (
    _alarm_key,
    extract_alarm_from_notification,
    normalize_yang_alarm,
)

_ws_log = logging.getLogger("netx.ume.alarm_ws")


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
                dedup_cooldown_s=ws_state._WS_IGNORE_COOLDOWN_S,
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
                    dedup_cooldown_s=ws_state._WS_IGNORE_COOLDOWN_S,
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
            label = ws_state._WS_ALARM_ACTION_LABEL.get(action, action)
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
        if ws_state._ws_wake_event.is_set():
            try:
                ws_app.close()
            except Exception:
                pass
            break
        time.sleep(0.5)

    if thread.is_alive():
        thread.join(timeout=5.0)
    ws_state._ws_wake_event.clear()


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
            log_cooldown_s=ws_state._LOOP_STATUS_COOLDOWN_S,
        )

    while stop_event is None or not stop_event.is_set():
        if is_paused is not None and is_paused():
            _loop_status("paused", log_level="warning")
            time.sleep(1.0)
            continue

        if not ws_state._STARTUP_ALARM_SYNC_GATE.is_set():
            _loop_status("waiting_startup_alarm_sync", log_level="info")
            ws_state._STARTUP_ALARM_SYNC_GATE.wait(timeout=1.0)
            continue

        if is_ume_subscription_lost():
            with ws_state._ume_lost_lock:
                lost_detail = str(ws_state._ume_subscription_lost_reason or "")
            _loop_status(
                "subscription_lost",
                detail=lost_detail or "UME subscription missing on server",
                log_level="warning",
            )
            ws_state._ws_wake_event.wait(timeout=5.0)
            ws_state._ws_wake_event.clear()
            continue

        subscription_id, wss_uri = get_active_subscription()
        if not subscription_id or not wss_uri:
            _loop_status("no_subscription")
            ws_state._ws_wake_event.wait(timeout=2.0)
            ws_state._ws_wake_event.clear()
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
            if ws_state._ws_wake_event.is_set():
                ws_state._ws_wake_event.clear()
                break
            time.sleep(0.5)
            slept += 0.5
        backoff_s = min(max_backoff_s, backoff_s * 2.0)


def shutdown_ws_consumer() -> None:
    """Process exit: close WSS only; subscription remains on UME until user cancels."""
    request_ws_reconnect()
    ws_state._ws_wake_event.set()


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
                on_status("ws:disabled_no_base_url")
            return
        append_ws_log("ws consumer thread started")
        with ws_state._shutdown_lock:
            ws_state._active_client = client
        run_alarm_ws_consumer_loop(
            client,
            on_status=on_status,
            stop_event=ev,
            is_paused=is_paused,
        )

    thread = threading.Thread(target=_target, name="ume-alarm-ws-consumer", daemon=True)
    thread.start()
    return thread

