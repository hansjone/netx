"""WebCRT interactive session object (I/O, SFTP, close)."""
from __future__ import annotations

import logging
import queue
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
from .webcrt_channel import (
    _BoundedByteQueue,
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

    def touch(self) -> None:
        self.last_activity = time.time()

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
        if not text or self._log_fh is None:
            return
        try:
            self._log_fh.write(text)
            self._log_fh.flush()
            max_bytes = int(getattr(settings, "webcrt_session_log_max_bytes", 0) or 0)
            if max_bytes > 0:
                try:
                    pos = int(self._log_fh.tell())
                except Exception:  # noqa: BLE001
                    pos = 0
                if pos >= max_bytes:
                    self._log_fh.write(
                        f"\n# rotated at {pos} bytes (cap={max_bytes}) ts={_utc_iso()}\n"
                    )
                    self._log_fh.flush()
                    self._log_fh.close()
                    self._log_fh = None
                    # Re-open fresh file (append mode continues same path; rotate by rename).
                    try:
                        path = _session_log_path(self.session_id)
                        rotated = path.with_suffix(path.suffix + f".{int(pos)}.old")
                        if path.exists():
                            path.replace(rotated)
                    except Exception:  # noqa: BLE001
                        _log.debug("webcrt session log rotate rename failed", exc_info=True)
                    self.open_session_log()
        except Exception:
            _log.warning("webcrt session log append failed session=%s", self.session_id, exc_info=True)

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

    def write_stdin(self, data: str, *, audit_source: str = "stdin", audit_line: str | None = None) -> None:
        if self.closed or self.conn is None:
            raise RuntimeError("session_closed")
        text = str(data or "")
        if not text:
            return
        audit_override = str(audit_line).strip() if audit_line is not None else None
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
        self._note_stdin_for_audit(text, source=audit_source, audit_line=audit_override)
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

    def _note_stdin_for_audit(
        self,
        text: str,
        *,
        source: str = "stdin",
        audit_line: str | None = None,
    ) -> None:
        """Extract completed command lines from stdin and emit webcrt.command audits."""
        with self._cmd_buf_lock:
            self._cmd_buf, buf_lines = feed_command_line_buffer(self._cmd_buf, text)
            redacted = bool(self._password_mode)
            if redacted and (buf_lines or audit_line):
                self._password_mode = False
            if audit_line is not None and ("\r" in text or "\n" in text):
                from .webcrt_channel import normalize_audit_line

                cmd = normalize_audit_line(audit_line)
                lines = [cmd] if cmd.strip() else []
            else:
                lines = buf_lines
        for cmd in lines:
            if not str(cmd).strip():
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
        """Track device prompts so the next typed line can be redacted if it is a password."""
        chunk = str(text or "")
        if not chunk:
            return
        with self._cmd_buf_lock:
            self._stdout_tail = (self._stdout_tail + chunk)[-4000:]
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
                        decoded = _decode_bytes(chunk, self.encoding)
                        self.append_session_log(decoded)
                        self._note_stdout_for_audit(decoded)
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
                    from .webcrt_session_registry import close_session

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
                self.write_stdin(cmd + "\r", audit_source="post_login")
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
