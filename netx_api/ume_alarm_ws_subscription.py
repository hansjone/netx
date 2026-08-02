"""UME alarm WSS subscription state APIs and manual control."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

from sqlalchemy.orm import Session

from . import ume_alarm_ws_state as ws_state
from .config import settings
from .ume_alarm_subscription_store import (
    DEFAULT_SUBSCRIPTION_KEY,
    clear_subscription,
    load_subscription,
    save_subscription,
)
from .ume_alarm_ws_log import (
    append_ws_log,
    is_ume_subscription_missing_error,
    parse_subscription_id_from_already_exists_error,
)
from .ume_client import UMEClient
from .ume_sync_service import (
    apply_alarm_to_current,
    extract_alarm_from_notification,
    normalize_yang_alarm,
    _utc_now_naive,
)

_ws_log = logging.getLogger("netx.ume.alarm_ws")


def mark_ume_subscription_lost(reason: str) -> None:
    detail = str(reason or "").strip()[:500]
    with ws_state._ume_lost_lock:
        if ws_state._ume_subscription_lost and detail == ws_state._ume_subscription_lost_reason:
            return
        ws_state._ume_subscription_lost = True
        ws_state._ume_subscription_lost_reason = detail
    append_ws_log(
        f"UME 侧订阅已丢失: {detail[:200]}",
        level="warning",
        dedup_cooldown_s=ws_state._LOOP_STATUS_COOLDOWN_S,
    )
    request_ws_reconnect()


def clear_ume_subscription_lost_flag() -> None:
    with ws_state._ume_lost_lock:
        ws_state._ume_subscription_lost = False
        ws_state._ume_subscription_lost_reason = ""


def is_ume_subscription_lost() -> bool:
    with ws_state._ume_lost_lock:
        return bool(ws_state._ume_subscription_lost)


def _server_subscription_lost_fields() -> dict[str, Any]:
    with ws_state._ume_lost_lock:
        lost = bool(ws_state._ume_subscription_lost)
        reason = str(ws_state._ume_subscription_lost_reason or "")
    return {
        "server_subscription_lost": lost,
        "server_subscription_lost_reason": reason,
    }


def _set_ws_connection_state(state: str, *, detail: str = "") -> None:
    with ws_state._ws_connection_lock:
        ws_state._ws_connection_state = str(state or "").strip() or "unknown"
        ws_state._ws_connection_detail = str(detail or "").strip()[:240]


def get_ws_connection_status() -> dict[str, Any]:
    with ws_state._ws_connection_lock:
        state = str(ws_state._ws_connection_state or "init")
        detail = str(ws_state._ws_connection_detail or "")
    return {
        "state": state,
        "label": ws_state._WS_CONNECTION_LABELS.get(state, state),
        "detail": detail,
    }


def _notify_ws_connection(
    state: str,
    *,
    detail: str = "",
    subscription_id: str = "",
    on_status: Callable[[str], None] | None = None,
    log_level: str = "info",
    log_cooldown_s: float = ws_state._WS_NOTIFY_COOLDOWN_S,
) -> None:
    _set_ws_connection_state(state, detail=detail)
    label = ws_state._WS_CONNECTION_LABELS.get(state, state)
    log_msg = str(detail or label).strip()
    notify_key = f"{state}\0{log_msg}\0{subscription_id}"
    now = time.monotonic()
    should_log = True
    with ws_state._WS_NOTIFY_LOCK:
        if notify_key == ws_state._WS_NOTIFY_LAST_KEY and now - ws_state._WS_NOTIFY_LAST_TS < max(1.0, float(log_cooldown_s)):
            should_log = False
        else:
            ws_state._WS_NOTIFY_LAST_KEY = notify_key
            ws_state._WS_NOTIFY_LAST_TS = now
    if should_log:
        append_ws_log(
            log_msg,
            level=log_level,
            subscription_id=subscription_id,
            dedup=False,
        )
    if on_status is not None:
        on_status(f"ws:{state}")


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
    with ws_state._subscription_lock:
        ws_state._subscription_id = str(subscription_id or "").strip()
        ws_state._subscription_uri = str(wss_uri or "").strip()
        ws_state._subscription_topic = str(topic or "ALARM").strip() or "ALARM"


def _clear_active_subscription() -> None:
    with ws_state._subscription_lock:
        ws_state._subscription_id = ""
        ws_state._subscription_uri = ""
        ws_state._subscription_topic = "ALARM"


def get_active_subscription() -> tuple[str, str]:
    with ws_state._subscription_lock:
        return str(ws_state._subscription_id or ""), str(ws_state._subscription_uri or "")


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
    with ws_state._subscription_lock:
        sub_id = str(ws_state._subscription_id or "")
        uri = str(ws_state._subscription_uri or "")
        topic = str(ws_state._subscription_topic or "ALARM")
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
    ws_state._ws_wake_event.set()


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
        status_out = get_subscription_status()
        return {**status_out, "already_exists": True}

    mem_id, mem_uri = get_active_subscription()
    if mem_id and mem_uri:
        request_ws_reconnect()
        append_ws_log(f"establish skipped: in-memory id={mem_id}", subscription_id=mem_id)
        status_out = get_subscription_status()
        return {**status_out, "already_exists": True}

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

    with ws_state._shutdown_lock:
        ws_state._active_client = client
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
    status_out = get_subscription_status()
    return {
        **status_out,
        "ok": True,
        "cleared_local": True,
        "ume_already_missing": ume_already_missing,
    }


