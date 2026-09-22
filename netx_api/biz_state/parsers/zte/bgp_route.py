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
_TOTAL_RE = re.compile(r"(?i)total\s+number\s+of\s+routes\s*:\s*(\d+)")
_HEADER_NETS = frozenset(
    {
        "network",
        "dest",
        "destination",
        "next",
        "hop",
        "metric",
        "locprf",
        "loc_prf",
        "intag",
        "rtprf",
        "tag",
        "path",
        "from",
    }
)


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


def _looks_like_prefix(net: str) -> bool:
    tok = str(net or "").strip()
    if not tok or tok.lower() in _HEADER_NETS:
        return False
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}", tok):
        return True
    # IPv6 prefix / bare address
    if ":" in tok and re.search(r"[0-9A-Fa-f]:", tok):
        return True
    return False


def _split_rest(rest: str) -> tuple[str, str, str, str]:
    """Parse trailing Metric LocPrf Tag/RtPrf Path columns (some may be blank)."""
    parts = str(rest or "").split()
    if not parts:
        return "", "", "", ""
    metric = loc = tag = ""
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


def _empty_if_total_zero(raw_text: str) -> bool:
    """True when device reports Total number of routes: 0."""
    m = _TOTAL_RE.search(str(raw_text or ""))
    return bool(m and int(m.group(1)) == 0)


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
        if not _looks_like_prefix(net) or net in seen:
            continue
        nh = row_get(r, "NEXT_HOP", "next_hop")
        if str(nh or "").strip().lower() in _HEADER_NETS:
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
                "next_hop": nh[:128],
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
        if low.startswith(("network", "dest ", "destination")):
            continue
        if "next hop" in low or low.startswith("status") or low.startswith("origin"):
            continue
        if low.startswith("routes ") or low.startswith("current as"):
            continue
        if low.startswith("local ") or low.startswith("remote ") or low.startswith("total "):
            continue
        if low.startswith("route distinguisher") or low.startswith("valid ") or low.startswith("invalid "):
            continue
        m = _ROUTE_RE.match(line)
        if not m:
            continue
        net = m.group("net")
        if not _looks_like_prefix(net) or net in seen:
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
    if _empty_if_total_zero(raw_text):
        return []

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
