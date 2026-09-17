"""ZTE: show arp [| one-line]."""

from __future__ import annotations

import re
from typing import Any

_ARP_AGE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")


def is_valid_arp_age(age: str) -> bool:
    """True when Age looks like a dynamic timer (HH:MM:SS), not static flags like H."""
    return bool(_ARP_AGE_TIME_RE.match(str(age or "").strip()))


def normalize_arp(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, command, params)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    ip_re = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("---") or line.lower().startswith("arp protect"):
            continue
        if line.lower().startswith("the count"):
            continue
        if "hardware" in line.lower() and "address" in line.lower():
            continue
        parts = line.split()
        if len(parts) < 4 or not ip_re.match(parts[0]):
            continue
        ip = parts[0]
        age = parts[1]
        mac = parts[2]
        iface = parts[3]
        exter = parts[4] if len(parts) > 4 else ""
        inter = parts[5] if len(parts) > 5 else ""
        sub = parts[6] if len(parts) > 6 else ""
        key = (ip, iface)
        if key in seen:
            continue
        seen.add(key)
        dynamic = is_valid_arp_age(age)
        out.append(
            {
                "ip": ip[:64],
                "age": age[:32],
                "mac": mac[:64],
                "interface": iface[:128],
                "exter_vlan": exter[:32],
                "inter_vlan": inter[:32],
                "sub_interface": sub[:128],
                "entry_type": "dynamic" if dynamic else "static",
            }
        )
    return out
