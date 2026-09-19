"""ZTE: show ipv6 forwarding route [vrf <vrf>]."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm
from .bgp_peer import _detect_vrf

RULE_KEYS = ("zte_zxros_show_ipv6_forwarding_route",)

_DEST_RE = re.compile(
    r"^\s*(?P<dest>[0-9A-Fa-f:]+/\d+)\s+(?P<owner>\S+)\s+(?P<pri>\S+)\s+"
    r"(?P<metric>\S+)(?:\s+(?P<flags>\S+))?\s*$"
)
_NH_RE = re.compile(r"^\s+(?P<gw>[0-9A-Fa-f:]+|\S+),(?P<iface>\S+)\s*$")


def _map_fsm_rows(rows: list[dict[str, Any]], *, vrf: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    cur: dict[str, Any] | None = None
    for r in rows:
        dest = row_get(r, "DEST", "dest")
        gw = row_get(r, "GATEWAY", "gateway")
        iface = row_get(r, "INTERFACE", "interface")
        if dest:
            cur = {
                "vrf": vrf[:128],
                "dest": dest[:128],
                "gateway": "",
                "interface": "",
                "owner": row_get(r, "OWNER", "owner")[:64],
                "pri": row_get(r, "PRI", "pri")[:16],
                "metric": row_get(r, "METRIC", "metric")[:32],
                "flags": row_get(r, "FLAGS", "flags")[:16],
            }
            if gw or iface:
                cur["gateway"] = gw[:128]
                cur["interface"] = iface[:128]
                key = (dest, gw)
                if key not in seen:
                    seen.add(key)
                    out.append(cur)
                    cur = None
            continue
        if cur and (gw or iface):
            cur["gateway"] = gw[:128]
            cur["interface"] = iface[:128]
            key = (cur["dest"], gw)
            if key not in seen:
                seen.add(key)
                out.append(dict(cur))
            cur = None
    return out


def _hand_parse(*, raw_text: str, vrf: str = "", **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    pending: dict[str, Any] | None = None
    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _DEST_RE.match(line)
        if m:
            pending = {
                "vrf": vrf[:128],
                "dest": m.group("dest")[:128],
                "gateway": "",
                "interface": "",
                "owner": m.group("owner")[:64],
                "pri": m.group("pri")[:16],
                "metric": m.group("metric")[:32],
                "flags": (m.group("flags") or "")[:16],
            }
            continue
        m2 = _NH_RE.match(line)
        if m2 and pending:
            gw = m2.group("gw")
            key = (pending["dest"], gw)
            if key not in seen:
                seen.add(key)
                row = dict(pending)
                row["gateway"] = gw[:128]
                row["interface"] = m2.group("iface")[:128]
                out.append(row)
            pending = None
    return out


def normalize_ipv6_route(
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
        cmd = str(command or "show ipv6 forwarding route").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )

    def _map(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _map_fsm_rows(rows, vrf=vrf)

    def _hand(*, raw_text: str, **kw: Any) -> list[dict[str, Any]]:
        return _hand_parse(raw_text=raw_text, vrf=vrf, **kw)

    return prefer_fsm(tables, RULE_KEYS, _map, _hand, raw_text=raw_text)


normalize_ipv6_route.RULE_KEYS = RULE_KEYS
