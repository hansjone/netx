"""VRF / VPN-instance list discovery parser (multi-vendor TextFSM + fallback)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from .pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_ip_vrf",)

_VRF_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_./:-]+)\s+"
    r"(?P<rd>\d+:\d+|<not set>|\S+:\S+)\s*"
    r"(?:(?P<protocols>ipv4(?:,ipv6)?|ipv6(?:,ipv4)?|ipv4|ipv6)\s*)?"
    r"(?P<vrf_id>\d+)?\s*$",
    re.I,
)


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        name = row_get(r, "NAME", "VPN_INSTANCE", "VRF", "vrf_name")
        if not name or name.lower() in ("name", "vrf", "vpn-instance"):
            continue
        if name in seen:
            continue
        seen.add(name)
        protocols = row_get(r, "PROTOCOLS", "ADDRESS_FAMILY", "protocols")
        # Legacy templates may put protocols in INTERFACES
        if not protocols:
            protocols = row_get(r, "INTERFACES", "interfaces")
        out.append(
            {
                "vrf_name": name[:128],
                "rd": row_get(r, "RD", "DEFAULT_RD", "rd")[:64],
                "protocols": protocols[:64],
                "vrf_id": row_get(r, "VRF_ID", "vrf_id")[:32],
            }
        )
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in str(raw_text or "").splitlines():
        m = _VRF_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if name.lower() in ("name", "vrf", "vpn-instance", "total"):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "vrf_name": name[:128],
                "rd": (m.group("rd") or "")[:64],
                "protocols": (m.group("protocols") or "")[:64],
                "vrf_id": (m.group("vrf_id") or "")[:32],
            }
        )
    return out


def normalize_vrf_list(
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
        cmd = str(command or "show ip vrf").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(
        tables,
        RULE_KEYS,
        _map_fsm_rows,
        _hand_parse,
        raw_text=raw_text,
    )


normalize_vrf_list.RULE_KEYS = RULE_KEYS
