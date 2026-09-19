"""ZTE config intent: ``show running-config l2vpn`` / MIM ``!<l2vpn>``.

One row per ``pseudo-wire`` / ``backup-pw`` under ``vpws`` / ``vpls``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_VPN_RE = re.compile(r'^\s*(vpws|vpls)\s+("?)([^"\s]+)\2\s*$', re.I)
_DESC_RE = re.compile(r"^\s*description\s+(.*?)\s*$", re.I)
_AP_RE = re.compile(r"^\s*access-point\s+(\S+)\s*$", re.I)
_PW_RE = re.compile(r"^\s*pseudo-wire\s+(\S+)", re.I)
_BACKUP_PW_RE = re.compile(r"^\s*backup-pw\s+(\S+)", re.I)
_NEI_RE = re.compile(
    r"^\s*neighbou?r\s+(\S+)\s+vcid\s+(\S+)\s*$",
    re.I,
)
_ENC_RE = re.compile(r"^\s*encapsulation\s+(\S+)\s*$", re.I)


def _flush_pw(
    cur_vpn: dict[str, Any] | None,
    cur_pw: dict[str, Any] | None,
    out: list[dict[str, Any]],
    seen: set[tuple[str, str]],
) -> None:
    if not cur_vpn or not cur_pw:
        return
    pw = str(cur_pw.get("pw_name") or "")
    vpn = str(cur_vpn.get("vpn_name") or "")
    key = (vpn, pw)
    if not pw or not vpn or key in seen:
        return
    seen.add(key)
    out.append(
        {
            "vpn_type": str(cur_vpn.get("vpn_type") or "")[:16],
            "vpn_name": vpn[:128],
            "pw_name": pw[:128],
            "peer": str(cur_pw.get("peer") or "")[:64],
            "vcid": str(cur_pw.get("vcid") or "")[:64],
            "encapsulation": str(cur_pw.get("encapsulation") or "")[:32],
            "access_point": str(cur_vpn.get("access_point") or "")[:128],
            "description": str(cur_vpn.get("description") or "")[:256],
        }
    )


def _start_pw(
    cur_vpn: dict[str, Any] | None,
    cur_pw: dict[str, Any] | None,
    out: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    pw_name: str,
) -> dict[str, Any]:
    _flush_pw(cur_vpn, cur_pw, out, seen)
    return {"pw_name": pw_name.strip(), "peer": "", "vcid": "", "encapsulation": ""}


def normalize_config_l2vpn_pw(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (fsm_tables, vendor, device_type, command, params)
    body = extract_mim_section(raw_text, "l2vpn")
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    cur_vpn: dict[str, Any] | None = None
    cur_pw: dict[str, Any] | None = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = _VPN_RE.match(line)
        if m:
            _flush_pw(cur_vpn, cur_pw, out, seen)
            cur_pw = None
            cur_vpn = {
                "vpn_type": m.group(1).lower(),
                "vpn_name": m.group(3).strip(),
                "description": "",
                "access_point": "",
            }
            continue
        if not cur_vpn:
            continue
        if re.match(r"^\$\s*$", line):
            _flush_pw(cur_vpn, cur_pw, out, seen)
            cur_pw = None
            cur_vpn = None
            continue
        m = _DESC_RE.match(line)
        if m and cur_pw is None:
            cur_vpn["description"] = (m.group(1) or "").strip().strip('"')[:256]
            continue
        m = _AP_RE.match(line)
        if m:
            cur_vpn["access_point"] = m.group(1).strip()
            continue
        m = _PW_RE.match(line)
        if m:
            cur_pw = _start_pw(cur_vpn, cur_pw, out, seen, m.group(1))
            continue
        m = _BACKUP_PW_RE.match(line)
        if m:
            cur_pw = _start_pw(cur_vpn, cur_pw, out, seen, m.group(1))
            continue
        # indented $ closes nested block; flush PW once peer is known so
        # backup-pw / redundancy neighbours do not overwrite the primary.
        if re.match(r"^\s+\$\s*$", line):
            if cur_pw and cur_pw.get("peer"):
                _flush_pw(cur_vpn, cur_pw, out, seen)
                cur_pw = None
            continue
        if not cur_pw:
            continue
        m = _NEI_RE.match(line)
        if m:
            cur_pw["peer"] = m.group(1).strip()
            cur_pw["vcid"] = m.group(2).strip()
            continue
        m = _ENC_RE.match(line)
        if m:
            cur_pw["encapsulation"] = m.group(1).strip()
    _flush_pw(cur_vpn, cur_pw, out, seen)
    return out


normalize_config_l2vpn_pw.RULE_KEYS = RULE_KEYS
