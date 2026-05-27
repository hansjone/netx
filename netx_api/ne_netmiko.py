"""Shared Netmiko helpers for managed NE connect/collect."""

from __future__ import annotations


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
