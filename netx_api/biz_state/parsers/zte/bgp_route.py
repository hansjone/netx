"""ZTE: show bgp … neighbor {in|out} [vrf] routes.

Hand-only parser. IN/OUT, status vs plain, From column, RD+VRF annotations and
heavy IPv6 wraps diverge too much for one TextFSM; dual-path policy is hand-first
(``RULE_KEYS=()`` skips FSM in ``run_parser``).
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from .bgp_peer import _detect_bgp_afi, _detect_local_as, _detect_vrf

# Declared empty → run_parser will not apply TextFSM for this metric.
RULE_KEYS: tuple[str, ...] = ()

# Status must stay glued: "* i" / "*i" / "*>i" — never leave "i" as NETWORK.
_STATUS_LEAD_RE = re.compile(
    r"^\s*(?P<flags>\*(?:\s*[<>isd]+)?|[<>isd]+)\s+"
)
# After optional status: network + next-hop + remainder
_NET_NH_RE = re.compile(
    r"^\s*(?P<net>\S+)\s+(?P<nh>\S+)(?:\s+(?P<rest>.*))?$"
)
_NET_ONLY_RE = re.compile(r"^\s*(?P<net>\S+)\s*$")
_DIR_RE = re.compile(r"(?i)\bneighbor\s+(in|out)\s+")
_NEI_RE = re.compile(r"(?i)\bneighbor\s+(?:in|out)\s+(\S+)")
_TOTAL_RE = re.compile(r"(?i)total\s+number\s+of\s+routes\s*:\s*(\d+)")
# Route Distinguisher:2:111 (default for vrf css-srv6-mpls-1)
_RD_RE = re.compile(
    r"(?i)^Route\s+Distinguisher\s*:\s*(?P<rd>\S+)"
    r"(?:\s*\(\s*default\s+for\s+vrf\s+(?P<vrf>\S+)\s*\))?"
)
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
_PATH_ENDS = frozenset({"?", "i", "e", "incomplete"})


def _normalize_cli_text(raw_text: str) -> str:
    """NBSP / odd spaces from paste or terminals → regular space."""
    return str(raw_text or "").replace("\u00a0", " ").replace("\u2007", " ")


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
    return _looks_like_ip_or_prefix(net) and "/" in str(net or "")


def _declared_total(raw_text: str) -> int | None:
    m = _TOTAL_RE.search(_normalize_cli_text(raw_text))
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _route_dedupe_key(*, rd: str, net: str, nh: str) -> str:
    """ECMP / multi-RD: same prefix with different RD or next-hop is distinct."""
    return f"{rd}\0{net}\0{nh}"


def _split_rest(rest: str, *, path_continuation: bool = False) -> tuple[str, str, str, str]:
    """Parse trailing [From] Metric LocPrf Tag/RtPrf Path columns.

    OUT tables include a From column (IP) between Next Hop and Metric.
    ``path_continuation``: indented wrap after network/nh (often ``0 100 283610 ?``).
    """
    parts = [p for p in str(rest or "").split() if p]
    if not parts:
        return "", "", "", ""

    # Drop leading From IP when present (OUT neighbor tables).
    if _looks_like_ip_or_prefix(parts[0]) and "/" not in parts[0]:
        parts = parts[1:]
        if not parts:
            return "", "", "", ""

    if path_continuation and parts[-1] in _PATH_ENDS:
        if len(parts) == 1:
            return "", "", "", parts[0]
        if len(parts) == 2 and parts[0].isdigit():
            # ``100 ?`` → loc_prf + origin; ``4761 ?`` → AS-path + origin
            n = int(parts[0])
            if n <= 255:
                return "", parts[0], "", parts[1]
            return "", "", "", " ".join(parts)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            # ``0 100 ?`` → metric + loc + path
            return parts[0], parts[1], "", parts[2]
        if len(parts) >= 4 and all(parts[i].isdigit() for i in range(3)):
            # ``0 100 283610 ?`` → metric loc tag path
            return parts[0], parts[1], parts[2], " ".join(parts[3:])
        if len(parts) >= 3 and parts[0].isdigit():
            return "", "", parts[0], " ".join(parts[1:])

    metric = loc = tag = ""
    nums: list[str] = []
    path_parts: list[str] = []
    for p in parts:
        if not path_parts and re.fullmatch(r"\d+", p):
            nums.append(p)
        else:
            path_parts.append(p)
    # One number before origin → LocPrf (OUT often omits Metric).
    if len(nums) == 1 and path_parts:
        loc = nums[0]
    elif len(nums) >= 1:
        metric = nums[0]
        if len(nums) >= 2:
            loc = nums[1]
        if len(nums) >= 3:
            tag = nums[2]
    path = " ".join(path_parts)
    return metric, loc, tag, path


def _rest_looks_complete(rest: str) -> bool:
    """True when remainder has a path origin — safe to emit without waiting for wrap."""
    parts = str(rest or "").split()
    if not parts:
        return False
    if parts[-1] in _PATH_ENDS:
        return True
    # From-only (single IP) → wait for metric/path wrap
    if len(parts) == 1 and _looks_like_ip_or_prefix(parts[0]) and "/" not in parts[0]:
        return False
    return len(parts) >= 2


def _empty_if_total_zero(raw_text: str) -> bool:
    total = _declared_total(raw_text)
    return total == 0


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
    if low.startswith("valid ") or low.startswith("invalid "):
        return True
    if re.match(r"^\d{1,2}:\d{2}:\d{2}\b", low):
        return True
    if low.endswith("#") or "#'" in low:
        return True
    if re.search(r"\S+\s*#\s*$", line):
        return True
    if low.startswith("show "):
        return True
    return False


def _emit_route(
    seen: set[str],
    *,
    local_as: str,
    afi: str,
    vrf: str,
    neighbor: str,
    direction: str,
    rd: str,
    net: str,
    nh: str,
    rest: str,
    flags: str,
    path_continuation: bool = False,
) -> dict[str, Any] | None:
    if not _looks_like_prefix(net):
        return None
    if nh and not _looks_like_ip_or_prefix(nh):
        if re.search(r"[A-Za-z]", nh):
            return None
        nh = ""
    key = _route_dedupe_key(rd=rd, net=net, nh=nh or "")
    if key in seen:
        return None
    seen.add(key)
    metric, loc, tag, path = _split_rest(rest, path_continuation=path_continuation)
    return {
        "local_as": local_as[:16],
        "afi": afi[:32],
        "vrf": vrf[:128],
        "neighbor": neighbor[:128],
        "direction": direction[:8],
        "rd": (rd or "")[:64],
        "network": net[:128],
        "next_hop": (nh or "")[:128],
        "metric": metric[:32],
        "loc_prf": loc[:32],
        "tag": tag[:32],
        "path": path[:256],
        "status_codes": re.sub(r"\s+", "", (flags or "").strip())[:16],
        "as_num": "",
        "state": "",
        "pfx_rcd": "",
    }


def _hand_parse(
    *,
    raw_text: str,
    local_as: str = "",
    afi: str = "unknown",
    vrf: str = "",
    neighbor: str = "",
    direction: str = "",
    **_kw: Any,
):
    """Yield neighbor in/out route rows (streaming; joins heavy IPv6 / From wraps)."""
    seen: set[str] = set()
    pending_net = ""
    pending_flags = ""
    pending_nh = ""
    pending_rest = ""
    current_rd = ""
    current_vrf = vrf

    def _flush_pending(*, rest: str = "", path_continuation: bool = False) -> dict[str, Any] | None:
        nonlocal pending_net, pending_flags, pending_nh, pending_rest
        if not pending_net:
            return None
        merged = " ".join(x for x in (pending_rest, rest) if x).strip()
        row = _emit_route(
            seen,
            local_as=local_as,
            afi=afi,
            vrf=current_vrf,
            neighbor=neighbor,
            direction=direction,
            rd=current_rd,
            net=pending_net,
            nh=pending_nh,
            rest=merged,
            flags=pending_flags,
            path_continuation=path_continuation,
        )
        pending_net = ""
        pending_flags = ""
        pending_nh = ""
        pending_rest = ""
        return row

    def _start_pending(*, net: str, nh: str, flags: str, rest: str):
        nonlocal pending_net, pending_flags, pending_nh, pending_rest
        flushed = _flush_pending()
        pending_net = net
        pending_flags = flags
        pending_nh = nh
        pending_rest = rest
        return flushed

    for raw in _normalize_cli_text(raw_text).splitlines():
        line = raw.rstrip()
        rd_m = _RD_RE.match(line.strip())
        if rd_m:
            row = _flush_pending()
            if row:
                yield row
            current_rd = (rd_m.group("rd") or "").strip()
            vrf_from_rd = (rd_m.group("vrf") or "").strip()
            if vrf_from_rd:
                current_vrf = vrf_from_rd
            continue
        if _skip_noise_line(line):
            continue

        # Indented continuation while a route is open
        if pending_net and line[:1].isspace():
            tok = line.strip()
            if not tok:
                continue
            parts = tok.split()
            first = parts[0] if parts else ""
            if not pending_nh:
                if _looks_like_ip_or_prefix(first) and "/" not in first:
                    pending_nh = first
                    more = " ".join(parts[1:])
                    if more and _rest_looks_complete(more):
                        row = _flush_pending(rest=more, path_continuation=True)
                        if row:
                            yield row
                    elif more:
                        pending_rest = " ".join(x for x in (pending_rest, more) if x)
                    continue
                if not _looks_like_prefix(first):
                    row = _flush_pending(rest=tok, path_continuation=True)
                    if row:
                        yield row
                    continue
            else:
                row = _flush_pending(rest=tok, path_continuation=True)
                if row:
                    yield row
                continue

        # Peel optional status codes (* i / *i / > …)
        flags = ""
        body = line
        st = _STATUS_LEAD_RE.match(line)
        if st:
            flags = (st.group("flags") or "").strip()
            body = line[st.end() :]

        # Full / partial: network + next-hop (+ rest)
        m = _NET_NH_RE.match(body)
        if m and _looks_like_prefix(m.group("net")) and _looks_like_ip_or_prefix(m.group("nh")):
            rest = (m.group("rest") or "").strip()
            if _rest_looks_complete(rest):
                row = _flush_pending()
                if row:
                    yield row
                row = _emit_route(
                    seen,
                    local_as=local_as,
                    afi=afi,
                    vrf=current_vrf,
                    neighbor=neighbor,
                    direction=direction,
                    rd=current_rd,
                    net=m.group("net"),
                    nh=m.group("nh"),
                    rest=rest,
                    flags=flags,
                )
                if row:
                    yield row
            else:
                row = _start_pending(
                    net=m.group("net"), nh=m.group("nh"), flags=flags, rest=rest
                )
                if row:
                    yield row
            continue

        # Network alone → wait for next-hop wrap
        m_net = _NET_ONLY_RE.match(body)
        if m_net and _looks_like_prefix(m_net.group("net")):
            row = _start_pending(net=m_net.group("net"), nh="", flags=flags, rest="")
            if row:
                yield row
            continue

    row = _flush_pending()
    if row:
        yield row


def normalize_bgp_route(
    *,
    raw_text: str,
    command: str = "",
    params: dict[str, str] | None = None,
    **_kw: Any,
):
    """Hand-only normalize; returns [] or a streaming iterator of route dicts."""
    raw_text = _normalize_cli_text(raw_text)
    if _empty_if_total_zero(raw_text):
        return []
    return _hand_parse(
        raw_text=raw_text,
        local_as=_detect_local_as(command, params),
        afi=_detect_bgp_afi(command, params),
        vrf=_detect_vrf(command, params),
        neighbor=_detect_neighbor(command, params),
        direction=_detect_direction(command, params),
    )


normalize_bgp_route.RULE_KEYS = RULE_KEYS
