"""ZTE: show bgp … neighbor {in|out} [vrf] routes."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm
from .bgp_peer import _detect_bgp_afi, _detect_local_as, _detect_vrf

RULE_KEYS = ("zte_zxros_show_bgp_neighbor_routes",)

# Single-line IPv4-style: * 10.1.0.0/24  10.0.0.1  … path
_ROUTE_ONE_LINE_RE = re.compile(
    r"^\s*(?P<flags>[*<>isd]*)\s*"
    r"(?P<net>\S+)\s+"
    r"(?P<nh>\S+)\s+"
    r"(?P<rest>.*)$"
)
# Network alone (often IPv6 wrap): * 2407::1/128   or bare prefix for "out"
_NET_ONLY_RE = re.compile(
    r"^\s*(?P<flags>[*<>isd]*)\s*(?P<net>\S+)\s*$"
)
_DIR_RE = re.compile(r"(?i)\bneighbor\s+(in|out)\s+")
# Neighbor may be IPv4 or IPv6 (consume until EOL / pipe)
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


def _looks_like_ip_or_prefix(tok: str) -> bool:
    """True for IPv4/IPv6 address or prefix; rejects times like 09:50:02."""
    s = str(tok or "").strip()
    if not s or s.lower() in _HEADER_NETS:
        return False
    try:
        if "/" in s:
            ipaddress.ip_network(s, strict=False)
        else:
            ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _looks_like_prefix(net: str) -> bool:
    return _looks_like_ip_or_prefix(net)


def _split_rest(rest: str, *, path_continuation: bool = False) -> tuple[str, str, str, str]:
    """Parse trailing Metric LocPrf Tag/RtPrf Path columns (some may be blank).

    ``path_continuation``: indented wrap line after next-hop (often ``20 65254 ?``
    or ``4761 ?``) — prefer path/tag over inventing a metric.
    """
    parts = str(rest or "").split()
    if not parts:
        return "", "", "", ""
    if path_continuation and parts[-1] in ("?", "i", "e", "incomplete"):
        if len(parts) == 1:
            return "", "", "", parts[0]
        if len(parts) == 2 and parts[0].isdigit():
            # ``4761 ?`` → path
            return "", "", "", " ".join(parts)
        if len(parts) >= 3 and parts[0].isdigit():
            # ``20 65254 ?`` → rtprf + path
            return "", "", parts[0], " ".join(parts[1:])
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


def _skip_noise_line(line: str) -> bool:
    low = line.strip().lower()
    if not low:
        return True
    if low.startswith(("network", "dest ", "destination")):
        return True
    if "next hop" in low or low.startswith("status") or low.startswith("origin"):
        return True
    if low.startswith("routes ") or low.startswith("current as"):
        return True
    if low.startswith("local ") or low.startswith("remote ") or low.startswith("total "):
        return True
    if low.startswith("route distinguisher") or low.startswith("valid ") or low.startswith(
        "invalid "
    ):
        return True
    # Banner / clock lines (e.g. "09:50:02 Indonesia Sat Sep 19 2026")
    if re.match(r"^\d{1,2}:\d{2}:\d{2}\b", low):
        return True
    if low.endswith("#") or "#'" in low:
        return True
    if re.search(r"\S+\s*#\s*$", line):
        return True
    return False


def _emit_route(
    out: list[dict[str, Any]],
    seen: set[str],
    *,
    local_as: str,
    afi: str,
    vrf: str,
    neighbor: str,
    direction: str,
    net: str,
    nh: str,
    rest: str,
    flags: str,
    path_continuation: bool = False,
) -> None:
    if not _looks_like_prefix(net) or net in seen:
        return
    if nh and not _looks_like_ip_or_prefix(nh):
        # Path/metric-only continuation without a real next-hop — keep empty nh
        if re.search(r"[A-Za-z]", nh):
            return
    seen.add(net)
    metric, loc, tag, path = _split_rest(rest, path_continuation=path_continuation)
    out.append(
        {
            "local_as": local_as[:16],
            "afi": afi[:32],
            "vrf": vrf[:128],
            "neighbor": neighbor[:128],
            "direction": direction[:8],
            "network": net[:128],
            "next_hop": (nh or "")[:128],
            "metric": metric[:32],
            "loc_prf": loc[:32],
            "tag": tag[:32],
            "path": path[:256],
            "status_codes": (flags or "").strip()[:16],
            "as_num": "",
            "state": "",
            "pfx_rcd": "",
        }
    )


def _map_fsm_rows(
    rows: list[dict[str, Any]],
    *,
    local_as: str,
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
        if nh and not _looks_like_ip_or_prefix(nh):
            # Reject FSM false hits like NETWORK=20 NEXT_HOP=65254
            continue
        seen.add(net)
        path = row_get(r, "PATH", "path")
        out.append(
            {
                "local_as": local_as[:16],
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
    local_as: str = "",
    afi: str = "unknown",
    vrf: str = "",
    neighbor: str = "",
    direction: str = "",
    **_kw: Any,
) -> list[dict[str, Any]]:
    """Parse neighbor in/out tables; join IPv6 network / next-hop / path wraps."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending_net = ""
    pending_flags = ""
    pending_nh = ""

    def _flush_pending(*, rest: str = "", path_continuation: bool = False) -> None:
        nonlocal pending_net, pending_flags, pending_nh
        if not pending_net:
            return
        _emit_route(
            out,
            seen,
            local_as=local_as,
            afi=afi,
            vrf=vrf,
            neighbor=neighbor,
            direction=direction,
            net=pending_net,
            nh=pending_nh,
            rest=rest,
            flags=pending_flags,
            path_continuation=path_continuation,
        )
        pending_net = ""
        pending_flags = ""
        pending_nh = ""

    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if _skip_noise_line(line):
            continue

        # Continuation: indented next-hop after network-only line
        if pending_net and not pending_nh and line[:1].isspace():
            tok = line.strip()
            if _looks_like_ip_or_prefix(tok) and "/" not in tok:
                pending_nh = tok
                continue
            # Metrics/path without explicit next-hop (rare)
            if tok and not _looks_like_prefix(tok.split()[0] if tok.split() else ""):
                _flush_pending(rest=tok, path_continuation=True)
                continue

        # Continuation: indented path/metric after network+nh
        if pending_net and pending_nh and line[:1].isspace():
            tok = line.strip()
            if tok:
                _flush_pending(rest=tok, path_continuation=True)
                continue

        # Full one-liner (typical IPv4)
        m = _ROUTE_ONE_LINE_RE.match(line)
        if m and _looks_like_prefix(m.group("net")) and _looks_like_ip_or_prefix(m.group("nh")):
            _flush_pending()
            _emit_route(
                out,
                seen,
                local_as=local_as,
                afi=afi,
                vrf=vrf,
                neighbor=neighbor,
                direction=direction,
                net=m.group("net"),
                nh=m.group("nh"),
                rest=m.group("rest"),
                flags=m.group("flags") or "",
            )
            continue

        # Network alone → wait for next-hop / path wraps (IPv6)
        m_net = _NET_ONLY_RE.match(line)
        if m_net and _looks_like_prefix(m_net.group("net")):
            _flush_pending()
            pending_net = m_net.group("net")
            pending_flags = m_net.group("flags") or ""
            pending_nh = ""
            continue

    _flush_pending()
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
    local_as = _detect_local_as(command, params)
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
            rows,
            local_as=local_as,
            afi=afi,
            vrf=vrf,
            neighbor=neighbor,
            direction=direction,
        )

    def _hand(*, raw_text: str, **kw: Any) -> list[dict[str, Any]]:
        return _hand_parse(
            raw_text=raw_text,
            local_as=local_as,
            afi=afi,
            vrf=vrf,
            neighbor=neighbor,
            direction=direction,
            **kw,
        )

    return prefer_fsm(tables, RULE_KEYS, _map, _hand, raw_text=raw_text)


normalize_bgp_route.RULE_KEYS = RULE_KEYS
