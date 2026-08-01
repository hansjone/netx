"""WebCRT HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
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
    wait_session_ready,
    _decode_bytes,
    _normalize_encoding,
)

_log = logging.getLogger("netx.webcrt.router")

router = APIRouter(prefix="/v1/webcrt", tags=["webcrt"])


class WebcrtSessionCreate(BaseModel):
    ne_id: str | None = Field(default=None)
    ume_ne_id: str | None = Field(default=None)
    cols: int = Field(default=80, ge=20, le=500)
    rows: int = Field(default=24, ge=5, le=200)
    encoding: str = Field(default="utf-8")
    # SSH transport keepalive interval (seconds). None = server default; 0 = off.
    keepalive_sec: int | None = Field(default=None, ge=0, le=600)
    post_login_commands: list[str] = Field(default_factory=list)
    # Default async so UI can open WS while connect runs; tests may force sync via service API.
    async_connect: bool = Field(default=True)
    # One-shot credentials (not written to DB).
    username: str | None = None
    password: str | None = None


class WebcrtQuickConnectBody(BaseModel):
    """SecureCRT-style: upsert session host then open a session."""

    name: str = ""
    ip_address: str
    port: int = 22
    protocol: str = "ssh"
    username: str = ""
    password: str = ""
    save_password: bool = False
    cols: int = Field(default=80, ge=20, le=500)
    rows: int = Field(default=24, ge=5, le=200)
    encoding: str = Field(default="utf-8")
    keepalive_sec: int | None = Field(default=None, ge=0, le=600)
    post_login_commands: list[str] = Field(default_factory=list)
    async_connect: bool = Field(default=True)


class WebcrtSftpListBody(BaseModel):
    ne_id: str | None = Field(default=None)
    ume_ne_id: str | None = Field(default=None)
    path: str = Field(default=".")


class WebcrtSftpDownloadBody(BaseModel):
    ne_id: str | None = Field(default=None)
    ume_ne_id: str | None = Field(default=None)
    path: str


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


@router.get("/meta/device-types")
def api_webcrt_device_types() -> dict[str, Any]:
    from .device_types import SUPPORTED_VENDORS, WEBCRT_DEVICE_TYPES

    return {"device_types": list(WEBCRT_DEVICE_TYPES), "vendors": list(SUPPORTED_VENDORS)}


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
        encoding=body.encoding,
        keepalive_sec=body.keepalive_sec,
        post_login_commands=list(body.post_login_commands or [])[:20],
        async_connect=bool(body.async_connect),
        username_override=body.username,
        password_override=body.password,
    )


@router.post("/sessions/quick-connect")
def api_quick_connect(
    body: WebcrtQuickConnectBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from .ne_service import upsert_webcrt_session_host

    proto = str(body.protocol or "ssh").strip().lower()
    if proto not in ("ssh", "telnet"):
        raise HTTPException(status_code=400, detail="invalid_protocol")
    save_password = bool(body.save_password) and proto == "ssh"
    ne_out, action = upsert_webcrt_session_host(
        db,
        name=body.name,
        ip_address=body.ip_address,
        port=body.port,
        protocol=proto,
        username=body.username,
        password=body.password,
        save_password=save_password,
    )
    # Pass SSH credentials as one-shot overrides (covers unsaved password + reused inventory).
    pwd_override: str | None = None
    user_override: str | None = None
    if proto == "ssh":
        user_override = str(body.username or "").strip() or None
        if str(body.password or "").strip():
            pwd_override = str(body.password)
    # SSH with password: wait for auth so wrong credentials can re-prompt (SecureCRT-like).
    wait_for_auth = proto == "ssh" and bool(pwd_override)
    async_connect = bool(body.async_connect) and not wait_for_auth
    try:
        session = create_session(
            db,
            ne_id=ne_out.id,
            cols=body.cols,
            rows=body.rows,
            client=_client_label(request=request),
            encoding=body.encoding,
            keepalive_sec=body.keepalive_sec,
            post_login_commands=list(body.post_login_commands or [])[:20],
            async_connect=async_connect,
            username_override=user_override,
            password_override=pwd_override,
        )
    except HTTPException as exc:
        # NE row already exists; return it so the UI retries in place (no duplicate hosts).
        if exc.status_code == 502 and proto == "ssh":
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "connect_failed",
                    "message": str(exc.detail or "connect_failed"),
                    "ne": ne_out.model_dump(mode="json"),
                    "ne_action": action,
                    "list_source": "webcrt",
                },
            ) from exc
        raise
    return {
        **session,
        "ne": ne_out.model_dump(mode="json"),
        "ne_action": action,
        "list_source": "webcrt",
    }


@router.delete("/sessions/{session_id}")
def api_close_session(session_id: str, request: Request) -> dict[str, Any]:
    return close_session(session_id, reason="client_delete", client=_client_label(request=request))


@router.post("/sftp/list")
def api_sftp_list(body: WebcrtSftpListBody, db: Session = Depends(get_db)) -> dict[str, Any]:
    from .webcrt_sftp import sftp_list

    mid = str(body.ne_id or "").strip()
    uid = str(body.ume_ne_id or "").strip()
    if bool(mid) == bool(uid):
        raise HTTPException(status_code=400, detail="exactly_one_of_ne_id_or_ume_ne_id_required")
    return sftp_list(db, managed_ne_id=mid or None, ume_ne_id=uid or None, path=body.path)


@router.post("/sftp/download")
def api_sftp_download(body: WebcrtSftpDownloadBody, db: Session = Depends(get_db)) -> Any:
    from fastapi.responses import Response

    from .webcrt_sftp import sftp_download

    mid = str(body.ne_id or "").strip()
    uid = str(body.ume_ne_id or "").strip()
    if bool(mid) == bool(uid):
        raise HTTPException(status_code=400, detail="exactly_one_of_ne_id_or_ume_ne_id_required")
    data, filename = sftp_download(db, managed_ne_id=mid or None, ume_ne_id=uid or None, path=body.path)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/sftp/upload")
async def api_sftp_upload(
    db: Session = Depends(get_db),
    ne_id: str | None = Form(default=None),
    ume_ne_id: str | None = Form(default=None),
    remote_path: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    from .webcrt_sftp import sftp_upload

    mid = str(ne_id or "").strip()
    uid = str(ume_ne_id or "").strip()
    if bool(mid) == bool(uid):
        raise HTTPException(status_code=400, detail="exactly_one_of_ne_id_or_ume_ne_id_required")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="sftp_file_too_large")
    return sftp_upload(
        db,
        managed_ne_id=mid or None,
        ume_ne_id=uid or None,
        remote_path=remote_path,
        data=content,
    )


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
            "state": "connecting" if sess.state == "connecting" else "connected",
            "phase": "authenticating" if sess.state == "connecting" else "ready",
            "message": "authenticating" if sess.state == "connecting" else "",
            "session_id": sess.session_id,
            "ne_id": sess.ne_id,
            "ne_name": sess.ne_name,
            "ne_ip": sess.ne_ip,
            "protocol": sess.protocol,
            "encoding": sess.encoding,
            "cols": sess.cols,
            "rows": sess.rows,
            "device_type": sess.device_type,
            "vendor": sess.vendor,
            "cli_hop": bool(sess.cli_hop_guard),
        }
    )

    # Wait for async connect without blocking the event loop; emit phase updates.
    if sess.state == "connecting":
        loop = asyncio.get_running_loop()
        budget = max(30, int(settings.webcrt_connect_timeout_sec or 90)) + 15
        deadline = time.time() + budget
        while True:
            cur = get_session(session_id) or sess
            if cur.state != "connecting":
                sess = cur
                break
            elapsed = max(0.0, time.time() - float(cur.connect_started_at or time.time()))
            phase = "authenticating" if elapsed < 6.0 else "waiting_prompt"
            try:
                await websocket.send_json(
                    {
                        "type": "status",
                        "state": "connecting",
                        "phase": phase,
                        "message": phase,
                        "elapsed_ms": int(elapsed * 1000),
                        "session_id": cur.session_id,
                    }
                )
            except Exception:
                break
            remaining = deadline - time.time()
            if remaining <= 0:
                await websocket.send_json(
                    {"type": "status", "state": "error", "message": "connect_timeout"}
                )
                await websocket.close(code=4502)
                return
            slice_timeout = min(1.0, max(0.2, remaining))
            try:
                await loop.run_in_executor(
                    None,
                    lambda t=slice_timeout: wait_session_ready(session_id, timeout=t),
                )
                sess = get_session(session_id) or cur
                break
            except HTTPException as exc:
                if exc.status_code == 504:
                    # Slice timeout while still connecting — keep polling with progress.
                    continue
                await websocket.send_json(
                    {"type": "status", "state": "error", "message": str(exc.detail)}
                )
                await websocket.close(code=4502)
                return
            except Exception as exc:
                await websocket.send_json(
                    {"type": "status", "state": "error", "message": f"connect_failed:{exc}"}
                )
                await websocket.close(code=4502)
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
            "encoding": sess.encoding,
            "cols": sess.cols,
            "rows": sess.rows,
            "device_type": sess.device_type,
            "vendor": sess.vendor,
            "cli_hop": bool(sess.cli_hop_guard),
            "connect_ms": (
                int((sess.connect_finished_at - sess.connect_started_at) * 1000)
                if sess.connect_finished_at
                else None
            ),
        }
    )

    # Replay full login transcript (kept for StrictMode remount / brief reconnect).
    bootstrap = bytes(sess.bootstrap_output or b"")
    if bootstrap:
        try:
            if _normalize_encoding(sess.encoding) != "utf-8":
                bootstrap = _decode_bytes(bootstrap, sess.encoding).encode("utf-8", errors="replace")
            await websocket.send_bytes(bootstrap)
        except Exception:
            try:
                await websocket.send_json(
                    {"type": "stdout", "data": _decode_bytes(bytes(sess.bootstrap_output or b""), sess.encoding)}
                )
            except Exception:
                _log.debug("webcrt bootstrap send failed session=%s", session_id, exc_info=True)

    stop = asyncio.Event()
    stdin_buf: list[str] = []
    stdin_flush_task: asyncio.Task[None] | None = None

    async def flush_stdin() -> None:
        nonlocal stdin_buf
        if not stdin_buf:
            return
        data = "".join(stdin_buf)
        stdin_buf = []
        try:
            await asyncio.get_running_loop().run_in_executor(None, sess.write_stdin, data)
        except Exception as exc:
            await websocket.send_json(
                {"type": "status", "state": "error", "message": f"write_failed:{exc}"}
            )
            stop.set()

    async def schedule_stdin_flush() -> None:
        await asyncio.sleep(0.008)
        await flush_stdin()

    async def pump_stdout() -> None:
        loop = asyncio.get_running_loop()
        pending: list[bytes] = []
        last_flush = loop.time()

        def _to_browser_bytes(raw: bytes) -> bytes:
            if _normalize_encoding(sess.encoding) == "utf-8":
                return raw
            return _decode_bytes(raw, sess.encoding).encode("utf-8", errors="replace")

        async def _flush_pending() -> bool:
            nonlocal pending, last_flush
            if not pending:
                return True
            blob = _to_browser_bytes(b"".join(pending))
            pending = []
            last_flush = loop.time()
            try:
                await websocket.send_bytes(blob)
                return True
            except Exception:
                return False

        while not stop.is_set():
            chunk = await loop.run_in_executor(
                None, lambda: sess.take_stdout(attach_gen, timeout=0.05)
            )
            if chunk == "stale":
                break
            if chunk == "empty":
                if pending and (loop.time() - last_flush) >= 0.016:
                    if not await _flush_pending():
                        stop.set()
                        break
                continue
            if chunk is None:
                await _flush_pending()
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
            pending.append(chunk)
            if sum(len(p) for p in pending) >= 8192 or (loop.time() - last_flush) >= 0.016:
                if not await _flush_pending():
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
            msg_raw = await websocket.receive()
            if msg_raw.get("type") == "websocket.disconnect":
                break
            if "bytes" in msg_raw and msg_raw["bytes"] is not None:
                # Binary stdin: decode with session encoding.
                try:
                    text = _decode_bytes(bytes(msg_raw["bytes"]), sess.encoding)
                except Exception:
                    continue
                stdin_buf.append(text)
                if stdin_flush_task is None or stdin_flush_task.done():
                    stdin_flush_task = asyncio.create_task(schedule_stdin_flush())
                continue
            raw = msg_raw.get("text")
            if raw is None:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                msg = {"type": "stdin", "data": raw}
            mtype = str(msg.get("type") or "").strip().lower()
            if mtype == "stdin":
                data = msg.get("data")
                if data is None:
                    continue
                stdin_buf.append(str(data))
                # Coalesce high-frequency keystrokes briefly.
                if len(stdin_buf) >= 8:
                    if stdin_flush_task and not stdin_flush_task.done():
                        stdin_flush_task.cancel()
                    await flush_stdin()
                elif stdin_flush_task is None or stdin_flush_task.done():
                    stdin_flush_task = asyncio.create_task(schedule_stdin_flush())
            elif mtype == "resize":
                cols = int(msg.get("cols") or sess.cols)
                rows = int(msg.get("rows") or sess.rows)
                await asyncio.get_running_loop().run_in_executor(None, sess.resize, cols, rows)
            elif mtype == "break":
                try:
                    await asyncio.get_running_loop().run_in_executor(None, sess.send_break)
                except Exception as exc:
                    await websocket.send_json(
                        {"type": "status", "state": "error", "message": f"break_failed:{exc}"}
                    )
            elif mtype == "ping":
                sess.touch()
                await websocket.send_json({"type": "pong"})
            elif mtype == "close":
                stop.set()
                await flush_stdin()
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
        if stdin_flush_task and not stdin_flush_task.done():
            stdin_flush_task.cancel()
        try:
            await flush_stdin()
        except Exception:
            pass
        reader_task.cancel()
        try:
            await reader_task
        except Exception:
            pass
        # Keep device session briefly so React remount / blip can re-attach.
        if get_session(session_id) is not None:
            detach_session(
                session_id,
                grace_sec=8.0,
                client=_client_label(websocket=websocket),
                attach_gen=attach_gen,
            )
