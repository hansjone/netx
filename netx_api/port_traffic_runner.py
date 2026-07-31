"""Port traffic collection worker: claim task round, sample interfaces via CLI."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from .cli_resolve import resolve_cli_target
from .config import settings
from .db import SessionLocal
from .models import PortTrafficSample, PortTrafficTarget, PortTrafficTask
from .ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .port_traffic_commands import commands_for_vendor, detail_command
from .port_traffic_parsers import parse_zte_interface_detail

_log = logging.getLogger("netx.port_traffic.runner")
_pools: dict[str, ThreadPoolExecutor] = {}
_pools_lock = Lock()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:1020]


def _pool_for_task(task_id: str, concurrency: int) -> ThreadPoolExecutor:
    with _pools_lock:
        pool = _pools.get(task_id)
        if pool is None:
            workers = max(1, min(20, int(concurrency or 5)))
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"pt-{task_id[:8]}")
            _pools[task_id] = pool
        return pool


def _release_pool(task_id: str) -> None:
    with _pools_lock:
        pool = _pools.pop(task_id, None)
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=False)
        except TypeError:
            pool.shutdown(wait=False)
        except Exception:
            _log.exception("port_traffic pool shutdown failed task=%s", task_id)


def _set_target_error(target_row_id: str, message: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(PortTrafficTarget, target_row_id)
        if row:
            row.last_error = message[:1020]
            db.commit()
    finally:
        db.close()


def _claim_collect_round(task_id: str) -> list[str] | None:
    """Mark task collect_running and return active target row ids, or None if skip."""
    for attempt in range(8):
        db = SessionLocal()
        try:
            task = db.get(PortTrafficTask, task_id)
            if not task:
                return None
            if str(task.status or "") != "running":
                return None
            if bool(task.collect_running):
                return None
            ended = task.last_collect_ended_at
            interval = max(15, int(task.interval_sec or 60))
            if ended is not None:
                elapsed = (_utcnow() - ended).total_seconds()
                if elapsed < interval:
                    return None
            targets = (
                db.query(PortTrafficTarget)
                .filter(
                    PortTrafficTarget.task_id == task_id,
                    PortTrafficTarget.status == "active",
                )
                .all()
            )
            if not targets:
                return None
            task.collect_running = True
            task.last_collect_started_at = _utcnow()
            task.last_error = ""
            task.updated_at = _utcnow()
            db.commit()
            return [str(t.id) for t in targets]
        except Exception:
            db.rollback()
            _log.exception("port_traffic claim failed task=%s attempt=%s", task_id, attempt)
            time.sleep(0.05 * (attempt + 1))
        finally:
            db.close()
    return None


def _finish_collect_round(task_id: str, *, error: str = "") -> None:
    db = SessionLocal()
    try:
        task = db.get(PortTrafficTask, task_id)
        if not task:
            return
        task.collect_running = False
        task.last_collect_ended_at = _utcnow()
        if error:
            task.last_error = error[:1020]
        task.updated_at = _utcnow()
        db.commit()
    finally:
        db.close()
    _release_pool(task_id)


def _run_show(creds: dict[str, Any], command: str, read_timeout: int) -> str:
    conn = open_netmiko_connection(creds, session_timeout=read_timeout + 60)
    try:
        return str(conn.send_command(command_string=command, read_timeout=read_timeout) or "")
    finally:
        close_netmiko_connection(conn)


def _sample_one_target(target_row_id: str) -> None:
    creds: dict[str, Any] | None = None
    cmd = ""
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    cap = int(settings.ne_collect_run_timeout_cap_sec or 600)

    db = SessionLocal()
    try:
        row = db.get(PortTrafficTarget, target_row_id)
        if not row or str(row.status or "") != "active":
            return
        source = str(row.source or "").strip().lower()
        target_id = str(row.target_id or "").strip()
        ifname = str(row.ifname or "").strip()
        vendor_hint = str(row.vendor or "")
        try:
            if source == "managed":
                creds, device = resolve_cli_target(db, managed_ne_id=target_id)
            elif source == "ume":
                creds, device = resolve_cli_target(db, ume_ne_id=target_id)
            else:
                row.last_error = "invalid_source"
                db.commit()
                return
        except HTTPException as exc:
            row.last_error = str(exc.detail or "resolve_failed")[:1020]
            db.commit()
            return
        except Exception as exc:
            row.last_error = _format_error(exc)
            db.commit()
            return

        vendor = str(device.get("vendor") or vendor_hint or "")
        device_type = str(device.get("device_type") or "")
        cmds = commands_for_vendor(vendor, device_type)
        if cmds is None:
            row.last_error = "unsupported_vendor"
            db.commit()
            return
        cmd = detail_command(cmds, ifname)
    finally:
        db.close()

    if not creds or not cmd:
        return

    budget = min(cap, per_cmd + 90)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run_show, creds, cmd, per_cmd)
            raw = fut.result(timeout=budget)
    except Exception as exc:
        _set_target_error(target_row_id, _format_error(exc))
        return

    parsed = parse_zte_interface_detail(raw)
    if parsed.in_bps == 0 and parsed.out_bps == 0 and parsed.bw_bps == 0 and not parsed.ifname:
        _set_target_error(target_row_id, "parse_empty")
        return

    now = _utcnow()
    db = SessionLocal()
    try:
        row = db.get(PortTrafficTarget, target_row_id)
        if not row:
            return
        bw = int(parsed.bw_bps or row.bw_bps or 0)
        if bw and not row.bw_bps:
            row.bw_bps = bw
        db.add(
            PortTrafficSample(
                id=uuid4().hex,
                target_row_id=target_row_id,
                ts=now,
                in_bps=float(parsed.in_bps),
                out_bps=float(parsed.out_bps),
                in_util_pct=float(parsed.in_util_pct),
                out_util_pct=float(parsed.out_util_pct),
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


def dispatch_collect(task_id: str) -> int:
    """Claim and sample all active targets for a running task. Returns target count."""
    target_ids = _claim_collect_round(task_id)
    if not target_ids:
        return 0

    db = SessionLocal()
    try:
        task = db.get(PortTrafficTask, task_id)
        concurrency = int(task.concurrency or 5) if task else 5
    finally:
        db.close()

    pool = _pool_for_task(task_id, concurrency)
    futures = [pool.submit(_sample_one_target, tid) for tid in target_ids]
    errors = 0
    try:
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:
                errors += 1
                _log.exception("port_traffic target worker failed task=%s", task_id)
    finally:
        err_msg = f"{errors}_target_errors" if errors else ""
        _finish_collect_round(task_id, error=err_msg)
    return len(target_ids)
