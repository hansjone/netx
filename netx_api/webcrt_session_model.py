"""WebCRT interactive session object (I/O, SFTP, close)."""
from __future__ import annotations

import codecs
import logging
import queue
import re
import select
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from netmiko import ConnectHandler

from .config import settings
from .ne_session_factory import (
    close_netmiko_connection,
    extract_cli_prompt_marker,
    should_close_cli_hop_session,
)
from .webcrt_cmd_audit import AuditCmd, CommandTracker
from .webcrt_channel import (
    _BoundedByteQueue,
    _normalize_encoding,
    _audit,
    _decode_bytes,
    _encode_text,
    _session_log_path,
    _utc_iso,
    feed_command_line_buffer,
    looks_like_password_prompt,
    map_network_cli_enter,
    map_network_cli_keys,
)

_log = logging.getLogger("netx.webcrt")

# Operator typed an exit-style command (arms the hop prompt-return heuristic).
_HOP_EXIT_RE = re.compile(r"(?:^|[\r\n])\s*(?:quit|exit|logout|logoff|q|qu|qui|ex|exi)\s*[\r\n]", re.IGNORECASE)

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
    # Owning netx user; empty = legacy unbound (tests / auth_disabled).
    owner_user_id: str = ""
    owner_username: str = ""
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
    # Live connect transcript (hop/stelnet/bastion) streamed to WS before ready.
    connect_echo_acc: str = ""
    connect_echo_q: queue.Queue = field(default_factory=queue.Queue, repr=False)
    _connect_echo_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
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
    # Interactive command audit: line buffer + password-prompt redaction.
    _cmd_buf: str = field(default="", repr=False)
    _cmd_buf_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _password_mode: bool = field(default=False, repr=False)
    _stdout_tail: str = field(default="", repr=False)
    _last_prompt_line: str = field(default="", repr=False)
    _last_audited_command: str = field(default="", repr=False)
    _last_audited_at: float = field(default=0.0, repr=False)
    # Live audit: device-echo based command tracker (active while the reader runs).
    _tracker: CommandTracker = field(default_factory=CommandTracker, repr=False)
    # asyncio wake-up for the WS stdout pump: (loop, event) + "already scheduled" flag.
    _notify: Any = field(default=None, repr=False)
    _wake_flag: bool = field(default=False, repr=False)
    _log_last_flush: float = field(default=0.0, repr=False)
    _log_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _hop_exit_until: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self._tracker.linux_like = not bool(self.cli_keymap)

    def touch(self) -> None:
        self.last_activity = time.time()

    def _should_emit_command_audit(self, cmd: str, *, dedup_sec: float = 0.45) -> bool:
        """Drop duplicate audits from double-fired Enter (same command within dedup_sec)."""
        norm = str(cmd or "").strip()
        if not norm:
            return False
        now = time.time()
        with self._cmd_buf_lock:
            if norm == self._last_audited_command and (now - self._last_audited_at) < dedup_sec:
                return False
            self._last_audited_command = norm
            self._last_audited_at = now
        return True

    def push_connect_echo(self, text: str) -> None:
        """Queue login transcript for the WS wait-loop (thread-safe)."""
        chunk = str(text or "")
        if not chunk or self.closed:
            return
        with self._connect_echo_lock:
            self.connect_echo_acc += chunk
        try:
            self.connect_echo_q.put_nowait(chunk)
        except Exception:
            pass

    def drain_connect_echo(self) -> str:
        """Pop all pending connect-echo chunks (called from asyncio WS loop)."""
        parts: list[str] = []
        while True:
            try:
                parts.append(self.connect_echo_q.get_nowait())
            except queue.Empty:
                break
        return "".join(parts)

    def connect_echo_text(self) -> str:
        with self._connect_echo_lock:
            return str(self.connect_echo_acc or "")

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
        """Buffered transcript write; flushed by the reader every ~250ms (and on attach)."""
        fh = self._log_fh
        if not text or fh is None:
            return
        try:
            with self._log_lock:
                fh.write(text)
            if time.monotonic() - self._log_last_flush >= 0.25:
                self.flush_session_log()
        except Exception:
            _log.warning("webcrt session log append failed session=%s", self.session_id, exc_info=True)

    def flush_session_log(self) -> None:
        fh = self._log_fh
        if fh is None:
            return
        try:
            with self._log_lock:
                fh.flush()
                self._log_last_flush = time.monotonic()
                max_bytes = int(getattr(settings, "webcrt_session_log_max_bytes", 0) or 0)
                if max_bytes <= 0:
                    return
                try:
                    pos = int(fh.tell())
                except Exception:  # noqa: BLE001
                    pos = 0
                if pos < max_bytes:
                    return
                fh.write(f"\n# rotated at {pos} bytes (cap={max_bytes}) ts={_utc_iso()}\n")
                fh.flush()
                fh.close()
                self._log_fh = None
            # Re-open fresh file (rotate by rename).
            try:
                path = _session_log_path(self.session_id)
                rotated = path.with_suffix(path.suffix + f".{int(pos)}.old")
                if path.exists():
                    path.replace(rotated)
            except Exception:  # noqa: BLE001
                _log.debug("webcrt session log rotate rename failed", exc_info=True)
            self.open_session_log()
        except Exception:
            _log.warning("webcrt session log flush failed session=%s", self.session_id, exc_info=True)

    def close_session_log(self) -> None:
        with self._log_lock:
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

    # ---- asyncio hand-off (no polling thread per WebSocket) -------------------- #
    def set_notifier(self, loop: Any, event: Any) -> None:
        with self._stdout_lock:
            self._notify = (loop, event)
            self._wake_flag = False

    def clear_notifier(self, event: Any) -> None:
        with self._stdout_lock:
            n = self._notify
            if n is not None and n[1] is event:
                self._notify = None

    def bump_attach(self) -> int:
        """New WebSocket owns stdout: invalidate the previous pump and wake it."""
        with self._stdout_lock:
            self.attach_gen += 1
            gen = self.attach_gen
        self._wake(force=True)
        return gen

    def _wake(self, *, force: bool = False) -> None:
        """Called from any thread after queue changes; schedules at most one loop wake-up."""
        n = self._notify
        if n is None:
            return
        if self._wake_flag and not force:
            return
        self._wake_flag = True
        try:
            n[0].call_soon_threadsafe(n[1].set)
        except Exception:
            self._wake_flag = False
            self._notify = None

    def drain_stdout(self, attach_gen: int, max_bytes: int = 262144) -> tuple[str, bytes]:
        """Non-blocking take for the WS pump.

        Returns ``(status, data)``: ``stale`` (newer WS owns stdout), ``eof`` (reader
        ended; ``data`` may still hold the final bytes), ``data`` or ``empty``.
        """
        self._wake_flag = False
        with self._stdout_lock:
            if attach_gen != self.attach_gen:
                return "stale", b""
            chunks, eof = self.out_queue.drain(max_bytes)
        data = b"".join(chunks)
        if eof:
            return "eof", data
        return ("data" if data else "empty"), data

    # ---- stdin ----------------------------------------------------------------- #
    def write_stdin(
        self,
        data: str,
        *,
        audit_source: str = "stdin",
        audit_line: str | None = None,
        audit_lines: list[str] | None = None,
    ) -> None:
        if self.closed or self.conn is None:
            raise RuntimeError("session_closed")
        text = str(data or "")
        if not text:
            return
        audit_override = str(audit_line).strip() if audit_line is not None else None
        audit_list = [str(x).strip() for x in (audit_lines or []) if str(x).strip()] or None
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
        if self.cli_hop_guard and (_HOP_EXIT_RE.search(text) or "\x04" in text):
            self._hop_exit_until = time.monotonic() + 8.0
        live_cmds: list[AuditCmd] = []
        if self._audit_live():
            # Register Enter first (cheap), send to the device, then publish: the audit
            # row is resolved from the device echo, so nothing here delays the keystroke.
            hints = list(audit_list or [])
            if audit_override and (not hints or hints[-1] != audit_override):
                hints.append(audit_override)
            live_cmds = self._audit_stdin_live(text, source=str(audit_source or "stdin"), hints=hints)
        else:
            self._note_stdin_for_audit(
                text,
                source=audit_source,
                audit_line=audit_override,
                audit_lines=audit_list,
            )
        self._send_raw(text)
        if live_cmds:
            self._emit_audit_cmds(live_cmds)
        self.touch()

    def _send_raw(self, text: str) -> None:
        with self._write_lock:
            # Prefer raw channel I/O for interactive typing (char echo / backspace).
            conn = self.conn
            if conn is None:
                raise RuntimeError("session_closed")
            channel = getattr(conn, "remote_conn", None)
            try:
                if channel is not None and hasattr(channel, "send") and callable(channel.send):
                    payload = _encode_text(text, self.encoding)
                    # Paramiko may write partially when the window is full; the reader
                    # keeps a short channel timeout, so a timed-out send is simply retried.
                    view = memoryview(payload)
                    deadline = time.monotonic() + 30.0
                    while len(view):
                        try:
                            n = int(channel.send(view) or 0)
                        except socket.timeout:
                            if time.monotonic() > deadline or self.closed:
                                raise
                            continue
                        if n <= 0:
                            time.sleep(0.005)
                            continue
                        view = view[n:]
                    self.bytes_in += len(payload)
                elif channel is not None and hasattr(channel, "write") and callable(channel.write):
                    payload = _encode_text(text, self.encoding)
                    channel.write(payload)
                    self.bytes_in += len(payload)
                else:
                    conn.write_channel(text)
                    self.bytes_in += len(text)
            except Exception:
                if self.closed or self.conn is None:
                    raise
                self.conn.write_channel(text)
                self.bytes_in += len(text)

    # ---- command audit ------------------------------------------------------- #
    def _audit_live(self) -> bool:
        """Device-echo audit is active only while the reader thread feeds the tracker."""
        t = self._reader
        return t is not None and t.is_alive()

    def _audit_stdin_live(self, text: str, *, source: str, hints: list[str]) -> list[AuditCmd]:
        if source == "prompt_sync":
            return []
        had_enter = "\r" in text or "\n" in text
        abort_text = ""
        with self._cmd_buf_lock:
            cut = text.find("\x03")
            if cut >= 0:
                abort_text, _ = feed_command_line_buffer(self._cmd_buf, text[:cut])
            self._cmd_buf, buf_lines = feed_command_line_buffer(self._cmd_buf, text)
        if source == "post_login":
            return self._tracker.immediate(buf_lines, source) if had_enter else []
        saw_input = bool(text.strip("\r\n"))
        return self._tracker.on_stdin(
            buf_lines,
            saw_input=saw_input,
            had_enter=had_enter,
            client_hints=hints,
            source=source,
            abort="\x03" in text,
            abort_text=abort_text,
        )

    def _emit_audit_cmds(self, cmds: list[AuditCmd]) -> None:
        for c in cmds:
            if not str(c.command).strip():
                continue
            try:
                _audit(
                    "command",
                    session_id=self.session_id,
                    ne_id=self.ne_id,
                    ne_name=self.ne_name,
                    ne_ip=self.ne_ip,
                    protocol=self.protocol,
                    owner_user_id=self.owner_user_id,
                    owner_username=self.owner_username,
                    command="***" if c.redacted else str(c.command)[:512],
                    redacted=bool(c.redacted),
                    source=str(c.source or "stdin")[:32],
                    via=c.via,
                    prompt=str(c.prompt or "")[:128],
                )
            except Exception:
                _log.debug("webcrt command audit failed session=%s", self.session_id, exc_info=True)

    def seed_audit_row(self, transcript: str) -> None:
        """Prime the input row from the login transcript (prompt printed before the reader)."""
        try:
            self._tracker.set_row("")
            self._tracker.seed(str(transcript or "")[-4000:])
        except Exception:
            _log.debug("webcrt audit seed failed session=%s", self.session_id, exc_info=True)

    def reset_audit_row(self, row: str) -> None:
        try:
            self._tracker.set_row(row)
        except Exception:
            pass

    def _note_stdin_for_audit(
        self,
        text: str,
        *,
        source: str = "stdin",
        audit_line: str | None = None,
        audit_lines: list[str] | None = None,
    ) -> None:
        """Extract completed command lines from stdin and emit webcrt.command audits."""
        need_echo_settle = False
        with self._cmd_buf_lock:
            self._cmd_buf, buf_lines = feed_command_line_buffer(self._cmd_buf, text)
            redacted = bool(self._password_mode)
            if redacted and (buf_lines or audit_line or audit_lines):
                self._password_mode = False
            if "\r" in text or "\n" in text:
                # Tab completion / abbrev expansion often lands in stdout just after Enter.
                # History-arrow edits only send a short fragment ("33"/"ip") — wait for
                # the device CSI redraw of the full line before picking the audit text.
                from .webcrt_channel import _looks_like_edit_fragment

                need_echo_settle = any("\t" in str(x) for x in buf_lines) or ("\t" in str(text))
                if not need_echo_settle and self._last_prompt_line:
                    need_echo_settle = any(
                        _looks_like_edit_fragment(str(x), self._last_prompt_line) for x in buf_lines
                    )
            else:
                return

        if need_echo_settle and str(source or "stdin") == "stdin" and not redacted:
            # Let device redraw / execute-echo update _stdout_tail before we pick the line.
            time.sleep(0.12)

        with self._cmd_buf_lock:
            from .webcrt_channel import resolve_audit_commands

            if redacted and (buf_lines or audit_line or audit_lines):
                lines = ["***"] * max(1, len(buf_lines) or 1)
            else:
                try:
                    lines = resolve_audit_commands(
                        buf_lines,
                        audit_line=audit_line,
                        audit_lines=audit_lines,
                        prompt_hint=self._last_prompt_line,
                        stdout_tail=self._stdout_tail,
                        source=str(source or "stdin"),
                    )
                except Exception:
                    _log.exception(
                        "webcrt resolve_audit_commands failed session=%s", self.session_id
                    )
                    lines = []
            self._last_prompt_line = ""
        for cmd in lines:
            if not str(cmd).strip():
                continue
            if not self._should_emit_command_audit(cmd):
                continue
            try:
                _audit(
                    "command",
                    session_id=self.session_id,
                    ne_id=self.ne_id,
                    ne_name=self.ne_name,
                    ne_ip=self.ne_ip,
                    protocol=self.protocol,
                    owner_user_id=self.owner_user_id,
                    owner_username=self.owner_username,
                    command="***" if redacted else str(cmd)[:512],
                    redacted=bool(redacted),
                    source=str(source or "stdin")[:32],
                )
            except Exception:
                _log.debug("webcrt command audit failed session=%s", self.session_id, exc_info=True)

    def _note_stdout_for_audit(self, text: str) -> None:
        """Feed device output to the audit tracker (live) or the legacy tail buffer."""
        chunk = str(text or "")
        if not chunk:
            return
        if self._audit_live():
            cmds = self._tracker.feed(chunk)
            self._password_mode = self._tracker.password_mode
            if cmds:
                self._emit_audit_cmds(cmds)
            return
        self._note_stdout_legacy(chunk)

    def _audit_housekeeping(self) -> None:
        if self._tracker.pending:
            cmds = self._tracker.expire()
            if cmds:
                self._emit_audit_cmds(cmds)
        self.flush_session_log()

    def _note_stdout_legacy(self, text: str) -> None:
        """Track device prompts so the next typed line can be redacted if it is a password."""
        chunk = str(text or "")
        if not chunk:
            return
        with self._cmd_buf_lock:
            self._stdout_tail = (self._stdout_tail + chunk)[-8000:]
            from .webcrt_channel import extract_last_prompt_command

            line = extract_last_prompt_command(self._stdout_tail)
            if line:
                self._last_prompt_line = line
            else:
                from .webcrt_channel import _is_prompt_only_line, _strip_ansi

                tail = _strip_ansi(self._stdout_tail).rsplit("\r", 1)[-1].split("\n")[-1]
                if _is_prompt_only_line(tail):
                    self._last_prompt_line = ""
            if looks_like_password_prompt(self._stdout_tail):
                self._password_mode = True

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

    @staticmethod
    def _wait_readable(channel: Any, timeout: float) -> None:
        """Block until a telnet-style socket is readable (or timeout)."""
        sock = getattr(channel, "sock", None)
        try:
            if sock is not None and sock.fileno() >= 0:
                select.select([sock], [], [], timeout)
                return
        except Exception:
            pass
        time.sleep(min(timeout, 0.01))

    def _reader_loop(self) -> None:
        conn = self.conn
        if conn is None:
            self.out_queue.put(None)
            self._wake()
            return
        channel = getattr(conn, "remote_conn", None)
        hop_return = False
        poll = max(0.002, float(getattr(settings, "webcrt_reader_poll_sec", 0.01) or 0.01))
        # Incremental decode: a multi-byte char split across recv() chunks stays intact.
        dec = codecs.getincrementaldecoder("gbk" if _normalize_encoding(self.encoding) == "gbk" else "utf-8")(
            errors="replace"
        )
        # Paramiko: park in recv() with a short timeout (wakes the moment data arrives)
        # instead of spin-polling recv_ready().
        blocking = False
        if (
            channel is not None
            and hasattr(channel, "recv")
            and hasattr(channel, "recv_ready")
            and hasattr(channel, "settimeout")
        ):
            try:
                channel.settimeout(0.2)
                blocking = True
            except Exception:
                blocking = False
        last_house = time.monotonic()
        try:
            while not self.closed:
                chunk = b""
                try:
                    if blocking:
                        try:
                            chunk = channel.recv(65536)
                        except socket.timeout:
                            chunk = b""
                        else:
                            if not chunk:
                                break  # EOF
                        if not chunk:
                            if channel.recv_ready():
                                continue
                            if getattr(channel, "closed", False) or (
                                hasattr(channel, "exit_status_ready") and channel.exit_status_ready()
                            ):
                                break
                    elif channel is not None and hasattr(channel, "recv_ready") and hasattr(channel, "recv"):
                        if channel.recv_ready():
                            chunk = channel.recv(65536)
                            if not chunk:
                                break
                        elif hasattr(channel, "exit_status_ready") and channel.exit_status_ready():
                            break
                        else:
                            time.sleep(poll)
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
                            self._wait_readable(channel, 0.2)
                    else:
                        text = conn.read_channel()
                        if text:
                            chunk = _encode_text(str(text), self.encoding)
                        else:
                            time.sleep(poll)
                except EOFError:
                    break
                except Exception as exc:
                    if self.closed:
                        break
                    _log.debug("webcrt reader error session=%s: %s", self.session_id, exc)
                    if channel is not None and getattr(channel, "closed", False):
                        break
                    time.sleep(0.05)
                    continue
                now = time.monotonic()
                if chunk:
                    self.last_activity = time.time()
                    self.bytes_out += len(chunk)
                    self.out_queue.put(chunk)
                    self._wake()
                    decoded = ""
                    try:
                        decoded = dec.decode(bytes(chunk))
                        if decoded:
                            self.append_session_log(decoded)
                            self._note_stdout_for_audit(decoded)
                    except Exception:
                        _log.debug("webcrt reader side-effects failed session=%s", self.session_id, exc_info=True)
                    if self.cli_hop_guard and self._note_cli_hop_output(chunk, decoded):
                        hop_return = True
                        notice = (
                            "\r\n*** WebCRT: 目标会话已结束，已断开代理连接 "
                            "(target session ended; closing hop proxy) ***\r\n"
                        )
                        self.out_queue.put(notice.encode("utf-8", errors="replace"))
                        self._wake()
                        break
                if now - last_house >= 0.25:
                    last_house = now
                    try:
                        self._audit_housekeeping()
                    except Exception:
                        _log.debug("webcrt housekeeping failed session=%s", self.session_id, exc_info=True)
        finally:
            if hop_return and not self.closed:
                try:
                    from .webcrt_session_registry import close_session

                    close_session(self.session_id, reason="cli_hop_return")
                except Exception:
                    self.close("cli_hop_return")
            self.out_queue.put(None)
            self._wake()
            try:
                self.flush_session_log()
            except Exception:
                pass

    def _note_cli_hop_output(self, chunk: bytes, text: str | None = None) -> bool:
        """Accumulate stdout and return True when nested CLI hop has returned to proxy."""
        if text is None:
            try:
                text = _decode_bytes(chunk, self.encoding)
            except Exception:
                text = str(chunk)
        self._hop_scan_buf = (self._hop_scan_buf + text)[-12000:]
        marker = str(self.cli_hop_prompt or "").strip()
        last = extract_cli_prompt_marker(self._hop_scan_buf)
        if last and (not marker or last != marker):
            self._cli_hop_seen_other_prompt = True
        # Prompt-only "back on the hop" is ambiguous when hop and target share a hostname
        # (target ``system-view`` prints the same ``[~r1]`` as the hop). Only trust it
        # shortly after the operator typed an exit command; the nested "connection closed"
        # message needs no such gate.
        armed = self._cli_hop_seen_other_prompt and time.monotonic() <= self._hop_exit_until
        return should_close_cli_hop_session(
            self._hop_scan_buf,
            self.cli_hop_prompt,
            seen_other_prompt=armed,
        )

    def run_post_login_commands(self) -> None:
        cmds = [str(c).rstrip("\r\n") for c in (self.post_login_commands or []) if str(c).strip()]
        if not cmds or self.closed or self.conn is None:
            return
        for cmd in cmds[:20]:
            try:
                self.write_stdin(cmd + "\r", audit_source="post_login")
                time.sleep(0.15)
            except Exception:
                _log.debug("post_login command failed session=%s", self.session_id, exc_info=True)
                break

    def _teardown_conn(self, conn: Any) -> None:
        self.close_sftp()
        if conn is None:
            return
        for attr in ("remote_conn", "remote_conn_pre", "_netx_jump_client"):
            obj = getattr(conn, attr, None)
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass

    def close(self, reason: str = "closed") -> None:
        if self.closed:
            return
        self.closed = True
        self.state = "closed"
        self.close_reason = reason or "closed"
        self._ready_event.set()
        # Enters still waiting for a device echo would be lost with the session.
        try:
            pending_cmds = self._tracker.flush_all()
            if pending_cmds:
                self._emit_audit_cmds(pending_cmds)
        except Exception:
            _log.debug("webcrt audit flush on close failed session=%s", self.session_id, exc_info=True)
        conn = self.conn
        self.conn = None
        # Tear the transport down off-thread: Netmiko's disconnect() runs vendor cleanup
        # (prompt probing, several seconds on Huawei) which would stall the event loop /
        # reaper, and keeps the device VTY occupied longer than needed.
        threading.Thread(
            target=self._teardown_conn,
            args=(conn,),
            name=f"webcrt-close-{self.session_id[:8]}",
            daemon=True,
        ).start()
        try:
            self.out_queue.put_nowait(None)
        except Exception:
            pass
        self._wake(force=True)
        self.close_session_log()
