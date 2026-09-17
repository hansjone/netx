"""ZTE: show nd6 cache [| one-line]."""

from __future__ import annotations

import re
from typing import Any

_ND6_ROW_RE = re.compile(
    r"^(?P<addr>\S+)\s+(?P<link>\S+)\s+(?P<age>\S+)\s+(?P<status>\S+)\s+"
    r"(?P<iface>\S+)\s+(?P<type>\S+)\s*$",
    re.I,
)


def normalize_nd6_cache(
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
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("s-static") or low.startswith("total cache") or low.startswith("only current"):
            continue
        if low.startswith("address") and "link" in low:
            continue
        m = _ND6_ROW_RE.match(line)
        if not m:
            continue
        addr = m.group("addr")
        iface = m.group("iface")
        key = (addr, iface)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "address": addr[:128],
                "link_address": m.group("link")[:64],
                "age": m.group("age")[:64],
                "status": m.group("status")[:32],
                "interface": iface[:128],
                "type": m.group("type")[:32],
            }
        )
    return out
