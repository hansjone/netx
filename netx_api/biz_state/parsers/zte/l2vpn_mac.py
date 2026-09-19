"""ZTE: show mac l2vpn."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_mac_l2vpn",)

_MAC_RE = re.compile(
    r"^(?P<mac>[0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})\s+"
    r"(?P<vpn>\S+)\s+(?P<vlan>\S+)\s+(?P<out>.+?)\s+(?P<attr>\S+)\s*$"
)


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        mac = row_get(r, "MAC", "mac")
        vpn = row_get(r, "VPN", "vpn")
        if not mac or mac.lower() == "mac":
            continue
        key = (mac, vpn)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "mac": mac[:64],
                "vpn": vpn[:128],
                "vlan": row_get(r, "VLAN", "vlan")[:32],
                "outgoing": row_get(r, "OUTGOING", "outgoing")[:256],
                "attribute": row_get(r, "ATTRIBUTE", "attribute")[:64],
            }
        )
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("mac "):
            continue
        m = _MAC_RE.match(line)
        if not m:
            continue
        key = (m.group("mac"), m.group("vpn"))
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "mac": m.group("mac")[:64],
                "vpn": m.group("vpn")[:128],
                "vlan": m.group("vlan")[:32],
                "outgoing": m.group("out").strip()[:256],
                "attribute": m.group("attr")[:64],
            }
        )
    return out


def normalize_l2vpn_mac(
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
        cmd = str(command or "show mac l2vpn").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_l2vpn_mac.RULE_KEYS = RULE_KEYS
