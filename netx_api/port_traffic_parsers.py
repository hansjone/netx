"""Parsers for ZTE show interface brief / detail (rate bit/s)."""

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
_RE_BW_DETAIL = re.compile(
    r"\bBW\s+(\d+(?:\.\d+)?)\s*([kKmMgGtT])?\s*(?:G?bit|bit)/s\b",
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
        if " is " in line and "ifindex" in line.lower():
            break
        ifname = _slice(line, *_COL_IF).split()[0] if _slice(line, *_COL_IF) else ""
        if not ifname or ifname.lower() == "interface":
            continue
        bw_raw = _slice(line, *_COL_BW)
        out.append(
            BriefPort(
                ifname=ifname,
                attribute=_slice(line, *_COL_ATTR),
                mode=_slice(line, *_COL_MODE),
                bw_raw=bw_raw,
                bw_bps=parse_bw_to_bps(bw_raw),
                admin=_slice(line, *_COL_ADMIN).lower(),
                phy=_slice(line, *_COL_PHY).lower(),
                prot=_slice(line, *_COL_PROT).lower(),
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
