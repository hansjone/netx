"""ZTE ZXROS status table parsers (ISIS / interface / ARP / ND6 / BGP)."""

from __future__ import annotations

import re
from typing import Any

from ...lldp_shared import resolve_vendor_key
from ...ntc_parse import parse_cli, resolve_cli_platform, row_get

_IFACE_RE = re.compile(
    r"^(?P<iface>\S+)\s+(?P<attr>\S+)\s+(?P<mode>\S+)"
    r"(?:\s+(?P<bw>\S+))?\s+(?P<admin>up|down)\s+(?P<phy>up|down)\s+(?P<prot>up|down)"
    r"(?:\s+(?P<desc>.*))?$",
    re.I,
)

_ISIS_ROW_RE = re.compile(
    r"^(?P<iface>\S+)\s+(?P<sys>\S+)\s+(?P<state>\S+)\s+(?P<lev>\S+)\s+"
    r"(?P<holds>\S+)\s+(?P<snpa>\S+)\s+(?P<pri>\S+)\s+(?P<mt>\S+)\s+"
    r"(?P<nsf>\S+)\s+(?P<af>\S+)\s*$",
    re.I,
)

_ARP_ROW_RE = re.compile(
    r"^(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<age>\S+)\s+(?P<mac>\S+)\s+"
    r"(?P<iface>\S+)\s+(?P<ext_vlan>\S+)\s+(?P<int_vlan>\S+)\s+(?P<sub>\S+)\s*$",
    re.I,
)

_ND6_ROW_RE = re.compile(
    r"^(?P<addr>\S+)\s+(?P<link>\S+)\s+(?P<age>\S+)\s+(?P<status>\S+)\s+"
    r"(?P<iface>\S+)\s+(?P<type>\S+)\s*$",
    re.I,
)

_BGP_PEER_RE = re.compile(
    r"^(?P<nei>\d{1,3}(?:\.\d{1,3}){3})\s+(?P<ver>\d+)\s+(?P<asn>\d+)\s+"
    r"(?P<rx>\d+)\s+(?P<tx>\d+)\s+(?P<up>\S+)\s+(?P<state>\S+)\s*$",
    re.I,
)

_PROCESS_RE = re.compile(r"(?i)^\s*Process\s+ID\s*:\s*(\d+)\s*$")
_HEADER_HINTS = (
    "interface",
    "system id",
    "address",
    "neighbor",
    "ip",
    "hardware",
    "link-address",
)


def _is_header(line: str) -> bool:
    low = line.lower()
    return any(h in low for h in _HEADER_HINTS) and ("---" in low or "  " in line)


def normalize_isis_adjacency(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, command, params)
    out: list[dict[str, Any]] = []
    process_id = ""
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        pm = _PROCESS_RE.match(line)
        if pm:
            process_id = pm.group(1)
            continue
        if line.lower().startswith("interface") and "system" in line.lower():
            continue
        m = _ISIS_ROW_RE.match(line)
        if not m:
            continue
        out.append(
            {
                "process_id": process_id,
                "interface": m.group("iface")[:128],
                "system_id": m.group("sys")[:128],
                "state": m.group("state")[:32],
                "lev": m.group("lev")[:16],
                "holds": m.group("holds")[:32],
                "snpa": m.group("snpa")[:64],
                "pri": m.group("pri")[:16],
                "mt": m.group("mt")[:16],
                "nsf": m.group("nsf")[:32],
                "af": m.group("af")[:64],
            }
        )
    return out


def normalize_interface_brief(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = params
    platform = resolve_cli_platform(
        vendor=vendor,
        device_type=device_type,
        vendor_key=resolve_vendor_key(vendor, device_type),
    )
    cmd = str(command or "show interface brief").strip() or "show interface brief"
    rows = parse_cli(platform=platform, command=cmd, text=raw_text) if platform else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        iface = row_get(r, "INTERFACE", "interface")
        if not iface or iface.lower() == "interface":
            continue
        if iface in seen:
            continue
        seen.add(iface)
        out.append(
            {
                "interface": iface[:128],
                "attribute": row_get(r, "ATTRIBUTE", "attribute")[:64],
                "mode": row_get(r, "MODE", "mode")[:64],
                "bw": row_get(r, "BW", "bw")[:32],
                "admin": row_get(r, "ADMIN", "admin")[:16],
                "phy": row_get(r, "PHY", "phy")[:16],
                "prot": row_get(r, "PROT", "prot")[:16],
                "description": row_get(r, "DESCRIPTION", "description")[:256],
            }
        )
    if out:
        return out
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("interface"):
            continue
        m = _IFACE_RE.match(line)
        if not m:
            continue
        iface = m.group("iface")
        if iface in seen:
            continue
        seen.add(iface)
        out.append(
            {
                "interface": iface[:128],
                "attribute": (m.group("attr") or "")[:64],
                "mode": (m.group("mode") or "")[:64],
                "bw": (m.group("bw") or "")[:32],
                "admin": (m.group("admin") or "")[:16],
                "phy": (m.group("phy") or "")[:16],
                "prot": (m.group("prot") or "")[:16],
                "description": (m.group("desc") or "").strip()[:256],
            }
        )
    return out


_ARP_AGE_TIME_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")


def is_valid_arp_age(age: str) -> bool:
    """True when Age looks like a dynamic timer (HH:MM:SS), not static flags like H."""
    return bool(_ARP_AGE_TIME_RE.match(str(age or "").strip()))


def normalize_arp(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, command, params)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    ip_re = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("---") or line.lower().startswith("arp protect"):
            continue
        if line.lower().startswith("the count"):
            continue
        if "hardware" in line.lower() and "address" in line.lower():
            continue
        parts = line.split()
        if len(parts) < 4 or not ip_re.match(parts[0]):
            continue
        ip = parts[0]
        age = parts[1]
        mac = parts[2]
        iface = parts[3]
        exter = parts[4] if len(parts) > 4 else ""
        inter = parts[5] if len(parts) > 5 else ""
        sub = parts[6] if len(parts) > 6 else ""
        key = (ip, iface)
        if key in seen:
            continue
        seen.add(key)
        dynamic = is_valid_arp_age(age)
        out.append(
            {
                "ip": ip[:64],
                "age": age[:32],
                "mac": mac[:64],
                "interface": iface[:128],
                "exter_vlan": exter[:32],
                "inter_vlan": inter[:32],
                "sub_interface": sub[:128],
                "entry_type": "dynamic" if dynamic else "static",
            }
        )
    return out


def normalize_nd6_cache(
    *,
    raw_text: str,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type, command, params)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("s-static") or low.startswith("total cache") or low.startswith("only current"):
            continue
        if low.startswith("address") and "link" in low:
            continue
        m = _ND6_ROW_RE.match(line)
        if not m:
            continue
        addr = m.group("addr")
        iface = m.group("iface")
        key = (addr, iface)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "address": addr[:128],
                "link_address": m.group("link")[:64],
                "age": m.group("age")[:64],
                "status": m.group("status")[:32],
                "interface": iface[:128],
                "type": m.group("type")[:32],
            }
        )
    return out


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
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (vendor, device_type)
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
                "state_or_pfx": state_raw[:64],
            }
        )
    return out
