from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any

import websocket

from .config import settings

_log = logging.getLogger("netx.oclaw.alarm_forwarder")

_OUTBOUND_Q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=5000)
_STOP_EVENT = threading.Event()
_THREAD: threading.Thread | None = None
_CONN_LOCK = threading.Lock()
_WS: Any | None = None
_CONNECTED = threading.Event()
_STATS_LOCK = threading.Lock()
_STATS: dict[str, int] = {
    "published_ok": 0,
    "published_fail": 0,
    "queued": 0,
}


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


def enqueue_alarm_forward(payload: dict[str, Any]) -> bool:
    if not is_forwarder_enabled():
        return False
    try:
        _OUTBOUND_Q.put_nowait(dict(payload))
        with _STATS_LOCK:
            _STATS["queued"] = int(_STATS.get("queued", 0)) + 1
        return True
    except queue.Full:
        _log.warning("oclaw alarm forward queue full; dropping alarm_key=%s", payload.get("alarm_key"))
        return False


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
        if not is_forwarder_enabled():
            time.sleep(2.0)
            continue
        url = _bridge_url()
        ws = None
        try:
            ws = websocket.create_connection(url, timeout=20)
            ws.settimeout(30)
            if not _send_auth(ws):
                raise RuntimeError("oclaw netx-bridge auth failed")
            with _CONN_LOCK:
                _WS = ws
            _CONNECTED.set()
            backoff_s = 2.0
            _log.info("oclaw netx-bridge connected url=%s", url[:120])
            while not _STOP_EVENT.is_set():
                try:
                    payload = _OUTBOUND_Q.get(timeout=1.0)
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
                except Exception as exc:
                    _log.warning("oclaw forward failed alarm_key=%s err=%s", payload.get("alarm_key"), str(exc)[:120])
                    try:
                        _OUTBOUND_Q.put_nowait(payload)
                    except queue.Full:
                        pass
                    raise
                finally:
                    _OUTBOUND_Q.task_done()
        except Exception as exc:
            _CONNECTED.clear()
            _log.warning("oclaw netx-bridge disconnected: %s", str(exc)[:200])
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
    if not is_forwarder_enabled():
        _log.info("oclaw alarm forwarder disabled")
        return None
    _STOP_EVENT.clear()
    _THREAD = threading.Thread(target=_run_loop, name="oclaw-alarm-forwarder", daemon=True)
    _THREAD.start()
    return _THREAD


def shutdown_oclaw_alarm_forwarder() -> None:
    _STOP_EVENT.set()


def forwarder_status() -> dict[str, Any]:
    with _STATS_LOCK:
        stats = dict(_STATS)
    return {
        "enabled": is_forwarder_enabled(),
        "connected": _CONNECTED.is_set(),
        "queue_size": int(_OUTBOUND_Q.qsize()),
        "url": _bridge_url(),
        "published_ok": int(stats.get("published_ok", 0)),
        "published_fail": int(stats.get("published_fail", 0)),
        "queued_total": int(stats.get("queued", 0)),
    }
