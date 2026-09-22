"""ZTE: show bgp {afi} [unicast] [vrf <vrf>] summary (and l2vpn summaries)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_bgp_summary",)

_PEER_LINE_RE = re.compile(
    r"^(?P<nei>\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f:]+)\s+"
    r"(?P<ver>\d+)\s+(?P<asn>\S+)\s+"
    r"(?P<rx>\d+)\s+(?P<tx>\d+)\s+(?P<up>\S+)\s+(?P<state>\S+)\s*$",
    re.I,
)
# Wrapped neighbor may be IPv4 or IPv6 on its own line
_NEI_ONLY_RE = re.compile(
    r"^(?P<nei>\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f:]+)\s*$",
    re.I,
)
_CONT_RE = re.compile(
    r"^\s+(?P<ver>\d+)\s+(?P<asn>\S+)\s+"
    r"(?P<rx>\d+)\s+(?P<tx>\d+)\s+(?P<up>\S+)\s+(?P<state>\S+)\s*$",
    re.I,
)
_VRF_IN_CMD_RE = re.compile(r"(?i)\bvrf\s+(\S+)")


def _detect_bgp_afi(command: str, params: dict[str, str] | None) -> str:
    if params and params.get("afi"):
        return str(params.get("afi") or "").strip().lower()
    low = str(command or "").lower()
    if "l2vpn" in low and "evpn" in low:
        return "evpn"
    if "l2vpn" in low and "vpls" in low:
        return "vpls"
    if "vpnv6" in low:
        return "vpnv6"
    if "vpnv4" in low:
        return "vpnv4"
    if "ipv6" in low:
        return "ipv6"
    if "ipv4" in low:
        return "ipv4"
    return "unknown"


def _detect_vrf(command: str, params: dict[str, str] | None) -> str:
    if params and params.get("vrf"):
        return str(params.get("vrf") or "").strip()
    m = _VRF_IN_CMD_RE.search(str(command or ""))
    return m.group(1).strip() if m else ""


def _state_and_pfx(state_raw: str) -> tuple[str, str]:
    raw = str(state_raw or "").strip()
    if raw.isdigit():
        return "Established", raw
    # e.g. (NoNeg)
    return raw, ""


def _peer_row(
    *,
    afi: str,
    vrf: str,
    nei: str,
    ver: str,
    asn: str,
    rx: str,
    tx: str,
    up: str,
    state_raw: str,
) -> dict[str, Any]:
    state, pfx = _state_and_pfx(state_raw)
    return {
        "afi": afi[:32],
        "vrf": vrf[:128],
        "neighbor": nei[:128],
        "ver": ver[:8],
        "as_num": asn[:16],
        "msg_rcvd": rx[:32],
        "msg_send": tx[:32],
        "up_down": up[:32],
        "state": state[:64],
        "pfx_rcd": pfx[:32],
    }


def _map_fsm_rows(
    rows: list[dict[str, Any]],
    *,
    afi: str,
    vrf: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending = ""
    for r in rows:
        nei = row_get(r, "NEIGHBOR", "neighbor")
        ver = row_get(r, "VER", "ver")
        if nei and not ver:
            pending = nei
            continue
        if not nei and pending and ver:
            nei = pending
            pending = ""
        if not nei or not ver:
            continue
        if "#" in nei or not re.match(
            r"^(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f]*:[0-9A-Fa-f:]+)$",
            nei,
            re.I,
        ):
            continue
        if nei in seen:
            continue
        seen.add(nei)
        state_raw = row_get(r, "STATE_PFX", "STATE", "state", "pfx_rcd")
        out.append(
            _peer_row(
                afi=afi,
                vrf=vrf,
                nei=nei,
                ver=ver,
                asn=row_get(r, "ASN", "AS", "as_num"),
                rx=row_get(r, "MSG_RCVD", "msg_rcvd"),
                tx=row_get(r, "MSG_SEND", "msg_send"),
                up=row_get(r, "UP_DOWN", "up_down"),
                state_raw=state_raw,
            )
        )
    return out


def _hand_parse(
    *,
    raw_text: str,
    afi: str = "unknown",
    vrf: str = "",
    **_kw: Any,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending = ""
    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        low = line.strip().lower()
        if low.startswith("bgp router") or low.startswith("local as") or low.startswith("all "):
            continue
        if low.startswith("neighbor") and "msg" in low:
            continue
        m = _PEER_LINE_RE.match(line.strip())
        if m:
            nei = m.group("nei")
            if nei in seen:
                continue
            seen.add(nei)
            pending = ""
            out.append(
                _peer_row(
                    afi=afi,
                    vrf=vrf,
                    nei=nei,
                    ver=m.group("ver"),
                    asn=m.group("asn"),
                    rx=m.group("rx"),
                    tx=m.group("tx"),
                    up=m.group("up"),
                    state_raw=m.group("state"),
                )
            )
            continue
        m_only = _NEI_ONLY_RE.match(line.strip())
        if m_only:
            pending = m_only.group("nei")
            continue
        m_cont = _CONT_RE.match(line)
        if m_cont and pending:
            nei = pending
            pending = ""
            if nei in seen:
                continue
            seen.add(nei)
            out.append(
                _peer_row(
                    afi=afi,
                    vrf=vrf,
                    nei=nei,
                    ver=m_cont.group("ver"),
                    asn=m_cont.group("asn"),
                    rx=m_cont.group("rx"),
                    tx=m_cont.group("tx"),
                    up=m_cont.group("up"),
                    state_raw=m_cont.group("state"),
                )
            )
    return out


def normalize_bgp_peer(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    afi = _detect_bgp_afi(command, params)
    vrf = _detect_vrf(command, params)
    tables = dict(fsm_tables or {})
    if not any(tables.get(k) for k in RULE_KEYS):
        platform = resolve_cli_platform(
            vendor=vendor,
            device_type=device_type,
            vendor_key=resolve_vendor_key(vendor, device_type),
        )
        cmd = str(command or "show bgp summary").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )

    def _map(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _map_fsm_rows(rows, afi=afi, vrf=vrf)

    def _hand(*, raw_text: str, **kw: Any) -> list[dict[str, Any]]:
        return _hand_parse(raw_text=raw_text, afi=afi, vrf=vrf, **kw)

    return prefer_fsm(tables, RULE_KEYS, _map, _hand, raw_text=raw_text)


normalize_bgp_peer.RULE_KEYS = RULE_KEYS
