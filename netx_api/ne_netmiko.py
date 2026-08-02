"""Shared Netmiko helpers for managed NE connect/collect."""

from __future__ import annotations

from typing import Any


def normalize_netmiko_device_type(device_type: str, protocol: str) -> str:
    dt = str(device_type or "").strip()
    proto = str(protocol or "ssh").strip().lower()
    low = dt.lower()
    # Raw / SecureCRT-style sessions (WebCRT quick-connect stores device_type=generic).
    if low in ("generic", "generic_ssh", "generic_telnet", "terminal_server", "generic_termserver"):
        return "generic_telnet" if proto == "telnet" or "telnet" in low else "generic_termserver_ssh"
    # Netmiko ships linux / linux_ssh but not linux_telnet — use generic_telnet.
    if low in ("linux", "linux_ssh", "linux_telnet") or low.startswith("linux_"):
        if proto == "telnet" or "telnet" in low:
            return "generic_telnet"
        return "linux_ssh"
    if "zte" in low:
        if dt == "zte":
            return f"zte_zxros_{proto}"
        if "telnet" not in low and "ssh" not in low:
            return f"{dt}_{proto}"
        return dt
    if "telnet" not in low and "ssh" not in low:
        return f"{dt}_{proto}"
    return dt


def is_cisco_ios_device_type(device_type: str) -> bool:
    dt = str(device_type or "").strip().lower()
    return "cisco_ios" in dt or dt in {"cisco_ios", "cisco_ios_ssh", "cisco_ios_telnet"}


def send_show_command(conn: Any, command: str, *, read_timeout: int = 120) -> str:
    """Send a show/display command via ``send_command`` (wait for device prompt).

    Do not use ``send_command_timing`` for Cisco config collection: long idle during
    ``Building configuration...`` is treated as end-of-output and truncates the config.

    ``cmd_verify=False``: Netmiko's default echo check often raises
    ``Pattern not detected: 'show\\ lldp\\ ...'`` on IOSv / hop / slow echo paths.
    """
    cmd = str(command or "").strip()
    if not cmd:
        return ""
    try:
        return str(
            conn.send_command(
                command_string=cmd,
                read_timeout=read_timeout,
                cmd_verify=False,
            )
            or ""
        )
    except TypeError:
        # Older Netmiko without cmd_verify kwarg.
        return str(conn.send_command(command_string=cmd, read_timeout=read_timeout) or "")
