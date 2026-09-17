"""ZTE: show running-config if-intf → interface / VRF map.

Pipeline: TextFSM ``zte_zxros_show_running_config_if_intf`` first; hand fallback.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_running_config_if_intf",)

_IFACE_RE = re.compile(r"^\s*interface\s+(\S+)\s*$", re.I)
_VRF_RE = re.compile(r"^\s*ip\s+vrf\s+forwarding\s+(\S+)\s*$", re.I)


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        iface = row_get(r, "INTERFACE", "interface")
        vrf = row_get(r, "VRF", "vrf")
        if not iface or not vrf or iface in seen:
            continue
        seen.add(iface)
        out.append({"interface": iface[:128], "vrf": vrf[:128]})
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur = ""
    cur_vrf = ""
    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        m_if = _IFACE_RE.match(line)
        if m_if:
            if cur and cur_vrf and cur not in seen:
                seen.add(cur)
                out.append({"interface": cur[:128], "vrf": cur_vrf[:128]})
            cur = m_if.group(1).strip()
            cur_vrf = ""
            continue
        if line.strip() == "$":
            if cur and cur_vrf and cur not in seen:
                seen.add(cur)
                out.append({"interface": cur[:128], "vrf": cur_vrf[:128]})
            cur = ""
            cur_vrf = ""
            continue
        m_vrf = _VRF_RE.match(line)
        if m_vrf and cur:
            cur_vrf = m_vrf.group(1).strip()
    if cur and cur_vrf and cur not in seen:
        out.append({"interface": cur[:128], "vrf": cur_vrf[:128]})
    return out


def parse_if_intf_vrf_map(raw_text: str = "", *, rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
    """Build interface → vrf from metric rows or raw if-intf text."""
    if rows is None:
        rows = _hand_parse(raw_text=raw_text)
    out: dict[str, str] = {}
    for r in rows or []:
        iface = str((r or {}).get("interface") or "").strip()
        vrf = str((r or {}).get("vrf") or "").strip()
        if iface and vrf:
            out[iface] = vrf
    return out


def normalize_if_intf(
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
        cmd = str(command or "show running-config if-intf").strip()
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


normalize_if_intf.RULE_KEYS = RULE_KEYS
