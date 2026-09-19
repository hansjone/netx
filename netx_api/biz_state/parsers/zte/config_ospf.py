"""ZTE config intent: OSPFv2/v3 from ``!<ospfv2>`` / ``!<ospfv3>``.

One row per ``(af, process, vrf, area, interface)``. Areas with no interfaces
still emit a row with empty ``interface``. Redistribute is process-scoped and
applied when the process block closes (it often appears after areas).
Never captures digest keys / encrypted auth material.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_V2_RE = re.compile(r"(?i)^\s*router\s+ospf\s+(\S+)(?:\s+vrf\s+(\S+))?\s*$")
_V3_RE = re.compile(r"(?i)^\s*ipv6\s+router\s+ospf\s+(\S+)(?:\s+vrf\s+(\S+))?\s*$")
_RID_RE = re.compile(r"(?i)^\s*router-id\s+(\S+)\s*$")
_AREA_RE = re.compile(r"(?i)^\s*area\s+(\S+)\s*$")
_IFACE_RE = re.compile(r"(?i)^\s*interface\s+(\S+)\s*$")
_COST_RE = re.compile(r"(?i)^\s*cost\s+(\d+)\s*$")
_HELLO_RE = re.compile(r"(?i)^\s*hello-interval\s+(\d+)\s*$")
_DEAD_RE = re.compile(r"(?i)^\s*dead-interval\s+(\d+)\s*$")
_NET_RE = re.compile(r"(?i)^\s*network\s+(\S+)\s*$")
_REDIST_RE = re.compile(r"(?i)^\s*redistribute\s+(\S+)")
_DOLLAR_RE = re.compile(r"^(\s*)\$\s*$")


def _indent(spaces: str) -> int:
    return len(spaces.expandtabs(2))


def _queue_iface(
    proc: dict[str, Any],
    area: dict[str, Any],
    iface: dict[str, Any],
) -> None:
    proc.setdefault("_pending", []).append(
        {
            "area": str(area.get("area") or ""),
            "area_type": str(area.get("area_type") or "normal"),
            "interface": str(iface.get("interface") or ""),
            "network_type": str(iface.get("network_type") or ""),
            "cost": str(iface.get("cost") or ""),
            "hello_interval": str(iface.get("hello_interval") or ""),
            "dead_interval": str(iface.get("dead_interval") or ""),
            "bfd": str(iface.get("bfd") or ""),
        }
    )


def _emit_process(
    proc: dict[str, Any] | None,
    out: list[dict[str, Any]],
    seen: set[tuple[str, ...]],
) -> None:
    if not proc:
        return
    redist = ",".join(sorted(proc.get("_redistribute") or []))[:256]
    for item in proc.get("_pending") or []:
        key = (
            proc["af"],
            proc["process_id"],
            proc["vrf"],
            item["area"],
            item["interface"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "af": proc["af"][:8],
                "process_id": str(proc["process_id"])[:32],
                "vrf": str(proc.get("vrf") or "")[:128],
                "router_id": str(proc.get("router_id") or "")[:64],
                "area": item["area"][:64],
                "area_type": item["area_type"][:32],
                "interface": item["interface"][:128],
                "network_type": item["network_type"][:32],
                "cost": item["cost"][:16],
                "hello_interval": item["hello_interval"][:16],
                "dead_interval": item["dead_interval"][:16],
                "bfd": item["bfd"][:16],
                "redistribute": redist,
            }
        )


def _close_area(proc: dict[str, Any] | None, area: dict[str, Any] | None) -> None:
    if not proc or not area:
        return
    if int(area.get("_iface_count") or 0) == 0:
        _queue_iface(
            proc,
            area,
            {
                "interface": "",
                "network_type": "",
                "cost": "",
                "hello_interval": "",
                "dead_interval": "",
                "bfd": "",
            },
        )


def _parse_ospf_body(body: str, *, af: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    proc: dict[str, Any] | None = None
    area: dict[str, Any] | None = None
    iface: dict[str, Any] | None = None
    head_re = _V3_RE if af == "ipv6" else _V2_RE

    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = head_re.match(line)
        if m:
            if iface and proc and area:
                _queue_iface(proc, area, iface)
            _close_area(proc, area)
            _emit_process(proc, out, seen)
            iface = None
            area = None
            proc = {
                "af": af,
                "process_id": m.group(1).strip(),
                "vrf": (m.group(2) or "").strip(),
                "router_id": "",
                "_redistribute": set(),
                "_pending": [],
            }
            continue
        if not proc:
            continue

        m = _DOLLAR_RE.match(line)
        if m:
            ind = _indent(m.group(1))
            if iface is not None and ind >= 4:
                _queue_iface(proc, area, iface)  # type: ignore[arg-type]
                iface = None
            elif area is not None and ind >= 2:
                if iface and area:
                    _queue_iface(proc, area, iface)
                    iface = None
                _close_area(proc, area)
                area = None
            else:
                if iface and area:
                    _queue_iface(proc, area, iface)
                    iface = None
                _close_area(proc, area)
                area = None
                _emit_process(proc, out, seen)
                proc = None
            continue

        if iface is not None:
            m = _COST_RE.match(line)
            if m:
                iface["cost"] = m.group(1)
                continue
            m = _HELLO_RE.match(line)
            if m:
                iface["hello_interval"] = m.group(1)
                continue
            m = _DEAD_RE.match(line)
            if m:
                iface["dead_interval"] = m.group(1)
                continue
            m = _NET_RE.match(line)
            if m:
                iface["network_type"] = m.group(1).lower()
                continue
            low = line.strip().lower()
            if low == "bfd" or low.startswith("bfd interval") or low.startswith("bfd enable"):
                iface["bfd"] = "enable"
                continue
            if low == "bfd disable":
                iface["bfd"] = "disable"
                continue
            continue

        m = _AREA_RE.match(line)
        if m:
            if iface and area:
                _queue_iface(proc, area, iface)
                iface = None
            _close_area(proc, area)
            area = {
                "area": m.group(1).strip(),
                "area_type": "normal",
                "_iface_count": 0,
            }
            continue

        if area is not None:
            m = _IFACE_RE.match(line)
            if m:
                if iface:
                    _queue_iface(proc, area, iface)
                iface = {
                    "interface": m.group(1).strip(),
                    "network_type": "",
                    "cost": "",
                    "hello_interval": "",
                    "dead_interval": "",
                    "bfd": "",
                }
                area["_iface_count"] = int(area.get("_iface_count") or 0) + 1
                continue
            low = line.strip().lower()
            if low.startswith("stub"):
                area["area_type"] = "stub"
                continue
            if low.startswith("nssa"):
                area["area_type"] = "nssa"
                continue

        # process-level (also after areas closed)
        if iface is None:
            m = _RID_RE.match(line)
            if m:
                proc["router_id"] = m.group(1).strip()
                continue
            m = _REDIST_RE.match(line)
            if m:
                proc["_redistribute"].add(m.group(1).lower())
                continue

    if iface and proc and area:
        _queue_iface(proc, area, iface)
    _close_area(proc, area)
    _emit_process(proc, out, seen)
    return out


def normalize_config_ospf(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (fsm_tables, vendor, device_type, params)
    cmd = str(command or "").lower()
    text = str(raw_text or "")
    if "ospfv3" in cmd:
        return _parse_ospf_body(extract_mim_section(text, "ospfv3"), af="ipv6")
    if "ospfv2" in cmd or re.search(r"(?i)running-config\s+ospf(?:v2)?\b", cmd):
        return _parse_ospf_body(extract_mim_section(text, "ospfv2"), af="ipv4")
    if re.search(r"(?i)^!<ospfv3>\s*$", text, re.M) and not re.search(
        r"(?i)^!<ospfv2>\s*$", text, re.M
    ):
        return _parse_ospf_body(extract_mim_section(text, "ospfv3"), af="ipv6")
    return _parse_ospf_body(extract_mim_section(text, "ospfv2"), af="ipv4")


normalize_config_ospf.RULE_KEYS = RULE_KEYS
