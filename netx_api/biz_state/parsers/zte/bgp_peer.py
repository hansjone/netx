"""ZTE: show bgp {vpnv4|ipv4|vpnv6} unicast summary."""

from __future__ import annotations

import re
from typing import Any

_BGP_PEER_RE = re.compile(
    r"^(?P<nei>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<ver>\d+)\s+(?P<asn>\d+)\s+"
    r"(?P<rx>\d+)\s+(?P<tx>\d+)\s+(?P<up>\S+)\s+(?P<state>\S+)\s*$",
    re.I,
)

RULE_KEYS: tuple[str, ...] = ()


def _detect_bgp_afi(command: str, params: dict[str, str] | None) -> str:
    if params and params.get("afi"):
        return str(params.get("afi") or "").strip().lower()
    low = str(command or "").lower()
    if "vpnv6" in low:
        return "vpnv6"
    if "vpnv4" in low:
        return "vpnv4"
    if "ipv6" in low:
        return "ipv6"
    if "ipv4" in low:
        return "ipv4"
    return "unknown"


def normalize_bgp_peer(
    *,
    raw_text: str,
    fsm_tables=None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, fsm_tables)
    afi = _detect_bgp_afi(command, params)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("bgp router") or low.startswith("local as") or low.startswith("all "):
            continue
        if low.startswith("neighbor") and "msg" in low:
            continue
        m = _BGP_PEER_RE.match(line)
        if not m:
            continue
        nei = m.group("nei")
        if nei in seen:
            continue
        seen.add(nei)
        state_raw = m.group("state")
        if state_raw.isdigit():
            state = "Established"
            pfx = state_raw
        else:
            state = state_raw
            pfx = ""
        out.append(
            {
                "afi": afi[:32],
                "neighbor": nei[:64],
                "ver": m.group("ver")[:8],
                "as_num": m.group("asn")[:16],
                "msg_rcvd": m.group("rx")[:32],
                "msg_send": m.group("tx")[:32],
                "up_down": m.group("up")[:32],
                "state": state[:64],
                "pfx_rcd": pfx[:32],
            }
        )
    return out

normalize_bgp_peer.RULE_KEYS = RULE_KEYS
