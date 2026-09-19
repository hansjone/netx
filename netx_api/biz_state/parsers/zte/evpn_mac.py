"""ZTE: show bgp evpn mac."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_bgp_evpn_mac",)

_ROUTE_RE = re.compile(
    r"^\s*(?P<flags>[*>isd]*)\s*"
    r"(?P<net>\S+)\s+(?P<nh>\S+)\s+(?P<rest>.*)$"
)


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        net = row_get(r, "NETWORK", "network")
        if not net or net.lower() == "network" or net in seen:
            continue
        seen.add(net)
        out.append(
            {
                "network": net[:256],
                "next_hop": row_get(r, "NEXT_HOP", "next_hop")[:128],
                "metric": row_get(r, "METRIC", "metric")[:32],
                "loc_prf": row_get(r, "LOC_PRF", "loc_prf")[:32],
                "rt_prf": row_get(r, "RT_PRF", "rt_prf")[:32],
                "path": row_get(r, "PATH", "path")[:256],
                "status_codes": "",
            }
        )
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.strip().lower()
        if low.startswith("network") or low.startswith("status") or low.startswith("origin"):
            continue
        if low.startswith("route distinguisher"):
            continue
        m = _ROUTE_RE.match(line)
        if not m:
            continue
        net = m.group("net")
        if "/" not in net and ":" not in net and not re.search(r"[0-9a-fA-F]{4}\.", net):
            # skip non-NLRI-looking tokens
            if not re.match(r"^\[", net):
                continue
        if net in seen:
            continue
        seen.add(net)
        rest = m.group("rest").split()
        metric = loc = rt = ""
        path_parts: list[str] = []
        for p in rest:
            if not path_parts and re.fullmatch(r"\d+", p):
                if not metric:
                    metric = p
                elif not loc:
                    loc = p
                elif not rt:
                    rt = p
                else:
                    path_parts.append(p)
            else:
                path_parts.append(p)
        out.append(
            {
                "network": net[:256],
                "next_hop": m.group("nh")[:128],
                "metric": metric[:32],
                "loc_prf": loc[:32],
                "rt_prf": rt[:32],
                "path": " ".join(path_parts)[:256],
                "status_codes": (m.group("flags") or "").strip()[:16],
            }
        )
    return out


def normalize_evpn_mac(
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
        cmd = str(command or "show bgp evpn mac").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_evpn_mac.RULE_KEYS = RULE_KEYS
