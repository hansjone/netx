"""Netmiko session factory: direct connect, vendor CLI hop (ZTE/Huawei/Cisco), or Linux SSH bastion."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import paramiko
from netmiko import ConnectHandler

from .config import settings
from .ne_netmiko import normalize_netmiko_device_type

_log = logging.getLogger("netx.ne.session")

_HOP_PLACEHOLDERS = ("target_ip", "target_port", "target_user", "target_password", "vrf", "hop_user", "hop_host")

# ZTE CLI jump: ssh/telnet <ip> [vrf <name>] — target user/password via secondary auth.
_LEGACY_HOP_TEMPLATES = frozenset({"ssh {target_user}@{target_ip}", "ssh {target_ip}", "telnet {target_ip}"})


def default_zte_hop_template(protocol: str, vrf: str = "") -> str:
    cmd = "telnet" if str(protocol or "ssh").strip().lower() == "telnet" else "ssh"
    v = str(vrf or "").strip()
    if v:
        return f"{cmd} {{target_ip}} vrf {{vrf}}"
    return f"{cmd} {{target_ip}}"


def default_cisco_hop_template(protocol: str, vrf: str = "") -> str:
    """Cisco CLI jump: ssh -vrf VRF IP; telnet IP [/vrf VRF]."""
    v = str(vrf or "").strip()
    if str(protocol or "ssh").strip().lower() == "telnet":
        if v:
            return "telnet {target_ip} /vrf {vrf}"
        return "telnet {target_ip}"
    if v:
        return "ssh -vrf {vrf} {target_ip}"
    return "ssh {target_ip}"


def default_huawei_hop_template(protocol: str, vrf: str = "") -> str:
    """Huawei CLI jump: telnet [vpn-instance VRF] IP; stelnet = SSH."""
    v = str(vrf or "").strip()
    if str(protocol or "ssh").strip().lower() == "telnet":
        if v:
            return "telnet vpn-instance {vrf} {target_ip}"
        return "telnet {target_ip}"
    if v:
        return "stelnet {target_ip} -vpn-instance {vrf}"
    return "stelnet {target_ip}"


def default_bastion_username_template() -> str:
    """SSH username sent to bastion (OpenSSH splits user@host at the last @)."""
    return "{hop_user}@{target_user}@{target_ip}"


_LEGACY_BASTION_USERNAME_TEMPLATE = "{hop_user}@{target_user}@{target_ip}@{hop_host}"


def resolve_bastion_ssh_username(rendered: str, hop_host: str) -> str:
    """Map template output to the SSH username Paramiko must send.

    CLI ``ssh hop@target@ip@bastion`` is parsed by OpenSSH as user ``hop@target@ip``
    and host ``bastion``. Legacy templates that included ``{hop_host}`` duplicated the
    bastion address inside the username and break authentication.
    """
    user = str(rendered or "").strip()
    host = str(hop_host or "").strip()
    if not user or not host:
        return user
    suffix = f"@{host}"
    if user.endswith(suffix):
        return user[:-len(suffix)]
    return user


def bastion_ssh_cli(username: str, hop_host: str, hop_port: int = 22) -> str:
    """Human-readable ssh command equivalent (for logs/UI)."""
    host = str(hop_host or "").strip()
    user = str(username or "").strip()
    target = f"{user}@{host}" if user else host
    port = int(hop_port or 22)
    if port != 22:
        return f"ssh -p {port} {target}"
    return f"ssh {target}"


def default_hop_command_template(vendor: str, protocol: str, vrf: str = "") -> str:
    v = str(vendor or "zte").strip().lower()
    if v == "bastion":
        return default_bastion_username_template()
    if v == "huawei":
        return default_huawei_hop_template(protocol, vrf)
    if v == "cisco":
        return default_cisco_hop_template(protocol, vrf)
    return default_zte_hop_template(protocol, vrf)


def _hop_vendor(creds: dict[str, Any]) -> str:
    return str(creds.get("hop_vendor") or "zte").strip().lower()


def render_hop_command(template: str, creds: dict[str, Any]) -> str:
    """Render hop command from template using whitelisted placeholders only."""
    tpl = str(template or "").strip()
    if not tpl or tpl in _LEGACY_HOP_TEMPLATES:
        tpl = default_hop_command_template(
            _hop_vendor(creds),
            str(creds.get("hop_protocol") or "ssh"),
            str(creds.get("hop_vrf") or ""),
        )
    values = {
        "target_ip": str(creds.get("ip_address") or ""),
        "target_port": str(int(creds.get("port") or 22)),
        "target_user": str(creds.get("username") or ""),
        "target_password": str(creds.get("password") or ""),
        "vrf": str(creds.get("hop_vrf") or "").strip(),
        "hop_user": str(creds.get("hop_username") or ""),
        "hop_host": str(creds.get("hop_host") or "").strip(),
    }
    out = tpl
    for key in _HOP_PLACEHOLDERS:
        out = out.replace("{" + key + "}", values[key])
    if "{" in out or "}" in out:
        raise ValueError("hop_command_template_invalid_placeholder")
    return out


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
) -> ConnectHandler:
    """Netmiko session over an already-authenticated SSH client (bastion protocol proxy)."""
    base_cls = _netmiko_driver_class(device_type)

    class _PreauthSession(base_cls):
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
    secret = str(enable_secret or "").strip()
    if secret:
        dev["secret"] = secret
    return dev


def _connect_direct(creds: dict[str, Any], *, session_timeout: int | None = None) -> ConnectHandler:
    device_type = normalize_netmiko_device_type(creds["device_type"], creds["protocol"])
    dev = _base_connect_kwargs(
        device_type=device_type,
        host=str(creds["ip_address"]),
        port=int(creds["port"] or 22),
        username=str(creds["username"]),
        password=str(creds["password"]),
        enable_secret=str(creds.get("enable_secret") or ""),
        session_timeout=session_timeout,
    )
    return ConnectHandler(**dev)


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
    deadline = time.time() + int(settings.ne_connect_timeout_sec or 30)
    sent_user = False
    sent_pass = False
    while time.time() < deadline:
        buf = _read_channel(conn, wait=0.3, max_loops=8)
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
            return
        if not buf.strip():
            time.sleep(0.3)
            continue
        if re.search(r"[>#]\s*$", buf):
            if sent_pass or (sent_user and not need_pass):
                return
        time.sleep(0.3)
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


def _connect_via_cli_hop(creds: dict[str, Any], *, session_timeout: int | None = None) -> ConnectHandler:
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
    )
    conn = ConnectHandler(**hop_dev)
    try:
        _read_channel(conn, wait=0.5)
        hop_cmd = render_hop_command(str(creds.get("hop_command_template") or ""), creds)
        _send_line(conn, hop_cmd)
        _interactive_target_auth(conn, str(creds["username"]), str(creds["password"]))
        return conn
    except Exception:
        try:
            conn.disconnect()
        except Exception:
            pass
        raise


def _connect_via_bastion(creds: dict[str, Any], *, session_timeout: int | None = None) -> ConnectHandler:
    """SSH to bastion with composite username; bastion proxies to target (protocol proxy)."""
    hop_host = str(creds.get("hop_host") or "").strip()
    hop_user = str(creds.get("hop_username") or "").strip()
    hop_pass = str(creds.get("hop_password") or "")
    if not hop_host or not hop_user or not hop_pass:
        raise ValueError("hop_credentials_incomplete")

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


def _connect_via_linux_hop(creds: dict[str, Any], *, session_timeout: int | None = None) -> ConnectHandler:
    """SSH to Linux bastion, then direct-tcpip tunnel to target (classic ProxyJump-style)."""
    hop_host = str(creds.get("hop_host") or "").strip()
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
    )
    dev["sock"] = channel
    conn = ConnectHandler(**dev)
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


def open_netmiko_connection(creds: dict[str, Any], *, session_timeout: int | None = None) -> ConnectHandler:
    """Open a Netmiko connection to the target NE (direct or via configured hop)."""
    if creds.get("hop_enabled"):
        vendor = _hop_vendor(creds)
        if vendor == "linux":
            return _connect_via_linux_hop(creds, session_timeout=session_timeout)
        if vendor == "bastion":
            return _connect_via_bastion(creds, session_timeout=session_timeout)
        return _connect_via_cli_hop(creds, session_timeout=session_timeout)
    return _connect_direct(creds, session_timeout=session_timeout)
