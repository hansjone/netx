"""ZTE: show isis adjacency [| one-line]."""

from __future__ import annotations

import re
from typing import Any

_ISIS_ROW_RE = re.compile(
    r"^(?P<iface>\S+)\s+(?P<sys>\S+)\s+(?P<state>\S+)\s+(?P<lev>\S+)\s+"
    r"(?P<holds>\S+)\s+(?P<snpa>\S+)\s+(?P<pri>\S+)\s+(?P<mt>\S+)\s+"
    r"(?P<nsf>\S+)\s+(?P<af>\S+)\s*$",
    re.I,
)
_PROCESS_RE = re.compile(r"(?i)^\s*Process\s+ID\s*:\s*(\d+)\s*$")

RULE_KEYS: tuple[str, ...] = ()


def normalize_isis_adjacency(
    *,
    raw_text: str,
    fsm_tables=None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, command, params, fsm_tables)
    out: list[dict[str, Any]] = []
    process_id = ""
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        pm = _PROCESS_RE.match(line)
        if pm:
            process_id = pm.group(1)
            continue
        if line.lower().startswith("interface") and "system" in line.lower():
            continue
        m = _ISIS_ROW_RE.match(line)
        if not m:
            continue
        out.append(
            {
                "process_id": process_id,
                "interface": m.group("iface")[:128],
                "system_id": m.group("sys")[:128],
                "state": m.group("state")[:32],
                "lev": m.group("lev")[:16],
                "holds": m.group("holds")[:32],
                "snpa": m.group("snpa")[:64],
                "pri": m.group("pri")[:16],
                "mt": m.group("mt")[:16],
                "nsf": m.group("nsf")[:32],
                "af": m.group("af")[:64],
            }
        )
    return out

normalize_isis_adjacency.RULE_KEYS = RULE_KEYS
