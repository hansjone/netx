"""ZTE config intent: static routes from ``!<static>`` / ``!<ipv6-static-route>``.

Commands:
  ``show running-config static``
  ``show running-config ipv6-static-route``
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_V4_HEAD = re.compile(
    r"(?i)^\s*ip\s+route(?:\s+vrf\s+(?P<vrf>\S+))?\s+"
    r"(?P<prefix>\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"(?P<mask>\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"(?P<rest>.*)$"
)
_V6_HEAD = re.compile(
    r"(?i)^\s*ipv6\s+route(?:\s+vrf\s+(?P<vrf>\S+))?\s+"
    r"(?P<prefix>\S+)\s+"
    r"(?P<rest>.*)$"
)

_IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
# Broad ZTE if-name match: whitelist prefixes + generic if-like tokens.
_IFACE_RE = re.compile(
    r"(?i)^(?:"
    r"null\d*"
    r"|(?:xxv|xl|x|c|v6|q|fe)?gei-\S+"
    r"|bvi\S*|vlan\S*|smartgroup\S*|loopback\S*|mgmt_?\S*"
    r"|pos\S*|(?:fast)?eth\S*"
    r"|(?:te_|gre_|ip_|v6)?tunnel\S*"
    r"|irb\S*|ve\S*|dialer\S*|serial\S*|ppp\S*|bundle\S*"
    r"|fei-\S*|qi-\S*"
    r"|[a-z][a-z0-9]*[-_][a-z0-9_./:-]+"  # e.g. te_tunnel36, gre_tunnel1
    r")$"
)
_KW = frozenset(
    {
        "name",
        "track",
        "tag",
        "metric",
        "distance",
        "preference",
        "nexthop-vrf",
        "bfd",
        "enable",
        "disable",
        "permanent",
    }
)


def _is_ipv4(tok: str) -> bool:
    return bool(_IPV4_RE.match(tok or ""))


def _is_ipv6_tok(tok: str) -> bool:
    t = str(tok or "")
    return ":" in t and not _is_iface(t)


def _is_iface(tok: str) -> bool:
    t = str(tok or "").strip()
    if not t or t.lower() in _KW:
        return False
    if _is_ipv4(t):
        return False
    # IPv6 next-hop (has colon) is not an interface name.
    if ":" in t and re.match(r"(?i)^[0-9a-f:]+(/\d+)?$", t):
        return False
    return bool(_IFACE_RE.match(t))


def _take_kw(tokens: list[str], i: int, row: dict[str, str]) -> int:
    """Consume optional keyword/value pairs starting at ``i``; return new index."""
    n = len(tokens)
    while i < n:
        low = tokens[i].lower()
        if low in ("bfd",):
            # bfd [enable|disable]
            row["bfd"] = "enable"
            i += 1
            if i < n and tokens[i].lower() in ("enable", "disable"):
                row["bfd"] = tokens[i].lower()
                i += 1
            continue
        if low in ("enable", "disable", "permanent"):
            if low == "permanent":
                row["permanent"] = "yes"
            i += 1
            continue
        if low in ("name", "track", "tag", "metric", "distance", "preference", "nexthop-vrf"):
            if i + 1 >= n:
                break
            val = tokens[i + 1]
            if low == "nexthop-vrf":
                row["nexthop_vrf"] = val
            elif low == "name":
                row["route_name"] = val
            else:
                row[low] = val
            i += 2
            continue
        break
    return i


def _parse_v4_rest(rest: str) -> dict[str, str]:
    tokens = (rest or "").split()
    row: dict[str, str] = {
        "next_hop": "",
        "interface": "",
        "nexthop_vrf": "",
        "metric": "",
        "track": "",
        "tag": "",
        "route_name": "",
        "bfd": "",
        "distance": "",
    }
    if not tokens:
        return row
    i = 0
    # nexthop-vrf without explicit IP
    if tokens[0].lower() == "nexthop-vrf" and len(tokens) >= 2:
        row["nexthop_vrf"] = tokens[1]
        _take_kw(tokens, 2, row)
        return row
    if _is_iface(tokens[0]):
        # interface [next-hop]
        row["interface"] = tokens[0]
        i = 1
        if i < len(tokens) and _is_ipv4(tokens[i]):
            row["next_hop"] = tokens[i]
            i += 1
    elif _is_ipv4(tokens[0]):
        # next-hop [interface]
        row["next_hop"] = tokens[0]
        i = 1
        if i < len(tokens) and _is_iface(tokens[i]):
            row["interface"] = tokens[i]
            i += 1
    _take_kw(tokens, i, row)
    return row


def _parse_v6_rest(rest: str) -> dict[str, str]:
    tokens = (rest or "").split()
    row: dict[str, str] = {
        "next_hop": "",
        "interface": "",
        "nexthop_vrf": "",
        "metric": "",
        "track": "",
        "tag": "",
        "route_name": "",
        "bfd": "",
        "distance": "",
    }
    if not tokens:
        return row
    i = 0
    if tokens[0].lower() == "nexthop-vrf" and len(tokens) >= 2:
        row["nexthop_vrf"] = tokens[1]
        _take_kw(tokens, 2, row)
        return row
    if _is_iface(tokens[0]):
        row["interface"] = tokens[0]
        i = 1
        if i < len(tokens) and _is_ipv6_tok(tokens[i]):
            row["next_hop"] = tokens[i]
            i += 1
    elif _is_ipv6_tok(tokens[0]):
        row["next_hop"] = tokens[0]
        i = 1
        if i < len(tokens) and _is_iface(tokens[i]):
            row["interface"] = tokens[i]
            i += 1
    _take_kw(tokens, i, row)
    return row


def _row(
    *,
    af: str,
    vrf: str,
    prefix: str,
    mask: str,
    extra: dict[str, str],
) -> dict[str, Any]:
    return {
        "af": af[:8],
        "vrf": (vrf or "")[:128],
        "prefix": prefix[:128],
        "mask": (mask or "")[:64],
        "next_hop": (extra.get("next_hop") or "")[:64],
        "interface": (extra.get("interface") or "")[:128],
        "nexthop_vrf": (extra.get("nexthop_vrf") or "")[:128],
        "metric": (extra.get("metric") or "")[:16],
        "bfd": (extra.get("bfd") or "")[:16],
        "track": (extra.get("track") or "")[:64],
        "route_name": (extra.get("route_name") or "")[:128],
        "tag": (extra.get("tag") or "")[:32],
        "distance": (extra.get("distance") or extra.get("preference") or "")[:16],
    }


def _parse_v4(body: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = _V4_HEAD.match(line)
        if not m:
            continue
        extra = _parse_v4_rest(m.group("rest") or "")
        row = _row(
            af="ipv4",
            vrf=(m.group("vrf") or "").strip(),
            prefix=m.group("prefix").strip(),
            mask=m.group("mask").strip(),
            extra=extra,
        )
        key = (
            row["vrf"],
            row["prefix"],
            row["mask"],
            row["next_hop"],
            row["interface"],
            row["nexthop_vrf"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _parse_v6(body: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = _V6_HEAD.match(line)
        if not m:
            continue
        extra = _parse_v6_rest(m.group("rest") or "")
        row = _row(
            af="ipv6",
            vrf=(m.group("vrf") or "").strip(),
            prefix=m.group("prefix").strip(),
            mask="",
            extra=extra,
        )
        key = (
            row["vrf"],
            row["prefix"],
            row["next_hop"],
            row["interface"],
            row["nexthop_vrf"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _detect_af(command: str, raw_text: str) -> str:
    cmd = str(command or "").lower()
    if "ipv6-static" in cmd or "ipv6 static" in cmd:
        return "ipv6"
    if re.search(r"(?i)^\s*show\s+running-config\s+static\b", cmd):
        return "ipv4"
    # Full dump / ambiguous: prefer section presence
    text = str(raw_text or "")
    if re.search(r"(?i)^!<ipv6-static-route>\s*$", text, re.M) and not re.search(
        r"(?i)^!<static>\s*$", text, re.M
    ):
        return "ipv6"
    return "ipv4"


def normalize_config_static_route(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (fsm_tables, vendor, device_type, params)
    af = _detect_af(command, raw_text)
    if af == "ipv6":
        body = extract_mim_section(raw_text, "ipv6-static-route")
        return _parse_v6(body)
    body = extract_mim_section(raw_text, "static")
    return _parse_v4(body)


normalize_config_static_route.RULE_KEYS = RULE_KEYS
