"""ZTE config intent: ``show running-config if-intf`` / MIM ``!<if-intf>``.

Duplicate ``interface`` blocks (common for tunnels) are merged: later values
override empty ones; ``admin`` always takes the last explicit setting.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_IFACE_RE = re.compile(r"^\s*interface\s+(\S+)\s*$", re.I)
_VRF_RE = re.compile(r"^\s*ip\s+vrf\s+forwarding\s+(\S+)\s*$", re.I)
_IP_RE = re.compile(r"^\s*ip\s+address\s+(\S+)(?:\s+(\S+))?\s*$", re.I)
_IP6_RE = re.compile(r"^\s*ipv6\s+address\s+(\S+)\s*$", re.I)
_DESC_RE = re.compile(r"^\s*description\s+(.*?)\s*$", re.I)
_MTU_RE = re.compile(r"^\s*mtu\s+(\d+)\s*$", re.I)


def _row_from_cur(cur: dict[str, Any]) -> dict[str, Any]:
    ips = cur.get("_ips") or []
    ip6s = cur.get("_ip6s") or []
    return {
        "interface": str(cur.get("interface") or "")[:128],
        "vrf": str(cur.get("vrf") or "")[:128],
        "description": str(cur.get("description") or "")[:256],
        "admin": str(cur.get("admin") or "up")[:16],
        "ip_address": ",".join(ips)[:256],
        "ipv6_address": ",".join(ip6s)[:256],
        "mtu": str(cur.get("mtu") or "")[:16],
        "_admin_set": bool(cur.get("_admin_set")),
    }


def _flush(
    cur: dict[str, Any] | None,
    out: list[dict[str, Any]],
    by_iface: dict[str, int],
) -> None:
    if not cur:
        return
    iface = str(cur.get("interface") or "")
    if not iface:
        return
    row = _row_from_cur(cur)
    admin_set = bool(row.pop("_admin_set", False))
    if iface in by_iface:
        prev = out[by_iface[iface]]
        # Only override admin when this block explicitly set shutdown / no shutdown.
        if admin_set:
            prev["admin"] = row["admin"]
        for key in ("vrf", "description", "mtu"):
            if row[key]:
                prev[key] = row[key]
        if row["ip_address"]:
            existing = [x for x in (prev["ip_address"] or "").split(",") if x]
            for ip in row["ip_address"].split(","):
                if ip and ip not in existing:
                    existing.append(ip)
            prev["ip_address"] = ",".join(existing)[:256]
        if row["ipv6_address"]:
            existing = [x for x in (prev["ipv6_address"] or "").split(",") if x]
            for ip in row["ipv6_address"].split(","):
                if ip and ip not in existing:
                    existing.append(ip)
            prev["ipv6_address"] = ",".join(existing)[:256]
        return
    by_iface[iface] = len(out)
    out.append(row)


def normalize_config_interface(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (fsm_tables, vendor, device_type, command, params)
    body = extract_mim_section(raw_text, "if-intf")
    out: list[dict[str, Any]] = []
    by_iface: dict[str, int] = {}
    cur: dict[str, Any] | None = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = _IFACE_RE.match(line)
        if m:
            _flush(cur, out, by_iface)
            cur = {
                "interface": m.group(1).strip(),
                "vrf": "",
                "description": "",
                "admin": "up",
                "_admin_set": False,
                "mtu": "",
                "_ips": [],
                "_ip6s": [],
            }
            continue
        if not cur:
            continue
        if re.match(r"^\$\s*$", line):
            _flush(cur, out, by_iface)
            cur = None
            continue
        low = line.strip().lower()
        if low == "shutdown":
            cur["admin"] = "down"
            cur["_admin_set"] = True
            continue
        if low == "no shutdown":
            cur["admin"] = "up"
            cur["_admin_set"] = True
            continue
        m = _VRF_RE.match(line)
        if m:
            cur["vrf"] = m.group(1).strip()
            continue
        m = _DESC_RE.match(line)
        if m:
            cur["description"] = (m.group(1) or "").strip().strip('"')[:256]
            continue
        m = _IP_RE.match(line)
        if m:
            addr = m.group(1).strip()
            mask = (m.group(2) or "").strip()
            cur["_ips"].append(f"{addr}/{mask}" if mask else addr)
            continue
        m = _IP6_RE.match(line)
        if m:
            cur["_ip6s"].append(m.group(1).strip())
            continue
        m = _MTU_RE.match(line)
        if m:
            cur["mtu"] = m.group(1)
    _flush(cur, out, by_iface)
    return out


normalize_config_interface.RULE_KEYS = RULE_KEYS
