"""One-shot discover for placeholder candidates (VRF list, etc.)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..cli_creds import cli_creds_skip_reason
from ..cli_resolve import resolve_cli_target
from ..config import settings
from ..lldp_shared import resolve_vendor_key
from ..models import BizStateTask
from ..ne_netmiko import disable_target_paging, send_show_command
from ..ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .parsers import get_parser, run_parser
from .profiles import get_profile


def resolve_discover_profile(
    *,
    discover_profile_id: str = "",
    collect_profile_id: str = "",
    placeholder: str = "",
) -> Any:
    if discover_profile_id:
        p = get_profile(discover_profile_id)
        if not p:
            raise HTTPException(status_code=404, detail="discover_profile_not_found")
        return p
    collect = get_profile(collect_profile_id)
    if not collect:
        raise HTTPException(status_code=404, detail="collect_profile_not_found")
    ph_name = str(placeholder or "").strip()
    for ph in collect.placeholders:
        if ph_name and ph.name != ph_name:
            continue
        if ph.discover_profile_id:
            disc = get_profile(ph.discover_profile_id)
            if disc:
                return disc
    raise HTTPException(status_code=400, detail="no_discover_profile_for_placeholder")


def discover_params(
    db: Session,
    *,
    source: str = "managed",
    ne_id: str = "",
    task_id: str = "",
    discover_profile_id: str = "",
    collect_profile_id: str = "",
    placeholder: str = "",
) -> dict[str, Any]:
    src = str(source or "managed").strip().lower() or "managed"
    nid = str(ne_id or "").strip()
    if task_id and not nid:
        task = db.get(BizStateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task_not_found")
        src = str(task.source or src)
        nid = str(task.ne_id or "")
    if not nid:
        raise HTTPException(status_code=400, detail="ne_id_required")

    disc = resolve_discover_profile(
        discover_profile_id=discover_profile_id,
        collect_profile_id=collect_profile_id,
        placeholder=placeholder,
    )
    value_field = "vrf_name"
    label_field = "vrf_name"
    if collect_profile_id:
        collect = get_profile(collect_profile_id)
        if collect:
            for ph in collect.placeholders:
                if placeholder and ph.name != placeholder:
                    continue
                if ph.discover_value_field:
                    value_field = ph.discover_value_field
                if ph.discover_label_field:
                    label_field = ph.discover_label_field
                break

    try:
        if src == "managed":
            creds, info = resolve_cli_target(db, managed_ne_id=nid)
        elif src == "ume":
            creds, info = resolve_cli_target(db, ume_ne_id=nid)
        else:
            raise HTTPException(status_code=400, detail="invalid_source")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"resolve_failed: {exc}") from exc

    skip = cli_creds_skip_reason(creds, interactive=False)
    if skip:
        raise HTTPException(status_code=400, detail=skip)

    vendor = str(info.get("vendor") or creds.get("vendor") or "")
    device_type = str(info.get("device_type") or creds.get("device_type") or "")
    command = str(disc.command_template or "").strip()
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    raw = ""
    try:
        conn = open_netmiko_connection(creds, session_timeout=per_cmd + 60)
        try:
            try:
                disable_target_paging(conn, vendor=vendor, device_type=device_type)
            except Exception:
                pass
            raw = send_show_command(conn, command, read_timeout=per_cmd)
        finally:
            close_netmiko_connection(conn)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"cli_failed: {exc}",
            "discover_profile_id": disc.profile_id,
            "command": command,
            "candidates": [],
            "raw_preview": str(raw or "")[:4000],
        }

    records: list[dict[str, Any]] = []
    if get_parser(disc.parser_id):
        try:
            records, _fsm_tables, _rule_keys = run_parser(
                disc.parser_id,
                raw_text=raw,
                vendor=vendor,
                device_type=device_type,
                command=disc.textfsm_command or command,
                params={},
                textfsm_command=disc.textfsm_command or "",
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": f"parse_failed: {exc}",
                "discover_profile_id": disc.profile_id,
                "command": command,
                "candidates": [],
                "raw_preview": str(raw or "")[:4000],
            }

    candidates = []
    seen: set[str] = set()
    for rec in records:
        val = str(rec.get(value_field) or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        label = str(rec.get(label_field) or val).strip() or val
        candidates.append(
            {
                "value": val,
                "label": label,
                "rd": str(rec.get("rd") or ""),
                "protocols": str(rec.get("protocols") or ""),
                "extra": rec,
            }
        )

    return {
        "ok": True,
        "error": "",
        "discover_profile_id": disc.profile_id,
        "command": command,
        "vendor_key": resolve_vendor_key(vendor, device_type),
        "value_field": value_field,
        "candidates": candidates,
        "raw_preview": str(raw or "")[:4000],
    }
