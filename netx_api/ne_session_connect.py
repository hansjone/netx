"""Netmiko connect paths: direct, vendor CLI hop, bastion, and Linux jump."""
from __future__ import annotations

import logging
import re
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
    so the channel is left for the user — not consumed by library automation.
    """

    class _InteractiveSession(base_cls):  # type: ignore[misc,valid-type]
        def disable_paging(self, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401
            return ""

        def set_terminal_width(self, *args: Any, **kwargs: Any) -> str:  # noqa: ANN401
            return ""

        def session_preparation(self) -> None:
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


def _build_netmiko_connection(dev: dict[str, Any], *, interactive: bool = False) -> ConnectHandler:
    """Instantiate Netmiko from connect kwargs; optional interactive skips paging cmds."""
    from .ne_netmiko import is_cisco_ios_device_type

    device_type = str(dev.get("device_type") or "").strip()
    if interactive:
        base_cls = _netmiko_driver_class(device_type)
        return _interactive_driver_class(base_cls)(**dev)
    if is_cisco_ios_device_type(device_type):
        base_cls = _netmiko_driver_class(device_type)
        return _cisco_ios_collection_driver_class(base_cls)(**dev)
    return ConnectHandler(**dev)


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
    time.sleep(wait)
    chunks: list[str] = []
    for _ in range(max_loops):
        part = conn.read_channel()
        if not part:
            break
        chunks.append(part)
        time.sleep(0.2)
    return "".join(chunks)


def _send_line(conn: ConnectHandler, line: str) -> None:
    text = str(line or "")
    if not text.endswith("\n"):
        text += "\n"
    conn.write_channel(text)


def _prompt_needs_auth(text: str) -> tuple[bool, bool]:
    low = text.lower()
    need_user = bool(re.search(r"(username|login|user\s*name)\s*[:>]", low))
    need_pass = bool(re.search(r"password\s*[:>]", low))
    return need_user, need_pass


def _interactive_target_auth(conn: ConnectHandler, username: str, password: str) -> None:
    """Respond to username/password prompts after hop command (target credentials)."""
    from .ne_cli_errors import find_auth_failure_snippet

    deadline = time.time() + int(settings.ne_connect_timeout_sec or 30)
    sent_user = False
    sent_pass = False
    acc = ""
    while time.time() < deadline:
        buf = _read_channel(conn, wait=0.3, max_loops=8)
        if buf:
            acc += buf
            denied = find_auth_failure_snippet(acc)
            if denied:
                raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
        need_user, need_pass = _prompt_needs_auth(buf)
        if need_pass and not sent_pass:
            _send_line(conn, password)
            sent_pass = True
            continue
        if need_user and not sent_user:
            _send_line(conn, username)
            sent_user = True
            continue
        if sent_pass and not need_user and not need_pass:
            denied = find_auth_failure_snippet(acc)
            if denied:
                raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
            return
        if not buf.strip():
            time.sleep(0.3)
            continue
        if re.search(r"[>#]\s*$", buf):
            denied = find_auth_failure_snippet(acc)
            if denied:
                raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
            if sent_pass or (sent_user and not need_pass):
                return
        time.sleep(0.3)
    denied = find_auth_failure_snippet(acc)
    if denied:
        raise paramiko.AuthenticationException(f"target_auth_rejected: {denied}")
    if not sent_pass:
        raise TimeoutError("target_auth_timeout")


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
    conn = _build_netmiko_connection(hop_dev, interactive=interactive)
    try:
        # MUST resize before stelnet/telnet — nested session captures hop TTY size at start
        # and often ignores later WINCH. Wrong width → mid-line edit redraw wraps in WebCRT.
        _resize_pty(conn, cols, rows)
        pre = _read_channel(conn, wait=0.5)
        hop_prompt = extract_cli_prompt_marker(pre)
        if not hop_prompt:
            # Nudge hop CLI once so the prompt is visible for later return-to-proxy detection.
            try:
                conn.write_channel(getattr(conn, "RETURN", None) or "\n")
            except Exception:
                _send_line(conn, "")
            pre = pre + _read_channel(conn, wait=0.35, max_loops=10)
            hop_prompt = extract_cli_prompt_marker(pre)
        hop_cmd = render_hop_command(str(creds.get("hop_command_template") or ""), creds)
        _send_line(conn, hop_cmd)
        _interactive_target_auth(conn, str(creds["username"]), str(creds["password"]))
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


def _connect_via_bastion(
    creds: dict[str, Any],
    *,
    session_timeout: int | None = None,
    session_log: Any = None,
    interactive: bool = False,
    keepalive: int | None = None,
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
    ssh_client = None
    try:
        ssh_client = _bastion_ssh_connect(
            host=hop_host,
            port=hop_port,
            username=ssh_username,
            password=hop_pass,
            timeout=timeout,
        )
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
    if auth_mode == "manual":
        target_pass = str(creds.get("password") or "")
        if target_pass:
            try:
                _read_channel(conn, wait=0.5)
                _interactive_target_auth(conn, str(creds["username"]), target_pass)
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
) -> ConnectHandler:
    """Open a Netmiko connection to the target NE (direct or via configured hop).

    ``interactive=True`` (WebCRT) skips Netmiko's automatic ``terminal length`` /
    ``terminal width`` (and vendor equivalents). Collection / MCP keep the default.
    """
    ka = keepalive
    if ka is None and interactive:
        ka = int(getattr(settings, "webcrt_keepalive_sec", 0) or 0) or None
    if creds.get("hop_enabled"):
        vendor = _hop_vendor(creds)
        if vendor == "linux":
            return _connect_via_linux_hop(
                creds,
                session_timeout=session_timeout,
                session_log=session_log,
                interactive=interactive,
                keepalive=ka,
            )
        if vendor == "bastion":
            return _connect_via_bastion(
                creds,
                session_timeout=session_timeout,
                session_log=session_log,
                interactive=interactive,
                keepalive=ka,
            )
        return _connect_via_cli_hop(
            creds,
            session_timeout=session_timeout,
            session_log=session_log,
            cols=cols,
            rows=rows,
            interactive=interactive,
            keepalive=ka,
        )
    return _connect_direct(
        creds,
        session_timeout=session_timeout,
        session_log=session_log,
        interactive=interactive,
        keepalive=ka,
    )

