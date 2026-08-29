"""Port traffic collection worker: claim device round, sample interfaces via one CLI session."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .cli_creds import cli_creds_skip_reason
from .cli_resolve import resolve_cli_target
from .cli_timeout import run_cli_with_timeout
from .config import settings
from .db import SessionLocal
from .models import PortTrafficDevice, PortTrafficEvent, PortTrafficSample, PortTrafficTarget
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .ne_netmiko import send_show_command
from .port_traffic_commands import commands_for_vendor, detail_command
from .port_traffic_parsers import parse_interface_detail, resolve_util_pct

_log = logging.getLogger("netx.port_traffic.runner")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:1020]


def _append_event(
    db,
    *,
    device_id: str,
    message: str,
    level: str = "error",
    target_row_id: str = "",
    ifname: str = "",
) -> None:
    msg = str(message or "").strip()
    if not msg or not device_id:
        return
    db.add(
        PortTrafficEvent(
            id=uuid4().hex,
            device_id=device_id,
            target_row_id=str(target_row_id or ""),
            ifname=str(ifname or ""),
            level=str(level or "error")[:16],
            message=msg[:4000],
            created_at=_utcnow(),
        )
    )


def _set_target_error(target_row_id: str, message: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(PortTrafficTarget, target_row_id)
        if row:
            row.last_error = message[:1020]
            _append_event(
                db,
                device_id=str(row.device_id or ""),
                target_row_id=str(row.id),
                ifname=str(row.ifname or ""),
                message=message,
                level="error",
            )
            db.commit()
    finally:
        db.close()


def _finish_collect_round(device_id: str, *, error: str = "") -> None:
    db = SessionLocal()
    try:
        device = db.get(PortTrafficDevice, device_id)
        if not device:
            return
        device.collect_running = False
        device.last_collect_ended_at = _utcnow()
        if error:
            device.last_error = error[:1020]
            _append_event(db, device_id=device_id, message=error, level="error")
        device.updated_at = _utcnow()
        db.commit()
    finally:
        db.close()


def _claim_collect_round(device_id: str) -> list[str] | None:
    for attempt in range(8):
        db = SessionLocal()
        try:
            device = db.get(PortTrafficDevice, device_id)
            if not device:
                return None
            if str(device.status or "") != "running":
                return None
            if bool(device.collect_running):
                return None
            ended = device.last_collect_ended_at
            interval = max(15, int(device.interval_sec or 60))
            if ended is not None:
                elapsed = (_utcnow() - ended).total_seconds()
                if elapsed < interval:
                    return None
            targets = (
                db.query(PortTrafficTarget)
                .filter(
                    PortTrafficTarget.device_id == device_id,
                    PortTrafficTarget.status == "active",
                )
                .all()
            )
            if not targets:
                return None
            device.collect_running = True
            device.last_collect_started_at = _utcnow()
            device.last_error = ""
            device.updated_at = _utcnow()
            db.commit()
            return [str(t.id) for t in targets]
        except Exception:
            db.rollback()
            _log.exception("port_traffic claim failed device=%s attempt=%s", device_id, attempt)
            time.sleep(0.05 * (attempt + 1))
        finally:
            db.close()
    return None


def _save_sample(target_row_id: str, parsed: Any, vendor_hint_bw: int = 0) -> None:
    now = _utcnow()
    db = SessionLocal()
    try:
        row = db.get(PortTrafficTarget, target_row_id)
        if not row:
            return
        bw = int(parsed.bw_bps or row.bw_bps or vendor_hint_bw or 0)
        if bw and not row.bw_bps:
            row.bw_bps = bw
        in_bps = float(parsed.in_bps)
        out_bps = float(parsed.out_bps)
        if (
            in_bps == 0
            and out_bps == 0
            and bw == 0
            and float(parsed.in_util_pct or 0) == 0
            and float(parsed.out_util_pct or 0) == 0
            and not parsed.ifname
        ):
            row.last_error = "parse_empty"
            _append_event(
                db,
                device_id=str(row.device_id or ""),
                target_row_id=str(row.id),
                ifname=str(row.ifname or ""),
                message="parse_empty",
                level="warn",
            )
            db.commit()
            return
        db.add(
            PortTrafficSample(
                id=uuid4().hex,
                target_row_id=target_row_id,
                series_id=str(row.series_id or ""),
                ts=now,
                in_bps=in_bps,
                out_bps=out_bps,
                in_util_pct=resolve_util_pct(float(parsed.in_util_pct), in_bps, bw),
                out_util_pct=resolve_util_pct(float(parsed.out_util_pct), out_bps, bw),
                bw_bps=bw,
                rate_period_sec=int(parsed.rate_period_sec or 0),
                raw_ok=True,
                message="",
            )
        )
        row.last_error = ""
        row.last_sample_at = now
        db.commit()
    except Exception:
        db.rollback()
        _log.exception("port_traffic sample save failed target=%s", target_row_id)
    finally:
        db.close()


def _sample_targets_shared_session(device_id: str, target_ids: list[str]) -> tuple[int, str]:
    """One CLI login for the device; run show per interface.

    Returns (error_count, device_error). device_error is set when the whole
    round fails for one shared reason (e.g. managed_ne_not_found).
    """
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    cap = int(settings.ne_collect_run_timeout_cap_sec or 600)

    db = SessionLocal()
    try:
        device = db.get(PortTrafficDevice, device_id)
        if not device:
            return len(target_ids), "device_not_found"
        source = str(device.source or "").strip().lower()
        ne_id = str(device.ne_id or "").strip()
        vendor_hint = str(device.vendor or "")
        try:
            if source == "managed":
                creds, info = resolve_cli_target(db, managed_ne_id=ne_id)
            elif source == "ume":
                creds, info = resolve_cli_target(db, ume_ne_id=ne_id)
            else:
                for tid in target_ids:
                    _set_target_error(tid, "invalid_source")
                return len(target_ids), "invalid_source"
        except HTTPException as exc:
            msg = str(exc.detail or "resolve_failed")[:1020]
            for tid in target_ids:
                _set_target_error(tid, msg)
            return len(target_ids), msg
        except Exception as exc:
            msg = _format_error(exc)
            for tid in target_ids:
                _set_target_error(tid, msg)
            return len(target_ids), msg

        skip = cli_creds_skip_reason(creds, interactive=False)
        if skip:
            for tid in target_ids:
                _set_target_error(tid, skip)
            return len(target_ids), skip

        vendor = str(info.get("vendor") or vendor_hint or "")
        device_type = str(info.get("device_type") or "")
        cmds = commands_for_vendor(vendor, device_type)
        if cmds is None:
            for tid in target_ids:
                _set_target_error(tid, "unsupported_vendor")
            return len(target_ids), "unsupported_vendor"
        vendor_key = cmds.vendor_key

        targets = (
            db.query(PortTrafficTarget)
            .filter(PortTrafficTarget.id.in_(target_ids), PortTrafficTarget.status == "active")
            .all()
        )
        ifaces = [(str(t.id), str(t.ifname or "").strip()) for t in targets if t.ifname]
    finally:
        db.close()

    if not ifaces:
        return 0, ""

    budget = min(cap, per_cmd * max(1, len(ifaces)) + 90)
    holder: dict[str, Any] = {}

    def _run_session() -> tuple[int, str]:
        from .ne_netmiko import disable_target_paging

        conn = open_netmiko_connection(creds, session_timeout=budget)
        holder["conn"] = conn
        local_errors = 0
        try:
            try:
                disable_target_paging(
                    conn,
                    vendor=str(creds.get("vendor") or ""),
                    device_type=str(creds.get("device_type") or ""),
                )
            except Exception:
                pass
            for tid, ifname in ifaces:
                if holder.get("timed_out"):
                    raise TimeoutError("port_traffic_aborted")
                try:
                    cmd = detail_command(cmds, ifname)
                    raw = send_show_command(conn, cmd, read_timeout=per_cmd)
                    parsed = parse_interface_detail(
                        raw,
                        vendor_key,
                        command=cmd,
                        ifname=ifname,
                    )
                    _save_sample(tid, parsed)
                except Exception as exc:
                    local_errors += 1
                    _set_target_error(tid, _format_error(exc))
                    _log.exception(
                        "port_traffic iface sample failed device=%s if=%s", device_id, ifname
                    )
            return local_errors, ""
        finally:
            holder.pop("conn", None)
            close_netmiko_connection(conn)

    try:
        # Budget acquired inside run_cli_with_timeout; avoid double-acquire.
        return run_cli_with_timeout(
            _run_session,
            timeout_sec=budget,
            conn_holder=holder,
            label="port_traffic",
            acquire_budget=True,
        )
    except TimeoutError as exc:
        msg = str(exc)[:1020]
        for tid, _ in ifaces:
            _set_target_error(tid, msg)
        return len(ifaces), msg
    except Exception as exc:
        msg = _format_error(exc)
        for tid, _ in ifaces:
            _set_target_error(tid, msg)
        _log.exception("port_traffic session failed device=%s", device_id)
        return len(ifaces), msg


def dispatch_collect(device_id: str) -> int:
    """Claim and sample all active interfaces for a running device. Returns target count."""
    target_ids = _claim_collect_round(device_id)
    if not target_ids:
        return 0

    session_err = ""
    try:
        errors, session_err = _sample_targets_shared_session(device_id, target_ids)
    except Exception:
        errors = len(target_ids)
        session_err = "collect_failed"
        _log.exception("port_traffic collect failed device=%s", device_id)
    finally:
        if session_err and errors:
            err_msg = session_err[:1020]
        else:
            err_msg = f"{errors}_target_errors" if errors else ""
        _finish_collect_round(device_id, error=err_msg)
    return len(target_ids)


# Alias used by older call sites
dispatch_collect_device = dispatch_collect
