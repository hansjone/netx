"""ZTE: show bgp … neighbor {in|out} [vrf] routes."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm
from .bgp_peer import _detect_bgp_afi, _detect_vrf

RULE_KEYS = ("zte_zxros_show_bgp_neighbor_routes",)

_ROUTE_RE = re.compile(
    r"^\s*(?P<flags>[*<>isd]*)\s*"
    r"(?P<net>\d{1,3}(?:\.\d{1,3}){3}/\d+|[0-9A-Fa-f:]+(?:/\d+)?)\s+"
    r"(?P<nh>\S+)\s+"
    r"(?P<rest>.*)$"
)
_DIR_RE = re.compile(r"(?i)\bneighbor\s+(in|out)\s+")
_NEI_RE = re.compile(r"(?i)\bneighbor\s+(?:in|out)\s+(\S+)")


def _detect_direction(command: str, params: dict[str, str] | None) -> str:
    if params and params.get("direction"):
        return str(params.get("direction") or "").strip().lower()
    m = _DIR_RE.search(str(command or ""))
    return m.group(1).lower() if m else ""


def _detect_neighbor(command: str, params: dict[str, str] | None) -> str:
    if params and params.get("neighbor"):
        return str(params.get("neighbor") or "").strip()
    m = _NEI_RE.search(str(command or ""))
    return m.group(1).strip() if m else ""


def _split_rest(rest: str) -> tuple[str, str, str, str]:
    """Parse trailing Metric LocPrf Tag/RtPrf Path columns (some may be blank)."""
    parts = str(rest or "").split()
    if not parts:
        return "", "", "", ""
    # Last token(s) are AS path + origin; path ends with i|e|?
    path = " ".join(parts)
    metric = loc = tag = ""
    # Heuristic: numeric-only leading fields are metric/loc/tag when present
    nums: list[str] = []
    path_parts: list[str] = []
    for p in parts:
        if not path_parts and re.fullmatch(r"\d+", p):
            nums.append(p)
        else:
            path_parts.append(p)
    if len(nums) >= 1:
        metric = nums[0]
    if len(nums) >= 2:
        loc = nums[1]
    if len(nums) >= 3:
        tag = nums[2]
    path = " ".join(path_parts)
    return metric, loc, tag, path


def _map_fsm_rows(
    rows: list[dict[str, Any]],
    *,
    afi: str,
    vrf: str,
    neighbor: str,
    direction: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        net = row_get(r, "NETWORK", "network")
        if not net or net.lower() == "network" or net in seen:
            continue
        seen.add(net)
        path = row_get(r, "PATH", "path")
        out.append(
            {
                "afi": afi[:32],
                "vrf": vrf[:128],
                "neighbor": neighbor[:128],
                "direction": direction[:8],
                "network": net[:128],
                "next_hop": row_get(r, "NEXT_HOP", "next_hop")[:128],
                "metric": row_get(r, "METRIC", "metric")[:32],
                "loc_prf": row_get(r, "LOC_PRF", "loc_prf")[:32],
                "tag": row_get(r, "TAG", "RT_PRF", "tag")[:32],
                "path": path[:256],
                "status_codes": "",
                "as_num": "",
                "state": "",
                "pfx_rcd": "",
            }
        )
    return out


def _hand_parse(
    *,
    raw_text: str,
    afi: str = "unknown",
    vrf: str = "",
    neighbor: str = "",
    direction: str = "",
    **_kw: Any,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.strip().lower()
        if low.startswith("network") or "next hop" in low:
            continue
        if low.startswith("status") or low.startswith("origin") or low.startswith("routes "):
            continue
        if low.startswith("local ") or low.startswith("remote ") or low.startswith("total "):
            continue
        if low.startswith("route distinguisher"):
            continue
        m = _ROUTE_RE.match(line)
        if not m:
            continue
        net = m.group("net")
        if net in seen:
            continue
        seen.add(net)
        metric, loc, tag, path = _split_rest(m.group("rest"))
        out.append(
            {
                "afi": afi[:32],
                "vrf": vrf[:128],
                "neighbor": neighbor[:128],
                "direction": direction[:8],
                "network": net[:128],
                "next_hop": m.group("nh")[:128],
                "metric": metric[:32],
                "loc_prf": loc[:32],
                "tag": tag[:32],
                "path": path[:256],
                "status_codes": (m.group("flags") or "").strip()[:16],
                "as_num": "",
                "state": "",
                "pfx_rcd": "",
            }
        )
    return out


def normalize_bgp_route(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    afi = _detect_bgp_afi(command, params)
    vrf = _detect_vrf(command, params)
    neighbor = _detect_neighbor(command, params)
    direction = _detect_direction(command, params)
    tables = dict(fsm_tables or {})
    if not any(tables.get(k) for k in RULE_KEYS):
        platform = resolve_cli_platform(
            vendor=vendor,
            device_type=device_type,
            vendor_key=resolve_vendor_key(vendor, device_type),
        )
        cmd = str(command or "show bgp neighbor routes").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )

    def _map(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _map_fsm_rows(
            rows, afi=afi, vrf=vrf, neighbor=neighbor, direction=direction
        )

    def _hand(*, raw_text: str, **kw: Any) -> list[dict[str, Any]]:
        return _hand_parse(
            raw_text=raw_text,
            afi=afi,
            vrf=vrf,
            neighbor=neighbor,
            direction=direction,
            **kw,
        )

    return prefer_fsm(tables, RULE_KEYS, _map, _hand, raw_text=raw_text)


normalize_bgp_route.RULE_KEYS = RULE_KEYS
