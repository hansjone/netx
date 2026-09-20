"""ZTE: show l2vpn forwardinfo detail (per-PW VPLS/VPWS block)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ....lldp_shared import resolve_vendor_key
from ....ntc_parse import apply_rules, resolve_cli_platform, row_get
from ..common.pipeline import prefer_fsm

RULE_KEYS = ("zte_zxros_show_l2vpn_forwardinfo_detail",)

_SERVICE_RE = re.compile(r"^Service type and instance name:\[([^\]]+)\]\s*$", re.I)
# Two-column "Label : value   Label : value" or single "Label : value"
_KV_PAIR_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 /|_.-]*)\s*:\s*"
    r"(\S.*?)"
    r"(?=\s{2,}[A-Za-z][A-Za-z0-9 /|_.-]*\s*:|\s*$)"
)


def _norm_label(label: str) -> str:
    return re.sub(r"\s+", " ", str(label or "").strip()).lower()


_LABEL_MAP = {
    "peer ip address": "peer",
    "vcid": "vcid",
    "connection mode": "conn_mode",
    "vcid extend": "vcid_ext",
    "signaling protocol": "signaling",
    "vc type": "vc_type",
    "last status change time": "last_change",
    "create time": "create_time",
    "mpls vc local label": "local_label",
    "remote label": "remote_label",
    "pw name": "pw_name",
    "control word": "control_word",
    "activation status": "activation_status",
    "band width": "bandwidth",
    "tunnel destination": "tunnel_dest",
    "related interface name": "related_if",
    "frr type": "frr_type",
    "vc status": "vc_status",
    "remote status": "remote_status",
    "mc selection": "mc_selection",
    "vccv cc type": "vccv_cc",
    "vccv cv type": "vccv_cv",
}


def _empty() -> dict[str, str]:
    return {
        "service_instance": "",
        "pw_name": "",
        "peer": "",
        "vcid": "",
        "conn_mode": "",
        "vcid_ext": "",
        "signaling": "",
        "vc_type": "",
        "last_change": "",
        "create_time": "",
        "local_label": "",
        "remote_label": "",
        "control_word": "",
        "activation_status": "",
        "bandwidth": "",
        "tunnel_dest": "",
        "related_if": "",
        "frr_type": "",
        "vc_status": "",
        "remote_status": "",
        "mc_selection": "",
        "vccv_cc": "",
        "vccv_cv": "",
    }


def _row_out(cur: dict[str, str]) -> dict[str, Any] | None:
    pw = str(cur.get("pw_name") or "").strip()
    if not pw:
        return None
    return {
        "service_instance": str(cur.get("service_instance") or "")[:256],
        "pw_name": pw[:128],
        "peer": str(cur.get("peer") or "")[:64],
        "vcid": str(cur.get("vcid") or "")[:64],
        "conn_mode": str(cur.get("conn_mode") or "")[:32],
        "vcid_ext": str(cur.get("vcid_ext") or "")[:32],
        "signaling": str(cur.get("signaling") or "")[:32],
        "vc_type": str(cur.get("vc_type") or "")[:32],
        "last_change": str(cur.get("last_change") or "")[:64],
        "create_time": str(cur.get("create_time") or "")[:64],
        "local_label": str(cur.get("local_label") or "")[:32],
        "remote_label": str(cur.get("remote_label") or "")[:32],
        "control_word": str(cur.get("control_word") or "")[:32],
        "activation_status": str(cur.get("activation_status") or "")[:32],
        "bandwidth": str(cur.get("bandwidth") or "")[:64],
        "tunnel_dest": str(cur.get("tunnel_dest") or "")[:64],
        "related_if": str(cur.get("related_if") or "")[:128],
        "frr_type": str(cur.get("frr_type") or "")[:32],
        "vc_status": str(cur.get("vc_status") or "")[:32],
        "remote_status": str(cur.get("remote_status") or "")[:32],
        "mc_selection": str(cur.get("mc_selection") or "")[:32],
        "vccv_cc": str(cur.get("vccv_cc") or "")[:64],
        "vccv_cv": str(cur.get("vccv_cv") or "")[:64],
    }


def _map_fsm_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        cur = _empty()
        cur["service_instance"] = row_get(r, "SERVICE_INSTANCE", "service_instance")
        cur["pw_name"] = row_get(r, "PW_NAME", "pw_name")
        cur["peer"] = row_get(r, "PEER", "peer")
        cur["vcid"] = row_get(r, "VCID", "vcid")
        cur["conn_mode"] = row_get(r, "CONN_MODE", "conn_mode")
        cur["vcid_ext"] = row_get(r, "VCID_EXT", "vcid_ext")
        cur["signaling"] = row_get(r, "SIGNALING", "signaling")
        cur["vc_type"] = row_get(r, "VC_TYPE", "vc_type")
        cur["last_change"] = row_get(r, "LAST_CHANGE", "last_change")
        cur["create_time"] = row_get(r, "CREATE_TIME", "create_time")
        cur["local_label"] = row_get(r, "LOCAL_LABEL", "local_label")
        cur["remote_label"] = row_get(r, "REMOTE_LABEL", "remote_label")
        cur["control_word"] = row_get(r, "CONTROL_WORD", "control_word")
        cur["activation_status"] = row_get(r, "ACTIVATION", "activation_status")
        cur["bandwidth"] = row_get(r, "BANDWIDTH", "bandwidth")
        cur["tunnel_dest"] = row_get(r, "TUNNEL_DEST", "tunnel_dest")
        cur["related_if"] = row_get(r, "RELATED_IF", "related_if")
        cur["frr_type"] = row_get(r, "FRR_TYPE", "frr_type")
        cur["vc_status"] = row_get(r, "VC_STATUS", "vc_status")
        cur["remote_status"] = row_get(r, "REMOTE_STATUS", "remote_status")
        cur["mc_selection"] = row_get(r, "MC_SELECTION", "mc_selection")
        cur["vccv_cc"] = row_get(r, "VCCV_CC", "vccv_cc")
        cur["vccv_cv"] = row_get(r, "VCCV_CV", "vccv_cv")
        row = _row_out(cur)
        if not row or not str(row.get("vc_status") or "").strip():
            continue
        key = f"{row['pw_name']}|{row['peer']}|{row['vcid']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _hand_parse(*, raw_text: str, **_kw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur: dict[str, str] | None = None

    def _flush() -> None:
        nonlocal cur
        if not cur:
            return
        row = _row_out(cur)
        cur = None
        if not row:
            return
        key = f"{row['pw_name']}|{row['peer']}|{row['vcid']}"
        if key in seen:
            return
        seen.add(key)
        out.append(row)

    for raw in str(raw_text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        sm = _SERVICE_RE.match(line.strip())
        if sm:
            _flush()
            cur = _empty()
            cur["service_instance"] = sm.group(1).strip()
            continue
        if cur is None:
            continue
        for m in _KV_PAIR_RE.finditer(line):
            field = _LABEL_MAP.get(_norm_label(m.group(1)))
            if not field:
                continue
            cur[field] = m.group(2).strip()
    _flush()
    return out


def normalize_l2vpn_pw_detail(
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
        cmd = str(command or "show l2vpn forwardinfo detail").strip()
        if platform and cmd:
            tables = apply_rules(
                platform=platform, text=raw_text, rule_keys=RULE_KEYS, command=cmd
            )
    return prefer_fsm(tables, RULE_KEYS, _map_fsm_rows, _hand_parse, raw_text=raw_text)


normalize_l2vpn_pw_detail.RULE_KEYS = RULE_KEYS
