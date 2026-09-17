"""ZTE: show arp [| one-line].

Pipeline: TextFSM ``zte_zxros_show_arp`` → map → hand fallback.
Cross-command VRF comes from profile ``enrich_joins`` (if_intf), not here.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_arp",)

_ARP_AGE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def is_valid_arp_age(age: str) -> bool:
    """True when Age looks like a dynamic timer (HH:MM:SS), not static flags like H."""
    return bool(_ARP_AGE_TIME_RE.match(str(age or "").strip()))


def _row_from_fields(
    *,
    ip: str,
    age: str,
    mac: str,
    iface: str,
    exter: str = "",
    inter: str = "",
    sub: str = "",
) -> dict[str, Any] | None:
    ip = str(ip or "").strip()
    iface = str(iface or "").strip()
    if not ip or not iface:
        return None
    age = str(age or "").strip()
    dynamic = is_valid_arp_age(age)
    return {
        "ip": ip[:64],
        "age": age[:32],
        "mac": str(mac or "").strip()[:64],
        "interface": iface[:128],
        "exter_vlan": str(exter or "").strip()[:32],
        "inter_vlan": str(inter or "").strip()[:32],
        "sub_interface": str(sub or "").strip()[:128],
        "entry_type": "dynamic" if dynamic else "static",
        "vrf": "",
    }


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        ip = row_get(r, "IP", "ip")
        iface = row_get(r, "INTERFACE", "interface")
        key = (ip, iface)
        if not ip or not iface or key in seen:
            continue
        mapped = _row_from_fields(
            ip=ip,
            age=row_get(r, "AGE", "age"),
            mac=row_get(r, "MAC", "mac", "HARDWARE", "hardware"),
            iface=iface,
            exter=row_get(r, "EXTER_VLAN", "exter_vlan"),
            inter=row_get(r, "INTER_VLAN", "inter_vlan"),
            sub=row_get(r, "SUB_INTERFACE", "sub_interface"),
        )
        if not mapped:
            continue
        seen.add(key)
        out.append(mapped)
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("---") or line.lower().startswith("arp protect"):
            continue
        if line.lower().startswith("the count"):
            continue
        if "hardware" in line.lower() and "address" in line.lower():
            continue
        parts = line.split()
        if len(parts) < 4 or not _IP_RE.match(parts[0]):
            continue
        ip, age, mac, iface = parts[0], parts[1], parts[2], parts[3]
        key = (ip, iface)
        if key in seen:
            continue
        mapped = _row_from_fields(
            ip=ip,
            age=age,
            mac=mac,
            iface=iface,
            exter=parts[4] if len(parts) > 4 else "",
            inter=parts[5] if len(parts) > 5 else "",
            sub=parts[6] if len(parts) > 6 else "",
        )
        if not mapped:
            continue
        seen.add(key)
        out.append(mapped)
    return out


def normalize_arp(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
    raws: Mapping[str, str] | None = None,
    aux_records: Mapping[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    _ = (params, raws, aux_records)
    tables = dict(fsm_tables or {})
    if not any(tables.get(k) for k in RULE_KEYS):
        platform = resolve_cli_platform(
            vendor=vendor,
            device_type=device_type,
            vendor_key=resolve_vendor_key(vendor, device_type),
        )
        cmd = str(command or "show arp").strip() or "show arp"
        if platform:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(
        tables,
        RULE_KEYS,
        _map_fsm_rows,
        _hand_parse,
        raw_text=raw_text,
        vendor=vendor,
        device_type=device_type,
        command=command,
    )


normalize_arp.RULE_KEYS = RULE_KEYS
