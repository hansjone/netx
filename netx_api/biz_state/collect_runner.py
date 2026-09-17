"""Collect runner: expand task items → CLI → match → parse → batch rows."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from ..cli_creds import cli_creds_skip_reason
from ..cli_resolve import resolve_cli_target
from ..cli_timeout import run_cli_with_timeout
from ..config import settings
from ..db import SessionLocal
from ..lldp_shared import resolve_vendor_key
from ..models import (
    BizStateBatch,
    BizStateBatchCommand,
    BizStateEvent,
    BizStateLldpNeighbor,
    BizStateTask,
    BizStateTaskItem,
    BizStateTaskItemBinding,
)
from ..ne_netmiko import disable_target_paging, send_show_command
from ..ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .command_match import expand_from_bindings, match_command, normalize_command
from .parsers import get_parser
from .profiles import get_profile

_log = logging.getLogger("netx.biz_state.runner")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:1020]


def _append_event(db, *, task_id: str, message: str, level: str = "error") -> None:
    msg = str(message or "").strip()
    if not msg or not task_id:
        return
    db.add(
        BizStateEvent(
            id=uuid4().hex,
            task_id=task_id,
            level=str(level or "error")[:16],
            message=msg[:4000],
            created_at=_utcnow(),
        )
    )


def _bindings_for_item(db, item_id: str) -> list[dict[str, str]]:
    rows = (
        db.query(BizStateTaskItemBinding)
        .filter(BizStateTaskItemBinding.item_id == item_id)
        .all()
    )
    params: dict[str, str] = {}
    for r in rows:
        ph = str(r.placeholder or "").strip()
        val = str(r.value or "").strip()
        if ph and val:
            params[ph] = val
    return [params] if params else []


def _persist_lldp_rows(
    db,
    *,
    batch: BizStateBatch,
    cmd_row: BizStateBatchCommand,
    records: list[dict[str, Any]],
) -> int:
    n = 0
    seen: set[tuple[str, str, str]] = set()
    for rec in records:
        local_if = str(rec.get("local_if") or "").strip()[:128]
        remote_sys = str(rec.get("remote_sys") or "").strip()[:256]
        remote_if = str(rec.get("remote_if") or "").strip()[:128]
        if not local_if and not remote_sys and not remote_if:
            continue
        key = (local_if, remote_sys, remote_if)
        if key in seen:
            continue
        seen.add(key)
        db.add(
            BizStateLldpNeighbor(
                id=uuid4().hex,
                batch_id=batch.id,
                batch_command_id=cmd_row.id,
                task_id=batch.task_id,
                ne_id=batch.ne_id,
                local_if=local_if,
                remote_sys=remote_sys,
                remote_if=remote_if,
                remote_ip=str(rec.get("remote_ip") or "")[:128],
                protocol=str(rec.get("protocol") or "lldp")[:32],
                collected_at=_utcnow(),
            )
        )
        n += 1
    return n


def _finish_task(task_id: str, *, error: str = "") -> None:
    db = SessionLocal()
    try:
        task = db.get(BizStateTask, task_id)
        if not task:
            return
        task.collect_running = False
        task.last_collect_ended_at = _utcnow()
        task.last_error = str(error or "")[:1020]
        task.updated_at = _utcnow()
        if error:
            _append_event(db, task_id=task_id, message=error, level="error")
        db.commit()
    finally:
        db.close()


def dispatch_collect(task_id: str) -> None:
    """Claim and run one collect round."""
    db = SessionLocal()
    batch_id = ""
    try:
        task = db.get(BizStateTask, task_id)
        if not task:
            return
        if task.collect_running:
            return
        if str(task.status or "") not in ("running", "draft", "paused"):
            return

        items = (
            db.query(BizStateTaskItem)
            .filter(
                BizStateTaskItem.task_id == task_id,
                BizStateTaskItem.enabled.is_(True),
            )
            .order_by(BizStateTaskItem.sort_order.asc())
            .all()
        )
        if not items:
            task.last_error = "no enabled task items"
            task.updated_at = _utcnow()
            db.commit()
            return

        task.collect_running = True
        task.last_collect_started_at = _utcnow()
        task.last_error = ""
        task.updated_at = _utcnow()

        batch = BizStateBatch(
            id=uuid4().hex,
            task_id=task.id,
            source=task.source,
            ne_id=task.ne_id,
            ne_name=task.ne_name,
            vendor=task.vendor,
            status="running",
            started_at=_utcnow(),
        )
        db.add(batch)
        db.commit()
        batch_id = batch.id
        vendor = str(task.vendor or "")
        device_type = str(task.device_type or "")
        source = str(task.source or "managed").strip().lower()
        ne_id = str(task.ne_id or "").strip()
        retention = int(task.retention_batches or 30)
    finally:
        db.close()

    if not batch_id:
        return

    error = ""
    try:
        _run_collect_session(
            task_id=task_id,
            batch_id=batch_id,
            source=source,
            ne_id=ne_id,
            vendor=vendor,
            device_type=device_type,
            retention=retention,
        )
    except Exception as exc:
        _log.exception("biz_state collect failed task=%s", task_id)
        error = _format_error(exc)
        db = SessionLocal()
        try:
            batch = db.get(BizStateBatch, batch_id)
            if batch:
                batch.status = "failed"
                batch.message = error
                batch.ended_at = _utcnow()
                db.commit()
        finally:
            db.close()
    finally:
        _finish_task(task_id, error=error)


def _run_collect_session(
    *,
    task_id: str,
    batch_id: str,
    source: str,
    ne_id: str,
    vendor: str,
    device_type: str,
    retention: int,
) -> None:
    per_cmd = int(settings.ne_collect_read_timeout_sec or 120)
    cap = int(settings.ne_collect_run_timeout_cap_sec or 600)

    db = SessionLocal()
    try:
        task = db.get(BizStateTask, task_id)
        batch = db.get(BizStateBatch, batch_id)
        if not task or not batch:
            return

        try:
            if source == "managed":
                creds, info = resolve_cli_target(db, managed_ne_id=ne_id)
            elif source == "ume":
                creds, info = resolve_cli_target(db, ume_ne_id=ne_id)
            else:
                raise RuntimeError("invalid_source")
        except HTTPException as exc:
            raise RuntimeError(str(exc.detail or "resolve_failed")) from exc

        skip = cli_creds_skip_reason(creds, interactive=False)
        if skip:
            raise RuntimeError(skip)

        vendor_eff = str(info.get("vendor") or vendor or "")
        device_type_eff = str(info.get("device_type") or device_type or "")
        if vendor_eff and vendor_eff != task.vendor:
            task.vendor = vendor_eff
        if device_type_eff and device_type_eff != task.device_type:
            task.device_type = device_type_eff
        db.commit()

        vendor_key = resolve_vendor_key(vendor_eff, device_type_eff)

        items = (
            db.query(BizStateTaskItem)
            .filter(
                BizStateTaskItem.task_id == task_id,
                BizStateTaskItem.enabled.is_(True),
            )
            .order_by(BizStateTaskItem.sort_order.asc())
            .all()
        )

        # Build work list before opening session
        work: list[tuple[str, dict[str, str], str, str, str]] = []
        # concrete, params, profile_id, item_id, mode
        for item in items:
            if item.kind == "custom_raw":
                cmd = normalize_command(item.command_override)
                if cmd:
                    work.append((cmd, {}, "", item.id, "custom"))
                continue
            profile = get_profile(item.source_profile_id)
            if profile is None:
                _append_event(
                    db,
                    task_id=task_id,
                    message=f"unknown profile {item.source_profile_id}",
                    level="error",
                )
                continue
            binds = _bindings_for_item(db, item.id)
            try:
                pairs = expand_from_bindings(
                    profile=profile,
                    bindings=binds,
                    command_override=item.command_override,
                )
            except ValueError as exc:
                _append_event(db, task_id=task_id, message=str(exc), level="error")
                continue
            for concrete, params in pairs:
                work.append((concrete, params, profile.profile_id, item.id, "normal"))

        if not work:
            batch.status = "failed"
            batch.message = "no commands to run"
            batch.ended_at = _utcnow()
            db.commit()
            raise RuntimeError("no commands to run")

        budget = min(cap, per_cmd * max(1, len(work)) + 90)
        holder: dict[str, Any] = {}

        def _session() -> tuple[int, int, bool, bool]:
            from ..ne_netmiko import drain_read_channel

            conn = open_netmiko_connection(creds, session_timeout=budget)
            holder["conn"] = conn
            total_rows = 0
            cmd_count = 0
            any_fail = False
            any_ok = False
            try:
                try:
                    disable_target_paging(
                        conn,
                        vendor=str(creds.get("vendor") or vendor_eff or ""),
                        device_type=str(creds.get("device_type") or device_type_eff or ""),
                    )
                except Exception:
                    pass
                try:
                    drain_read_channel(conn)
                except Exception:
                    pass

                sdb = SessionLocal()
                try:
                    batch_row = sdb.get(BizStateBatch, batch_id)
                    if not batch_row:
                        return 0, 0, True, True
                    for concrete, params, profile_id, item_id, mode in work:
                        if holder.get("timed_out"):
                            raise TimeoutError("biz_state_aborted")
                        cmd_count += 1
                        cmd_row = BizStateBatchCommand(
                            id=uuid4().hex,
                            batch_id=batch_id,
                            task_item_id=item_id,
                            profile_id=profile_id,
                            raw_command=concrete[:512],
                            params_json=dict(params or {}),
                            created_at=_utcnow(),
                        )
                        try:
                            raw = send_show_command(conn, concrete, read_timeout=per_cmd)
                            cmd_row.raw_text = str(raw or "")
                        except Exception as exc:
                            any_fail = True
                            cmd_row.parse_status = "failed"
                            cmd_row.message = _format_error(exc)
                            sdb.add(cmd_row)
                            sdb.commit()
                            continue

                        if mode == "custom":
                            cmd_row.parse_status = "skipped_custom"
                            cmd_row.message = "custom_raw"
                            sdb.add(cmd_row)
                            sdb.commit()
                            any_ok = True
                            continue

                        hit = match_command(vendor_key=vendor_key, command=concrete)
                        if not hit:
                            any_fail = True
                            cmd_row.parse_status = "unmatched"
                            cmd_row.message = "no profile matched concrete command"
                            sdb.add(cmd_row)
                            sdb.commit()
                            continue

                        cmd_row.profile_id = hit.profile.profile_id
                        cmd_row.parser_id = hit.profile.parser_id
                        cmd_row.metric_id = hit.profile.metric_id
                        merged = {**params, **hit.params}
                        cmd_row.params_json = merged

                        parser = get_parser(hit.profile.parser_id)
                        if not parser:
                            any_fail = True
                            cmd_row.parse_status = "failed"
                            cmd_row.message = f"unknown parser {hit.profile.parser_id}"
                            sdb.add(cmd_row)
                            sdb.commit()
                            continue

                        try:
                            records = parser(
                                raw_text=cmd_row.raw_text,
                                vendor=vendor_eff,
                                device_type=device_type_eff,
                                command=hit.profile.textfsm_command or concrete,
                                params=merged,
                            )
                        except Exception as exc:
                            any_fail = True
                            cmd_row.parse_status = "failed"
                            cmd_row.message = f"parse: {_format_error(exc)}"
                            sdb.add(cmd_row)
                            sdb.commit()
                            continue

                        n = 0
                        if hit.profile.metric_id == "lldp_neighbor":
                            n = _persist_lldp_rows(
                                sdb, batch=batch_row, cmd_row=cmd_row, records=records
                            )
                        cmd_row.row_count = n
                        cmd_row.parse_status = "ok"
                        total_rows += n
                        any_ok = True
                        sdb.add(cmd_row)
                        sdb.commit()
                finally:
                    sdb.close()
                return total_rows, cmd_count, any_fail, any_ok
            finally:
                holder.pop("conn", None)
                close_netmiko_connection(conn)

        try:
            total_rows, cmd_count, any_fail, any_ok = run_cli_with_timeout(
                _session,
                timeout_sec=budget,
                conn_holder=holder,
                label="biz_state",
                acquire_budget=True,
            )
        except TimeoutError as exc:
            raise RuntimeError(str(exc)[:1020]) from exc

        batch = db.get(BizStateBatch, batch_id)
        if batch:
            batch.command_count = cmd_count
            batch.row_count = total_rows
            batch.ended_at = _utcnow()
            if any_fail and any_ok:
                batch.status = "partial"
            elif any_fail and not any_ok:
                batch.status = "failed"
                batch.message = "all commands failed"
            else:
                batch.status = "success"
            db.commit()
            if batch.status in ("success", "partial"):
                try:
                    from .compare_service import try_auto_compare_for_task

                    try_auto_compare_for_task(db, task_id, batch_id)
                except Exception:
                    _log.exception("biz_state auto compare hook failed task=%s", task_id)

        _purge_old_batches(db, task_id=task_id, keep=retention)
    finally:
        db.close()


def _purge_old_batches(db, *, task_id: str, keep: int) -> None:
    keep_n = max(1, int(keep or 30))
    rows = (
        db.query(BizStateBatch)
        .filter(BizStateBatch.task_id == task_id)
        .order_by(BizStateBatch.started_at.desc())
        .all()
    )
    drop = rows[keep_n:]
    for b in drop:
        bid = b.id
        db.query(BizStateLldpNeighbor).filter(BizStateLldpNeighbor.batch_id == bid).delete()
        db.query(BizStateBatchCommand).filter(BizStateBatchCommand.batch_id == bid).delete()
        db.delete(b)
    if drop:
        db.commit()


def trigger_collect_now(task_id: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        task = db.get(BizStateTask, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        if task.collect_running:
            raise HTTPException(status_code=409, detail="collect already running")
    finally:
        db.close()
    dispatch_collect(task_id)
    return {"ok": True, "task_id": task_id}
