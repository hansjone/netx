"""VRF / VPN-instance list discovery parser (multi-vendor TextFSM + fallback)."""

from __future__ import annotations

import re
from typing import Any

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import parse_cli, resolve_cli_platform, row_get


def normalize_vrf_list(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = params
    platform = resolve_cli_platform(
        vendor=vendor,
        device_type=device_type,
        vendor_key=resolve_vendor_key(vendor, device_type),
    )
    cmd = str(command or "").strip()
    rows = parse_cli(platform=platform, command=cmd, text=raw_text) if platform and cmd else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        name = row_get(r, "NAME", "VPN_INSTANCE", "VRF", "vrf_name")
        if not name or name.lower() in ("name", "vrf", "vpn-instance"):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "vrf_name": name[:128],
                "rd": row_get(r, "DEFAULT_RD", "RD", "rd")[:64],
                "protocols": row_get(r, "PROTOCOLS", "ADDRESS_FAMILY", "protocols")[:64],
            }
        )
    if out:
        return out
    for line in str(raw_text or "").splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_./:-]+)\s+(\d+:\d+|<not set>|\S+:\S+)\s*", line)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in ("name", "vrf", "vpn-instance", "total"):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append({"vrf_name": name[:128], "rd": m.group(2)[:64], "protocols": ""})
    return out
