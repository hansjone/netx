"""Netmiko session factory: direct connect or via ZTE jump host."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from netmiko import ConnectHandler

from .config import settings
from .ne_netmiko import normalize_netmiko_device_type

_log = logging.getLogger("netx.ne.session")

_HOP_PLACEHOLDERS = ("target_ip", "target_port", "target_user", "target_password", "vrf")

# ZTE CLI jump: ssh/telnet <ip> [vrf <name>] — target user/password via secondary auth.
_LEGACY_HOP_TEMPLATES = frozenset({"ssh {target_user}@{target_ip}", "ssh {target_ip}", "telnet {target_ip}"})


def default_zte_hop_template(protocol: str, vrf: str = "") -> str:
    cmd = "telnet" if str(protocol or "ssh").strip().lower() == "telnet" else "ssh"
    v = str(vrf or "").strip()
    if v:
        return f"{cmd} {{target_ip}} vrf {{vrf}}"
    return f"{cmd} {{target_ip}}"


def render_hop_command(template: str, creds: dict[str, Any]) -> str:
    """Render hop command from template using whitelisted placeholders only."""
    tpl = str(template or "").strip()
    if not tpl or tpl in _LEGACY_HOP_TEMPLATES:
        tpl = default_zte_hop_template(
            str(creds.get("hop_protocol") or "ssh"),
            str(creds.get("hop_vrf") or ""),
        )
    values = {
        "target_ip": str(creds.get("ip_address") or ""),
        "target_port": str(int(creds.get("port") or 22)),
        "target_user": str(creds.get("username") or ""),
        "target_password": str(creds.get("password") or ""),
        "vrf": str(creds.get("hop_vrf") or "").strip(),
    }
    out = tpl
    for key in _HOP_PLACEHOLDERS:
        out = out.replace("{" + key + "}", values[key])
    if "{" in out or "}" in out:
        raise ValueError("hop_command_template_invalid_placeholder")
    return out


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


def _connect_via_zte_hop(creds: dict[str, Any], *, session_timeout: int | None = None) -> ConnectHandler:
    hop_host = str(creds.get("hop_host") or "").strip()
    hop_user = str(creds.get("hop_username") or "").strip()
    hop_pass = str(creds.get("hop_password") or "")
    if not hop_host or not hop_user or not hop_pass:
        raise ValueError("hop_credentials_incomplete")

    hop_protocol = str(creds.get("hop_protocol") or "ssh")
    hop_device_type = normalize_netmiko_device_type("zte_zxros", hop_protocol)
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


def open_netmiko_connection(creds: dict[str, Any], *, session_timeout: int | None = None) -> ConnectHandler:
    """Open a Netmiko connection to the target NE (direct or via configured hop)."""
    if creds.get("hop_enabled"):
        return _connect_via_zte_hop(creds, session_timeout=session_timeout)
    return _connect_direct(creds, session_timeout=session_timeout)
