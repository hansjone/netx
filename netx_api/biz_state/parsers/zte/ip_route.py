"""ZTE: show ip forwarding route [vrf <vrf>]."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm
from .bgp_peer import _detect_vrf

RULE_KEYS = ("zte_zxros_show_ip_forwarding_route",)

_ROUTE_RE = re.compile(
    r"^\s*(?P<flags>[*>R]*)\s*"
    r"(?P<dest>\d{1,3}(?:\.\d{1,3}){3}/\d+)\s+"
    r"(?P<gw>\S+)\s+(?P<iface>\S+)\s+(?P<owner>\S+)\s+"
    r"(?P<pri>\S+)\s+(?P<metric>\S+)\s*$"
)


def _map_fsm_rows(rows: list[dict[str, Any]], *, vrf: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        dest = row_get(r, "DEST", "dest")
        if not dest or dest.lower() == "dest" or dest in seen:
            continue
        seen.add(dest)
        out.append(
            {
                "vrf": vrf[:128],
                "dest": dest[:64],
                "gateway": row_get(r, "GATEWAY", "gateway")[:64],
                "interface": row_get(r, "INTERFACE", "interface")[:128],
                "owner": row_get(r, "OWNER", "owner")[:64],
                "pri": row_get(r, "PRI", "pri")[:16],
                "metric": row_get(r, "METRIC", "metric")[:32],
                "flags": row_get(r, "FLAGS", "flags")[:16],
            }
        )
    return out


def _hand_parse(*, raw_text: str, vrf: str = "", **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().lower().startswith("dest"):
            continue
        m = _ROUTE_RE.match(line)
        if not m:
            continue
        dest = m.group("dest")
        if dest in seen:
            continue
        seen.add(dest)
        out.append(
            {
                "vrf": vrf[:128],
                "dest": dest[:64],
                "gateway": m.group("gw")[:64],
                "interface": m.group("iface")[:128],
                "owner": m.group("owner")[:64],
                "pri": m.group("pri")[:16],
                "metric": m.group("metric")[:32],
                "flags": (m.group("flags") or "").strip()[:16],
            }
        )
    return out


def normalize_ip_route(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    vrf = _detect_vrf(command, params)
    tables = dict(fsm_tables or {})
    if not any(tables.get(k) for k in RULE_KEYS):
        platform = resolve_cli_platform(
            vendor=vendor,
            device_type=device_type,
            vendor_key=resolve_vendor_key(vendor, device_type),
        )
        cmd = str(command or "show ip forwarding route").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )

    def _map(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _map_fsm_rows(rows, vrf=vrf)

    def _hand(*, raw_text: str, **kw: Any) -> list[dict[str, Any]]:
        return _hand_parse(raw_text=raw_text, vrf=vrf, **kw)

    return prefer_fsm(tables, RULE_KEYS, _map, _hand, raw_text=raw_text)


normalize_ip_route.RULE_KEYS = RULE_KEYS
