"""ZTE config intent: ``show running-config vrf`` / MIM ``!<vrf>``."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .config_common import extract_mim_section, is_secret_line

RULE_KEYS: tuple[str, ...] = ()

_VRF_RE = re.compile(r"^\s*ip\s+vrf\s+(\S+)\s*$", re.I)
_RD_RE = re.compile(r"^\s*rd\s+(\S+)\s*$", re.I)
_DESC_RE = re.compile(r'^\s*description\s+("?)(.*?)\1\s*$', re.I)
_AF_RE = re.compile(r"^\s*address-family\s+(ipv4|ipv6)\s*$", re.I)
_RT_RE = re.compile(r"^\s*route-target\s+(export|import)\s+(\S+)\s*$", re.I)


def _flush(cur: dict[str, Any] | None, out: list[dict[str, Any]], seen: set[str]) -> None:
    if not cur:
        return
    name = str(cur.get("vrf_name") or "")
    if not name or name in seen:
        return
    seen.add(name)
    afs = sorted(cur.get("_afs") or [])
    exports = sorted(cur.get("_rt_export") or [])
    imports = sorted(cur.get("_rt_import") or [])
    out.append(
        {
            "vrf_name": name[:128],
            "rd": str(cur.get("rd") or "")[:64],
            "description": str(cur.get("description") or "")[:256],
            "address_families": ",".join(afs)[:64],
            "rt_export": ",".join(exports)[:512],
            "rt_import": ",".join(imports)[:512],
        }
    )


def normalize_config_vrf(
    *,
    raw_text: str,
    fsm_tables: Mapping[str, list[dict[str, Any]]] | None = None,
    vendor: str = "",
    device_type: str = "",
    command: str = "",
    params: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    _ = (fsm_tables, vendor, device_type, command, params)
    body = extract_mim_section(raw_text, "vrf")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur: dict[str, Any] | None = None
    for raw in body.splitlines():
        line = raw.rstrip()
        if is_secret_line(line):
            continue
        m = _VRF_RE.match(line)
        if m:
            _flush(cur, out, seen)
            cur = {
                "vrf_name": m.group(1).strip(),
                "rd": "",
                "description": "",
                "_afs": set(),
                "_rt_export": set(),
                "_rt_import": set(),
            }
            continue
        if not cur:
            continue
        if line.strip() == "$" and not line.startswith(" "):
            # top-level close for vrf block (indent 0 $)
            if not line.startswith(" ") and not line.startswith("\t"):
                _flush(cur, out, seen)
                cur = None
            continue
        # ZTE uses bare `$` at column 0 to end blocks; nested `$` are indented
        if re.match(r"^\$\s*$", line):
            _flush(cur, out, seen)
            cur = None
            continue
        m = _RD_RE.match(line)
        if m:
            cur["rd"] = m.group(1).strip()
            continue
        m = _DESC_RE.match(line)
        if m:
            cur["description"] = (m.group(2) or "").strip()[:256]
            continue
        m = _AF_RE.match(line)
        if m:
            cur["_afs"].add(m.group(1).lower())
            continue
        m = _RT_RE.match(line)
        if m:
            direction, value = m.group(1).lower(), m.group(2).strip()
            if direction == "export":
                cur["_rt_export"].add(value)
            else:
                cur["_rt_import"].add(value)
    _flush(cur, out, seen)
    return out


normalize_config_vrf.RULE_KEYS = RULE_KEYS
