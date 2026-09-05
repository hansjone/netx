"""DSH / netxops key-alarm subscribe hub (netx is the fixed-IP WebSocket server).

Clients (netxops on each DSH host) dial out to:
  ws[s]://<netx-host>:<port>/v1/integrations/dsh-alarm/ws
authenticate with the same API token used for REST, then receive `netx.alarm` events.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .auth_deps import resolve_user_from_token
from .auth_scopes import SCOPE_ALARMS_READ, has_scope
from .config import settings
from .db import SessionLocal

_log = logging.getLogger("netx.dsh.alarm_hub")

_LOCK = threading.Lock()
_CLIENTS: set[WebSocket] = set()
_LOOP: asyncio.AbstractEventLoop | None = None
_STATS = {
    "published": 0,
    "deliver_ok": 0,
    "deliver_fail": 0,
    "subscribers": 0,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bind_event_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Remember the API process loop so sync publishers can schedule sends."""
    global _LOOP
    _LOOP = loop


def subscriber_count() -> int:
    with _LOCK:
        return len(_CLIENTS)


def hub_status() -> dict[str, Any]:
    with _LOCK:
        clients = len(_CLIENTS)
        stats = dict(_STATS)
    stats["subscribers"] = clients
    return {
        "enabled": True,
        "path": "/v1/integrations/dsh-alarm/ws",
        "subscribers": clients,
        "published": int(stats.get("published") or 0),
        "deliver_ok": int(stats.get("deliver_ok") or 0),
        "deliver_fail": int(stats.get("deliver_fail") or 0),
    }


def _authorize_token(token: str) -> tuple[bool, str]:
    raw = str(token or "").strip()
    if not bool(settings.auth_enabled):
        return True, "auth_disabled"
    if not raw:
        return False, "token_required"
    db = SessionLocal()
    try:
        hit = resolve_user_from_token(db, raw)
        if hit is None:
            return False, "invalid_token"
        user, _via, scopes, _token_id, _jti = hit
        if has_scope(scopes, SCOPE_ALARMS_READ) or str(user.role or "").lower() == "admin":
            return True, str(user.username or user.id or "user")
        return False, "missing_alarms_read_scope"
    except Exception as exc:  # noqa: BLE001
        _log.warning("dsh alarm hub auth error: %s", exc)
        return False, "auth_error"
    finally:
        db.close()


async def _send_json(ws: WebSocket, payload: dict[str, Any]) -> bool:
    if ws.client_state != WebSocketState.CONNECTED:
        return False
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False


async def _broadcast(payload: dict[str, Any]) -> int:
    with _LOCK:
        clients = list(_CLIENTS)
    if not clients:
        return 0
    envelope = {
        "type": "event",
        "event": "netx.alarm",
        "ts": _utc_now_iso(),
        "payload": payload,
    }
    dead: list[WebSocket] = []
    ok = 0
    for ws in clients:
        if await _send_json(ws, envelope):
            ok += 1
        else:
            dead.append(ws)
    if dead:
        with _LOCK:
            for ws in dead:
                _CLIENTS.discard(ws)
            _STATS["subscribers"] = len(_CLIENTS)
            _STATS["deliver_fail"] += len(dead)
        for ws in dead:
            try:
                await ws.close()
            except Exception:
                pass
    with _LOCK:
        _STATS["deliver_ok"] += ok
    return ok


def publish_alarm(payload: dict[str, Any]) -> int:
    """Fan-out a matched key-alert payload to all connected DSH subscribers.

    Safe to call from sync UME/alarm threads. Returns the number of clients that
    accepted the message (0 when nobody is subscribed).
    """
    if not isinstance(payload, dict):
        return 0
    with _LOCK:
        _STATS["published"] += 1
        clients = len(_CLIENTS)
        loop = _LOOP
    if clients <= 0:
        return 0
    if loop is None or not loop.is_running():
        _log.warning("dsh alarm hub has subscribers but no running event loop")
        return 0

    future = asyncio.run_coroutine_threadsafe(_broadcast(dict(payload)), loop)
    try:
        return int(future.result(timeout=5))
    except Exception as exc:  # noqa: BLE001
        _log.warning("dsh alarm hub publish failed: %s", exc)
        return 0


async def dsh_alarm_ws_loop(websocket: WebSocket) -> None:
    """Accept one netxops subscriber: auth → ping/pong → receive netx.alarm pushes."""
    await websocket.accept()
    bind_event_loop(asyncio.get_running_loop())
    authed = False
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                import json

                msg = json.loads(raw)
            except Exception:
                await _send_json(websocket, {"type": "error", "error": "invalid_json"})
                continue
            if not isinstance(msg, dict):
                await _send_json(websocket, {"type": "error", "error": "invalid_message"})
                continue
            mtype = str(msg.get("type") or "").strip().lower()
            if not authed:
                if mtype != "auth":
                    await _send_json(websocket, {"type": "auth-fail", "error": "auth_required"})
                    await websocket.close(code=4401)
                    return
                ok, detail = _authorize_token(str(msg.get("token") or ""))
                if not ok:
                    await _send_json(websocket, {"type": "auth-fail", "error": detail})
                    await websocket.close(code=4401)
                    return
                authed = True
                with _LOCK:
                    _CLIENTS.add(websocket)
                    _STATS["subscribers"] = len(_CLIENTS)
                await _send_json(
                    websocket,
                    {
                        "type": "auth-ok",
                        "user": detail,
                        "ts": _utc_now_iso(),
                    },
                )
                _log.info("dsh alarm hub subscriber connected (%s)", detail)
                continue
            if mtype == "ping":
                await _send_json(websocket, {"type": "pong", "ts": _utc_now_iso()})
                continue
            await _send_json(websocket, {"type": "error", "error": f"unknown_type:{mtype}"})
    except WebSocketDisconnect:
        return
    finally:
        with _LOCK:
            _CLIENTS.discard(websocket)
            _STATS["subscribers"] = len(_CLIENTS)
        _log.info("dsh alarm hub subscriber disconnected")
