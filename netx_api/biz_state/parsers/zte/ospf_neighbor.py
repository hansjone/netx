"""ZTE: show ip ospf neighbor."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_ospf_neighbor",)

_PROC_RE = re.compile(r"Process\s+ID\s+(\d+)", re.I)
_NEI_RE = re.compile(
    r"^(?P<nid>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<pri>\S+)\s+(?P<state>\S+)\s+"
    r"(?P<dead>\S+)\s+(?P<addr>\S+)\s+(?P<iface>\S+)\s*$"
)


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for r in rows:
        nid = row_get(r, "NEIGHBOR_ID", "neighbor_id")
        iface = row_get(r, "INTERFACE", "interface")
        proc = row_get(r, "PROCESS_ID", "process_id") or "0"
        if not nid or not iface:
            continue
        key = (proc, nid, iface)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "process_id": proc[:32],
                "neighbor_id": nid[:64],
                "pri": row_get(r, "PRI", "pri")[:16],
                "state": row_get(r, "STATE", "state")[:64],
                "dead_time": row_get(r, "DEAD_TIME", "dead_time")[:32],
                "address": row_get(r, "ADDRESS", "address")[:64],
                "interface": iface[:128],
            }
        )
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    proc = "0"
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        pm = _PROC_RE.search(line)
        if pm:
            proc = pm.group(1)
            continue
        if line.lower().startswith("neighbor id"):
            continue
        m = _NEI_RE.match(line)
        if not m:
            continue
        key = (proc, m.group("nid"), m.group("iface"))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "process_id": proc[:32],
                "neighbor_id": m.group("nid")[:64],
                "pri": m.group("pri")[:16],
                "state": m.group("state")[:64],
                "dead_time": m.group("dead")[:32],
                "address": m.group("addr")[:64],
                "interface": m.group("iface")[:128],
            }
        )
    return out


def normalize_ospf_neighbor(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = params
    tables = dict(fsm_tables or {})
    if not any(tables.get(k) for k in RULE_KEYS):
        platform = resolve_cli_platform(
            vendor=vendor,
            device_type=device_type,
            vendor_key=resolve_vendor_key(vendor, device_type),
        )
        cmd = str(command or "show ip ospf neighbor").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_ospf_neighbor.RULE_KEYS = RULE_KEYS
