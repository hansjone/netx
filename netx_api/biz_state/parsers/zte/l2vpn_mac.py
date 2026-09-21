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

_SID_OUT_RE = re.compile(
    r"(?i)^SID\s+(?P<vpn_sid>\S+)\s*,\s*(?P<neighbor_sid>\S+)\s*$"
)
_AC_OUT_RE = re.compile(
    r"^(?P<ac_port>\S+)\s*,\s*[EeIi]:(?P<exter_vlan>\S+)\s*$"
)
_PW_OUT_RE = re.compile(r"^(?P<pw>\S+)\s*,\s*(?P<neighbor>\S+)\s*$")


def _empty_outgoing(raw: str = "") -> dict[str, str]:
    text = str(raw or "").strip()
    return {
        "outgoing": text[:256],
        "pw": "",
        "neighbor": "",
        "ac_port": "",
        "exter_vlan": "",
        "vpn_sid": "",
        "neighbor_sid": "",
    }


def _parse_outgoing(raw: str) -> dict[str, str]:
    """Split Outgoing Information into typed fields; keep raw in outgoing."""
    out = _empty_outgoing(raw)
    text = out["outgoing"]
    if not text:
        return out

    m = _SID_OUT_RE.match(text)
    if m:
        out["vpn_sid"] = m.group("vpn_sid")[:128]
        out["neighbor_sid"] = m.group("neighbor_sid")[:128]
        return out

    m = _AC_OUT_RE.match(text)
    if m:
        out["ac_port"] = m.group("ac_port")[:128]
        out["exter_vlan"] = m.group("exter_vlan")[:32]
        return out

    # ESI / other opaque forms stay in outgoing only.
    if text.upper().startswith("ESI:"):
        return out

    m = _PW_OUT_RE.match(text)
    if m:
        out["pw"] = m.group("pw")[:128]
        out["neighbor"] = m.group("neighbor")[:128]
        return out

    return out


def _is_mac(token: str) -> bool:
    t = str(token or "").strip()
    return bool(re.fullmatch(r"[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}", t))


def _row(
    *,
    mac: str,
    vpn: str,
    vlan: str,
    outgoing: str,
    attribute: str,
) -> dict[str, Any] | None:
    if not _is_mac(mac) or not vpn or vpn.lower() == "vpn":
        return None
    row = {
        "mac": mac[:64],
        "vpn": vpn[:128],
        "vlan": str(vlan or "").strip()[:32],
        "attribute": str(attribute or "").strip()[:64],
    }
    row.update(_parse_outgoing(outgoing))
    return row


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        mac = row_get(r, "MAC", "mac")
        vpn = row_get(r, "VPN", "vpn")
        key = (mac, vpn)
        if key in seen:
            continue
        mapped = _row(
            mac=mac,
            vpn=vpn,
            vlan=row_get(r, "VLAN", "vlan"),
            outgoing=row_get(r, "OUTGOING", "outgoing"),
            attribute=row_get(r, "ATTRIBUTE", "attribute"),
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
        if not line or line.lower().startswith("mac "):
            continue
        m = _MAC_RE.match(line)
        if not m:
            continue
        key = (m.group("mac"), m.group("vpn"))
        if key in seen:
            continue
        mapped = _row(
            mac=m.group("mac"),
            vpn=m.group("vpn"),
            vlan=m.group("vlan"),
            outgoing=m.group("out").strip(),
            attribute=m.group("attr"),
        )
        if not mapped:
            continue
        seen.add(key)
        out.append(mapped)
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
