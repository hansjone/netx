"""Per-VRF route summary counts (multi-vendor line scrape)."""

from __future__ import annotations

import re
from typing import Any

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

RULE_KEYS: tuple[str, ...] = ()


def normalize_vrf_route_summary(
    *,
    raw_text: str,
    fsm_tables=None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, command, fsm_tables)
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

normalize_vrf_route_summary.RULE_KEYS = RULE_KEYS
