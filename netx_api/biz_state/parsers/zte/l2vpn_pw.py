"""ZTE: show l2vpn forwardinfo (PW state)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_l2vpn_forwardinfo",)

_PW_RE = re.compile(
    r"^(?P<name>\S+)\s+(?P<peer>\S+)\s+(?P<fec>\S+)\s+(?P<pwt>\S+)\s+"
    r"(?:(?P<mode>[A-Za-z$]+)\s+)?"
    r"(?P<state>UP|DOWN)\s+(?P<ll>\S+)\s+(?P<rl>\S+)\s+(?P<owner>\S+)\s*$",
    re.I,
)


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        name = row_get(r, "PW_NAME", "pw_name")
        if not name or name.lower() == "pwname" or name in seen:
            continue
        seen.add(name)
        state = row_get(r, "STATE", "state")
        # FSM may glue mode into pw_type
        pwt = row_get(r, "PW_TYPE", "pw_type")
        out.append(
            {
                "pw_name": name[:128],
                "peer": row_get(r, "PEER", "peer")[:64],
                "fec": row_get(r, "FEC", "fec")[:32],
                "pw_type": pwt[:64],
                "state": state[:32],
                "local_label": row_get(r, "LLABEL", "local_label")[:32],
                "remote_label": row_get(r, "RLABEL", "remote_label")[:32],
                "vpn_owner": row_get(r, "VPN_OWNER", "vpn_owner")[:256],
            }
        )
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in str(raw_text or "").splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("pwname"):
            continue
        m = _PW_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        if name in seen:
            continue
        seen.add(name)
        mode = (m.group("mode") or "").strip()
        pwt = m.group("pwt")
        if mode:
            pwt = f"{pwt} {mode}"
        out.append(
            {
                "pw_name": name[:128],
                "peer": m.group("peer")[:64],
                "fec": m.group("fec")[:32],
                "pw_type": pwt[:64],
                "state": m.group("state")[:32],
                "local_label": m.group("ll")[:32],
                "remote_label": m.group("rl")[:32],
                "vpn_owner": m.group("owner")[:256],
            }
        )
    return out


def normalize_l2vpn_pw(
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
        cmd = str(command or "show l2vpn forwardinfo").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_l2vpn_pw.RULE_KEYS = RULE_KEYS
