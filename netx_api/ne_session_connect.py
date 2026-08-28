"""Netmiko connect paths: direct, vendor CLI hop, bastion, and Linux jump."""
from __future__ import annotations

import io
import logging
import re
import threading
import time
from typing import Any

import paramiko
from netmiko import ConnectHandler

from .config import settings
from .ne_cli_hop import (
    _attach_cli_hop_guard,
    extract_cli_prompt_marker,
)
from .ne_hop_templates import (
    _hop_vendor,
    expand_bastion_hop_fields,
    normalize_hop_host,
    render_hop_command,
    resolve_bastion_ssh_username,
)
from .ne_netmiko import normalize_netmiko_device_type

_log = logging.getLogger("netx.ne.session")

def _bastion_interactive_handler(password: str) -> tuple[Any, list[str]]:
    """Reply to bastion keyboard-interactive prompts (Vault password, OTP, etc.)."""
    seen_prompts: list[str] = []

    def handler(title: str, instructions: str, prompt_list: list[tuple[str, bool]]) -> list[str]:
        for prompt, _echo in prompt_list:
            seen_prompts.append(str(prompt or ""))
        if not prompt_list:
            return []
        return [password] * len(prompt_list)

    return handler, seen_prompts


def _bastion_auth_error_message(*, username: str, prompts: list[str], exc: Exception) -> str:
    parts = [
        "bastion_vault_auth_failed: verify hop_password (Vault password)",
        f"bastion_ssh_username={username!r}",
    ]
    if prompts:
        parts.append(f"prompts={prompts!r}")
    parts.append(f"detail={exc}")
    return "; ".join(parts)


def _bastion_start_transport(*, host: str, port: int, timeout: int) -> paramiko.Transport:
    transport = paramiko.Transport((host, int(port or 22)))
    transport.banner_timeout = timeout
    transport.auth_timeout = timeout
    transport.start_client(timeout=timeout)
    return transport


def _bastion_ssh_connect(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    timeout: int,
) -> paramiko.SSHClient:
    """SSH to protocol-proxy bastion (JumpServer/CBH/ZTE-TSM).

    Each strategy uses a fresh transport. Prefer password→keyboard-interactive
    fallback (OpenSSH-style) before a direct interactive attempt.
    """
    handler, prompt_trace = _bastion_interactive_handler(password)
    strategies: list[tuple[str, Any]] = [
        (
            "password_kb_fallback",
            lambda transport: transport.auth_password(username, password, fallback=True),
        ),
        (
            "interactive",
            lambda transport: transport.auth_interactive(username, handler),
        ),
    ]
    auth_errors: list[tuple[str, Exception]] = []

    for name, authenticate in strategies:
        transport: paramiko.Transport | None = None
        try:
            transport = _bastion_start_transport(host=host, port=port, timeout=timeout)
            authenticate(transport)
            if transport.is_authenticated():
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client._transport = transport  # noqa: SLF001
                return client
        except Exception as exc:
            auth_errors.append((name, exc))
        finally:
            if transport is not None and not transport.is_authenticated():
                try:
                    transport.close()
                except Exception:
                    pass

    preferred = next(
        (
            (name, exc)
            for name, exc in auth_errors
            if isinstance(exc, paramiko.AuthenticationException)
            and not isinstance(exc, paramiko.BadAuthenticationType)
        ),
        auth_errors[-1] if auth_errors else None,
    )
    if preferred is not None:
        _name, exc = preferred
        raise paramiko.AuthenticationException(
            _bastion_auth_error_message(username=username, prompts=prompt_trace, exc=exc)
        ) from exc
    raise paramiko.AuthenticationException(
        _bastion_auth_error_message(
            username=username,
            prompts=prompt_trace,
            exc=Exception("bastion_auth_failed"),
        )
    )


def _netmiko_driver_class(device_type: str) -> type:
    """Resolve Netmiko driver class (ConnectHandler is a factory func, not a base class)."""
    from netmiko.ssh_dispatcher import CLASS_MAPPER

    dt = str(device_type or "").strip()
    cls = CLASS_MAPPER.get(dt)
    if cls is None:
        raise ValueError(f"unsupported_device_type: {dt}")
    return cls


def _interactive_driver_class(base_cls: type) -> type:
    """Subclass for WebCRT: raw interactive PTY after transport auth (SecureCRT-like).

    Skips Netmiko session prep (prompt discovery, terminal length/width, force RETURN)
    and Huawei ``special_login_handler`` (read_until prompt / password-change). Those
    waits are why WebCRT stuck on ``waiting_prompt`` while connectivity probes succeed:
    collection still runs full Netmiko login; WebCRT must leave the PTY for live echo
    and hop secondary auth instead.
    """

    class _InteractiveSession(base_cls):  # type: ignore[misc,valid-type]
        def disable_paging(self, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401
            return ""

        def set_terminal_width(self, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401
            return ""

        def session_preparation(self) -> None:
            return None

        def special_login_handler(self, delay_factor: float = 1.0) -> None:  # noqa: ARG002
            return None

        def _try_session_preparation(self, force_data: bool = True) -> None:  # noqa: FBT001, FBT002
            del force_data
            try:
                self.session_preparation()
            except Exception:
                self.disconnect()
                raise

    _InteractiveSession.__name__ = f"Interactive{getattr(base_cls, '__name__', 'Netmiko')}"
    return _InteractiveSession


def _emit_progress(progress_cb: Any, text: str) -> None:
    """Best-effort live login transcript callback (WebCRT connect echo)."""
    if not progress_cb or not text:
        return
    try:
        progress_cb(str(text))
    except Exception:
        _log.debug("connect progress_cb failed", exc_info=True)


class _ProgressBytesIO(io.BytesIO):
    """BytesIO session_log that also tees reads into a connect-progress callback.

    Netmiko only accepts ``io.BufferedIOBase`` (or path / SessionLog). A plain
    custom log object raises ``ValueError`` and breaks every WebCRT connect.
    """

    def __init__(self, progress_cb: Any = None) -> None:
        super().__init__()
        self._progress_cb = progress_cb

    def write(self, b: Any) -> int:  # noqa: ANN401
        raw = b if isinstance(b, (bytes, bytearray)) else str(b or "").encode("utf-8", errors="replace")
        if raw:
            try:
                _emit_progress(self._progress_cb, bytes(raw).decode("utf-8", errors="replace"))
            except Exception:
                pass
        return super().write(raw)


class _ProgressSessionLog:
    """Deprecated alias kept for imports; prefer ``_ProgressBytesIO``."""

    def __init__(self, progress_cb: Any = None) -> None:
        self._buf = _ProgressBytesIO(progress_cb)

    def write(self, data: Any) -> int:  # noqa: ANN401
        return self._buf.write(data)

    def flush(self) -> None:
        self._buf.flush()

    def getvalue(self) -> bytes:
        return self._buf.getvalue()


def _cisco_ios_collection_driver_class(base_cls: type) -> type:
    """Cisco IOSv-friendly session prep: avoid cmd_verify on terminal width/length."""

    class _CiscoIosCollectionSession(base_cls):  # type: ignore[misc,valid-type]
        def session_preparation(self) -> None:
            # Default Netmiko waits for exact echo of "terminal width 511" (ReadTimeout on IOSv).
            self._test_channel_read(pattern=r"[>#]")
            try:
                self.set_terminal_width(command="terminal width 511", pattern=r"[>#]")
            except Exception:
                pass
            try:
                self.disable_paging(command="terminal length 0", cmd_verify=False, pattern=r"[>#]")
            except Exception:
                try:
                    self.send_command_timing("terminal length 0", read_timeout=15)
                except Exception:
                    pass
            self.set_base_prompt()

    _CiscoIosCollectionSession.__name__ = (
        f"CiscoIosCollection{getattr(base_cls, '__name__', 'Netmiko')}"
    )
    return _CiscoIosCollectionSession


def _zte_collection_driver_class(base_cls: type) -> type:
    """ZTE ZXROS collection: after login banner, RETURN once if prompt wait times out."""

    class _ZteCollectionSession(base_cls):  # type: ignore[misc,valid-type]
        def session_preparation(self) -> None:
            prompt_pat = r"[>#]"
            try:
                self._test_channel_read(pattern=prompt_pat)
            except Exception:
                # Long MOTD / idle after bastion proxy — nudge then wait again.
                try:
                    self.write_channel("\n")
                except Exception:
                    pass
                self._test_channel_read(pattern=prompt_pat)
            self.set_base_prompt()
            try:
                self.disable_paging(command="terminal length 0", cmd_verify=False, pattern=prompt_pat)
            except Exception:
                try:
                    self.write_channel("\n")
                    self.disable_paging(command="terminal length 0", cmd_verify=False, pattern=prompt_pat)
                except Exception:
                    pass
            time.sleep(0.3 * self.global_delay_factor)
            self.clear_buffer()

    _ZteCollectionSession.__name__ = f"ZteCollection{getattr(base_cls, '__name__', 'Netmiko')}"
    return _ZteCollectionSession


def _collection_driver_class(device_type: str, base_cls: type) -> type:
    """Vendor-specific collection session prep (non-interactive CLI / LLDP / sync)."""
    from .ne_netmiko import is_cisco_ios_device_type, is_zte_device_type

    if is_cisco_ios_device_type(device_type):
        return _cisco_ios_collection_driver_class(base_cls)
    if is_zte_device_type(device_type):
        return _zte_collection_driver_class(base_cls)
    return base_cls


def _build_netmiko_connection(dev: dict[str, Any], *, interactive: bool = False) -> ConnectHandler:
    """Instantiate Netmiko from connect kwargs; optional interactive skips paging cmds."""
    device_type = str(dev.get("device_type") or "").strip()
    if interactive:
        base_cls = _netmiko_driver_class(device_type)
        return _interactive_driver_class(base_cls)(**dev)
    raw_cls = _netmiko_driver_class(device_type)
    base_cls = _collection_driver_class(device_type, raw_cls)
    if base_cls is raw_cls:
        return ConnectHandler(**dev)
    return base_cls(**dev)


def _netmiko_over_ssh_client(
    ssh_client: paramiko.SSHClient,
    *,
    device_type: str,
    host: str,
    port: int,
    username: str,
    password: str,
    enable_secret: str,
    session_timeout: int | None,
    session_log: Any = None,
    interactive: bool = False,
    keepalive: int | None = None,
) -> ConnectHandler:
    """Netmiko session over an already-authenticated SSH client (bastion protocol proxy)."""
    base_cls = _netmiko_driver_class(device_type)
    if interactive:
        base_cls = _interactive_driver_class(base_cls)
    else:
        base_cls = _collection_driver_class(device_type, base_cls)

    class _PreauthSession(base_cls):  # type: ignore[misc,valid-type]
        def establish_connection(self, width: int = 511, height: int = 1000) -> None:
            from netmiko.channel import SSHChannel

            self.remote_conn_pre = ssh_client
            self.remote_conn = ssh_client.invoke_shell(term="vt100", width=width, height=height)
            self.remote_conn.settimeout(self.blocking_timeout)
            if self.keepalive:
                chan_transport = self.remote_conn.transport
                if chan_transport is not None:
                    chan_transport.set_keepalive(self.keepalive)
            self.channel = SSHChannel(conn=self.remote_conn, encoding=self.encoding)
            # Interactive WebCRT: do not block on vendor special_login_handler
            # (Huawei read_until ``[>]`` hangs on bastion/JumpServer banners).
            if not interactive:
                self.special_login_handler()

    dev = _base_connect_kwargs(
        device_type=device_type,
        host=host,
        port=port,
        username=username,
        password=password,
        enable_secret=enable_secret,
        session_timeout=session_timeout,
        session_log=session_log,
        keepalive=keepalive,
    )
    return _PreauthSession(**dev)


def _base_connect_kwargs(
    *,
    device_type: str,
    host: str,
    port: int,
    username: str,
    password: str,
    enable_secret: str,
    session_timeout: int | None = None,
    session_log: Any = None,
    keepalive: int | None = None,
) -> dict[str, Any]:
    timeout = int(settings.ne_connect_timeout_sec or 30)
    dev: dict[str, Any] = {
        "device_type": device_type,
        "host": host,
        "username": username,
        "password": password,
        "port": int(port or 22),
        "conn_timeout": timeout,
        "auth_timeout": timeout,
        "banner_timeout": timeout,
    }
    if session_timeout is not None:
        dev["session_timeout"] = session_timeout
    if keepalive is not None and int(keepalive) > 0:
        # Paramiko/Netmiko SSH transport keepalive (seconds between null packets).
        dev["keepalive"] = int(keepalive)
    secret = str(enable_secret or "").strip()
    if secret:
        dev["secret"] = secret
    if session_log is not None:
        dev["session_log"] = session_log
    return dev


def _connect_direct(
    creds: dict[str, Any],
    *,
    session_timeout: int | None = None,
    session_log: Any = None,
    interactive: bool = False,
    keepalive: int | None = None,
) -> ConnectHandler:
    device_type = normalize_netmiko_device_type(creds["device_type"], creds["protocol"])
    dev = _base_connect_kwargs(
        device_type=device_type,
        host=str(creds["ip_address"]),
        port=int(creds["port"] or 22),
        username=str(creds["username"]),
        password=str(creds["password"]),
        enable_secret=str(creds.get("enable_secret") or ""),
        session_timeout=session_timeout,
        session_log=session_log,
        keepalive=keepalive,
    )
    return _build_netmiko_connection(dev, interactive=interactive)


def _read_channel(conn: ConnectHandler, wait: float = 0.5, max_loops: int = 40) -> str:
    if wait > 0:
        time.sleep(wait)
    chunks: list[str] = []
    for _ in range(max_loops):
        try:
            part = conn.read_channel()
        except Exception:
            break
        if part is None or part is False:
            break
        # MagicMock truthy-but-empty: stop when unit tests leave read_channel unconfigured.
        if not isinstance(part, (str, bytes, bytearray)):
            text = str(part)
            # unittest.mock default str looks like "<MagicMock name=...>"
            if text.startswith("<MagicMock") or text.startswith("<Mock"):
                break
            part = text
        elif isinstance(part, (bytes, bytearray)):
            part = bytes(part).decode("utf-8", errors="replace")
        if not part:
            break
        chunks.append(part)
        time.sleep(0.05 if wait <= 0.15 else 0.15)
    return "".join(chunks)


def _send_line(conn: ConnectHandler, line: str) -> None:
    text = str(line or "")
    if not text.endswith("\n"):
        text += "\n"
    conn.write_channel(text)


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\].*?\x07|\x1b.")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text or ""))


def _auth_prompt_tail(text: str, *, lines: int = 1) -> str:
    """Last non-empty line(s) of an auth transcript (prompt detection)."""
    s = _strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")
    parts = [ln.strip() for ln in s.split("\n") if ln.strip()]
    if not parts:
        return ""
    n = max(1, int(lines))
    return "\n".join(parts[-n:])


def _prompt_needs_auth(text: str) -> tuple[bool, bool]:
    """Detect username/password prompts (Huawei stelnet: ``Please input the username:``)."""
    tail = _auth_prompt_tail(text).lower()
    if not tail:
        return False, False
    need_user = bool(
        re.search(
            r"(?:please\s+input\s+the\s+)?(?:user\s*name|username|login|用户名|用户)\s*[:>]\s*$",
            tail,
        )
    )
    need_pass = bool(
        re.search(r"(?:enter\s+)?(?:password|密码)\s*[:>]\s*$", tail)
    )
    return need_user, need_pass


def _prompt_needs_host_key_confirm(text: str) -> tuple[bool, bool]:
    """Huawei VRP stelnet first-connect host-key prompts.

    Returns ``(continue_access, save_public_key)`` for:
    - ``The server is not authenticated. Continue to access it? [Y/N]:`` → Y
    - ``Save the server's public key? [Y/N]:`` → N

    Also handles wrapped prompts where ``[Y/N]:`` is alone on the last line.
    """
    block = _auth_prompt_tail(text, lines=3)
    if not block:
        return False, False
    low = block.lower()
    # Do not treat password-change ``Change now? [Y/N]:`` as host-key trust.
    if re.search(
        r"(?:change\s*now|please\s*choose|password\s+needs\s+to\s+be\s+changed|修改密码|是否现在修改)",
        low,
    ):
        return False, False
    has_yn = bool(re.search(r"\[Y/N\]\s*:\s*$", block, flags=re.I | re.M))
    if not has_yn:
        return False, False
    continue_access = bool(
        re.search(
            r"(?:not\s+authenticated|continue\s+to\s+access|未认证|继续访问|是否继续)",
            low,
        )
    )
    save_key = bool(
        re.search(
            r"(?:save\s+the\s+server'?s?\s+public\s+key|保存.*公钥|是否保存.*(?:公钥|密钥))",
            low,
        )
    )
    # Bare ``[Y/N]:`` after a continue question without the word "public key".
    if continue_access and save_key:
        # Prefer the more specific save-key wording when both match the same block.
        if re.search(r"(?:save\s+the\s+server|保存.*公钥|是否保存)", low):
            return False, True
        return True, False
    return continue_access, save_key


def _looks_like_target_cli_prompt(text: str) -> bool:
    """True when transcript ends at a device CLI prompt (not ``[Y/N]:`` / login)."""
    tail = _auth_prompt_tail(text)
    if not tail:
        return False
    if re.search(r"\[Y/N\]", tail, flags=re.I):
        return False
    need_user, need_pass = _prompt_needs_auth(tail)
    if need_user or need_pass:
        return False
    # Huawei ``<sysname>`` / ``[sysname]``; Cisco ``R1#`` / ``R1>``.
    return bool(re.search(r"(?:[>#\]])\s*$", tail)) or bool(re.search(r"^<[^>\r\n]+>\s*$", tail))


def _interactive_target_auth(
    conn: ConnectHandler,
    username: str,
    password: str,
    *,
    progress_cb: Any = None,
) -> None:
    """Respond to username/password (and Huawei stelnet host-key) prompts after hop command."""
    from .ne_cli_errors import find_auth_failure_snippet

    # Hop stelnet can show Trying/Connected + two [Y/N] before password; keep budget generous.
    deadline = time.time() + max(60, int(settings.ne_connect_timeout_sec or 30) + 30)
    sent_user = False
    sent_pass = False
    answered_continue = False
    answered_save_key = False
    empty_after_pass = 0
    acc = ""
    while time.time() < deadline:
        buf = _read_channel(conn, wait=0.12, max_loops=12)
        if buf:
            acc += buf
            _emit_progress(progress_cb, buf)
            denied = find_auth_failure_snippet(acc)
            if denied:
                raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")

        # Match against accumulated tail so prompts split across reads are still seen.
        continue_access, save_key = _prompt_needs_host_key_confirm(acc)
        if continue_access and not answered_continue:
            _emit_progress(progress_cb, "\r\n[netx] host-key continue → Y\r\n")
            _send_line(conn, "Y")
            answered_continue = True
            continue
        if save_key and not answered_save_key:
            _emit_progress(progress_cb, "\r\n[netx] save public key → N\r\n")
            _send_line(conn, "N")
            answered_save_key = True
            continue

        need_user, need_pass = _prompt_needs_auth(acc)
        if need_pass and not sent_pass:
            _emit_progress(progress_cb, "\r\n[netx] sending password\r\n")
            _send_line(conn, password)
            sent_pass = True
            empty_after_pass = 0
            continue
        if need_user and not sent_user:
            _emit_progress(progress_cb, f"\r\n[netx] sending username ({username})\r\n")
            _send_line(conn, username)
            sent_user = True
            continue

        if sent_pass and not need_user and not need_pass and not continue_access and not save_key:
            denied = find_auth_failure_snippet(acc)
            if denied:
                raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
            if _looks_like_target_cli_prompt(acc):
                return
            # Do not treat a single empty read right after password as success —
            # Huawei still prints last-login banner / prompt.
            if not buf.strip():
                empty_after_pass += 1
                if empty_after_pass >= 4:
                    return
            else:
                empty_after_pass = 0
            time.sleep(0.15)
            continue

        if not buf.strip():
            time.sleep(0.2)
            continue
        # Huawei stelnet often reprints the hop ``[sysname]`` after host-key trust and
        # *before* ``Enter password:``. Do not treat that as target login success.
        if sent_pass and _looks_like_target_cli_prompt(buf):
            denied = find_auth_failure_snippet(acc)
            if denied:
                raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
            return
        if (
            sent_user
            and not sent_pass
            and not answered_continue
            and not answered_save_key
            and _looks_like_target_cli_prompt(buf)
        ):
            # Passwordless target after username only (no stelnet host-key dance).
            denied = find_auth_failure_snippet(acc)
            if denied:
                raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
            return
        time.sleep(0.15)
    denied = find_auth_failure_snippet(acc)
    if denied:
        raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
    if not sent_pass:
        raise TimeoutError("target_auth_timeout")
    # Password was sent but no clear prompt — hand channel to caller anyway.
    _emit_progress(progress_cb, "\r\n[netx] auth settle timeout; handing session to terminal\r\n")


def _hop_netmiko_device_type(vendor: str, hop_protocol: str) -> str:
    v = str(vendor or "zte").strip().lower()
    if v == "huawei":
        base = "huawei"
    elif v == "cisco":
        base = "cisco_ios"
    else:
        base = "zte_zxros"
    return normalize_netmiko_device_type(base, hop_protocol)


def _resize_pty(conn: ConnectHandler, cols: int | None = None, rows: int | None = None) -> None:
    """Set SSH PTY size so nested telnet/stelnet inherits the interactive terminal geometry.

    Netmiko defaults to 511x1000. If a CLI hop jump runs at that size and WebCRT is ~80
    columns, mid-line edit redraws (spaces / clear-to-EOL) wrap and garble the display.
    """
    if cols is None and rows is None:
        return
    channel = getattr(conn, "remote_conn", None)
    if channel is None or not hasattr(channel, "resize_pty"):
        return
    c = max(20, min(500, int(cols if cols is not None else 80)))
    r = max(5, min(200, int(rows if rows is not None else 24)))
    try:
        channel.resize_pty(width=c, height=r)
    except Exception:
        _log.debug("resize_pty failed cols=%s rows=%s", c, r, exc_info=True)


def _connect_via_cli_hop(
    creds: dict[str, Any],
    *,
    session_timeout: int | None = None,
    session_log: Any = None,
    cols: int | None = None,
    rows: int | None = None,
    interactive: bool = False,
    keepalive: int | None = None,
    progress_cb: Any = None,
) -> ConnectHandler:
    """Login to ZTE/Huawei/Cisco hop NE, run CLI jump command, then target secondary auth."""
    hop_host = str(creds.get("hop_host") or "").strip()
    hop_user = str(creds.get("hop_username") or "").strip()
    hop_pass = str(creds.get("hop_password") or "")
    if not hop_host or not hop_user or not hop_pass:
        raise ValueError("hop_credentials_incomplete")

    hop_protocol = str(creds.get("hop_protocol") or "ssh")
    hop_device_type = _hop_netmiko_device_type(_hop_vendor(creds), hop_protocol)
    hop_dev = _base_connect_kwargs(
        device_type=hop_device_type,
        host=hop_host,
        port=int(creds.get("hop_port") or 22),
        username=hop_user,
        password=hop_pass,
        enable_secret="",
        session_timeout=session_timeout or 180,
        session_log=session_log,
        keepalive=keepalive,
    )
    _emit_progress(progress_cb, f"\r\n[netx] connecting hop {_hop_vendor(creds)} {hop_host}…\r\n")
    conn = _build_netmiko_connection(hop_dev, interactive=interactive)
    try:
        # MUST resize before stelnet/telnet — nested session captures hop TTY size at start
        # and often ignores later WINCH. Wrong width → mid-line edit redraw wraps in WebCRT.
        _resize_pty(conn, cols, rows)
        pre = _read_channel(conn, wait=0.35)
        if pre:
            _emit_progress(progress_cb, pre)
        hop_prompt = extract_cli_prompt_marker(pre)
        if not hop_prompt:
            # Nudge hop CLI once so the prompt is visible for later return-to-proxy detection.
            try:
                conn.write_channel(getattr(conn, "RETURN", None) or "\n")
            except Exception:
                _send_line(conn, "")
            more = _read_channel(conn, wait=0.25, max_loops=12)
            if more:
                _emit_progress(progress_cb, more)
            pre = pre + more
            hop_prompt = extract_cli_prompt_marker(pre)
        # WebCRT skips hop session_preparation; wait a bit longer for a settled CLI
        # before stelnet/telnet so the jump command is not typed into a half-ready PTY.
        if interactive and not hop_prompt:
            for _ in range(6):
                more = _read_channel(conn, wait=0.3, max_loops=10)
                if more:
                    _emit_progress(progress_cb, more)
                    pre += more
                    hop_prompt = extract_cli_prompt_marker(pre)
                    if hop_prompt:
                        break
        hop_cmd = render_hop_command(str(creds.get("hop_command_template") or ""), creds)
        _emit_progress(progress_cb, f"\r\n[netx] hop jump: {hop_cmd}\r\n")
        _send_line(conn, hop_cmd)
        _interactive_target_auth(
            conn,
            str(creds["username"]),
            str(creds["password"]),
            progress_cb=progress_cb,
        )
        _attach_cli_hop_guard(
            conn,
            hop_prompt=hop_prompt,
            hop_vendor=_hop_vendor(creds),
            hop_host=hop_host,
        )
        if hop_prompt:
            _log.info(
                "cli hop guard armed vendor=%s hop=%s prompt=%r",
                _hop_vendor(creds),
                hop_host,
                hop_prompt,
            )
        else:
            _log.warning(
                "cli hop guard armed without hop prompt vendor=%s hop=%s (nested-close only)",
                _hop_vendor(creds),
                hop_host,
            )
        return conn
    except Exception:
        try:
            conn.disconnect()
        except Exception:
            pass
        raise


def _maybe_secondary_target_auth(
    conn: ConnectHandler,
    creds: dict[str, Any],
    *,
    progress_cb: Any = None,
    force: bool = False,
) -> None:
    """Run interactive target auth when the PTY still shows login / host-key prompts."""
    peek = _read_channel(conn, wait=0.45, max_loops=14)
    if peek:
        _emit_progress(progress_cb, peek)
    need_user, need_pass = _prompt_needs_auth(peek)
    cont, save = _prompt_needs_host_key_confirm(peek)
    target_user = str(creds.get("username") or "").strip()
    target_pass = str(creds.get("password") or "")
    if not force and not (need_user or need_pass or cont or save):
        return
    if not target_pass and not force:
        _emit_progress(
            progress_cb,
            "\r\n[netx] login prompt visible but target password empty; leaving for terminal\r\n",
        )
        return
    if not target_user and need_user:
        _emit_progress(progress_cb, "\r\n[netx] username prompt but target username empty\r\n")
        return
    _interactive_target_auth(conn, target_user, target_pass, progress_cb=progress_cb)


def _connect_via_bastion(
    creds: dict[str, Any],
    *,
    session_timeout: int | None = None,
    session_log: Any = None,
    interactive: bool = False,
    keepalive: int | None = None,
    progress_cb: Any = None,
) -> ConnectHandler:
    """SSH to bastion with composite username; bastion proxies to target (protocol proxy)."""
    hop_host, hop_user = expand_bastion_hop_fields(
        hop_host=str(creds.get("hop_host") or ""),
        hop_username=str(creds.get("hop_username") or ""),
    )
    hop_pass = str(creds.get("hop_password") or "")
    if not hop_host or not hop_user or not hop_pass:
        raise ValueError("hop_credentials_incomplete")
    # Keep render/logs aligned when hop_host was a pasted user@…@fqdn string.
    creds = {**creds, "hop_host": hop_host, "hop_username": hop_user}

    composite_rendered = render_hop_command(str(creds.get("hop_command_template") or ""), creds)
    ssh_username = resolve_bastion_ssh_username(composite_rendered, hop_host)
    device_type = normalize_netmiko_device_type(creds["device_type"], creds["protocol"])
    hop_port = int(creds.get("hop_port") or 22)
    timeout = int(settings.ne_connect_timeout_sec or 30)
    _emit_progress(
        progress_cb,
        f"\r\n[netx] bastion SSH {hop_user}@{hop_host} as {ssh_username!r}…\r\n",
    )
    ssh_client = None
    try:
        ssh_client = _bastion_ssh_connect(
            host=hop_host,
            port=hop_port,
            username=ssh_username,
            password=hop_pass,
            timeout=timeout,
        )
        _emit_progress(progress_cb, "\r\n[netx] bastion transport OK; opening shell…\r\n")
        conn = _netmiko_over_ssh_client(
            ssh_client,
            device_type=device_type,
            host=hop_host,
            port=hop_port,
            username=ssh_username,
            password=hop_pass,
            enable_secret=str(creds.get("enable_secret") or ""),
            session_timeout=session_timeout or 180,
            session_log=session_log,
            interactive=interactive,
            keepalive=keepalive,
        )
    except Exception:
        if ssh_client is not None:
            try:
                ssh_client.close()
            except Exception:
                pass
        raise

    auth_mode = str(creds.get("hop_target_auth_mode") or "bastion_managed").strip().lower()
    try:
        if auth_mode == "manual":
            target_pass = str(creds.get("password") or "")
            if target_pass:
                _maybe_secondary_target_auth(conn, creds, progress_cb=progress_cb, force=True)
            else:
                # Still drain banner so WebCRT live echo shows what the proxy printed.
                peek = _read_channel(conn, wait=0.4, max_loops=12)
                if peek:
                    _emit_progress(progress_cb, peek)
        else:
            # bastion_managed: normally no secondary auth, but if the proxy still presents
            # Username/Password or Huawei host-key prompts, answer them when creds exist.
            _maybe_secondary_target_auth(conn, creds, progress_cb=progress_cb, force=False)
    except Exception:
        try:
            conn.disconnect()
        except Exception:
            pass
        raise
    return conn


def _connect_via_linux_hop(
    creds: dict[str, Any],
    *,
    session_timeout: int | None = None,
    session_log: Any = None,
    interactive: bool = False,
    keepalive: int | None = None,
) -> ConnectHandler:
    """SSH to Linux bastion, then direct-tcpip tunnel to target (classic ProxyJump-style)."""
    hop_host = normalize_hop_host(str(creds.get("hop_host") or ""))
    hop_user = str(creds.get("hop_username") or "").strip()
    hop_pass = str(creds.get("hop_password") or "")
    if not hop_host or not hop_user or not hop_pass:
        raise ValueError("hop_credentials_incomplete")

    timeout = int(settings.ne_connect_timeout_sec or 30)
    hop_port = int(creds.get("hop_port") or 22)
    target_ip = str(creds["ip_address"])
    target_port = int(creds.get("port") or 22)

    jump = paramiko.SSHClient()
    jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        jump.connect(
            hop_host,
            port=hop_port,
            username=hop_user,
            password=hop_pass,
            timeout=timeout,
            banner_timeout=timeout,
            auth_timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        transport = jump.get_transport()
        if transport is None or not transport.is_active():
            raise ConnectionError("hop_connect_failed: jump transport inactive")
        channel = transport.open_channel(
            "direct-tcpip",
            (target_ip, target_port),
            ("127.0.0.1", 0),
            timeout=timeout,
        )
    except Exception:
        try:
            jump.close()
        except Exception:
            pass
        raise

    device_type = normalize_netmiko_device_type(creds["device_type"], creds["protocol"])
    dev = _base_connect_kwargs(
        device_type=device_type,
        host=target_ip,
        port=target_port,
        username=str(creds["username"]),
        password=str(creds["password"]),
        enable_secret=str(creds.get("enable_secret") or ""),
        session_timeout=session_timeout,
        session_log=session_log,
        keepalive=keepalive,
    )
    dev["sock"] = channel
    conn = _build_netmiko_connection(dev, interactive=interactive)
    conn._netx_jump_client = jump  # type: ignore[attr-defined]
    return conn


def close_netmiko_connection(conn: ConnectHandler | None) -> None:
    """Disconnect target session and any Linux bastion SSH client."""
    if conn is None:
        return
    jump = getattr(conn, "_netx_jump_client", None)
    try:
        conn.disconnect()
    except Exception:
        pass
    if jump is not None:
        try:
            jump.close()
        except Exception:
            pass


def open_netmiko_connection(
    creds: dict[str, Any],
    *,
    session_timeout: int | None = None,
    session_log: Any = None,
    cols: int | None = None,
    rows: int | None = None,
    interactive: bool = False,
    keepalive: int | None = None,
    progress_cb: Any = None,
) -> ConnectHandler:
    """Open a Netmiko connection to the target NE (direct or via configured hop).

    ``interactive=True`` (WebCRT) skips Netmiko's automatic ``terminal length`` /
    ``terminal width`` (and vendor equivalents). Collection / MCP keep the default.

    ``progress_cb`` receives live login transcript chunks (WebCRT connect echo).
    """
    ka = keepalive
    if ka is None and interactive:
        ka = int(getattr(settings, "webcrt_keepalive_sec", 0) or 0) or None
    # Never wrap session_log in a non-BufferedIOBase object — Netmiko rejects it
    # with ValueError and every WebCRT session fails to open.
    log = session_log
    if creds.get("hop_enabled"):
        vendor = _hop_vendor(creds)
        if vendor == "linux":
            return _connect_via_linux_hop(
                creds,
                session_timeout=session_timeout,
                session_log=log,
                interactive=interactive,
                keepalive=ka,
            )
        if vendor == "bastion":
            return _connect_via_bastion(
                creds,
                session_timeout=session_timeout,
                session_log=log,
                interactive=interactive,
                keepalive=ka,
                progress_cb=progress_cb,
            )
        return _connect_via_cli_hop(
            creds,
            session_timeout=session_timeout,
            session_log=log,
            cols=cols,
            rows=rows,
            interactive=interactive,
            keepalive=ka,
            progress_cb=progress_cb,
        )
    if progress_cb is not None:
        _emit_progress(progress_cb, "\r\n[netx] direct connect…\r\n")
    return _connect_direct(
        creds,
        session_timeout=session_timeout,
        session_log=log,
        interactive=interactive,
        keepalive=ka,
    )

