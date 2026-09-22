"""One-shot discover for placeholder candidates (VRF list, etc.).

Same NE + discover profile reuses CLI/parse results within a TTL so binding
multiple AF monitor items (vpnv4/vpnv6/VRF) does not re-login the device.
"""

from __future__ import annotations

import threading
import time
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
from .command_match import (
    _record_passes_discover_filter,
    shared_discover_placeholders,
)
from .parsers import get_parser, run_parser
from .profiles import get_profile

# Process-local cache: (source, ne_id, discover_profile_id) → parsed rows.
# Binding UI opens many collect profiles that share config_vrf / config_bgp_peer.
_CACHE_LOCK = threading.Lock()
_DISCOVER_CACHE: dict[str, dict[str, Any]] = {}
_DISCOVER_CACHE_TTL_SEC = 1800  # 30 min — covers a typical bind session


def _cache_key(source: str, ne_id: str, discover_profile_id: str) -> str:
    return f"{source}|{ne_id}|{discover_profile_id}"


def clear_discover_cache(
    *,
    source: str = "",
    ne_id: str = "",
    discover_profile_id: str = "",
) -> int:
    """Drop cache entries; empty args clear all. Returns removed count."""
    src = str(source or "").strip().lower()
    nid = str(ne_id or "").strip()
    pid = str(discover_profile_id or "").strip()
    with _CACHE_LOCK:
        if not src and not nid and not pid:
            n = len(_DISCOVER_CACHE)
            _DISCOVER_CACHE.clear()
            return n
        drop = [
            k
            for k, v in _DISCOVER_CACHE.items()
            if (not src or v.get("source") == src)
            and (not nid or v.get("ne_id") == nid)
            and (not pid or v.get("discover_profile_id") == pid)
        ]
        for k in drop:
            _DISCOVER_CACHE.pop(k, None)
        return len(drop)


def _cache_get(key: str) -> dict[str, Any] | None:
    now = time.time()
    with _CACHE_LOCK:
        hit = _DISCOVER_CACHE.get(key)
        if not hit:
            return None
        if float(hit.get("expires_at") or 0) <= now:
            _DISCOVER_CACHE.pop(key, None)
            return None
        return dict(hit)


def _cache_put(
    key: str,
    *,
    source: str,
    ne_id: str,
    discover_profile_id: str,
    command: str,
    vendor: str,
    device_type: str,
    records: list[dict[str, Any]],
    raw_preview: str,
) -> None:
    with _CACHE_LOCK:
        _DISCOVER_CACHE[key] = {
            "source": source,
            "ne_id": ne_id,
            "discover_profile_id": discover_profile_id,
            "command": command,
            "vendor": vendor,
            "device_type": device_type,
            "records": list(records),
            "raw_preview": raw_preview,
            "expires_at": time.time() + _DISCOVER_CACHE_TTL_SEC,
        }


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
    force_refresh: bool = False,
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
    collect = get_profile(collect_profile_id) if collect_profile_id else None
    pair_phs = shared_discover_placeholders(collect) if collect else []
    active_ph = None
    if collect:
        for ph in collect.placeholders:
            if placeholder and ph.name != placeholder:
                continue
            if ph.discover_value_field:
                value_field = ph.discover_value_field
            if ph.discover_label_field:
                label_field = ph.discover_label_field
            active_ph = ph
            break
        # Pair discover: filter with the first shared placeholder's AF rules.
        if pair_phs and not placeholder:
            active_ph = pair_phs[0]
            value_field = str(active_ph.discover_value_field or active_ph.name or value_field)
            label_field = str(active_ph.discover_label_field or label_field)

    cache_key = _cache_key(src, nid, disc.profile_id)
    cache_hit = False
    vendor = ""
    device_type = ""
    command = str(disc.command_template or "").strip()
    raw = ""
    records: list[dict[str, Any]] = []

    cached = None if force_refresh else _cache_get(cache_key)
    if cached:
        cache_hit = True
        records = list(cached.get("records") or [])
        command = str(cached.get("command") or command)
        vendor = str(cached.get("vendor") or "")
        device_type = str(cached.get("device_type") or "")
        raw = str(cached.get("raw_preview") or "")
    else:
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
        per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
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
                "cache_hit": False,
                "candidates": [],
                "raw_preview": str(raw or "")[:4000],
            }

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
                    "cache_hit": False,
                    "candidates": [],
                    "raw_preview": str(raw or "")[:4000],
                }

        _cache_put(
            cache_key,
            source=src,
            ne_id=nid,
            discover_profile_id=disc.profile_id,
            command=command,
            vendor=vendor,
            device_type=device_type,
            records=records,
            raw_preview=str(raw or "")[:4000],
        )

    candidates = []
    seen: set[str] = set()
    # Shared discover profile → one candidate per (placeholder fields) tuple.
    if pair_phs and len(pair_phs) >= 2:
        filter_ph = pair_phs[0]
        for rec in records:
            if not _record_passes_discover_filter(rec, filter_ph):
                continue
            bind: dict[str, str] = {}
            ok = True
            for ph in pair_phs:
                if not _record_passes_discover_filter(rec, ph):
                    ok = False
                    break
                vf = str(ph.discover_value_field or ph.name or "").strip()
                val = str(rec.get(vf) or "").strip()
                if not val:
                    ok = False
                    break
                bind[ph.name] = val
            if not ok:
                continue
            key = "|".join(f"{k}={bind[k]}" for k in sorted(bind))
            if key in seen:
                continue
            seen.add(key)
            as_num = str(rec.get("remote_as") or "").strip()
            label_parts = [bind.get(ph.name, "") for ph in pair_phs]
            label = " / ".join(p for p in label_parts if p)
            if as_num:
                label = f"{label} (AS {as_num})"
            candidates.append(
                {
                    "value": key,
                    "label": label,
                    "rd": str(rec.get("rd") or ""),
                    "protocols": str(
                        rec.get("protocols")
                        or rec.get("address_families")
                        or rec.get("afi")
                        or ""
                    ),
                    "bindings": bind,
                    "extra": rec,
                }
            )
    else:
        for rec in records:
            if active_ph and not _record_passes_discover_filter(rec, active_ph):
                continue
            val = str(rec.get(value_field) or "").strip()
            if not val or val in seen:
                continue
            seen.add(val)
            label = str(rec.get(label_field) or val).strip() or val
            as_num = str(rec.get("remote_as") or "").strip()
            if as_num and value_field == "neighbor":
                label = f"{label} (AS {as_num})"
            candidates.append(
                {
                    "value": val,
                    "label": label,
                    "rd": str(rec.get("rd") or ""),
                    "protocols": str(
                        rec.get("protocols")
                        or rec.get("address_families")
                        or rec.get("afi")
                        or ""
                    ),
                    "bindings": {str(active_ph.name if active_ph else value_field): val},
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
        "pair_mode": bool(pair_phs and len(pair_phs) >= 2),
        "cache_hit": cache_hit,
        "candidates": candidates,
        "raw_preview": str(raw or "")[:4000],
    }
