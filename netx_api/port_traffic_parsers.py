"""Interface brief/detail parsers via TextFSM only (``ntc_parse``).

No regex CLI parsers — add / fix templates under ``cli_templates/{vendor}/``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .ntc_parse import parse_cli, resolve_cli_platform, row_get


_BW_UNIT = {
    "k": 1_000,
    "m": 1_000_000,
    "g": 1_000_000_000,
    "t": 1_000_000_000_000,
}

_RE_BW_COMPACT = re.compile(r"^(\d+(?:\.\d+)?)\s*([kKmMgGtT])(?:bit)?s?$", re.I)
_RE_BW_DETAIL = re.compile(
    r"\bBW\s+(\d+(?:\.\d+)?)\s*([kKmMgGtT])?\s*(?:G?bit|bit)/s(?:ec)?\b",
    re.I,
)


@dataclass(frozen=True)
class BriefPort:
    ifname: str
    attribute: str = ""
    mode: str = ""
    bw_raw: str = ""
    bw_bps: int = 0
    admin: str = ""
    phy: str = ""
    prot: str = ""
    description: str = ""


@dataclass(frozen=True)
class DetailRates:
    ifname: str = ""
    admin_oper: str = ""
    description: str = ""
    bw_bps: int = 0
    rate_period_sec: int = 0
    in_bps: float = 0.0
    out_bps: float = 0.0
    in_util_pct: float = 0.0
    out_util_pct: float = 0.0


def resolve_util_pct(vendor_util: float, bps: float, bw_bps: int) -> float:
    """Prefer vendor-reported util; derive from rate/BW when util is missing but traffic exists."""
    u = float(vendor_util or 0.0)
    if u > 0:
        return u
    bw = int(bw_bps or 0)
    rate = float(bps or 0.0)
    if bw > 0 and rate > 0:
        return rate / float(bw) * 100.0
    return u


def parse_bw_to_bps(raw: str) -> int:
    """Normalize bandwidth field values from TextFSM (``1G``, ``BW 1 Gbit/s``, …)."""
    text = (raw or "").strip()
    if not text or text.upper() == "N/A":
        return 0
    m = _RE_BW_COMPACT.match(text.replace(" ", ""))
    if m:
        return int(float(m.group(1)) * _BW_UNIT[m.group(2).lower()])
    m2 = _RE_BW_DETAIL.search(text)
    if m2:
        value = float(m2.group(1))
        unit = (m2.group(2) or "g").lower()
        if m2.group(2) is None and "gbit" in text.lower():
            unit = "g"
        elif m2.group(2) is None and "mbit" in text.lower():
            unit = "m"
        elif m2.group(2) is None and "kbit" in text.lower():
            unit = "k"
        return int(value * _BW_UNIT.get(unit, 1_000_000_000))
    digits = re.sub(r"[^\d.]", "", text)
    if digits:
        try:
            return int(float(digits))
        except ValueError:
            return 0
    return 0


def brief_port_to_dict(row: BriefPort) -> dict[str, Any]:
    return {
        "ifname": row.ifname,
        "attribute": row.attribute,
        "mode": row.mode,
        "bw_raw": row.bw_raw,
        "bw_bps": row.bw_bps,
        "admin": row.admin,
        "phy": row.phy,
        "prot": row.prot,
        "description": row.description,
    }


def detail_to_dict(row: DetailRates) -> dict[str, Any]:
    return {
        "ifname": row.ifname,
        "admin_oper": row.admin_oper,
        "description": row.description,
        "bw_bps": row.bw_bps,
        "rate_period_sec": row.rate_period_sec,
        "in_bps": row.in_bps,
        "out_bps": row.out_bps,
        "in_util_pct": row.in_util_pct,
        "out_util_pct": row.out_util_pct,
    }


def _brief_command_for_vendor(vendor_key: str) -> str:
    from .port_traffic_commands import commands_for_vendor

    key = str(vendor_key or "zte").strip().lower()
    cmds = commands_for_vendor(key, key)
    return cmds.brief if cmds else "show interface brief"


def _detail_command_for_vendor(vendor_key: str, ifname: str = "") -> str:
    from .port_traffic_commands import commands_for_vendor, detail_command

    key = str(vendor_key or "zte").strip().lower()
    name = (ifname or "IFACE").strip() or "IFACE"
    cmds = commands_for_vendor(key, key)
    if cmds:
        return detail_command(cmds, name)
    return f"show interface {name}"


def _bw_field_to_bps(raw: str) -> int:
    """Normalize TextFSM bandwidth fields (e.g. ``1000000 Kbit``, ``1 Gbit/s``)."""
    text = (raw or "").strip()
    if not text:
        return 0
    m = re.search(r"(?i)(\d+(?:\.\d+)?)\s*([kKmMgGtT])\s*bit\b", text)
    if m:
        return int(float(m.group(1)) * _BW_UNIT[m.group(2).lower()])
    prefixed = text if text.upper().startswith("BW") else f"BW {text}"
    got = parse_bw_to_bps(prefixed)
    if got:
        return got
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return 0
    return parse_bw_to_bps(text)


def _norm_updown(token: str) -> str:
    text = (token or "").strip().lower()
    if "administratively" in text:
        return "down"
    if text.startswith("*"):
        text = text[1:]
    if text.startswith("up"):
        return "up"
    if text.startswith("down"):
        return "down"
    return text if text in {"up", "down"} else ""


def _yes_no_updown(token: str) -> str:
    text = (token or "").strip().lower()
    if text in {"yes", "up", "true", "1"}:
        return "up"
    if text in {"no", "down", "false", "0", "ghost"}:
        return "down"
    return _norm_updown(text)


def _map_brief_rows(rows: list[dict[str, Any]], *, vendor_key: str) -> list[BriefPort]:
    key = str(vendor_key or "zte").strip().lower()
    out: list[BriefPort] = []
    for row in rows:
        ifname = row_get(row, "interface", "ifname", "port", "intf", "port_id", "name")
        if not ifname or "#" in ifname or ">" in ifname:
            continue
        admin = _norm_updown(
            row_get(row, "admin", "admin_state", "status", "phy", "link", "link_status")
        )
        phy = _norm_updown(row_get(row, "phy", "status", "link_status", "port_state", "link"))
        prot = _norm_updown(row_get(row, "prot", "protocol", "proto", "protocol_status"))
        if key == "cisco":
            admin = _norm_updown(row_get(row, "status", "admin", "link_status")) or admin
            phy = admin
            prot = _norm_updown(row_get(row, "proto", "protocol", "prot")) or prot
        elif key == "huawei":
            phy = _norm_updown(row_get(row, "phy", "status")) or phy
            prot = _norm_updown(row_get(row, "protocol", "prot", "proto")) or prot
            admin = phy or admin
        elif key == "h3c":
            # Community: LINK / PROTOCOL (route mode) or LINK only (bridge mode).
            link = _norm_updown(row_get(row, "link", "admin", "phy"))
            admin = link or admin
            phy = link or phy
            prot = _norm_updown(row_get(row, "protocol", "prot", "proto")) or prot or phy
        elif key == "juniper":
            admin = _norm_updown(row_get(row, "admin_state", "admin", "status")) or admin
            phy = _norm_updown(row_get(row, "link_status", "phy", "status")) or phy
            prot = phy or prot or admin
        elif key == "nokia":
            admin = _norm_updown(row_get(row, "admin_state", "admin")) or admin
            link = _yes_no_updown(row_get(row, "link"))
            phy = _norm_updown(row_get(row, "port_state", "phy")) or link or phy
            prot = phy or prot or admin
        elif key == "mikrotik":
            # print brief often exposes flags / name; tolerate missing proto.
            admin = _norm_updown(row_get(row, "status", "admin", "link")) or admin or "up"
            phy = _norm_updown(row_get(row, "link", "status", "phy")) or phy or admin
            prot = _norm_updown(row_get(row, "protocol", "prot")) or prot or phy
        if admin not in {"up", "down"} and phy in {"up", "down"}:
            admin = phy
        if phy not in {"up", "down"}:
            phy = admin if admin in {"up", "down"} else phy
        if prot not in {"up", "down"}:
            # Prefer inferring prot from phy when vendor brief has no protocol column.
            if phy in {"up", "down"} and key in {"juniper", "nokia", "mikrotik", "h3c"}:
                prot = phy
            else:
                continue
        if admin not in {"up", "down"} or phy not in {"up", "down"}:
            continue
        bw_raw = row_get(row, "bw", "bandwidth", "bw_raw", "speed")
        out.append(
            BriefPort(
                ifname=ifname,
                attribute=row_get(row, "attribute", "attr", "port_type", "type"),
                mode=row_get(row, "mode", "port_mode", "duplex"),
                bw_raw=bw_raw,
                bw_bps=parse_bw_to_bps(bw_raw),
                admin=admin,
                phy=phy,
                prot=prot,
                description=row_get(row, "description", "descrip", "desc"),
            )
        )
    return out


def _period_to_sec(n: int, unit: str) -> int:
    u = (unit or "").lower()
    if u.startswith("second"):
        return int(n)
    if u.startswith("minute"):
        return int(n) * 60
    if u.startswith("hour"):
        return int(n) * 3600
    return int(n)


def _map_detail_rows(rows: list[dict[str, Any]]) -> list[DetailRates]:
    best: dict[str, DetailRates] = {}
    for row in rows:
        ifname = row_get(row, "ifname", "interface", "port")
        in_bps_s = row_get(row, "input_bps", "in_bps", "input_rate", "input")
        out_bps_s = row_get(row, "output_bps", "out_bps", "output_rate", "output")
        try:
            in_bps = float(in_bps_s or 0)
        except ValueError:
            in_bps = 0.0
        try:
            out_bps = float(out_bps_s or 0)
        except ValueError:
            out_bps = 0.0
        bw_raw = row_get(row, "bw_raw", "bandwidth", "bw")
        bw_bps = _bw_field_to_bps(bw_raw)
        period_s = row_get(row, "rate_period", "period", "rate_period_sec")
        period_unit = row_get(row, "rate_period_unit", "period_unit")
        try:
            rate_period_sec = _period_to_sec(int(float(period_s or 0)), period_unit)
        except ValueError:
            rate_period_sec = 0
        in_util_s = row_get(row, "in_util", "input_util", "inuti")
        out_util_s = row_get(row, "out_util", "output_util", "oututi")
        try:
            in_util = float((in_util_s or "0").rstrip("%"))
        except ValueError:
            in_util = 0.0
        try:
            out_util = float((out_util_s or "0").rstrip("%"))
        except ValueError:
            out_util = 0.0
        admin = _norm_updown(
            row_get(
                row,
                "admin_oper",
                "status",
                "admin",
                "link_status",
                "line_status",
                "admin_state",
            )
        )
        ifname = ifname or row_get(row, "port_id", "name")
        if not ifname and in_bps <= 0 and out_bps <= 0 and not bw_bps:
            continue
        cand = DetailRates(
            ifname=ifname,
            admin_oper=admin,
            description=row_get(row, "description", "descrip", "interface_description"),
            bw_bps=bw_bps,
            rate_period_sec=rate_period_sec,
            in_bps=in_bps,
            out_bps=out_bps,
            in_util_pct=in_util,
            out_util_pct=out_util,
        )
        key = ifname or "_"
        prev = best.get(key)
        if prev is None or (in_util_s or out_util_s or in_bps or out_bps):
            best[key] = cand
    return list(best.values())


def parse_interface_brief(
    text: str,
    vendor_key: str = "zte",
    *,
    command: str = "",
    device_type: str = "",
) -> list[BriefPort]:
    """Parse brief CLI via custom/community TextFSM. Empty list if no template match."""
    key = str(vendor_key or "zte").strip().lower()
    plat = resolve_cli_platform(vendor_key=key, device_type=device_type)
    cmd = (command or _brief_command_for_vendor(key)).strip()
    if not plat or not cmd:
        return []
    rows = parse_cli(platform=plat, command=cmd, text=text)
    return _map_brief_rows(rows, vendor_key=key)


def parse_interface_detail(
    text: str,
    vendor_key: str = "zte",
    *,
    command: str = "",
    device_type: str = "",
    ifname: str = "",
) -> DetailRates:
    """Parse detail CLI via custom/community TextFSM. Empty DetailRates if no match."""
    key = str(vendor_key or "zte").strip().lower()
    plat = resolve_cli_platform(vendor_key=key, device_type=device_type)
    cmd = (command or _detail_command_for_vendor(key, ifname=ifname)).strip()
    if not plat or not cmd:
        return DetailRates()
    rows = parse_cli(platform=plat, command=cmd, text=text)
    mapped = _map_detail_rows(rows)
    return mapped[0] if mapped else DetailRates()


def parse_zte_interface_brief(text: str) -> list[BriefPort]:
    return parse_interface_brief(text, "zte", device_type="zte_zxros")


def parse_zte_interface_detail(text: str, *, ifname: str = "") -> DetailRates:
    return parse_interface_detail(text, "zte", device_type="zte_zxros", ifname=ifname)


def parse_huawei_interface_brief(text: str) -> list[BriefPort]:
    return parse_interface_brief(text, "huawei", device_type="huawei_vrp")


def parse_huawei_interface_detail(text: str, *, ifname: str = "") -> DetailRates:
    return parse_interface_detail(text, "huawei", device_type="huawei_vrp", ifname=ifname)


def parse_cisco_interface_brief(text: str) -> list[BriefPort]:
    return parse_interface_brief(text, "cisco", device_type="cisco_ios")


def parse_cisco_interface_detail(text: str, *, ifname: str = "") -> DetailRates:
    return parse_interface_detail(text, "cisco", device_type="cisco_ios", ifname=ifname)


def parse_h3c_interface_brief(text: str) -> list[BriefPort]:
    return parse_interface_brief(text, "h3c", device_type="hp_comware")


def parse_h3c_interface_detail(text: str, *, ifname: str = "") -> DetailRates:
    return parse_interface_detail(text, "h3c", device_type="hp_comware", ifname=ifname)


def parse_juniper_interface_brief(text: str) -> list[BriefPort]:
    return parse_interface_brief(text, "juniper", device_type="juniper_junos")


def parse_juniper_interface_detail(text: str, *, ifname: str = "") -> DetailRates:
    return parse_interface_detail(text, "juniper", device_type="juniper_junos", ifname=ifname)


def parse_nokia_interface_brief(text: str) -> list[BriefPort]:
    return parse_interface_brief(text, "nokia", device_type="nokia_sros")


def parse_nokia_interface_detail(text: str, *, ifname: str = "") -> DetailRates:
    return parse_interface_detail(text, "nokia", device_type="nokia_sros", ifname=ifname)
