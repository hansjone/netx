"""Shared Netmiko helpers for managed NE connect/collect."""

from __future__ import annotations

from typing import Any


def normalize_netmiko_device_type(device_type: str, protocol: str) -> str:
    dt = str(device_type or "").strip()
    proto = str(protocol or "ssh").strip().lower()
    if "zte" in dt.lower():
        if dt == "zte":
            return f"zte_zxros_{proto}"
        if "telnet" not in dt and "ssh" not in dt:
            return f"{dt}_{proto}"
        return dt
    if "telnet" not in dt and "ssh" not in dt:
        return f"{dt}_{proto}"
    return dt


def is_cisco_ios_device_type(device_type: str) -> bool:
    dt = str(device_type or "").strip().lower()
    return "cisco_ios" in dt or dt in {"cisco_ios", "cisco_ios_ssh", "cisco_ios_telnet"}


def send_show_command(conn: Any, command: str, *, read_timeout: int = 120) -> str:
    """Send a show/display command via ``send_command`` (wait for device prompt).

    Do not use ``send_command_timing`` for Cisco config collection: long idle during
    ``Building configuration...`` is treated as end-of-output and truncates the config.
    """
    cmd = str(command or "").strip()
    if not cmd:
        return ""
    return str(conn.send_command(command_string=cmd, read_timeout=read_timeout) or "")
