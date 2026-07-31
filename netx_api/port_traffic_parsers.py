"""Parsers for ZTE / Huawei / Cisco interface brief & detail (rate bit/s)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_BW_UNIT = {
    "k": 1_000,
    "m": 1_000_000,
    "g": 1_000_000_000,
    "t": 1_000_000_000_000,
}

_RE_BW_COMPACT = re.compile(r"^(\d+(?:\.\d+)?)\s*([kKmMgGtT])(?:bit)?s?$", re.I)
# ZTE "BW 1 Gbit/s" and Cisco "BW 1000000 Kbit/sec"
_RE_BW_DETAIL = re.compile(
    r"\bBW\s+(\d+(?:\.\d+)?)\s*([kKmMgGtT])?\s*(?:G?bit|bit)/s(?:ec)?\b",
    re.I,
)
_RE_RATE_PERIOD = re.compile(r"Rate\s+period\s*:\s*(\d+)\s*s", re.I)
_RE_INPUT_BPS = re.compile(r"^\s*Input\s*:\s*([\d.]+)\s*bit/s", re.I | re.M)
_RE_OUTPUT_BPS = re.compile(r"^\s*Output\s*:\s*([\d.]+)\s*bit/s", re.I | re.M)
_RE_UTIL = re.compile(
    r"Intf\s+utilization\s*:\s*input\s*([\d.]+)%\s*output\s*([\d.]+)%",
    re.I,
)
_RE_IF_UP = re.compile(r"^(\S+)\s+is\s+(up|down)\b", re.I | re.M)
_RE_DESC = re.compile(r"^\s*Description:\s*(.+?)\s*$", re.I | re.M)
_RE_PROMPT_LINE = re.compile(r"[#>]\s*$")
_RE_UPDOWN = re.compile(r"^(up|down)$", re.I)
_RE_UPDOWN_TOKEN = re.compile(r"^(up|down)\b", re.I)
# ZTE / Huawei / Cisco / common logical iface names.
_RE_IFNAME = re.compile(
    r"^(?:"
    r"xxvgei|xgei|cgei|gei|fei|qli|smartgroup|bvi|vlan|loopback|mgmt|"
    r"null|pos|atm|tunnel|irb|pw|eth|ethernet|port-channel|bundle|"
    r"gigabitethernet|xgigabitethernet|fastethernet|tengigabitethernet|"
    r"hundredgige|fivegige|fortygige|serial|dialer|cellular|multilink|"
    r"10ge|25ge|40ge|100ge|eth-trunk|vlanif|meth|loopback"
    r")[\w./:-]*$",
    re.I,
)
_RE_CISCO_BRIEF_ROW = re.compile(
    r"^(\S+)\s+(\S+)\s+(YES|NO)\s+(\S+)\s+(.+?)\s+(up|down)\s*$",
    re.I,
)
_RE_CISCO_IF_STATE = re.compile(
    r"^(\S+)\s+is\s+(administratively\s+)?(up|down),\s*line\s+protocol\s+is\s+(up|down)\b",
    re.I | re.M,
)
_RE_CISCO_RATE = re.compile(
    r"(\d+)\s+(second|minute|hour)s?\s+(input|output)\s+rate\s+([\d.]+)\s*bits/sec",
    re.I,
)
_RE_HW_BRIEF_ROW = re.compile(
    r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)\s*$"
)
_RE_HW_STATE = re.compile(
    r"^(\S+)\s+current\s+state\s*:\s*(UP|DOWN|Administratively\s+DOWN)\b",
    re.I | re.M,
)
_RE_HW_IN_RATE = re.compile(
    r"Last\s+(\d+)\s+seconds\s+input\s+rate\s*:\s*([\d.]+)\s*bits/sec",
    re.I,
)
_RE_HW_OUT_RATE = re.compile(
    r"Last\s+(\d+)\s+seconds\s+output\s+rate\s*:\s*([\d.]+)\s*bits/sec",
    re.I,
)
_RE_HW_IN_UTIL = re.compile(
    r"Last\s+(\d+)\s+seconds\s+input\s+utility\s+rate\s*:\s*([\d.]+)\s*%",
    re.I,
)
_RE_HW_OUT_UTIL = re.compile(
    r"Last\s+(\d+)\s+seconds\s+output\s+utility\s+rate\s*:\s*([\d.]+)\s*%",
    re.I,
)

# Fixed-width columns from ZTE `show interface brief` header.
_COL_IF = (0, 24)
_COL_ATTR = (24, 35)
_COL_MODE = (35, 48)
_COL_BW = (48, 54)
_COL_ADMIN = (54, 60)
_COL_PHY = (60, 66)
_COL_PROT = (66, 72)
_COL_DESC = (72, None)


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


def parse_bw_to_bps(raw: str) -> int:
    text = (raw or "").strip()
    if not text or text.upper() == "N/A":
        return 0
    m = _RE_BW_COMPACT.match(text.replace(" ", ""))
    if m:
        value = float(m.group(1))
        mult = _BW_UNIT[m.group(2).lower()]
        return int(value * mult)
    m2 = _RE_BW_DETAIL.search(text)
    if m2:
        value = float(m2.group(1))
        unit = (m2.group(2) or "g").lower()
        # "BW 1 Gbit/s" — unit letter may be in "Gbit" when group2 empty after "1 "
        if m2.group(2) is None and "gbit" in text.lower():
            unit = "g"
        elif m2.group(2) is None and "mbit" in text.lower():
            unit = "m"
        elif m2.group(2) is None and "kbit" in text.lower():
            unit = "k"
        mult = _BW_UNIT.get(unit, 1_000_000_000)
        return int(value * mult)
    # Plain "BW 1000000000" unlikely; try digits only
    digits = re.sub(r"[^\d.]", "", text)
    if digits:
        try:
            return int(float(digits))
        except ValueError:
            return 0
    return 0


def _slice(line: str, start: int, end: int | None) -> str:
    if end is None:
        return line[start:].rstrip() if len(line) > start else ""
    if len(line) <= start:
        return ""
    return line[start:end].strip()


def _looks_like_brief_port(ifname: str, admin: str, phy: str, prot: str) -> bool:
    """Reject prompts / hostname lines that leak after the brief table."""
    name = (ifname or "").strip()
    if not name or name.lower() == "interface":
        return False
    if "#" in name or ">" in name:
        return False
    if _RE_PROMPT_LINE.search(name):
        return False
    # Real brief rows always have Admin/Phy/Prot as up|down.
    if not (
        _RE_UPDOWN.match(admin or "")
        and _RE_UPDOWN.match(phy or "")
        and _RE_UPDOWN.match(prot or "")
    ):
        return False
    # Prefer known iface prefixes; still allow smartgroup/bvi style names.
    if not _RE_IFNAME.match(name):
        return False
    return True


def parse_zte_interface_brief(text: str) -> list[BriefPort]:
    """Parse ZTE `show interface brief` into port rows."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[BriefPort] = []
    started = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("Interface") and "Admin" in line and "Description" in line:
            started = True
            continue
        if not started:
            continue
        stripped = line.strip()
        # Trailing device prompt after the table (Netmiko often leaves hostname#).
        if "#" in stripped or (stripped.endswith(">") and " " not in stripped):
            admin_probe = _slice(line, *_COL_ADMIN).lower()
            if not _RE_UPDOWN.match(admin_probe):
                break
        if " is " in line and "ifindex" in line.lower():
            break
        if_field = _slice(line, *_COL_IF)
        ifname = if_field.split()[0] if if_field else ""
        admin = _slice(line, *_COL_ADMIN).lower()
        phy = _slice(line, *_COL_PHY).lower()
        prot = _slice(line, *_COL_PROT).lower()
        if not _looks_like_brief_port(ifname, admin, phy, prot):
            continue
        bw_raw = _slice(line, *_COL_BW)
        out.append(
            BriefPort(
                ifname=ifname,
                attribute=_slice(line, *_COL_ATTR),
                mode=_slice(line, *_COL_MODE),
                bw_raw=bw_raw,
                bw_bps=parse_bw_to_bps(bw_raw),
                admin=admin,
                phy=phy,
                prot=prot,
                description=_slice(line, *_COL_DESC).strip(),
            )
        )
    return out


def parse_zte_interface_detail(text: str) -> DetailRates:
    """Parse ZTE `show interface {ifname}` rate / util / BW."""
    blob = text or ""
    ifname = ""
    admin_oper = ""
    m_if = _RE_IF_UP.search(blob)
    if m_if:
        ifname = m_if.group(1)
        admin_oper = m_if.group(2).lower()
    desc = ""
    m_desc = _RE_DESC.search(blob)
    if m_desc:
        desc = m_desc.group(1).strip()

    bw_bps = 0
    m_bw = _RE_BW_DETAIL.search(blob)
    if m_bw:
        bw_bps = parse_bw_to_bps(m_bw.group(0))
    else:
        # Fallback line scan
        for line in blob.splitlines():
            if re.search(r"\bBW\b", line, re.I):
                bw_bps = parse_bw_to_bps(line)
                if bw_bps:
                    break

    rate_period = 0
    m_rp = _RE_RATE_PERIOD.search(blob)
    if m_rp:
        rate_period = int(m_rp.group(1))

    # Prefer Rate period block: first Input/Output after "Rate period"
    in_bps = 0.0
    out_bps = 0.0
    rp_idx = blob.lower().find("rate period")
    rate_blob = blob[rp_idx:] if rp_idx >= 0 else blob
    # Stop before Peak rate to avoid peak Input/Output
    peak_idx = rate_blob.lower().find("peak rate")
    if peak_idx >= 0:
        rate_blob = rate_blob[:peak_idx]
    m_in = _RE_INPUT_BPS.search(rate_blob)
    m_out = _RE_OUTPUT_BPS.search(rate_blob)
    if m_in:
        in_bps = float(m_in.group(1))
    if m_out:
        out_bps = float(m_out.group(1))

    in_util = 0.0
    out_util = 0.0
    m_util = _RE_UTIL.search(blob)
    if m_util:
        in_util = float(m_util.group(1))
        out_util = float(m_util.group(2))

    return DetailRates(
        ifname=ifname,
        admin_oper=admin_oper,
        description=desc,
        bw_bps=bw_bps,
        rate_period_sec=rate_period,
        in_bps=in_bps,
        out_bps=out_bps,
        in_util_pct=in_util,
        out_util_pct=out_util,
    )


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


def _norm_updown(token: str) -> str:
    """Normalize Huawei tokens like up(s) / *down to up|down|''."""
    text = (token or "").strip().lower()
    if text.startswith("*"):
        text = text[1:]
    m = _RE_UPDOWN_TOKEN.match(text)
    return m.group(1).lower() if m else ""


def parse_huawei_interface_brief(text: str) -> list[BriefPort]:
    """Parse Huawei `display interface brief` into port rows (BW ignored / 0)."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[BriefPort] = []
    started = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.lstrip().lower()
        if low.startswith("interface") and "phy" in low and ("inuti" in low or "protocol" in low):
            started = True
            continue
        if not started:
            continue
        stripped = line.strip()
        if stripped.startswith("<") and stripped.endswith(">"):
            break
        if "#" in stripped and " " not in stripped.split("#", 1)[0]:
            break
        m = _RE_HW_BRIEF_ROW.match(stripped)
        if not m:
            continue
        ifname = m.group(1)
        phy = _norm_updown(m.group(2))
        prot = _norm_updown(m.group(3))
        if not ifname or ifname.lower() == "interface":
            continue
        if "#" in ifname or ">" in ifname:
            continue
        if not _RE_IFNAME.match(ifname):
            continue
        if not phy or not prot:
            continue
        out.append(
            BriefPort(
                ifname=ifname,
                attribute="",
                mode="",
                bw_raw="",
                bw_bps=0,
                admin=phy,  # Huawei brief has no separate Admin; PHY is closest.
                phy=phy,
                prot=prot,
                description="",
            )
        )
    return out


def parse_huawei_interface_detail(text: str) -> DetailRates:
    """Parse Huawei `display interface {if}` Last N seconds rate / utility."""
    blob = text or ""
    ifname = ""
    admin_oper = ""
    m_state = _RE_HW_STATE.search(blob)
    if m_state:
        ifname = m_state.group(1)
        st = m_state.group(2).lower()
        admin_oper = "down" if "down" in st else "up"
    desc = ""
    m_desc = _RE_DESC.search(blob)
    if m_desc:
        desc = m_desc.group(1).strip()

    rate_period = 0
    in_bps = 0.0
    out_bps = 0.0
    m_in = _RE_HW_IN_RATE.search(blob)
    if m_in:
        rate_period = int(m_in.group(1))
        in_bps = float(m_in.group(2))
    m_out = _RE_HW_OUT_RATE.search(blob)
    if m_out:
        if not rate_period:
            rate_period = int(m_out.group(1))
        out_bps = float(m_out.group(2))

    in_util = 0.0
    out_util = 0.0
    m_iu = _RE_HW_IN_UTIL.search(blob)
    if m_iu:
        if not rate_period:
            rate_period = int(m_iu.group(1))
        in_util = float(m_iu.group(2))
    m_ou = _RE_HW_OUT_UTIL.search(blob)
    if m_ou:
        if not rate_period:
            rate_period = int(m_ou.group(1))
        out_util = float(m_ou.group(2))

    return DetailRates(
        ifname=ifname,
        admin_oper=admin_oper,
        description=desc,
        bw_bps=0,  # sample has no BW; leave 0 for now
        rate_period_sec=rate_period,
        in_bps=in_bps,
        out_bps=out_bps,
        in_util_pct=in_util,
        out_util_pct=out_util,
    )


def parse_interface_brief(text: str, vendor_key: str = "zte") -> list[BriefPort]:
    key = str(vendor_key or "zte").strip().lower()
    if key == "huawei":
        return parse_huawei_interface_brief(text)
    if key == "cisco":
        return parse_cisco_interface_brief(text)
    return parse_zte_interface_brief(text)


def parse_interface_detail(text: str, vendor_key: str = "zte") -> DetailRates:
    key = str(vendor_key or "zte").strip().lower()
    if key == "huawei":
        return parse_huawei_interface_detail(text)
    if key == "cisco":
        return parse_cisco_interface_detail(text)
    return parse_zte_interface_detail(text)


def _cisco_period_to_sec(n: int, unit: str) -> int:
    u = (unit or "").lower()
    if u.startswith("second"):
        return int(n)
    if u.startswith("minute"):
        return int(n) * 60
    if u.startswith("hour"):
        return int(n) * 3600
    return int(n)


def parse_cisco_interface_brief(text: str) -> list[BriefPort]:
    """Parse Cisco `show ip interface brief` into port rows."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[BriefPort] = []
    started = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.lstrip().lower()
        if low.startswith("interface") and "status" in low and "protocol" in low:
            started = True
            continue
        if not started:
            continue
        stripped = line.strip()
        if stripped.endswith("#") or (stripped.endswith(">") and stripped.startswith("<")):
            break
        if "#" in stripped and " " not in stripped.split("#", 1)[0]:
            break
        m = _RE_CISCO_BRIEF_ROW.match(stripped)
        if not m:
            continue
        ifname = m.group(1)
        status = (m.group(5) or "").strip().lower()
        prot = (m.group(6) or "").strip().lower()
        if not _RE_IFNAME.match(ifname):
            continue
        if prot not in ("up", "down"):
            continue
        admin_down = "administratively" in status
        if "down" in status:
            phy = "down"
        elif "up" in status:
            phy = "up"
        else:
            continue
        admin = "down" if admin_down else phy
        out.append(
            BriefPort(
                ifname=ifname,
                attribute="",
                mode="",
                bw_raw="",
                bw_bps=0,
                admin=admin,
                phy=phy,
                prot=prot,
                description="",
            )
        )
    return out


def parse_cisco_interface_detail(text: str) -> DetailRates:
    """Parse Cisco `show interfaces {if}` BW + N minute/second rate (util ignored)."""
    blob = text or ""
    ifname = ""
    admin_oper = ""
    m_state = _RE_CISCO_IF_STATE.search(blob)
    if m_state:
        ifname = m_state.group(1)
        admin_oper = "down" if m_state.group(2) or m_state.group(3).lower() == "down" else "up"
    if not ifname:
        m_up = _RE_IF_UP.search(blob)
        if m_up:
            ifname = m_up.group(1)
            admin_oper = m_up.group(2).lower()

    desc = ""
    m_desc = _RE_DESC.search(blob)
    if m_desc:
        desc = m_desc.group(1).strip()

    bw_bps = 0
    for line in blob.splitlines():
        if "BW" in line.upper() and ("bit" in line.lower()):
            bw_bps = parse_bw_to_bps(line)
            if bw_bps:
                break

    rate_period = 0
    in_bps = 0.0
    out_bps = 0.0
    for m in _RE_CISCO_RATE.finditer(blob):
        period = _cisco_period_to_sec(int(m.group(1)), m.group(2))
        direction = m.group(3).lower()
        rate = float(m.group(4))
        if not rate_period:
            rate_period = period
        if direction == "input":
            in_bps = rate
        else:
            out_bps = rate

    return DetailRates(
        ifname=ifname,
        admin_oper=admin_oper,
        description=desc,
        bw_bps=bw_bps,
        rate_period_sec=rate_period,
        in_bps=in_bps,
        out_bps=out_bps,
        in_util_pct=0.0,  # sample has no util %; ignore for now
        out_util_pct=0.0,
    )
