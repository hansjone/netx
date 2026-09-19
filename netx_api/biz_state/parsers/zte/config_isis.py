"""ZTE config intent: IS-IS from ``!<isis>`` / ``show running-config isis``.

One row per ``(process, vrf, interface)``. Process-level attrs (area, system-id,
router-id, is-type) are copied onto each interface row.
Never captures hello-authentication / encrypted secrets.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_PROC_RE = re.compile(
    r"(?i)^\s*router\s+isis\s+(\S+)(?:\s+vrf\s+(\S+))?\s*$"
)
_AREA_RE = re.compile(r"(?i)^\s*area\s+(\S+)\s*$")
_SYS_RE = re.compile(r"(?i)^\s*system-id\s+(\S+)\s*$")
_RID_RE = re.compile(r"(?i)^\s*router-id\s+(\S+)\s*$")
_ISTYPE_RE = re.compile(r"(?i)^\s*is-type\s+(\S+)\s*$")
_IFACE_RE = re.compile(r"(?i)^\s*interface\s+(\S+)\s*$")
_CIRCUIT_RE = re.compile(r"(?i)^\s*circuit-type\s+(\S+)\s*$")
_NET_RE = re.compile(r"(?i)^\s*network\s+(\S+)\s*$")
_METRIC_RE = re.compile(r"(?i)^\s*metric\s+(\d+)\s*$")
_V6_METRIC_RE = re.compile(r"(?i)^\s*ipv6\s+metric\s+(\d+)\s*$")
_DOLLAR_RE = re.compile(r"^(\s*)\$\s*$")


def _indent(spaces: str) -> int:
    return len(spaces.expandtabs(2))


def _flush(
    proc: dict[str, Any] | None,
    iface: dict[str, Any] | None,
    out: list[dict[str, Any]],
    seen: set[tuple[str, ...]],
) -> None:
    if not proc or not iface:
        return
    key = (proc["process_id"], proc["vrf"], iface["interface"])
    if key in seen:
        return
    seen.add(key)
    out.append(
        {
            "process_id": str(proc["process_id"])[:32],
            "vrf": str(proc.get("vrf") or "")[:128],
            "area": str(proc.get("area") or "")[:64],
            "system_id": str(proc.get("system_id") or "")[:64],
            "router_id": str(proc.get("router_id") or "")[:64],
            "is_type": str(proc.get("is_type") or "")[:32],
            "interface": str(iface.get("interface") or "")[:128],
            "circuit_type": str(iface.get("circuit_type") or "")[:32],
            "network_type": str(iface.get("network_type") or "")[:32],
            "ip_enable": str(iface.get("ip_enable") or "")[:8],
            "ipv6_enable": str(iface.get("ipv6_enable") or "")[:8],
            "metric": str(iface.get("metric") or "")[:16],
            "ipv6_metric": str(iface.get("ipv6_metric") or "")[:16],
            "passive": str(iface.get("passive") or "")[:8],
            "bfd": str(iface.get("bfd") or "")[:16],
            "ipv6_bfd": str(iface.get("ipv6_bfd") or "")[:16],
        }
    )


def normalize_config_isis(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (fsm_tables, vendor, device_type, command, params)
    body = extract_mim_section(raw_text, "isis")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    proc: dict[str, Any] | None = None
    iface: dict[str, Any] | None = None
    in_af = False
    af_indent = 0

    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = _PROC_RE.match(line)
        if m:
            _flush(proc, iface, out, seen)
            iface = None
            in_af = False
            af_indent = 0
            proc = {
                "process_id": m.group(1).strip(),
                "vrf": (m.group(2) or "").strip(),
                "area": "",
                "system_id": "",
                "router_id": "",
                "is_type": "",
            }
            continue
        if not proc:
            continue

        # Skip address-family subtree (nested $ must not close the process).
        if in_af:
            m = _DOLLAR_RE.match(line)
            if m and _indent(m.group(1)) <= af_indent:
                in_af = False
                af_indent = 0
            continue

        if re.match(r"(?i)^\s*address-family\s+", line):
            in_af = True
            af_indent = len(line) - len(line.lstrip(" \t"))
            continue

        m = _DOLLAR_RE.match(line)
        if m:
            ind = _indent(m.group(1))
            if iface is not None and ind >= 2:
                _flush(proc, iface, out, seen)
                iface = None
                continue
            _flush(proc, iface, out, seen)
            iface = None
            proc = None
            continue

        if iface is not None:
            m = _CIRCUIT_RE.match(line)
            if m:
                iface["circuit_type"] = m.group(1).lower()
                continue
            m = _NET_RE.match(line)
            if m:
                iface["network_type"] = m.group(1).lower()
                continue
            m = _V6_METRIC_RE.match(line)
            if m:
                iface["ipv6_metric"] = m.group(1)
                continue
            m = _METRIC_RE.match(line)
            if m:
                iface["metric"] = m.group(1)
                continue
            low = line.strip().lower()
            if low == "ip router isis" or low.startswith("ip router isis "):
                iface["ip_enable"] = "yes"
                continue
            if low == "ipv6 router isis" or low.startswith("ipv6 router isis "):
                iface["ipv6_enable"] = "yes"
                continue
            if low == "passive-mode" or low == "passive":
                iface["passive"] = "yes"
                continue
            if low == "bfd-enable" or low.startswith("bfd-enable"):
                iface["bfd"] = "enable"
                continue
            if low == "ipv6 bfd-enable" or low.startswith("ipv6 bfd-enable"):
                iface["ipv6_bfd"] = "enable"
                continue
            continue

        m = _IFACE_RE.match(line)
        if m:
            _flush(proc, iface, out, seen)
            iface = {
                "interface": m.group(1).strip(),
                "circuit_type": "",
                "network_type": "",
                "ip_enable": "",
                "ipv6_enable": "",
                "metric": "",
                "ipv6_metric": "",
                "passive": "",
                "bfd": "",
                "ipv6_bfd": "",
            }
            continue
        m = _AREA_RE.match(line)
        if m:
            proc["area"] = m.group(1).strip()
            continue
        m = _SYS_RE.match(line)
        if m:
            proc["system_id"] = m.group(1).strip()
            continue
        m = _RID_RE.match(line)
        if m:
            # prefer IPv4-looking router-id at process level
            rid = m.group(1).strip()
            if ":" not in rid or not proc.get("router_id"):
                proc["router_id"] = rid
            continue
        m = _ISTYPE_RE.match(line)
        if m:
            proc["is_type"] = m.group(1).lower()
            continue

    _flush(proc, iface, out, seen)
    return out


normalize_config_isis.RULE_KEYS = RULE_KEYS
