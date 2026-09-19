"""ZTE: show opticalinfo brief."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_opticalinfo_brief",)

_OPT_RE = re.compile(
    r"^(?P<iface>\S+)\s+(?P<otype>\S+)\s+(?P<wave>\S+)\s+"
    r"(?P<rx>\S+)\s+(?P<tx>\S+)\s+(?P<status>\S+)",
)


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        iface = row_get(r, "INTERFACE", "interface")
        if not iface or iface.lower() == "interface" or iface in seen:
            continue
        seen.add(iface)
        out.append(
            {
                "interface": iface[:128],
                "optic_type": row_get(r, "OPTIC_TYPE", "optic_type")[:64],
                "wavelength": row_get(r, "WAVELENGTH", "wavelength")[:32],
                "rx_power": row_get(r, "RX_POWER", "rx_power")[:64],
                "tx_power": row_get(r, "TX_POWER", "tx_power")[:64],
                "status": row_get(r, "STATUS", "status")[:32],
            }
        )
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("interface"):
            continue
        m = _OPT_RE.match(line)
        if not m:
            continue
        iface = m.group("iface")
        if iface in seen:
            continue
        seen.add(iface)
        out.append(
            {
                "interface": iface[:128],
                "optic_type": m.group("otype")[:64],
                "wavelength": m.group("wave")[:32],
                "rx_power": m.group("rx")[:64],
                "tx_power": m.group("tx")[:64],
                "status": m.group("status")[:32],
            }
        )
    return out


def normalize_optical_brief(
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
        cmd = str(command or "show opticalinfo brief").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_optical_brief.RULE_KEYS = RULE_KEYS
