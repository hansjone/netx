"""Interactive WebCRT sessions: bridge browser WebSocket <-> Netmiko device channel."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from netmiko import ConnectHandler
from sqlalchemy.orm import Session

from .config import settings
from .ne_crypto import CredentialCryptoError
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection

_log = logging.getLogger("netx.webcrt")

_sessions_lock = threading.Lock()
_sessions: dict[str, "WebcrtSession"] = {}
_reaper_started = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def webcrt_data_root() -> Path:
    root = Path(str(settings.webcrt_data_dir or "data/webcrt"))
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _audit(event: str, **fields: Any) -> None:
    record = {"ts": _utc_iso(), "event": event, **fields}
    try:
        path = webcrt_data_root() / "audit.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        _log.exception("webcrt audit write failed")
    _log.info("webcrt.%s %s", event, {k: v for k, v in fields.items() if k != "detail"})


@dataclass
class WebcrtSession:
    session_id: str
    ne_id: str
    ne_name: str
    ne_ip: str
    protocol: str
    cols: int
    rows: int
    conn: ConnectHandler | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    attached: bool = False
    closed: bool = False
    close_reason: str = ""
    out_queue: queue.Queue[bytes | None] = field(default_factory=queue.Queue)
    _reader: threading.Thread | None = field(default=None, repr=False)
    _write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def touch(self) -> None:
        self.last_activity = time.time()

    def write_stdin(self, data: str) -> None:
        if self.closed or self.conn is None:
            raise RuntimeError("session_closed")
        text = str(data or "")
        if not text:
            return
        with self._write_lock:
            self.conn.write_channel(text)
        self.touch()

    def resize(self, cols: int, rows: int) -> None:
        if self.closed or self.conn is None:
            return
        c = max(20, min(500, int(cols or 80)))
        r = max(5, min(200, int(rows or 24)))
        self.cols = c
        self.rows = r
        channel = getattr(self.conn, "remote_conn", None)
        if channel is not None and hasattr(channel, "resize_pty"):
            try:
                channel.resize_pty(width=c, height=r)
            except Exception:
                _log.debug("resize_pty failed session=%s", self.session_id, exc_info=True)
        self.touch()

    def start_reader(self) -> None:
        if self._reader and self._reader.is_alive():
            return
        self._reader = threading.Thread(
            target=self._reader_loop,
            name=f"webcrt-reader-{self.session_id[:8]}",
            daemon=True,
        )
        self._reader.start()

    def _reader_loop(self) -> None:
        conn = self.conn
        if conn is None:
            self.out_queue.put(None)
            return
        channel = getattr(conn, "remote_conn", None)
        try:
            while not self.closed:
                chunk = b""
                try:
                    if channel is not None and hasattr(channel, "recv_ready") and hasattr(channel, "recv"):
                        if channel.recv_ready():
                            chunk = channel.recv(4096)
                            if not chunk:
                                break
                        elif hasattr(channel, "exit_status_ready") and channel.exit_status_ready():
                            break
                        else:
                            time.sleep(0.04)
                            continue
                    else:
                        text = conn.read_channel()
                        if text:
                            chunk = text.encode("utf-8", errors="replace")
                        else:
                            time.sleep(0.04)
                            continue
                except Exception as exc:
                    if self.closed:
                        break
                    _log.debug("webcrt reader error session=%s: %s", self.session_id, exc)
                    time.sleep(0.1)
                    continue
                if chunk:
                    self.touch()
                    self.out_queue.put(chunk)
        finally:
            self.out_queue.put(None)

    def close(self, reason: str = "closed") -> None:
        if self.closed:
            return
        self.closed = True
        self.close_reason = reason or "closed"
        try:
            close_netmiko_connection(self.conn)
        except Exception:
            pass
        self.conn = None
        try:
            self.out_queue.put_nowait(None)
        except Exception:
            pass


def _ensure_reaper() -> None:
    global _reaper_started
    with _sessions_lock:
        if _reaper_started:
            return
        _reaper_started = True
    t = threading.Thread(target=_reaper_loop, name="webcrt-reaper", daemon=True)
    t.start()


def _reaper_loop() -> None:
    while True:
        try:
            _reap_sessions()
        except Exception:
            _log.exception("webcrt reaper failed")
        time.sleep(5)


def _reap_sessions() -> None:
    idle = max(60, int(settings.webcrt_idle_timeout_sec or 1800))
    attach = max(10, int(settings.webcrt_attach_timeout_sec or 60))
    now = time.time()
    to_close: list[tuple[WebcrtSession, str]] = []
    with _sessions_lock:
        for sess in list(_sessions.values()):
            if sess.closed:
                _sessions.pop(sess.session_id, None)
                continue
            if not sess.attached and (now - sess.created_at) > attach:
                to_close.append((sess, "attach_timeout"))
            elif (now - sess.last_activity) > idle:
                to_close.append((sess, "idle_timeout"))
    for sess, reason in to_close:
        close_session(sess.session_id, reason=reason)


def active_session_count() -> int:
    with _sessions_lock:
        return sum(1 for s in _sessions.values() if not s.closed)


def get_session(session_id: str) -> WebcrtSession | None:
    with _sessions_lock:
        sess = _sessions.get(session_id)
        if sess is None or sess.closed:
            return None
        return sess


def create_session(
    db: Session,
    *,
    ne_id: str | None = None,
    ume_ne_id: str | None = None,
    cols: int = 80,
    rows: int = 24,
    client: str = "",
) -> dict[str, Any]:
    from .cli_resolve import resolve_cli_target

    _ensure_reaper()
    max_sessions = max(1, int(settings.webcrt_max_sessions or 20))
    if active_session_count() >= max_sessions:
        raise HTTPException(status_code=429, detail="webcrt_session_limit")

    mid = str(ne_id or "").strip()
    uid = str(ume_ne_id or "").strip()
    try:
        creds, device = resolve_cli_target(db, managed_ne_id=mid or None, ume_ne_id=uid or None)
    except HTTPException:
        raise
    except CredentialCryptoError as exc:
        raise HTTPException(status_code=400, detail=str(exc) or "credential_crypto_error") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"credential_error:{exc}") from exc

    if not str(creds.get("username") or "").strip() or not str(creds.get("password") or ""):
        raise HTTPException(status_code=400, detail="credentials_incomplete")

    session_id = str(uuid.uuid4())
    c = max(20, min(500, int(cols or 80)))
    r = max(5, min(200, int(rows or 24)))
    connect_timeout = max(30, int(settings.webcrt_connect_timeout_sec or 90))
    target_id = str(device.get("id") or mid or uid)
    target_ip = str(device.get("ip_address") or "")
    target_name = str(device.get("name") or target_ip)
    protocol = str(device.get("protocol") or creds.get("protocol") or "ssh")

    try:
        conn = open_netmiko_connection(creds, session_timeout=connect_timeout)
    except Exception as exc:
        _audit(
            "session_open_failed",
            session_id=session_id,
            ne_id=target_id,
            ne_ip=target_ip,
            source=str(device.get("source") or ""),
            client=client or "",
            error=str(exc)[:500],
        )
        raise HTTPException(status_code=502, detail=f"connect_failed:{exc}") from exc

    channel = getattr(conn, "remote_conn", None)
    if channel is not None and hasattr(channel, "resize_pty"):
        try:
            channel.resize_pty(width=c, height=r)
        except Exception:
            pass

    try:
        leftover = conn.read_channel()
    except Exception:
        leftover = ""

    sess = WebcrtSession(
        session_id=session_id,
        ne_id=target_id,
        ne_name=target_name,
        ne_ip=target_ip,
        protocol=protocol,
        cols=c,
        rows=r,
        conn=conn,
    )
    if leftover:
        sess.out_queue.put(str(leftover).encode("utf-8", errors="replace"))
    sess.start_reader()

    with _sessions_lock:
        _sessions[session_id] = sess

    _audit(
        "session_created",
        session_id=session_id,
        ne_id=sess.ne_id,
        ne_name=sess.ne_name,
        ne_ip=sess.ne_ip,
        protocol=sess.protocol,
        source=str(device.get("source") or ""),
        hop_enabled=bool(creds.get("hop_enabled")),
        hop_vendor=str(creds.get("hop_vendor") or "") if creds.get("hop_enabled") else "",
        client=client or "",
        active=active_session_count(),
    )
    return {
        "session_id": session_id,
        "ne_id": sess.ne_id,
        "ne_name": sess.ne_name,
        "ne_ip": sess.ne_ip,
        "source": str(device.get("source") or ""),
        "protocol": sess.protocol,
        "cols": sess.cols,
        "rows": sess.rows,
        "ws_path": f"/v1/webcrt/sessions/{session_id}/ws",
    }


def mark_attached(session_id: str) -> WebcrtSession:
    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="webcrt_session_not_found")
    if sess.attached:
        raise HTTPException(status_code=409, detail="webcrt_session_already_attached")
    sess.attached = True
    sess.touch()
    _audit("session_attached", session_id=session_id, ne_id=sess.ne_id, ne_ip=sess.ne_ip)
    return sess


def close_session(session_id: str, *, reason: str = "closed", client: str = "") -> dict[str, Any]:
    with _sessions_lock:
        sess = _sessions.pop(session_id, None)
    if sess is None:
        return {"ok": True, "session_id": session_id, "closed": False}
    if not sess.closed:
        sess.close(reason)
        _audit(
            "session_closed",
            session_id=session_id,
            ne_id=sess.ne_id,
            ne_ip=sess.ne_ip,
            reason=reason,
            client=client or "",
            active=active_session_count(),
        )
    return {"ok": True, "session_id": session_id, "closed": True, "reason": reason}


def list_sessions() -> dict[str, Any]:
    with _sessions_lock:
        items = [
            {
                "session_id": s.session_id,
                "ne_id": s.ne_id,
                "ne_name": s.ne_name,
                "ne_ip": s.ne_ip,
                "protocol": s.protocol,
                "attached": s.attached,
                "created_at": datetime.fromtimestamp(s.created_at, tz=timezone.utc).isoformat(),
                "last_activity": datetime.fromtimestamp(s.last_activity, tz=timezone.utc).isoformat(),
            }
            for s in _sessions.values()
            if not s.closed
        ]
    return {
        "total": len(items),
        "max_sessions": max(1, int(settings.webcrt_max_sessions or 20)),
        "idle_timeout_sec": max(60, int(settings.webcrt_idle_timeout_sec or 1800)),
        "items": items,
    }
