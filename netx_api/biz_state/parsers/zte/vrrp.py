"""ZTE: show vrrp {ipv4|ipv6} brief."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_vrrp_brief",)

_VRRP_RE = re.compile(
    r"^(?P<iface>\S+)\s+(?P<vrid>\d+)\s+(?P<pri>\d+)\s+(?P<time>\d+)\s+"
    r"(?:(?P<flags>[APL\s]+)\s+)?"
    r"(?P<state>Master|Backup|Init)\s+"
    r"(?P<master>\S+)\s+(?P<vrouter>\S+)\s*$",
    re.I,
)


def _detect_af(command: str, params: dict[str, str] | None) -> str:
    if params and params.get("af"):
        return str(params.get("af") or "").strip().lower()
    low = str(command or "").lower()
    if "ipv6" in low:
        return "ipv6"
    return "ipv4"


def _map_fsm_rows(rows: list[dict[str, Any]], *, af: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for r in rows:
        iface = row_get(r, "INTERFACE", "interface")
        vrid = row_get(r, "VR_ID", "vr_id")
        if not iface or not vrid:
            continue
        key = (af, iface, vrid)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "af": af[:16],
                "interface": iface[:128],
                "vr_id": vrid[:32],
                "priority": row_get(r, "PRIORITY", "priority")[:16],
                "time": row_get(r, "TIME", "time")[:16],
                "flags": row_get(r, "FLAGS", "flags")[:16],
                "state": row_get(r, "STATE", "state")[:32],
                "master_addr": row_get(r, "MASTER_ADDR", "master_addr")[:128],
                "vrouter_addr": row_get(r, "VROUTER_ADDR", "vrouter_addr")[:128],
            }
        )
    return out


def _hand_parse(*, raw_text: str, af: str = "ipv4", **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("interface"):
            continue
        m = _VRRP_RE.match(line)
        if not m:
            continue
        key = (af, m.group("iface"), m.group("vrid"))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "af": af[:16],
                "interface": m.group("iface")[:128],
                "vr_id": m.group("vrid")[:32],
                "priority": m.group("pri")[:16],
                "time": m.group("time")[:16],
                "flags": (m.group("flags") or "").strip()[:16],
                "state": m.group("state")[:32],
                "master_addr": m.group("master")[:128],
                "vrouter_addr": m.group("vrouter")[:128],
            }
        )
    return out


def normalize_vrrp(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    af = _detect_af(command, params)
    tables = dict(fsm_tables or {})
    if not any(tables.get(k) for k in RULE_KEYS):
        platform = resolve_cli_platform(
            vendor=vendor,
            device_type=device_type,
            vendor_key=resolve_vendor_key(vendor, device_type),
        )
        cmd = str(command or "show vrrp ipv4 brief").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )

    def _map(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _map_fsm_rows(rows, af=af)

    def _hand(*, raw_text: str, **kw: Any) -> list[dict[str, Any]]:
        return _hand_parse(raw_text=raw_text, af=af, **kw)

    return prefer_fsm(tables, RULE_KEYS, _map, _hand, raw_text=raw_text)


normalize_vrrp.RULE_KEYS = RULE_KEYS
