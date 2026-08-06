from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import websocket

from .config import settings
from .runtime_task_messages import fwd_state_code

_log = logging.getLogger("netx.oclaw.alarm_forwarder")


def _normalize_bridge_error(exc: BaseException | str) -> str:
    """Map low-level WS errors to stable fwd:* codes for UI i18n."""
    raw = str(exc or "").strip()
    low = raw.lower()
    name = type(exc).__name__ if isinstance(exc, BaseException) else ""
    if (
        "10061" in raw
        or "actively refused" in low
        or "积极拒绝" in raw
        or "connection refused" in low
        or name == "ConnectionRefusedError"
    ):
        return fwd_state_code("connect_refused")
    if "timed out" in low or "timeout" in low or name in {"TimeoutError", "socket.timeout"}:
        return fwd_state_code("connect_timeout")
    if "auth failed" in low or "auth-fail" in low or "invalid_token" in low:
        return fwd_state_code("auth_failed")
    if raw:
        return raw[:200]
    return fwd_state_code("disconnected")


_OUTBOUND_Q: "queue.Queue[dict[str, Any]] | None" = None
_Q_LOCK = threading.Lock()
_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_CONN_LOCK = threading.Lock()
_WS: Any | None = None
_CONNECTED = threading.Event()
_IS_PAUSED: Callable[[], bool] | None = None
_ON_STATUS: Callable[[str], None] | None = None
_STATS_LOCK = threading.Lock()
_STATS: dict[str, int] = {
    "published_ok": 0,
    "published_fail": 0,
    "queued": 0,
    "dropped": 0,
    "requeued": 0,
    "retry_exhausted": 0,
}


def _outbound_q() -> "queue.Queue[dict[str, Any]]":
    global _OUTBOUND_Q
    with _Q_LOCK:
        if _OUTBOUND_Q is None:
            maxsize = max(100, int(getattr(settings, "oclaw_forward_queue_max", 2000) or 2000))
            _OUTBOUND_Q = queue.Queue(maxsize=maxsize)
        return _OUTBOUND_Q


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bridge_token() -> str:
    return str(getattr(settings, "oclaw_analyze_token", "") or "").strip()


def _bridge_url() -> str:
    return str(getattr(settings, "oclaw_alarm_ws_url", "") or "").strip()


def is_forwarder_enabled() -> bool:
    if not bool(getattr(settings, "oclaw_alarm_ws_enabled", False)):
        return False
    return bool(_bridge_url()) and bool(_bridge_token())


def configure_oclaw_alarm_forwarder(
    *,
    is_paused: Callable[[], bool] | None = None,
    on_status: Callable[[str], None] | None = None,
) -> None:
    global _IS_PAUSED, _ON_STATUS
    _IS_PAUSED = is_paused
    _ON_STATUS = on_status


def _forwarder_paused() -> bool:
    if _IS_PAUSED is None:
        return False
    try:
        return bool(_IS_PAUSED())
    except Exception:
        return False


def is_forwarder_operational() -> bool:
    return is_forwarder_enabled() and not _forwarder_paused()


def _notify_status(msg: str) -> None:
    if _ON_STATUS is None:
        return
    try:
        _ON_STATUS(str(msg or "")[:240])
    except Exception:
        pass


def request_forwarder_reconnect() -> None:
    with _CONN_LOCK:
        ws = _WS
    if ws is not None:
        try:
            ws.close()
        except Exception:
            pass


def enqueue_alarm_forward(payload: dict[str, Any]) -> bool:
    if not is_forwarder_operational():
        return False
    try:
        _outbound_q().put_nowait(dict(payload))
        with _STATS_LOCK:
            _STATS["queued"] = int(_STATS.get("queued", 0)) + 1
        return True
    except queue.Full:
        with _STATS_LOCK:
            _STATS["dropped"] = int(_STATS.get("dropped", 0)) + 1
        _log.warning("oclaw alarm forward queue full; dropping alarm_key=%s", payload.get("alarm_key"))
        return False


def _requeue_or_drop(payload: dict[str, Any], *, reason: str) -> None:
    max_retries = max(0, int(getattr(settings, "oclaw_forward_max_retries", 3) or 3))
    item = dict(payload)
    attempts = int(item.get("_fwd_attempts") or 0) + 1
    item["_fwd_attempts"] = attempts
    if attempts > max_retries:
        with _STATS_LOCK:
            _STATS["retry_exhausted"] = int(_STATS.get("retry_exhausted", 0)) + 1
            _STATS["dropped"] = int(_STATS.get("dropped", 0)) + 1
        _log.warning(
            "oclaw forward drop after retries alarm_key=%s attempts=%s reason=%s",
            item.get("alarm_key"),
            attempts,
            reason[:80],
        )
        return
    try:
        _outbound_q().put_nowait(item)
        with _STATS_LOCK:
            _STATS["requeued"] = int(_STATS.get("requeued", 0)) + 1
    except queue.Full:
        with _STATS_LOCK:
            _STATS["dropped"] = int(_STATS.get("dropped", 0)) + 1
        _log.warning(
            "oclaw forward requeue full; dropping alarm_key=%s",
            item.get("alarm_key"),
        )


def _send_auth(ws: Any) -> bool:
    token = _bridge_token()
    ws.send(json.dumps({"type": "auth", "token": token}, ensure_ascii=False))
    deadline = time.time() + 10.0
    while time.time() < deadline:
        raw = ws.recv()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        if str(msg.get("type") or "").strip() == "auth-ok":
            return True
        if str(msg.get("type") or "").strip() == "auth-fail":
            return False
    return False


def _dispatch_one(ws: Any, payload: dict[str, Any]) -> dict[str, Any]:
    envelope = {
        "type": "event",
        "event": "netx.alarm",
        "payload": payload,
    }
    ws.send(json.dumps(envelope, ensure_ascii=False, default=str))
    deadline = time.time() + 15.0
    alarm_key = str(payload.get("alarm_key") or "")
    while time.time() < deadline:
        raw = ws.recv()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        if not isinstance(msg, dict):
            continue
        if str(msg.get("type") or "").strip() == "pong":
            continue
        if str(msg.get("type") or "").strip() == "ack":
            if alarm_key and str(msg.get("alarm_key") or "").strip() not in {"", alarm_key}:
                continue
            return msg
    return {"type": "ack", "alarm_key": alarm_key, "ok": False, "error": "ack_timeout"}


def _run_loop() -> None:
    global _WS
    backoff_s = 2.0
    while not _STOP_EVENT.is_set():
        if not is_forwarder_enabled() or _forwarder_paused():
            _CONNECTED.clear()
            if _forwarder_paused():
                _notify_status("fwd:paused")
            else:
                _notify_status("fwd:disabled")
            time.sleep(2.0)
            continue
        url = _bridge_url()
        ws = None
        try:
            _notify_status("fwd:connecting")
            ws = websocket.create_connection(url, timeout=20)
            ws.settimeout(30)
            if not _send_auth(ws):
                raise RuntimeError("oclaw netx-bridge auth failed")
            with _CONN_LOCK:
                _WS = ws
            _CONNECTED.set()
            backoff_s = 2.0
            _log.info("oclaw netx-bridge connected url=%s", url[:120])
            _notify_status("fwd:connected")
            while not _STOP_EVENT.is_set():
                if _forwarder_paused():
                    _notify_status("fwd:paused")
                    raise RuntimeError("forwarder paused")
                if not is_forwarder_enabled():
                    _notify_status("fwd:disabled")
                    raise RuntimeError("forwarder disabled")
                try:
                    payload = _outbound_q().get(timeout=1.0)
                except queue.Empty:
                    try:
                        ws.send(json.dumps({"type": "ping", "ts": _utc_now_iso()}, ensure_ascii=False))
                    except Exception:
                        raise
                    continue
                try:
                    ack = _dispatch_one(ws, payload)
                    ok = bool(ack.get("ok"))
                    err = str(ack.get("error") or "")[:240]
                    with _STATS_LOCK:
                        if ok:
                            _STATS["published_ok"] = int(_STATS.get("published_ok", 0)) + 1
                        else:
                            _STATS["published_fail"] = int(_STATS.get("published_fail", 0)) + 1
                    try:
                        from .key_alert_forward import record_forward_result

                        record_forward_result(
                            alarm_key=str(payload.get("alarm_key") or ""),
                            action=str(payload.get("action") or ""),
                            ok=ok,
                            error=err,
                            rule_key=str(payload.get("rule_key") or ""),
                        )
                    except Exception as rec_exc:
                        _log.warning("forward result record failed: %s", str(rec_exc)[:120])
                    if not ok:
                        _log.warning(
                            "oclaw ack failed alarm_key=%s error=%s",
                            payload.get("alarm_key"),
                            str(ack.get("error") or "")[:120],
                        )
                        # Soft ack failure: do not infinite-requeue; count as fail only.
                except Exception as exc:
                    _log.warning("oclaw forward failed alarm_key=%s err=%s", payload.get("alarm_key"), str(exc)[:120])
                    _requeue_or_drop(payload, reason=str(exc)[:120])
                    raise
                finally:
                    _outbound_q().task_done()
        except Exception as exc:
            _CONNECTED.clear()
            err = _normalize_bridge_error(exc)
            _log.warning("oclaw netx-bridge disconnected: %s", str(exc)[:200])
            _notify_status(err)
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 1.5, 60.0)
        finally:
            with _CONN_LOCK:
                _WS = None
            _CONNECTED.clear()
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass


def start_oclaw_alarm_forwarder() -> threading.Thread | None:
    global _THREAD
    if _THREAD is not None and _THREAD.is_alive():
        return _THREAD
    _STOP_EVENT.clear()
    _THREAD = threading.Thread(target=_run_loop, name="oclaw-alarm-forwarder", daemon=True)
    _THREAD.start()
    return _THREAD


def shutdown_oclaw_alarm_forwarder() -> None:
    _STOP_EVENT.set()


def forwarder_status() -> dict[str, Any]:
    with _STATS_LOCK:
        stats = dict(_STATS)
    paused = _forwarder_paused()
    enabled = is_forwarder_enabled()
    return {
        "enabled": enabled,
        "operational": bool(enabled and not paused),
        "paused": paused,
        "connected": _CONNECTED.is_set(),
        "queue_size": int(_outbound_q().qsize()),
        "queue_max": max(100, int(getattr(settings, "oclaw_forward_queue_max", 2000) or 2000)),
        "url": _bridge_url(),
        "published_ok": int(stats.get("published_ok", 0)),
        "published_fail": int(stats.get("published_fail", 0)),
        "queued_total": int(stats.get("queued", 0)),
        "dropped": int(stats.get("dropped", 0)),
        "requeued": int(stats.get("requeued", 0)),
        "retry_exhausted": int(stats.get("retry_exhausted", 0)),
    }
