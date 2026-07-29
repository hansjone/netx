"""Interactive WebCRT sessions: bridge browser WebSocket <-> Netmiko device channel."""

from __future__ import annotations

import io
import json
import logging
import queue
import re
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

# Network device CLIs (Huawei/ZTE/Cisco) often reject xterm DEL/CSI arrows over Telnet.
# Map to classic emacs-style control keys that VRP/IOS/ZXROS accept.
_NETWORK_CLI_KEY_SEQS: tuple[tuple[str, str], ...] = (
    ("\x1b[1~", "\x01"),  # Home -> Ctrl-A
    ("\x1b[3~", "\x04"),  # Delete -> Ctrl-D
    ("\x1b[4~", "\x05"),  # End -> Ctrl-E
    ("\x1b[H", "\x01"),
    ("\x1b[F", "\x05"),
    ("\x1bOH", "\x01"),
    ("\x1bOF", "\x05"),
    ("\x1b[A", "\x10"),  # Up -> Ctrl-P (history)
    ("\x1b[B", "\x0e"),  # Down -> Ctrl-N
    ("\x1b[C", "\x06"),  # Right -> Ctrl-F
    ("\x1b[D", "\x02"),  # Left -> Ctrl-B
    ("\x7f", "\x08"),  # DEL -> BS
)


def uses_network_cli_keymap(device_type: str = "", vendor: str = "") -> bool:
    blob = f"{device_type} {vendor}".strip().lower()
    if not blob:
        return True
    for token in ("linux", "ubuntu", "centos", "debian", "redhat", "unix"):
        if token in blob:
            return False
    return True


def map_network_cli_keys(data: str) -> str:
    """Rewrite xterm key sequences for network-device CLIs."""
    text = str(data or "")
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for seq, repl in _NETWORK_CLI_KEY_SEQS:
            if text.startswith(seq, i):
                out.append(repl)
                i += len(seq)
                matched = True
                break
        if not matched:
            out.append(text[i])
            i += 1
    return "".join(out)


def channel_return(conn: ConnectHandler | None) -> str:
    """Netmiko line ending for this session (SSH usually \\n, Telnet often \\r\\n)."""
    if conn is None:
        return "\n"
    ret = getattr(conn, "RETURN", None)
    if isinstance(ret, str) and ret:
        return ret
    return "\n"


def map_network_cli_enter(data: str, conn: ConnectHandler | None) -> str:
    """Map xterm Enter (\\r) to the device's Netmiko RETURN."""
    text = str(data or "")
    if not text:
        return text
    ret = channel_return(conn)
    if ret == "\r":
        return text
    # Prefer replacing CRLF first so Telnet RETURN \\r\\n does not double-expand.
    return text.replace("\r\n", ret).replace("\r", ret)


def _drain_channel(conn: ConnectHandler, *, rounds: int = 10, wait: float = 0.12) -> str:
    """Read whatever is already sitting on the channel after login."""
    chunks: list[str] = []
    empty_streak = 0
    for _ in range(max(1, rounds)):
        time.sleep(wait)
        try:
            part = conn.read_channel()
        except Exception:
            break
        if part:
            chunks.append(str(part))
            empty_streak = 0
        else:
            empty_streak += 1
            if empty_streak >= 2 and chunks:
                break
    return "".join(chunks)


def _session_log_text(buf: io.BytesIO | None) -> str:
    """Decode Netmiko session_log buffer into display text."""
    if buf is None:
        return ""
    try:
        raw = buf.getvalue()
    except Exception:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _looks_like_cli_prompt(text: str) -> bool:
    s = str(text or "").rstrip()
    if not s:
        return False
    # Common network CLI prompts: <r1>  [HUAWEI]  Router#  Router>
    return bool(re.search(r"(?:[>\]]|#)\s*$", s)) or bool(re.search(r"<[^>\r\n]+>\s*$", s))


# Cisco/Netmiko often yields "R2#R2#" when a sync Enter is appended without a newline.
_GLUED_PROMPT_RE = re.compile(r"(?<=[#>])(?=(?:[A-Za-z0-9][\w.\-:]{0,62})[#>])")


def normalize_cli_transcript(text: str) -> str:
    """Normalize login transcript for xterm (convertEol) and un-glue prompts."""
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = _GLUED_PROMPT_RE.sub("\n", s)
    lines = s.split("\n")
    while lines and not str(lines[-1]).strip():
        lines.pop()
    while len(lines) >= 2 and lines[-1] == lines[-2] and _looks_like_cli_prompt(lines[-1]):
        lines.pop()
    return "\n".join(lines)


def strip_trailing_prompt_lines(text: str) -> str:
    """Remove final prompt line(s) so a live RETURN can paint the interactive prompt."""
    lines = str(text or "").split("\n")
    while lines and not str(lines[-1]).strip():
        lines.pop()
    while lines and _looks_like_cli_prompt(lines[-1]):
        lines.pop()
        while lines and not str(lines[-1]).strip():
            lines.pop()
    return "\n".join(lines)


def prepare_bootstrap_output(text: str) -> str:
    """Login transcript for UI replay; ends with newline, without the final prompt."""
    body = strip_trailing_prompt_lines(normalize_cli_transcript(text))
    if not body:
        return ""
    return body if body.endswith("\n") else body + "\n"


def _prime_interactive_channel(conn: ConnectHandler) -> None:
    """Send one RETURN after Netmiko login so the interactive channel is fully ready."""
    try:
        _drain_channel(conn, rounds=4, wait=0.05)
    except Exception:
        pass
    try:
        conn.write_channel(channel_return(conn))
    except Exception:
        try:
            conn.write_channel("\n")
        except Exception:
            return
    try:
        _drain_channel(conn, rounds=8, wait=0.1)
    except Exception:
        pass



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
    device_type: str = ""
    vendor: str = ""
    cli_keymap: bool = True
    conn: ConnectHandler | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    attached: bool = False
    detach_deadline: float | None = None
    closed: bool = False
    close_reason: str = ""
    bootstrap_output: bytes = b""
    needs_live_prompt: bool = True
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
        if self.cli_keymap:
            text = map_network_cli_keys(text)
            text = map_network_cli_enter(text, self.conn)
        if not text:
            return
        with self._write_lock:
            # Network CLIs: use Netmiko write_channel so RETURN/encoding match the driver.
            if self.cli_keymap:
                self.conn.write_channel(text)
            else:
                channel = getattr(self.conn, "remote_conn", None)
                try:
                    if channel is not None and hasattr(channel, "send") and callable(channel.send):
                        payload = text.encode(getattr(self.conn, "encoding", None) or "utf-8", errors="replace")
                        channel.send(payload)
                    elif channel is not None and hasattr(channel, "write") and callable(channel.write):
                        encoding = getattr(self.conn, "encoding", None) or "utf-8"
                        channel.write(text.encode(encoding, errors="replace") if isinstance(text, str) else text)
                    else:
                        self.conn.write_channel(text)
                except Exception:
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
        time.sleep(2)


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
            if sess.attached:
                if (now - sess.last_activity) > idle:
                    to_close.append((sess, "idle_timeout"))
                continue
            # Not attached: either never attached, or briefly detached for reconnect.
            if sess.detach_deadline is not None:
                if now >= sess.detach_deadline:
                    to_close.append((sess, "detach_timeout"))
            elif (now - sess.created_at) > attach:
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
    device_type = str(device.get("device_type") or creds.get("device_type") or "")
    vendor = str(device.get("vendor") or creds.get("vendor") or "")
    cli_keymap = uses_network_cli_keymap(device_type, vendor)

    # Capture real Telnet/SSH login I/O (banner, Username/Password, prompts).
    log_buf = io.BytesIO()
    try:
        conn = open_netmiko_connection(
            creds, session_timeout=connect_timeout, session_log=log_buf
        )
    except Exception as exc:
        partial = _session_log_text(log_buf).strip()
        _audit(
            "session_open_failed",
            session_id=session_id,
            ne_id=target_id,
            ne_ip=target_ip,
            source=str(device.get("source") or ""),
            client=client or "",
            error=str(exc)[:500],
            transcript_len=len(partial),
        )
        detail = f"connect_failed:{exc}"
        if partial:
            # Keep detail bounded; UI surfaces this on open failure.
            detail = f"{detail}\n--- device transcript ---\n{partial[-4000:]}"
        raise HTTPException(status_code=502, detail=detail) from exc

    channel = getattr(conn, "remote_conn", None)
    if channel is not None and hasattr(channel, "resize_pty"):
        try:
            channel.resize_pty(width=c, height=r)
        except Exception:
            pass

    # Prefer session_log (full login transcript). Prime channel, then strip the final
    # prompt so WebSocket attach can paint a live interactive prompt (first keystrokes
    # then behave like subsequent lines).
    _prime_interactive_channel(conn)
    bootstrap = prepare_bootstrap_output(_session_log_text(log_buf))
    if not bootstrap.strip():
        try:
            more = _drain_channel(conn, rounds=6, wait=0.1)
        except Exception:
            more = ""
        if more:
            bootstrap = prepare_bootstrap_output(more)
    # Discard any unread bytes so the live reader starts clean.
    try:
        _drain_channel(conn, rounds=3, wait=0.05)
    except Exception:
        pass

    sess = WebcrtSession(
        session_id=session_id,
        ne_id=target_id,
        ne_name=target_name,
        ne_ip=target_ip,
        protocol=protocol,
        cols=c,
        rows=r,
        device_type=device_type,
        vendor=vendor,
        cli_keymap=cli_keymap,
        conn=conn,
        bootstrap_output=str(bootstrap or "").encode("utf-8", errors="replace"),
        needs_live_prompt=True,
    )
    # Keep bootstrap for WS attach replay; do not rely solely on out_queue (StrictMode remount).
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
    # Allow re-attach after brief WS drop (React StrictMode remount / network blip).
    sess.attached = True
    sess.detach_deadline = None
    sess.touch()
    _audit("session_attached", session_id=session_id, ne_id=sess.ne_id, ne_ip=sess.ne_ip)
    return sess


def detach_session(session_id: str, *, grace_sec: float = 8.0, client: str = "") -> dict[str, Any]:
    """Mark session unattached but keep device channel open briefly for reconnect."""
    sess = get_session(session_id)
    if sess is None:
        return {"ok": True, "session_id": session_id, "detached": False}
    if sess.closed:
        return {"ok": True, "session_id": session_id, "detached": False}
    sess.attached = False
    sess.detach_deadline = time.time() + max(1.0, float(grace_sec))
    sess.touch()
    _audit(
        "session_detached",
        session_id=session_id,
        ne_id=sess.ne_id,
        ne_ip=sess.ne_ip,
        grace_sec=grace_sec,
        client=client or "",
    )
    return {"ok": True, "session_id": session_id, "detached": True}


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
