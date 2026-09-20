"""ZTE: filtered ``show interface | include …`` (rate / util / BW)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_interface",)

_IF_HDR_RE = re.compile(
    r"^(?P<iface>\S+)\s+is\s+(?P<state>administratively\s+down|up|down)"
    r"(?:,\s*ifindex:\s*(?P<ifindex>\d+))?\s*$",
    re.I,
)
_DESC_RE = re.compile(r"^\s*Description:\s*(?P<desc>.*?)\s*$", re.I)
_BW_RE = re.compile(r"^\s*BW\s+(?P<bw>.+?)\s*$", re.I)
_IP_MTU_RE = re.compile(r"^\s*IP\s+MTU\s+(?P<mtu>\d+)\s+bytes", re.I)
_IPV6_MTU_RE = re.compile(r"^\s*IPv6\s+MTU\s+(?P<mtu>\d+)\s+bytes", re.I)
_MPLS_MTU_RE = re.compile(r"^\s*MPLS\s+MTU\s+(?P<mtu>\d+)\s+bytes", re.I)
_MTU_RE = re.compile(r"^\s*MTU\s+(?P<mtu>\d+)\s+bytes", re.I)
_MEDIA_RE = re.compile(r"^\s*The\s+port\s+is\s+(?P<media>\S+)", re.I)
_NEG_RE = re.compile(r"^\s*Negotiation\s+(?P<neg>\S+)", re.I)
_PERIOD_RE = re.compile(r"^\s*Rate\s+period\s*:\s*(?P<p>\d+)", re.I)
_IN_RE = re.compile(r"^\s*Input\s*:\s*(?P<bps>[\d.]+)\s*bit/s", re.I)
_OUT_RE = re.compile(r"^\s*Output\s*:\s*(?P<bps>[\d.]+)\s*bit/s", re.I)
_UTIL_RE = re.compile(
    r"^\s*Intf\s+utilization:\s*input\s*(?P<inu>[\d.]+)%\s+output\s*(?P<outu>[\d.]+)%",
    re.I,
)


def _empty_row(iface: str) -> dict[str, Any]:
    return {
        "interface": iface[:128],
        "port_status": "",
        "ifindex": "",
        "description": "",
        "port_media": "",
        "negotiation": "",
        "bw": "",
        "ip_mtu": "",
        "mtu": "",
        "mpls_mtu": "",
        "ipv6_mtu": "",
        "rate_period": "",
        "input_bps": "",
        "output_bps": "",
        "in_util": "",
        "out_util": "",
    }


def _normalize_port_status(state: str) -> str:
    s = str(state or "").strip().lower()
    # Guard against FSM greedily eating ", ifindex:"
    if "," in s:
        s = s.split(",", 1)[0].strip()
    if "administratively" in s:
        return "admin-down"
    if s == "up":
        return "up"
    if s == "down":
        return "down"
    return s[:32]


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        iface = row_get(r, "IFNAME", "interface")
        in_util = row_get(r, "IN_UTIL", "in_util")
        # Incomplete FSM rows (no util yet) are filldown noise — skip
        if not iface or not in_util or iface in seen:
            continue
        seen.add(iface)
        status_raw = row_get(r, "ADMIN_STATE", "ADMIN_OPER", "port_status", "admin")
        out.append(
            {
                "interface": iface[:128],
                "port_status": _normalize_port_status(status_raw)[:32],
                "ifindex": row_get(r, "IFINDEX", "ifindex")[:32],
                "description": row_get(r, "DESCRIPTION", "description")[:256],
                "port_media": row_get(r, "PORT_MEDIA", "port_media")[:32],
                "negotiation": row_get(r, "NEGOTIATION", "negotiation")[:32],
                "bw": row_get(r, "BW_RAW", "bw")[:64],
                "ip_mtu": row_get(r, "IP_MTU", "ip_mtu")[:16],
                "mtu": row_get(r, "MTU", "mtu")[:16],
                "mpls_mtu": row_get(r, "MPLS_MTU", "mpls_mtu")[:16],
                "ipv6_mtu": row_get(r, "IPV6_MTU", "ipv6_mtu")[:16],
                "rate_period": row_get(r, "RATE_PERIOD", "rate_period")[:16],
                "input_bps": row_get(r, "INPUT_BPS", "input_bps")[:32],
                "output_bps": row_get(r, "OUTPUT_BPS", "output_bps")[:32],
                "in_util": in_util[:16],
                "out_util": row_get(r, "OUT_UTIL", "out_util")[:16],
            }
        )
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    in_rates = False
    saw_input = False

    def _flush() -> None:
        nonlocal cur
        if cur and cur.get("interface"):
            out.append(cur)
        cur = None

    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _IF_HDR_RE.match(line.strip())
        if m:
            _flush()
            cur = _empty_row(m.group("iface"))
            cur["port_status"] = _normalize_port_status(m.group("state"))
            cur["ifindex"] = (m.group("ifindex") or "")[:32]
            in_rates = False
            saw_input = False
            continue
        if not cur:
            continue
        m = _DESC_RE.match(line)
        if m:
            cur["description"] = (m.group("desc") or "").strip()[:256]
            continue
        m = _MEDIA_RE.match(line)
        if m:
            cur["port_media"] = m.group("media")[:32]
            continue
        m = _NEG_RE.match(line)
        if m:
            cur["negotiation"] = m.group("neg")[:32]
            continue
        m = _BW_RE.match(line)
        if m:
            cur["bw"] = m.group("bw").strip()[:64]
            continue
        m = _IP_MTU_RE.match(line)
        if m:
            cur["ip_mtu"] = m.group("mtu")[:16]
            continue
        m = _IPV6_MTU_RE.match(line)
        if m:
            cur["ipv6_mtu"] = m.group("mtu")[:16]
            continue
        m = _MPLS_MTU_RE.match(line)
        if m:
            cur["mpls_mtu"] = m.group("mtu")[:16]
            continue
        m = _MTU_RE.match(line)
        if m:
            cur["mtu"] = m.group("mtu")[:16]
            continue
        m = _PERIOD_RE.match(line)
        if m:
            cur["rate_period"] = m.group("p")[:16]
            in_rates = True
            saw_input = False
            continue
        if in_rates and "Peak rate" in line:
            continue
        m = _IN_RE.match(line)
        if m and in_rates and not saw_input:
            cur["input_bps"] = m.group("bps")[:32]
            saw_input = True
            continue
        m = _OUT_RE.match(line)
        if m and in_rates and saw_input and not cur.get("output_bps"):
            cur["output_bps"] = m.group("bps")[:32]
            continue
        m = _UTIL_RE.match(line)
        if m:
            cur["in_util"] = m.group("inu")[:16]
            cur["out_util"] = m.group("outu")[:16]
            in_rates = False
            continue
    _flush()
    return out


def normalize_interface_detail(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = params
    tables = dict(fsm_tables or {})
    if not any(tables.get(k) for k in RULE_KEYS):
        platform = resolve_cli_platform(
            vendor=vendor,
            device_type=device_type,
            vendor_key=resolve_vendor_key(vendor, device_type),
        )
        cmd = str(command or "show interface").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_interface_detail.RULE_KEYS = RULE_KEYS
