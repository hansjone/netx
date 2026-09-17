"""ZTE: show interface brief."""

from __future__ import annotations

import re
from typing import Any

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import parse_cli, resolve_cli_platform, row_get

_IFACE_RE = re.compile(
    r"^(?P<iface>\S+)\s+(?P<attr>\S+)\s+(?P<mode>\S+)"
    r"(?:\s+(?P<bw>\S+))?\s+(?P<admin>up|down)\s+(?P<phy>up|down)\s+(?P<prot>up|down)"
    r"(?:\s+(?P<desc>.*))?$",
    re.I,
)


def normalize_interface_brief(
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
    cmd = str(command or "show interface brief").strip() or "show interface brief"
    rows = parse_cli(platform=platform, command=cmd, text=raw_text) if platform else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        iface = row_get(r, "INTERFACE", "interface")
        if not iface or iface.lower() == "interface":
            continue
        if iface in seen:
            continue
        seen.add(iface)
        out.append(
            {
                "interface": iface[:128],
                "attribute": row_get(r, "ATTRIBUTE", "attribute")[:64],
                "mode": row_get(r, "MODE", "mode")[:64],
                "bw": row_get(r, "BW", "bw")[:32],
                "admin": row_get(r, "ADMIN", "admin")[:16],
                "phy": row_get(r, "PHY", "phy")[:16],
                "prot": row_get(r, "PROT", "prot")[:16],
                "description": row_get(r, "DESCRIPTION", "description")[:256],
            }
        )
    if out:
        return out
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("interface"):
            continue
        m = _IFACE_RE.match(line)
        if not m:
            continue
        iface = m.group("iface")
        if iface in seen:
            continue
        seen.add(iface)
        out.append(
            {
                "interface": iface[:128],
                "attribute": (m.group("attr") or "")[:64],
                "mode": (m.group("mode") or "")[:64],
                "bw": (m.group("bw") or "")[:32],
                "admin": (m.group("admin") or "")[:16],
                "phy": (m.group("phy") or "")[:16],
                "prot": (m.group("prot") or "")[:16],
                "description": (m.group("desc") or "").strip()[:256],
            }
        )
    return out
