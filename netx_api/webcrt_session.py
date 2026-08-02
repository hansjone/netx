"""WebCRT interactive sessions and process-local registry."""
from __future__ import annotations

import io
import json
import logging
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
from .webcrt_channel import (
    _BoundedByteQueue,
    _audit,
    _capture_raw_channel,
    _decode_bytes,
    _drain_channel,
    _drain_raw_channel,
    _encode_text,
    _is_prompt_only_echo,
    _looks_like_cli_prompt,
    _looks_like_login_prompt,
    _looks_like_password_change_prompt,
    _normalize_encoding,
    _prime_interactive_channel,
    _session_log_path,
    _session_log_text,
    _utc_iso,
    _utc_now,
    channel_return,
    map_network_cli_enter,
    map_network_cli_keys,
    normalize_cli_transcript,
    prepare_bootstrap_output,
    read_session_log_tail,
    uses_network_cli_keymap,
    webcrt_data_root,
)

_log = logging.getLogger("netx.webcrt")

_sessions_lock = threading.Lock()
_sessions: dict[str, "WebcrtSession"] = {}
_reaper_started = False


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
    encoding: str = "utf-8"
    keepalive_sec: int = 0
    conn: ConnectHandler | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    attached: bool = False
    detach_deadline: float | None = None
    closed: bool = False
    close_reason: str = ""
    state: str = "connecting"
    connect_error: str = ""
    connect_started_at: float = field(default_factory=time.time)
    connect_finished_at: float | None = None
    bootstrap_output: bytes = b""
    # First WS attach gets login bootstrap; later attaches prefer session-log tail.
    bootstrap_replayed: bool = False
    needs_live_prompt: bool = True
    # React StrictMode remounts open a second WS before the first fully tears down.
    # Only the newest attach_gen may consume out_queue / mark detach.
    attach_gen: int = 0
    out_queue: _BoundedByteQueue = field(
        default_factory=lambda: _BoundedByteQueue(int(getattr(settings, "webcrt_out_queue_max", 2000) or 2000))
    )
    # Vendor CLI hop (Huawei/ZTE/Cisco): close when nested target session returns to hop.
    cli_hop_guard: bool = False
    cli_hop_prompt: str = ""
    post_login_commands: list[str] = field(default_factory=list)
    bytes_in: int = 0
    bytes_out: int = 0
    _reader: threading.Thread | None = field(default=None, repr=False)
    _write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _stdout_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _hop_scan_buf: str = field(default="", repr=False)
    _cli_hop_seen_other_prompt: bool = field(default=False, repr=False)
    _log_fh: Any = field(default=None, repr=False)
    _ready_event: threading.Event = field(default_factory=threading.Event, repr=False)
    # SFTP channel on the same SSH transport as the interactive shell (direct SSH only).
    sftp_ready: bool = False
    _sftp: Any = field(default=None, repr=False)
    _sftp_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def touch(self) -> None:
        self.last_activity = time.time()

    def close_sftp(self) -> None:
        with self._sftp_lock:
            sftp = self._sftp
            self._sftp = None
            self.sftp_ready = False
        if sftp is None:
            return
        try:
            sftp.close()
        except Exception:
            pass

    def _ssh_transport_unlocked(self) -> Any:
        """Caller must hold ``_sftp_lock``. Returns an active Paramiko Transport."""
        if self.closed or self.conn is None:
            raise RuntimeError("session_closed")
        if str(self.protocol or "ssh").lower() != "ssh":
            raise RuntimeError("sftp_requires_ssh")
        if self.cli_hop_guard:
            raise RuntimeError("sftp_hop_not_supported")
        channel = getattr(self.conn, "remote_conn", None)
        transport = None
        if channel is not None and hasattr(channel, "get_transport"):
            try:
                transport = channel.get_transport()
            except Exception:
                transport = None
        if transport is None or not bool(getattr(transport, "is_active", lambda: False)()):
            raise RuntimeError("ssh_transport_unavailable")
        return transport

    def _ensure_sftp_unlocked(self) -> Any:
        """Caller must hold ``_sftp_lock``. Shared probe client (sftp_ready)."""
        import paramiko

        if self._sftp is not None:
            sock = getattr(self._sftp, "sock", None)
            if sock is not None and not bool(getattr(sock, "closed", False)):
                return self._sftp
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        transport = self._ssh_transport_unlocked()
        self._sftp = paramiko.SFTPClient.from_transport(transport)
        if self._sftp is None:
            raise RuntimeError("sftp_open_failed")
        self.sftp_ready = True
        return self._sftp

    def open_sftp(self) -> Any:
        """Open/reuse an SFTP client on this session's SSH transport."""
        with self._sftp_lock:
            return self._ensure_sftp_unlocked()

    def open_ephemeral_sftp(self) -> Any:
        """Open a dedicated SFTP channel for one operation; caller must ``close()`` it.

        Only holds ``_sftp_lock`` briefly while resolving the SSH transport, so long
        list/upload/download work does not block other SFTP ops on the same session.
        """
        import paramiko

        with self._sftp_lock:
            transport = self._ssh_transport_unlocked()
            # Keep probe client warm for UI sftp_ready without sharing it for I/O.
            try:
                self._ensure_sftp_unlocked()
            except Exception:
                pass
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise RuntimeError("sftp_open_failed")
        return sftp

    def run_sftp(self, fn: Any) -> Any:
        """Run ``fn(sftp)`` on an ephemeral channel (does not hold the lock during ``fn``)."""
        sftp = self.open_ephemeral_sftp()
        try:
            return fn(sftp)
        finally:
            try:
                sftp.close()
            except Exception:
                pass

    def try_attach_sftp(self) -> bool:
        """Best-effort SFTP channel open after SSH login (does not fail the shell)."""
        if str(self.protocol or "ssh").lower() != "ssh" or self.cli_hop_guard:
            self.sftp_ready = False
            return False
        try:
            self.open_sftp()
            self.sftp_ready = True
            return True
        except Exception:
            self.sftp_ready = False
            _log.debug("webcrt sftp attach skipped session=%s", self.session_id, exc_info=True)
            return False

    def open_session_log(self) -> None:
        if not bool(getattr(settings, "webcrt_session_log_enabled", True)):
            return
        if self._log_fh is not None:
            return
        try:
            self._log_fh = _session_log_path(self.session_id).open("a", encoding="utf-8", errors="replace")
            self._log_fh.write(f"# session={self.session_id} ne={self.ne_id} ip={self.ne_ip} ts={_utc_iso()}\n")
            self._log_fh.flush()
        except Exception:
            _log.debug("webcrt session log open failed", exc_info=True)
            self._log_fh = None

    def append_session_log(self, text: str) -> None:
        if not text or self._log_fh is None:
            return
        try:
            self._log_fh.write(text)
            self._log_fh.flush()
        except Exception:
            pass

    def close_session_log(self) -> None:
        fh = self._log_fh
        self._log_fh = None
        if fh is None:
            return
        try:
            fh.write(f"\n# closed reason={self.close_reason} ts={_utc_iso()}\n")
            fh.close()
        except Exception:
            pass

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
            remaining = deadline - time.time()
            if remaining <= 0:
                return "empty"
            # Slice waits so we can notice attach_gen bumps without busy-spinning.
            try:
                chunk = self.out_queue.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            with self._stdout_lock:
                if attach_gen != self.attach_gen:
                    # Put back including EOF sentinel so the new owner still sees close.
                    self.out_queue.put(chunk)
                    return "stale"
                return chunk  # bytes | None

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
            channel = getattr(self.conn, "remote_conn", None)
            try:
                if channel is not None and hasattr(channel, "send") and callable(channel.send):
                    payload = _encode_text(text, self.encoding)
                    # Paramiko may write partially when the window is full.
                    view = memoryview(payload)
                    while len(view):
                        n = int(channel.send(view) or 0)
                        if n <= 0:
                            time.sleep(0.01)
                            continue
                        view = view[n:]
                    self.bytes_in += len(payload)
                elif channel is not None and hasattr(channel, "write") and callable(channel.write):
                    payload = _encode_text(text, self.encoding)
                    channel.write(payload)
                    self.bytes_in += len(payload)
                else:
                    self.conn.write_channel(text)
                    self.bytes_in += len(text)
            except Exception:
                self.conn.write_channel(text)
                self.bytes_in += len(text)
        self.touch()

    def send_break(self) -> None:
        """Send SSH break / Telnet IAC BREAK to interrupt paging or hung commands."""
        if self.closed or self.conn is None:
            raise RuntimeError("session_closed")
        channel = getattr(self.conn, "remote_conn", None)
        with self._write_lock:
            sent = False
            if channel is not None and hasattr(channel, "send_break") and callable(channel.send_break):
                try:
                    channel.send_break(0)
                    sent = True
                except Exception:
                    _log.debug("send_break failed session=%s", self.session_id, exc_info=True)
            if not sent and channel is not None and hasattr(channel, "send") and callable(channel.send):
                # Telnet IAC BREAK = 255 243
                try:
                    channel.send(b"\xff\xf3")
                    sent = True
                except Exception:
                    pass
            if not sent:
                # Fallback: Ctrl-C often interrupts device CLI more-pages.
                try:
                    self.conn.write_channel("\x03")
                except Exception:
                    raise RuntimeError("break_failed")
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
        poll = max(0.002, float(getattr(settings, "webcrt_reader_poll_sec", 0.01) or 0.01))
        try:
            while not self.closed:
                chunk = b""
                try:
                    if channel is not None and hasattr(channel, "recv_ready") and hasattr(channel, "recv"):
                        # Paramiko SSH: prefer short blocking recv over fixed spin-sleep.
                        ready = False
                        try:
                            ready = bool(channel.recv_ready())
                        except Exception:
                            ready = False
                        if ready:
                            chunk = channel.recv(16384)
                            if not chunk:
                                break
                        elif hasattr(channel, "exit_status_ready") and channel.exit_status_ready():
                            break
                        else:
                            # Brief block: settimeout + recv wakes sooner than sleep(0.04).
                            prev_timeout = None
                            try:
                                prev_timeout = channel.gettimeout()
                            except Exception:
                                prev_timeout = None
                            try:
                                channel.settimeout(poll)
                                chunk = channel.recv(16384)
                            except Exception:
                                chunk = b""
                            finally:
                                try:
                                    channel.settimeout(prev_timeout)
                                except Exception:
                                    pass
                            if not chunk:
                                continue
                    elif channel is not None and hasattr(channel, "read_very_eager"):
                        # Telnet: do NOT use conn.read_channel() — Netmiko strips ANSI.
                        data = channel.read_very_eager()
                        if data:
                            chunk = (
                                data
                                if isinstance(data, (bytes, bytearray))
                                else _encode_text(str(data), self.encoding)
                            )
                        else:
                            time.sleep(poll)
                            continue
                    else:
                        text = conn.read_channel()
                        if text:
                            chunk = _encode_text(str(text), self.encoding)
                        else:
                            time.sleep(poll)
                            continue
                except Exception as exc:
                    if self.closed:
                        break
                    _log.debug("webcrt reader error session=%s: %s", self.session_id, exc)
                    time.sleep(0.05)
                    continue
                if chunk:
                    self.touch()
                    self.bytes_out += len(chunk)
                    self.out_queue.put(chunk)
                    try:
                        self.append_session_log(_decode_bytes(chunk, self.encoding))
                    except Exception:
                        pass
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
                try:
                    close_session(self.session_id, reason="cli_hop_return")
                except Exception:
                    self.close("cli_hop_return")
            self.out_queue.put(None)

    def _note_cli_hop_output(self, chunk: bytes) -> bool:
        """Accumulate stdout and return True when nested CLI hop has returned to proxy."""
        try:
            text = _decode_bytes(chunk, self.encoding)
        except Exception:
            text = str(chunk)
        self._hop_scan_buf = (self._hop_scan_buf + text)[-12000:]
        marker = str(self.cli_hop_prompt or "").strip()
        last = extract_cli_prompt_marker(self._hop_scan_buf)
        if last and (not marker or last != marker):
            self._cli_hop_seen_other_prompt = True
        return should_close_cli_hop_session(
            self._hop_scan_buf,
            self.cli_hop_prompt,
            seen_other_prompt=self._cli_hop_seen_other_prompt,
        )

    def run_post_login_commands(self) -> None:
        cmds = [str(c).rstrip("\r\n") for c in (self.post_login_commands or []) if str(c).strip()]
        if not cmds or self.closed or self.conn is None:
            return
        for cmd in cmds[:20]:
            try:
                self.write_stdin(cmd + "\r")
                time.sleep(0.15)
            except Exception:
                _log.debug("post_login command failed session=%s", self.session_id, exc_info=True)
                break

    def close(self, reason: str = "closed") -> None:
        if self.closed:
            return
        self.closed = True
        self.state = "closed"
        self.close_reason = reason or "closed"
        self._ready_event.set()
        self.close_sftp()
        try:
            close_netmiko_connection(self.conn)
        except Exception:
            pass
        self.conn = None
        try:
            self.out_queue.put_nowait(None)
        except Exception:
            pass
        self.close_session_log()


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
    anti_idle = max(0, int(getattr(settings, "webcrt_anti_idle_sec", 0) or 0))
    anti_payload = str(getattr(settings, "webcrt_anti_idle_payload", " ") or " ")
    now = time.time()
    to_close: list[tuple[WebcrtSession, str]] = []
    to_nudge: list[WebcrtSession] = []
    with _sessions_lock:
        for sess in list(_sessions.values()):
            if sess.closed:
                _sessions.pop(sess.session_id, None)
                continue
            if sess.state == "connecting":
                # Connecting sessions use connect timeout, not attach timeout alone.
                connect_budget = max(30, int(settings.webcrt_connect_timeout_sec or 90)) + 30
                if (now - sess.connect_started_at) > connect_budget:
                    to_close.append((sess, "connect_timeout"))
                continue
            if sess.attached:
                if (now - sess.last_activity) > idle:
                    to_close.append((sess, "idle_timeout"))
                elif (
                    anti_idle > 0
                    and sess.state == "ready"
                    and sess.conn is not None
                    and (now - sess.last_activity) >= anti_idle
                ):
                    to_nudge.append(sess)
                continue
            # Not attached: either never attached, or briefly detached for reconnect.
            if sess.detach_deadline is not None:
                if now >= sess.detach_deadline:
                    to_close.append((sess, "detach_timeout"))
            else:
                # Start attach clock after connect finishes (not HTTP create time),
                # so slow auth + UI mount does not race attach_timeout.
                anchor = float(sess.connect_finished_at or sess.created_at or now)
                if (now - anchor) > attach:
                    to_close.append((sess, "attach_timeout"))
                elif (now - sess.last_activity) > idle:
                    to_close.append((sess, "idle_timeout"))
    for sess in to_nudge:
        try:
            # Touch without changing visible prompt when payload is empty/null-ish.
            payload = anti_payload
            if payload == "\\0":
                payload = "\x00"
            if payload:
                sess.write_stdin(payload)
            else:
                sess.touch()
        except Exception:
            _log.debug("webcrt anti-idle failed session=%s", sess.session_id, exc_info=True)
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


def find_ssh_session_for_ne(ne_id: str) -> WebcrtSession | None:
    """Prefer a ready/attached interactive SSH session for SFTP channel reuse."""
    nid = str(ne_id or "").strip()
    if not nid:
        return None
    with _sessions_lock:
        candidates = [
            s
            for s in _sessions.values()
            if (not s.closed)
            and str(s.ne_id) == nid
            and str(s.protocol or "ssh").lower() == "ssh"
            and s.conn is not None
            and s.state in ("ready", "connecting")
            and not s.cli_hop_guard
        ]
    if not candidates:
        return None
    # Prefer attached + ready sessions.
    candidates.sort(key=lambda s: (0 if s.attached and s.state == "ready" else 1, -s.last_activity))
    return candidates[0]


def wait_session_ready(session_id: str, *, timeout: float = 120.0) -> WebcrtSession:
    """Block until async connect finishes (ready or error). Used by tests and WS."""
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        sess = get_session(session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="webcrt_session_not_found")
        if sess.state == "ready":
            return sess
        if sess.state == "error":
            raise HTTPException(status_code=502, detail=sess.connect_error or "connect_failed")
        sess._ready_event.wait(timeout=0.25)
    raise HTTPException(status_code=504, detail="connect_timeout")


def _webcrt_creds_ready(creds: dict[str, Any]) -> bool:
    """True when WebCRT can open a session with the resolved credentials.

    Bastion-managed hops store the target password on the bastion side, so an empty
    NE password is valid (same as connectivity test). Direct / manual / Linux hops
    still require a target password for SSH.

    Telnet (no hop) allows empty username/password so the user can authenticate
    interactively in the terminal (SecureCRT-style).
    """
    hop_enabled = bool(creds.get("hop_enabled"))
    hop_vendor = str(creds.get("hop_vendor") or "").strip().lower()
    auth_mode = str(creds.get("hop_target_auth_mode") or "bastion_managed").strip().lower()
    protocol = str(creds.get("protocol") or "ssh").strip().lower()
    if hop_enabled and hop_vendor == "bastion" and auth_mode == "bastion_managed":
        return bool(
            str(creds.get("hop_host") or "").strip()
            and str(creds.get("hop_username") or "").strip()
            and str(creds.get("hop_password") or "")
        )
    if protocol == "telnet" and not hop_enabled:
        return True
    if not str(creds.get("username") or "").strip():
        return False
    return bool(str(creds.get("password") or ""))


def _finish_connect(
    sess: WebcrtSession,
    *,
    creds: dict[str, Any],
    device: dict[str, Any],
    connect_timeout: int,
    client: str,
) -> None:
    log_buf = io.BytesIO()
    try:
        conn = open_netmiko_connection(
            creds,
            session_timeout=connect_timeout,
            session_log=log_buf,
            cols=sess.cols,
            rows=sess.rows,
            interactive=True,
            keepalive=int(sess.keepalive_sec or 0),
        )
    except Exception as exc:
        partial = _session_log_text(log_buf).strip()
        from .ne_cli_errors import format_cli_failure

        classified = format_cli_failure(exc, partial)
        detail = f"connect_failed:{classified}"
        if partial:
            detail = f"{detail}\n--- device transcript ---\n{partial[-4000:]}"
        sess.state = "error"
        sess.connect_error = detail
        sess.connect_finished_at = time.time()
        sess._ready_event.set()
        _audit(
            "session_open_failed",
            session_id=sess.session_id,
            ne_id=sess.ne_id,
            ne_ip=sess.ne_ip,
            source=str(device.get("source") or ""),
            client=client or "",
            error=str(exc)[:500],
            transcript_len=len(partial),
        )
        return

    channel = getattr(conn, "remote_conn", None)
    if channel is not None and hasattr(channel, "resize_pty"):
        try:
            channel.resize_pty(width=sess.cols, height=sess.rows)
        except Exception:
            pass

    pre_log = _session_log_text(log_buf)
    # Pull post-auth banner/MOTD from the PTY. With interactive no-op session_preparation
    # (generic_termserver), Netmiko session_log is often empty — do not discard these bytes.
    try:
        early = _capture_raw_channel(conn, duration=0.35)
    except Exception:
        early = ""
    seed = f"{pre_log}{early}"
    already_prompted = _looks_like_cli_prompt(seed)
    primed = ""
    # Do not send Enter at Username:/Password: or Huawei password-change [Y/N]:
    # (Netmiko telnet_login already answers password-change with "N").
    if _looks_like_login_prompt(seed) or _looks_like_password_change_prompt(seed):
        try:
            primed = _capture_raw_channel(conn, duration=0.9)
        except Exception:
            primed = ""
    else:
        try:
            primed = _prime_interactive_channel(conn, already_prompted=already_prompted)
        except Exception:
            primed = ""
    combined = f"{seed}{primed}"
    # Final settle: keep stragglers in bootstrap (normalize collapses duplicate prompts).
    try:
        combined += _capture_raw_channel(conn, duration=0.35)
    except Exception:
        pass
    if not str(combined).strip():
        try:
            combined = _drain_channel(conn, rounds=6, wait=0.08)
        except Exception:
            combined = ""
    bootstrap = prepare_bootstrap_output(combined)
    # Discard lone punctuation left on the wire (would glue onto ``<r1>`` in xterm).
    try:
        leftover = _capture_raw_channel(conn, duration=0.12)
    except Exception:
        leftover = ""
    if leftover and leftover.strip() not in {":", ">", "#", "]", "$"}:
        bootstrap = prepare_bootstrap_output(f"{bootstrap}{leftover}")

    hop_guard = get_cli_hop_guard(conn)
    sess.conn = conn
    sess.cli_hop_guard = bool(hop_guard)
    sess.cli_hop_prompt = str((hop_guard or {}).get("hop_prompt") or "")
    sess.bootstrap_output = _encode_text(str(bootstrap or ""), sess.encoding)
    # Nudge Enter on WS attach only when we still need a shell prompt.
    # Never when already at CLI prompt or Username:/Password: (would empty-submit login).
    sess.needs_live_prompt = (
        not _looks_like_cli_prompt(bootstrap) and not _looks_like_login_prompt(bootstrap)
    )
    sess.open_session_log()
    if bootstrap:
        sess.append_session_log(bootstrap if bootstrap.endswith("\n") else bootstrap + "\n")
    sess.start_reader()
    # Drop late prompt echoes that race into the queue right after reader start.
    prompt_hint = ""
    if bootstrap:
        prompt_hint = str(bootstrap).replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")[-1].strip()
    settle_deadline = time.time() + 0.45
    while time.time() < settle_deadline:
        try:
            chunk = sess.out_queue.get_nowait()
        except queue.Empty:
            time.sleep(0.02)
            continue
        if chunk is None:
            sess.out_queue.put(None)
            break
        try:
            text = _decode_bytes(chunk, sess.encoding)
        except Exception:
            text = ""
        if _is_prompt_only_echo(text, prompt_hint):
            continue
        # Non-prompt data: put back and stop settling.
        sess.out_queue.put(chunk)
        break
    try:
        sess.run_post_login_commands()
    except Exception:
        _log.debug("post_login failed session=%s", sess.session_id, exc_info=True)
    # Same SSH transport: open SFTP channel when the device supports it.
    sftp_ok = sess.try_attach_sftp()
    sess.state = "ready"
    sess.connect_finished_at = time.time()
    sess._ready_event.set()
    elapsed_ms = int((sess.connect_finished_at - sess.connect_started_at) * 1000)
    _audit(
        "session_created",
        session_id=sess.session_id,
        ne_id=sess.ne_id,
        ne_name=sess.ne_name,
        ne_ip=sess.ne_ip,
        protocol=sess.protocol,
        encoding=sess.encoding,
        source=str(device.get("source") or ""),
        hop_enabled=bool(creds.get("hop_enabled")),
        hop_vendor=str(creds.get("hop_vendor") or "") if creds.get("hop_enabled") else "",
        cli_hop_guard=bool(hop_guard),
        cli_hop_prompt=str((hop_guard or {}).get("hop_prompt") or ""),
        sftp_ready=bool(sftp_ok),
        client=client or "",
        connect_ms=elapsed_ms,
        active=active_session_count(),
    )


def create_session(
    db: Session,
    *,
    ne_id: str | None = None,
    ume_ne_id: str | None = None,
    cols: int = 80,
    rows: int = 24,
    client: str = "",
    encoding: str = "utf-8",
    keepalive_sec: int | None = None,
    post_login_commands: list[str] | None = None,
    async_connect: bool = True,
    username_override: str | None = None,
    password_override: str | None = None,
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

    # One-shot credentials for SecureCRT-style "do not save password" / retry.
    if username_override is not None and str(username_override).strip():
        creds["username"] = str(username_override).strip()
    if password_override is not None:
        creds["password"] = str(password_override)

    protocol = str(device.get("protocol") or creds.get("protocol") or "ssh").strip().lower()
    creds["protocol"] = protocol
    # Netmiko telnet drivers dislike a completely missing username; use a placeholder
    # for the wire only (interactive login still happens in the terminal).
    if protocol == "telnet" and not bool(creds.get("hop_enabled")) and not str(creds.get("username") or "").strip():
        creds["username"] = "telnet"

    if not _webcrt_creds_ready(creds):
        raise HTTPException(status_code=400, detail="credentials_incomplete")

    session_id = str(uuid.uuid4())
    c = max(20, min(500, int(cols or 80)))
    r = max(5, min(200, int(rows or 24)))
    connect_timeout = max(30, int(settings.webcrt_connect_timeout_sec or 90))
    target_id = str(device.get("id") or mid or uid)
    target_ip = str(device.get("ip_address") or "")
    target_name = str(device.get("name") or target_ip)
    device_type = str(device.get("device_type") or creds.get("device_type") or "")
    vendor = str(device.get("vendor") or creds.get("vendor") or "")
    cli_keymap = uses_network_cli_keymap(device_type, vendor)
    enc = _normalize_encoding(encoding)
    if keepalive_sec is None:
        ka = max(0, int(getattr(settings, "webcrt_keepalive_sec", 0) or 0))
    else:
        ka = max(0, min(600, int(keepalive_sec)))

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
        encoding=enc,
        keepalive_sec=ka,
        state="connecting",
        post_login_commands=list(post_login_commands or [])[:20],
    )
    with _sessions_lock:
        _sessions[session_id] = sess

    _audit(
        "session_connecting",
        session_id=session_id,
        ne_id=sess.ne_id,
        ne_ip=sess.ne_ip,
        protocol=sess.protocol,
        encoding=enc,
        client=client or "",
        async_connect=bool(async_connect),
    )

    if async_connect:
        t = threading.Thread(
            target=_finish_connect,
            kwargs={
                "sess": sess,
                "creds": creds,
                "device": device,
                "connect_timeout": connect_timeout,
                "client": client or "",
            },
            name=f"webcrt-connect-{session_id[:8]}",
            daemon=True,
        )
        t.start()
    else:
        _finish_connect(
            sess,
            creds=creds,
            device=device,
            connect_timeout=connect_timeout,
            client=client or "",
        )
        if sess.state == "error":
            with _sessions_lock:
                _sessions.pop(session_id, None)
            raise HTTPException(status_code=502, detail=sess.connect_error or "connect_failed")

    return {
        "session_id": session_id,
        "ne_id": sess.ne_id,
        "ne_name": sess.ne_name,
        "ne_ip": sess.ne_ip,
        "source": str(device.get("source") or ""),
        "protocol": sess.protocol,
        "cols": sess.cols,
        "rows": sess.rows,
        "encoding": enc,
        "keepalive_sec": ka,
        "state": sess.state,
        "ws_path": f"/v1/webcrt/sessions/{session_id}/ws",
        "cli_hop": bool(sess.cli_hop_guard),
        "sftp_ready": bool(sess.sftp_ready),
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
        state=sess.state,
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
            bytes_in=sess.bytes_in,
            bytes_out=sess.bytes_out,
            queue_dropped=getattr(sess.out_queue, "dropped", 0),
            active=active_session_count(),
        )
    return {"ok": True, "session_id": session_id, "closed": True, "reason": reason}


def list_sessions() -> dict[str, Any]:
    with _sessions_lock:
        items = []
        for s in _sessions.values():
            if s.closed:
                continue
            state = str(s.state or "unknown")
            attached = bool(s.attached)
            # Lifecycle for ops UI: distinguish login vs live vs grace-period detach.
            if state == "connecting":
                lifecycle = "connecting"
            elif state == "error":
                lifecycle = "error"
            elif state == "ready" and attached:
                lifecycle = "ready"
            elif state == "ready" and not attached:
                lifecycle = "detached"
            else:
                lifecycle = state
            elapsed_ms = None
            if state == "connecting":
                elapsed_ms = int(max(0.0, time.time() - float(s.connect_started_at or time.time())) * 1000)
            items.append(
                {
                    "session_id": s.session_id,
                    "ne_id": s.ne_id,
                    "ne_name": s.ne_name,
                    "ne_ip": s.ne_ip,
                    "protocol": s.protocol,
                    "encoding": s.encoding,
                    "keepalive_sec": int(s.keepalive_sec or 0),
                    "state": state,
                    "lifecycle": lifecycle,
                    "attached": attached,
                    "detach_deadline": s.detach_deadline,
                    "connect_error": str(s.connect_error or "")[:500],
                    "elapsed_ms": elapsed_ms,
                    "created_at": datetime.fromtimestamp(s.created_at, tz=timezone.utc).isoformat(),
                    "last_activity": datetime.fromtimestamp(s.last_activity, tz=timezone.utc).isoformat(),
                    "bytes_in": s.bytes_in,
                    "bytes_out": s.bytes_out,
                    "queue_depth": s.out_queue.qsize(),
                    "queue_dropped": getattr(s.out_queue, "dropped", 0),
                    "connect_ms": (
                        int((s.connect_finished_at - s.connect_started_at) * 1000)
                        if s.connect_finished_at
                        else None
                    ),
                }
            )
    return {
        "total": len(items),
        "max_sessions": max(1, int(settings.webcrt_max_sessions or 20)),
        "idle_timeout_sec": max(60, int(settings.webcrt_idle_timeout_sec or 1800)),
        "keepalive_sec": int(getattr(settings, "webcrt_keepalive_sec", 0) or 0),
        "anti_idle_sec": int(getattr(settings, "webcrt_anti_idle_sec", 0) or 0),
        "items": items,
    }
