"""VRF list + per-VRF route summary parsers."""

from __future__ import annotations

import re
from typing import Any

from ...lldp_shared import resolve_vendor_key
from ...ntc_parse import parse_cli, resolve_cli_platform, row_get


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
    # Fallback: loose line scrape for labs without TextFSM hit
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


_SOURCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("connected", re.compile(r"(?i)^\s*connected\s+(\d+)\s*$")),
    ("static", re.compile(r"(?i)^\s*static\s+(\d+)\s*$")),
    ("local", re.compile(r"(?i)^\s*local\s+(\d+)\s*$")),
    ("ospf", re.compile(r"(?i)^\s*ospf(?:\s+\S+)?\s+(\d+)\s*$")),
    ("isis", re.compile(r"(?i)^\s*isis(?:\s+\S+)?\s+(\d+)\s*$")),
    ("bgp", re.compile(r"(?i)^\s*bgp(?:\s+\S+)?\s+(\d+)\s*$")),
    ("rip", re.compile(r"(?i)^\s*rip(?:\s+\S+)?\s+(\d+)\s*$")),
    ("total", re.compile(r"(?i)^\s*(?:total|totals?)\s+(?:routes?\s+)?(\d+)\s*$")),
]


def normalize_vrf_route_summary(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, command)
    vrf = str((params or {}).get("vrf") or (params or {}).get("vrf_name") or "").strip()
    text = str(raw_text or "")
    found: dict[str, int] = {}
    for src, pat in _SOURCE_PATTERNS:
        for line in text.splitlines():
            m = pat.match(line.strip())
            if m:
                found[src] = int(m.group(1))
                break

    if not found:
        # Count non-empty data-ish lines as opaque "routes" bucket
        n = 0
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("-") or s.lower().startswith(("code", "codes", "gateway", "routing table")):
                continue
            if re.match(r"^[A-Z*+]>?\s+\S+", s) or re.match(r"^\S+\s+\d+\.\d+\.\d+\.\d+", s):
                n += 1
        if n:
            found["routes"] = n

    rows: list[dict[str, Any]] = []
    for src, count in found.items():
        rows.append({"vrf": vrf[:128], "source": src[:64], "networks": count})
    if vrf and not rows:
        rows.append({"vrf": vrf[:128], "source": "empty", "networks": 0})
    return rows
