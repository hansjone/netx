"""ZTE: show opticalinfo brief [| one-line].

Power cells look like ``-6.0/[-20.0,-3.0]`` (value / [low,high] threshold).
50G+ (QSFP) modules print one lane per line; continuation lines are merged
into comma-separated ``rx_power`` / ``tx_power`` (and matching thresholds).
400G QSFP-DD may insert a lane-count token (``8X``) between Type and Wavelength;
that token is absorbed into ``wavelength`` so Rx/Tx columns stay aligned.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_opticalinfo_brief",)

# value/[low,high]  or bare N/A|Unsupported|number (no threshold)
_CELL_RE = re.compile(
    r"^(?P<val>N/A|Unsupported|-?\d+(?:\.\d+)?)(?:/(?P<th>\[[^\]]+\]))?$",
    re.I,
)
_HDR_RE = re.compile(
    r"^(?P<iface>\S+)\s+(?P<otype>offline)\s*$|"
    r"^(?P<iface2>\S+)\s+(?P<otype2>\S+)\s+(?:(?P<lanes>\d+X)\s+)?(?P<wave>\S+)\s+"
    r"(?P<rx>\S+)\s+(?P<tx>\S+)\s+(?P<status>\S+)(?:\s+(?P<intensity>\S+))?\s*$",
    re.I,
)
# Lane continuation (indented); require a status token so bare "7.5 7.7" summary is skipped.
_CONT_RE = re.compile(
    r"^\s+(?P<rx>\S+)\s+(?P<tx>\S+)\s+(?P<status>\S+)(?:\s+(?P<intensity>\S+))?\s*$",
    re.I,
)


def _parse_cell(raw: str) -> tuple[str, str] | None:
    s = str(raw or "").strip()
    if not s:
        return None
    m = _CELL_RE.match(s)
    if not m:
        return None
    return (m.group("val"), m.group("th") or "")


def _join(parts: list[str], *, limit: int = 128) -> str:
    return ",".join(p for p in parts if p != "")[:limit]


def _dedupe_join(parts: list[str], *, limit: int = 128) -> str:
    """Join thresholds; collapse when every lane shares the same bracket."""
    vals = [p for p in parts if p]
    if not vals:
        return ""
    if len(set(vals)) == 1:
        return vals[0][:limit]
    return _join(vals, limit=limit)


def _empty_row(iface: str, optic_type: str = "", wavelength: str = "") -> dict[str, Any]:
    return {
        "interface": iface[:128],
        "optic_type": optic_type[:64],
        "wavelength": re.sub(r"\s+", " ", str(wavelength or "").strip())[:32],
        "rx_power": "",
        "rx_threshold": "",
        "tx_power": "",
        "tx_threshold": "",
        "status": "",
        "_rx": [],
        "_rx_th": [],
        "_tx": [],
        "_tx_th": [],
        "_status": [],
    }


def _append_lane(row: dict[str, Any], rx_raw: str, tx_raw: str, status: str = "") -> bool:
    rx = _parse_cell(rx_raw)
    tx = _parse_cell(tx_raw)
    if rx is None or tx is None:
        return False
    # Skip bare numeric summary lines (no threshold on either side, no status).
    if not rx[1] and not tx[1] and not status:
        return False
    row["_rx"].append(rx[0])
    row["_rx_th"].append(rx[1])
    row["_tx"].append(tx[0])
    row["_tx_th"].append(tx[1])
    if status:
        row["_status"].append(status)
    return True


def _finalize(row: dict[str, Any]) -> dict[str, Any]:
    statuses = row.pop("_status", [])
    rx = row.pop("_rx", [])
    rx_th = row.pop("_rx_th", [])
    tx = row.pop("_tx", [])
    tx_th = row.pop("_tx_th", [])
    row["rx_power"] = _join(rx)
    row["rx_threshold"] = _dedupe_join(rx_th)
    row["tx_power"] = _join(tx)
    row["tx_threshold"] = _dedupe_join(tx_th)
    if statuses:
        # Prefer first Abnormal/Unknown if any, else first status.
        bad = next((s for s in statuses if s.lower() not in ("normal",)), None)
        row["status"] = (bad or statuses[0])[:32]
    return row


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate Filldown lane rows from TextFSM into one row per interface."""
    by: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in rows:
        iface = row_get(r, "INTERFACE", "interface")
        if not iface or iface.lower() == "interface":
            continue
        rx_raw = row_get(r, "RX_POWER", "rx_power")
        tx_raw = row_get(r, "TX_POWER", "tx_power")
        otype = row_get(r, "OPTIC_TYPE", "optic_type")
        wave = row_get(r, "WAVELENGTH", "wavelength")
        # Offline lines: only INTERFACE is new; Filldown may still hold prior type/wave.
        if (not rx_raw and not tx_raw) or otype.lower() == "offline":
            if iface not in by:
                by[iface] = _empty_row(iface, "offline")
                by[iface]["status"] = "offline"
                order.append(iface)
            continue
        if iface not in by:
            by[iface] = _empty_row(iface, otype, wave)
            order.append(iface)
        else:
            cur = by[iface]
            if otype and cur["optic_type"] in ("", "offline"):
                cur["optic_type"] = otype[:64]
            if wave and not cur["wavelength"]:
                cur["wavelength"] = wave[:32]
        _append_lane(
            by[iface],
            rx_raw,
            tx_raw,
            row_get(r, "STATUS", "status"),
        )
    return [_finalize(by[i]) for i in order]


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    def _flush() -> None:
        nonlocal cur
        if cur:
            out.append(_finalize(cur))
        cur = None

    for raw in str(raw_text or "").splitlines():
        if not raw.strip():
            continue
        low = raw.strip().lower()
        if low.startswith("interface") and "type" in low:
            continue
        m = _HDR_RE.match(raw.rstrip())
        if m:
            _flush()
            if m.group("otype") and m.group("otype").lower() == "offline":
                cur = _empty_row(m.group("iface"), "offline")
                cur["status"] = "offline"
                _flush()
                continue
            iface = m.group("iface2") or ""
            wave = m.group("wave") or ""
            lanes = m.group("lanes") or ""
            if lanes:
                wave = f"{lanes} {wave}".strip()
            cur = _empty_row(iface, m.group("otype2") or "", wave)
            _append_lane(cur, m.group("rx") or "", m.group("tx") or "", m.group("status") or "")
            continue
        if cur is None:
            continue
        m = _CONT_RE.match(raw)
        if not m:
            continue
        _append_lane(cur, m.group("rx"), m.group("tx"), m.group("status") or "")
    _flush()
    return out


def normalize_optical_brief(
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
        cmd = str(command or "show opticalinfo brief").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_optical_brief.RULE_KEYS = RULE_KEYS
