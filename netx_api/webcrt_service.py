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
from .ne_session_factory import (
    close_netmiko_connection,
    extract_cli_prompt_marker,
    get_cli_hop_guard,
    open_netmiko_connection,
    should_close_cli_hop_session,
)

_log = logging.getLogger("netx.webcrt")

_sessions_lock = threading.Lock()
_sessions: dict[str, "WebcrtSession"] = {}
_reaper_started = False
# Sentinel for take_stdout timeout (distinct from device EOF None).
_STDOUT_MISSING = object()

# Network device CLI key rewrites (SecureCRT-like).
# - Backspace: DEL(0x7f) -> BS(0x08)
# - Home/End/Delete: emacs controls (widely accepted)
# - Arrows: after login many boxes enable DECCKM (application cursor), so xterm
#   sends SS3 forms (\x1bOD) which VRP/IOS ignore; normalize SS3 -> CSI and
#   pass CSI through. Do not rewrite arrows to Ctrl-B/F — that leaves the
#   device cursor stuck at EOL when SS3 was what actually arrived.
_NETWORK_CLI_KEY_SEQS: tuple[tuple[str, str], ...] = (
    ("\x1b[1~", "\x01"),  # Home -> Ctrl-A
    ("\x1b[3~", "\x04"),  # Delete key -> Ctrl-D
    ("\x1b[4~", "\x05"),  # End -> Ctrl-E
    ("\x1b[H", "\x01"),
    ("\x1b[F", "\x05"),
    ("\x1bOH", "\x01"),
    ("\x1bOF", "\x05"),
    ("\x1bOA", "\x1b[A"),  # App Up -> CSI Up
    ("\x1bOB", "\x1b[B"),
    ("\x1bOC", "\x1b[C"),
    ("\x1bOD", "\x1b[D"),  # App Left -> CSI Left
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


def map_network_cli_keys(
    data: str,
    *,
    device_type: str = "",
    vendor: str = "",
    protocol: str = "",
) -> str:
    """Rewrite xterm key sequences for network-device CLIs."""
    del device_type, vendor, protocol  # protocol kept for call-site compatibility
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


def prepare_bootstrap_output(text: str) -> str:
    """Full login transcript for UI replay; keep final prompt, no trailing newline after it.

    Trailing newline would leave the cursor on a blank line so the first typed line
    looks wrong; cursor should sit after the prompt like a real CRT.
    """
    return normalize_cli_transcript(text)


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
    # React StrictMode remounts open a second WS before the first fully tears down.
    # Only the newest attach_gen may consume out_queue / mark detach.
    attach_gen: int = 0
    out_queue: queue.Queue[bytes | None] = field(default_factory=queue.Queue)
    # Vendor CLI hop (Huawei/ZTE/Cisco): close when nested target session returns to hop.
    cli_hop_guard: bool = False
    cli_hop_prompt: str = ""
    _reader: threading.Thread | None = field(default=None, repr=False)
    _write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stdout_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _hop_scan_buf: str = field(default="", repr=False)
    _cli_hop_seen_other_prompt: bool = field(default=False, repr=False)

    def touch(self) -> None:
        self.last_activity = time.time()

    def take_stdout(self, attach_gen: int, *, timeout: float = 0.25) -> bytes | None | str:
        """Exclusive stdout take for one WS attach generation.

        Returns:
          bytes — device output chunk
          None — device reader closed (session end)
          \"stale\" — a newer WebSocket owns this session; caller must stop
          \"empty\" — no data within timeout (keep polling)
        """
        deadline = time.time() + max(0.05, float(timeout))
        while True:
            with self._stdout_lock:
                if attach_gen != self.attach_gen:
                    return "stale"
                try:
                    chunk = self.out_queue.get_nowait()
                except queue.Empty:
                    chunk = _STDOUT_MISSING
                if chunk is not _STDOUT_MISSING:
                    if attach_gen != self.attach_gen:
                        # Put back including EOF sentinel so the new owner still sees close.
                        self.out_queue.put(chunk)
                        return "stale"
                    return chunk  # bytes | None
            if time.time() >= deadline:
                return "empty"
            time.sleep(0.02)

    def write_stdin(self, data: str) -> None:
        if self.closed or self.conn is None:
            raise RuntimeError("session_closed")
        text = str(data or "")
        if not text:
            return
        if self.cli_keymap:
            text = map_network_cli_keys(
                text,
                device_type=self.device_type,
                vendor=self.vendor,
                protocol=self.protocol,
            )
            text = map_network_cli_enter(text, self.conn)
        if not text:
            return
        with self._write_lock:
            # Prefer raw channel I/O for interactive typing (char echo / backspace).
            # Netmiko write_channel is fine for automation but can feel "half-duplex"
            # on some Telnet/VRP sessions when used keystroke-by-keystroke.
            channel = getattr(self.conn, "remote_conn", None)
            try:
                if channel is not None and hasattr(channel, "send") and callable(channel.send):
                    payload = text.encode(getattr(self.conn, "encoding", None) or "utf-8", errors="replace")
                    # Paramiko may write partially when the window is full.
                    view = memoryview(payload)
                    while len(view):
                        n = int(channel.send(view) or 0)
                        if n <= 0:
                            time.sleep(0.01)
                            continue
                        view = view[n:]
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
        hop_return = False
        try:
            while not self.closed:
                chunk = b""
                try:
                    if channel is not None and hasattr(channel, "recv_ready") and hasattr(channel, "recv"):
                        # Paramiko SSH: raw bytes keep ANSI / backspace echo intact.
                        if channel.recv_ready():
                            chunk = channel.recv(4096)
                            if not chunk:
                                break
                        elif hasattr(channel, "exit_status_ready") and channel.exit_status_ready():
                            break
                        else:
                            time.sleep(0.04)
                            continue
                    elif channel is not None and hasattr(channel, "read_very_eager"):
                        # Telnet: do NOT use conn.read_channel() — Netmiko strips ANSI
                        # escape codes, which removes Huawei backspace echo (\x1b[1D \x1b[1D).
                        data = channel.read_very_eager()
                        if data:
                            chunk = (
                                data
                                if isinstance(data, (bytes, bytearray))
                                else str(data).encode(
                                    getattr(conn, "encoding", None) or "utf-8",
                                    errors="replace",
                                )
                            )
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
                    if self.cli_hop_guard and self._note_cli_hop_output(chunk):
                        hop_return = True
                        notice = (
                            "\r\n*** WebCRT: 目标会话已结束，已断开代理连接 "
                            "(target session ended; closing hop proxy) ***\r\n"
                        )
                        self.out_queue.put(notice.encode("utf-8", errors="replace"))
                        break
        finally:
            if hop_return and not self.closed:
                # Prefer registry close for audit + remove; fall back to local close.
                try:
                    close_session(self.session_id, reason="cli_hop_return")
                except Exception:
                    self.close("cli_hop_return")
            self.out_queue.put(None)

    def _note_cli_hop_output(self, chunk: bytes) -> bool:
        """Accumulate stdout and return True when nested CLI hop has returned to proxy."""
        try:
            text = chunk.decode("utf-8", errors="replace")
        except Exception:
            text = str(chunk)
        self._hop_scan_buf = (self._hop_scan_buf + text)[-12000:]
        # Track a prompt that differs from the hop so same-sysname labs still need
        # an explicit nested-close message before we tear down.
        marker = str(self.cli_hop_prompt or "").strip()
        last = extract_cli_prompt_marker(self._hop_scan_buf)
        if last and (not marker or last != marker):
            self._cli_hop_seen_other_prompt = True
        return should_close_cli_hop_session(
            self._hop_scan_buf,
            self.cli_hop_prompt,
            seen_other_prompt=self._cli_hop_seen_other_prompt,
        )

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


def _webcrt_creds_ready(creds: dict[str, Any]) -> bool:
    """True when WebCRT can open a session with the resolved credentials.

    Bastion-managed hops store the target password on the bastion side, so an empty
    NE password is valid (same as connectivity test). Direct / manual / Linux hops
    still require a target password.
    """
    if not str(creds.get("username") or "").strip():
        return False
    hop_enabled = bool(creds.get("hop_enabled"))
    hop_vendor = str(creds.get("hop_vendor") or "").strip().lower()
    auth_mode = str(creds.get("hop_target_auth_mode") or "bastion_managed").strip().lower()
    if hop_enabled and hop_vendor == "bastion" and auth_mode == "bastion_managed":
        return bool(
            str(creds.get("hop_host") or "").strip()
            and str(creds.get("hop_username") or "").strip()
            and str(creds.get("hop_password") or "")
        )
    return bool(str(creds.get("password") or ""))


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

    if not _webcrt_creds_ready(creds):
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
            creds,
            session_timeout=connect_timeout,
            session_log=log_buf,
            cols=c,
            rows=r,
            interactive=True,
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
        from .ne_cli_errors import format_cli_failure

        classified = format_cli_failure(exc, partial)
        detail = f"connect_failed:{classified}"
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

    # Prefer session_log (full login transcript including final prompt).
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

    hop_guard = get_cli_hop_guard(conn)
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
        # Only nudge a live prompt when transcript has no recognizable prompt yet.
        needs_live_prompt=not _looks_like_cli_prompt(bootstrap),
        cli_hop_guard=bool(hop_guard),
        cli_hop_prompt=str((hop_guard or {}).get("hop_prompt") or ""),
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
        cli_hop_guard=bool(hop_guard),
        cli_hop_prompt=str((hop_guard or {}).get("hop_prompt") or ""),
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
        "cli_hop": bool(hop_guard),
    }


def mark_attached(session_id: str) -> tuple[WebcrtSession, int]:
    sess = get_session(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="webcrt_session_not_found")
    # Allow re-attach after brief WS drop (React StrictMode remount / network blip).
    # Bump generation so the previous WS pump stops and does not steal echo bytes.
    sess.attach_gen += 1
    attach_gen = sess.attach_gen
    sess.attached = True
    sess.detach_deadline = None
    sess.touch()
    _audit(
        "session_attached",
        session_id=session_id,
        ne_id=sess.ne_id,
        ne_ip=sess.ne_ip,
        attach_gen=attach_gen,
    )
    return sess, attach_gen


def detach_session(
    session_id: str,
    *,
    grace_sec: float = 8.0,
    client: str = "",
    attach_gen: int | None = None,
) -> dict[str, Any]:
    """Mark session unattached but keep device channel open briefly for reconnect."""
    sess = get_session(session_id)
    if sess is None:
        return {"ok": True, "session_id": session_id, "detached": False}
    if sess.closed:
        return {"ok": True, "session_id": session_id, "detached": False}
    # Ignore detach from an older StrictMode WS once a newer attach owns the session.
    if attach_gen is not None and attach_gen != sess.attach_gen:
        return {
            "ok": True,
            "session_id": session_id,
            "detached": False,
            "ignored_stale_attach": True,
        }
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
        attach_gen=attach_gen,
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
