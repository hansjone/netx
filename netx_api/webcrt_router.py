"""WebCRT HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .db import SessionLocal, get_db
from .auth_deps import resolve_user_from_token
from .config import settings
from .webcrt_service import (
    close_session,
    create_session,
    detach_session,
    get_session,
    list_sessions,
    mark_attached,
)

_log = logging.getLogger("netx.webcrt.router")

router = APIRouter(prefix="/v1/webcrt", tags=["webcrt"])


class WebcrtSessionCreate(BaseModel):
    ne_id: str | None = Field(default=None)
    ume_ne_id: str | None = Field(default=None)
    cols: int = Field(default=80, ge=20, le=500)
    rows: int = Field(default=24, ge=5, le=200)


def _client_label(request: Request | None = None, websocket: WebSocket | None = None) -> str:
    host = ""
    if request is not None:
        host = request.client.host if request.client else ""
    elif websocket is not None:
        host = websocket.client.host if websocket.client else ""
    return str(host or "")


@router.get("/sessions")
def api_list_sessions() -> dict[str, Any]:
    return list_sessions()


@router.post("/sessions")
def api_create_session(
    body: WebcrtSessionCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    mid = str(body.ne_id or "").strip()
    uid = str(body.ume_ne_id or "").strip()
    if bool(mid) == bool(uid):
        raise HTTPException(status_code=400, detail="exactly_one_of_ne_id_or_ume_ne_id_required")
    return create_session(
        db,
        ne_id=mid or None,
        ume_ne_id=uid or None,
        cols=body.cols,
        rows=body.rows,
        client=_client_label(request=request),
    )


@router.delete("/sessions/{session_id}")
def api_close_session(session_id: str, request: Request) -> dict[str, Any]:
    return close_session(session_id, reason="client_delete", client=_client_label(request=request))


@router.websocket("/sessions/{session_id}/ws")
async def websocket_session(websocket: WebSocket, session_id: str) -> None:
    if bool(settings.auth_enabled):
        token = str(websocket.query_params.get("access_token") or "").strip()
        if not token:
            auth = str(websocket.headers.get("authorization") or "").strip()
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
        db = SessionLocal()
        try:
            resolved = resolve_user_from_token(db, token) if token else None
        finally:
            db.close()
        if resolved is None:
            await websocket.close(code=4401)
            return
        websocket.state.auth_user = resolved[0]
        websocket.state.auth_via = resolved[1]

    await websocket.accept()
    attach_gen = 0
    try:
        sess, attach_gen = mark_attached(session_id)
    except HTTPException as exc:
        await websocket.send_json({"type": "status", "state": "error", "message": str(exc.detail)})
        await websocket.close(code=4404 if exc.status_code == 404 else 4409)
        return

    await websocket.send_json(
        {
            "type": "status",
            "state": "connected",
            "session_id": sess.session_id,
            "ne_id": sess.ne_id,
            "ne_name": sess.ne_name,
            "ne_ip": sess.ne_ip,
            "protocol": sess.protocol,
            "cols": sess.cols,
            "rows": sess.rows,
            "device_type": sess.device_type,
            "vendor": sess.vendor,
            "cli_hop": bool(sess.cli_hop_guard),
        }
    )

    # Replay full login transcript (kept for StrictMode remount / brief reconnect).
    bootstrap = bytes(sess.bootstrap_output or b"")
    if bootstrap:
        try:
            await websocket.send_json(
                {"type": "stdout", "data": bootstrap.decode("utf-8", errors="replace")}
            )
        except Exception:
            _log.debug("webcrt bootstrap send failed session=%s", session_id, exc_info=True)

    stop = asyncio.Event()

    async def pump_stdout() -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            chunk = await loop.run_in_executor(
                None, lambda: sess.take_stdout(attach_gen, timeout=0.25)
            )
            if chunk == "stale":
                # Newer WS owns the session (StrictMode remount); exit without stealing bytes.
                break
            if chunk == "empty":
                continue
            if chunk is None:
                stop.set()
                try:
                    await websocket.send_json(
                        {
                            "type": "status",
                            "state": "closed",
                            "message": sess.close_reason or "device_closed",
                        }
                    )
                except Exception:
                    pass
                break
            try:
                text = chunk.decode("utf-8", errors="replace")
                await websocket.send_json({"type": "stdout", "data": text})
            except Exception:
                stop.set()
                break

    reader_task = asyncio.create_task(pump_stdout())
    if sess.needs_live_prompt:
        sess.needs_live_prompt = False
        try:
            await asyncio.get_running_loop().run_in_executor(None, sess.write_stdin, "\r")
        except Exception:
            _log.debug("webcrt live prompt sync failed session=%s", session_id, exc_info=True)
    try:
        while not stop.is_set():
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                # Treat plain text as stdin.
                msg = {"type": "stdin", "data": raw}
            mtype = str(msg.get("type") or "").strip().lower()
            if mtype == "stdin":
                data = msg.get("data")
                if data is None:
                    continue
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        None, sess.write_stdin, str(data)
                    )
                except Exception as exc:
                    await websocket.send_json(
                        {"type": "status", "state": "error", "message": f"write_failed:{exc}"}
                    )
                    break
            elif mtype == "resize":
                cols = int(msg.get("cols") or sess.cols)
                rows = int(msg.get("rows") or sess.rows)
                await asyncio.get_running_loop().run_in_executor(None, sess.resize, cols, rows)
            elif mtype == "ping":
                sess.touch()
                await websocket.send_json({"type": "pong"})
            elif mtype == "close":
                stop.set()
                close_session(
                    session_id,
                    reason="client_close",
                    client=_client_label(websocket=websocket),
                )
                break
    except WebSocketDisconnect:
        _log.info("webcrt ws disconnected session=%s gen=%s", session_id, attach_gen)
    except Exception:
        _log.exception("webcrt ws error session=%s", session_id)
    finally:
        stop.set()
        reader_task.cancel()
        try:
            await reader_task
        except Exception:
            pass
        # Keep device session briefly so React remount / blip can re-attach.
        # Only the current attach_gen may detach — older StrictMode sockets must not.
        if get_session(session_id) is not None:
            detach_session(
                session_id,
                grace_sec=8.0,
                client=_client_label(websocket=websocket),
                attach_gen=attach_gen,
            )
