"""Collect runner: expand task items → CLI → match → parse → batch rows."""

from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError, OperationalError

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
    BizStateMetricRow,
    BizStateTask,
    BizStateTaskItem,
    BizStateTaskItemBinding,
)
from ..ne_netmiko import disable_target_paging, send_show_command
from ..ne_session_factory import close_netmiko_connection, open_netmiko_connection
from ..timeutil import utcnow_naive
from .collect_session import (
    CachedCommand,
    CollectSession,
    ResolvedAux,
    build_parse_bundle,
    resolve_aux_command,
    run_primary_with_bundle,
)
from .command_match import (
    EXPAND_ALL_COMMAND,
    expand_bindings_from_discover_records,
    expand_from_bindings,
    match_command,
    normalize_command,
)
from .parsers import get_parser, run_parser
from .profiles import get_profile
from .collect_stop import (
    STOP_USER_MESSAGE,
    clear_stop_requested,
    is_stop_requested,
    register_lane_holder,
    unregister_lane_holder,
)

_log = logging.getLogger("netx.biz_state.runner")

# concrete, params, profile_id, item_id, mode
WorkItem = tuple[str, dict[str, str], str, str, str]

_heavy_pool_lock = threading.Lock()
_heavy_pool: ThreadPoolExecutor | None = None


def _heavy_cli_pool() -> ThreadPoolExecutor:
    global _heavy_pool
    with _heavy_pool_lock:
        if _heavy_pool is None:
            n = max(1, int(getattr(settings, "biz_state_heavy_workers", 4) or 4))
            _heavy_pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="biz-heavy")
        return _heavy_pool


def work_item_lane(profile_id: str, mode: str) -> str:
    """Return collect lane for a work item (custom_raw always light)."""
    if str(mode or "").strip() == "custom":
        return "light"
    profile = get_profile(profile_id) or _resolve_collect_profile(profile_id)
    lane = str(getattr(profile, "collect_lane", "light") or "light").strip().lower() if profile else "light"
    return "heavy" if lane == "heavy" else "light"


def partition_work(work: list[WorkItem]) -> tuple[list[WorkItem], list[WorkItem]]:
    """Split work into (light, heavy) lanes."""
    light: list[WorkItem] = []
    heavy: list[WorkItem] = []
    for item in work:
        if work_item_lane(item[2], item[4]) == "heavy":
            heavy.append(item)
        else:
            light.append(item)
    return light, heavy


def _utcnow() -> datetime:
    return utcnow_naive()


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
    # Keep one row per binding value (same placeholder may appear many times).
    return [
        {"placeholder": str(r.placeholder or "").strip(), "value": str(r.value or "").strip()}
        for r in rows
        if str(r.placeholder or "").strip() and str(r.value or "").strip()
    ]


def _resolve_collect_profile(profile_id: str):
    """Resolve task-item profile; remap disabled if_intf → config_interface."""
    pid = str(profile_id or "").strip()
    if not pid:
        return None
    profile = get_profile(pid)
    if profile is None:
        return None
    if profile.enabled:
        return profile
    # Legacy IF VRF check merged into Config Interface Intent — VRF is a subset.
    if profile.metric_id == "if_intf" or pid.endswith(".if_intf"):
        vk = str(profile.vendor_key or "zte").strip() or "zte"
        remapped = get_profile(f"{vk}.config_interface") or get_profile("zte.config_interface")
        if remapped and remapped.enabled:
            return remapped
    return None


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


_GENERIC_METRICS = {
    "isis_adjacency",
    "interface_brief",
    "interface_detail",
    "arp",
    "if_intf",
    "nd6_cache",
    "bgp_peer",
    "ospf_neighbor",
    "vrrp",
    "optical_brief",
    "bgp_route",
    "ip_route",
    "ipv6_route",
    "l2vpn_pw",
    "l2vpn_pw_detail",
    "l2vpn_mac",
    "evpn_mac",
    "config_vrf",
    "config_interface",
    "config_bgp_peer",
    "config_l2vpn_pw",
    "config_static_route",
    "config_ospf",
    "config_isis",
}
_METRIC_CHUNK = 2000


def _run_primary_parse_job(job: Any) -> tuple[bool, bool]:
    """Parse primary+aux raws, write spool, submit persist. Returns (any_ok, any_fail)."""
    from .parse_pool import AuxRawCapture, PrimaryParseJob
    from .persist_pool import get_persist_pool
    from .spool import SpooledCommand, count_text_lines, write_meta, write_records

    if not isinstance(job, PrimaryParseJob):
        return False, True

    batch_id = job.batch_id
    cmd_id = job.cmd_id
    pending: list[SpooledCommand] = []
    any_ok = False
    any_fail = False

    def _declared_total(raw: str) -> int:
        m = re.search(r"(?i)total\s+number\s+of\s+routes\s*:\s*(\d+)", raw or "")
        return int(m.group(1)) if m else 0

    def _flush_item(item: SpooledCommand, *, records: list[dict[str, Any]] | None = None) -> None:
        if records is not None and item.persist_kind:
            item.records_rel_path = write_records(batch_id, item.id, records)
            item.row_count = len(records)
        try:
            write_meta(batch_id, item.id, item.to_meta())
        except Exception:
            _log.exception("biz_state write meta failed cmd=%s", item.id)
        pending.append(item)

    aux_results: dict[str, CachedCommand] = {}
    resolved_aux: list[ResolvedAux] = []

    for cap in list(job.aux_captures or []):
        if not isinstance(cap, AuxRawCapture):
            continue
        from .profiles import get_profile as _gp

        prof = _gp(cap.profile_id)
        entry = CachedCommand(
            raw=cap.raw,
            records=list(cap.records or []),
            fsm_tables=dict(cap.fsm_tables or {}),
            ok=bool(cap.ok),
            error=str(cap.error or ""),
            cmd_row_id=cap.aux_id,
        )
        if entry.ok and not entry.records and cap.parser_id and get_parser(cap.parser_id):
            try:
                records, fsm_tables, _keys = run_parser(
                    cap.parser_id,
                    raw_text=cap.raw,
                    vendor=job.vendor,
                    device_type=job.device_type,
                    command=cap.textfsm_command or cap.command,
                    textfsm_command=cap.textfsm_command or "",
                    params=dict(job.merged_params or {}),
                )
                entry.records = list(records or [])
                entry.fsm_tables = dict(fsm_tables or {})
            except Exception as exc:
                entry.ok = False
                entry.error = f"parse: {type(exc).__name__}: {exc}"

        aux_results[cap.key] = entry
        if prof is not None:
            resolved_aux.append(
                ResolvedAux(
                    key=cap.key,
                    profile_id=cap.profile_id,
                    command=cap.command,
                    textfsm_command=cap.textfsm_command or cap.command,
                    parser_id=cap.parser_id,
                    rule_keys=tuple(cap.rule_keys or ()),
                    profile=prof,
                )
            )

        aux_sp = SpooledCommand(
            id=cap.aux_id,
            batch_id=batch_id,
            task_item_id=job.task_item_id,
            profile_id=cap.profile_id,
            parser_id=cap.parser_id,
            metric_id=cap.metric_id,
            raw_command=cap.command[:512],
            params_json={},
            raw_rel_path=cap.raw_rel_path,
            raw_line_count=count_text_lines(cap.raw),
        )
        if cap.cache_hit and entry.ok and entry.records:
            aux_sp.parse_status = "aux_cached"
            aux_sp.message = (
                f"aux_for={cmd_id};cache_hit;src={entry.cmd_row_id}"
            )[:1020]
            aux_sp.row_count = len(entry.records or [])
        elif not entry.ok:
            aux_sp.parse_status = "aux_failed"
            aux_sp.message = f"aux_for={cmd_id};{entry.error}"[:1020]
            any_fail = True
        else:
            aux_sp.parse_status = "aux"
            aux_sp.message = f"aux_for={cmd_id}"[:1020]
            aux_sp.row_count = len(entry.records or [])

        persist_recs: list[dict[str, Any]] | None = None
        if entry.ok and entry.records and cap.metric_id in _GENERIC_METRICS:
            persist_key = (normalize_command(cap.command), cap.metric_id)
            do_persist = False
            persisted = job.persisted
            if persisted is not None:
                if job.cache_lock is not None:
                    with job.cache_lock:
                        if persist_key not in persisted:
                            persisted.add(persist_key)
                            do_persist = True
                elif persist_key not in persisted:
                    persisted.add(persist_key)
                    do_persist = True
            if do_persist:
                aux_sp.persist_kind = "metric"
                persist_recs = list(entry.records)
        _flush_item(aux_sp, records=persist_recs)

    bundle = build_parse_bundle(
        primary_raw=job.raw_text,
        primary_parser_id=job.parser_id,
        aux_results=aux_results,
        resolved_aux=resolved_aux,
    )
    # Include aux raws that lacked a profile (still needed for multi-raw parsers).
    for cap in list(job.aux_captures or []):
        if not isinstance(cap, AuxRawCapture):
            continue
        if cap.key in bundle.raws:
            continue
        entry = aux_results.get(cap.key) or CachedCommand(ok=False)
        bundle.raws[cap.key] = entry.raw
        bundle.command_rules[cap.key] = list(cap.rule_keys or [])
        if entry.records:
            bundle.aux_records[cap.key] = list(entry.records)
        bundle.fsm_extra.update(entry.fsm_tables or {})
    primary = SpooledCommand(
        id=cmd_id,
        batch_id=batch_id,
        task_item_id=job.task_item_id,
        profile_id=job.profile_id,
        parser_id=job.parser_id,
        metric_id=job.metric_id,
        raw_command=job.concrete[:512],
        params_json=dict(job.merged_params or {}),
        raw_rel_path=job.raw_rel_path,
        raw_line_count=int(getattr(job, "raw_line_count", 0) or 0)
        or count_text_lines(job.raw_text),
        declared_total=_declared_total(job.raw_text),
    )
    try:
        records, fsm_tables, rule_keys = run_primary_with_bundle(
            job.parser_id,
            bundle=bundle,
            vendor=job.vendor,
            device_type=job.device_type,
            command=job.textfsm_command or job.concrete,
            textfsm_command=job.textfsm_command or "",
            params=dict(job.merged_params or {}),
            enrich_joins=list(job.enrich_joins or []),
        )
        hints = []
        if rule_keys:
            nonempty = [k for k in rule_keys if fsm_tables.get(k)]
            hints.append(f"fsm_keys={','.join(rule_keys)};hit={','.join(nonempty)}")
        if bundle.aux_records:
            hints.append(
                "aux="
                + ",".join(f"{k}:{len(v)}" for k, v in bundle.aux_records.items())
            )
        if job.enrich_joins:
            hints.append(
                "enrich=" + ",".join(getattr(j, "from_aux", "") for j in job.enrich_joins)
            )
        declared = int(primary.declared_total or 0)
        nrec = len(records or [])
        if declared > 0:
            hints.append(f"declared={declared};parsed={nrec}")
        if hints:
            primary.message = ";".join(hints)[:1020]
        primary.parse_status = "ok"
        any_ok = True
        persist_recs = None
        if job.metric_id == "lldp_neighbor":
            primary.persist_kind = "lldp"
            persist_recs = list(records or [])
        elif job.metric_id in _GENERIC_METRICS:
            primary.persist_kind = "metric"
            persist_recs = list(records or [])
        _flush_item(primary, records=persist_recs)
        _ = fsm_tables  # kept for hints above
    except Exception as exc:
        any_fail = True
        primary.parse_status = "failed"
        primary.message = f"parse: {_format_error(exc)}"
        _flush_item(primary)

    if pending:
        get_persist_pool().submit(batch_id, pending)
    return any_ok, any_fail


def _emit_task_event(*, task_id: str, message: str, level: str = "error") -> None:
    """Short-lived session for lane events (no long-held DB during CLI)."""
    if not task_id or not str(message or "").strip():
        return
    db = SessionLocal()
    try:
        _append_event(db, task_id=task_id, message=message, level=level)
        db.commit()
    except Exception:
        _log.exception("biz_state emit event failed task=%s", task_id)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _flush_spooled_commands(
    batch_id: str,
    pending: list[Any],
) -> tuple[int, int]:
    """Insert SpooledCommand rows (+ metric/lldp) in one transaction. Returns (cmds, rows)."""
    from .spool import SpooledCommand, raw_max_bytes, read_raw_text, read_records

    if not pending:
        return 0, 0
    items: list[SpooledCommand] = list(pending)
    pending.clear()

    def _write(db) -> tuple[int, int]:
        batch = db.get(BizStateBatch, batch_id)
        if not batch:
            return 0, 0
        rows_n = 0
        max_raw = raw_max_bytes()
        for item in items:
            raw = ""
            truncated = False
            line_count = int(getattr(item, "raw_line_count", 0) or 0)
            if item.raw_rel_path:
                from .spool import count_file_lines

                if line_count <= 0:
                    try:
                        line_count = count_file_lines(item.raw_rel_path)
                    except Exception:
                        line_count = 0
                raw = read_raw_text(item.raw_rel_path, max_bytes=max_raw)
                if max_raw > 0 and "[truncated" in raw:
                    truncated = True
            # Prefer full-file line count; fall back to stored text.
            if line_count <= 0 and raw:
                from .spool import count_text_lines

                line_count = count_text_lines(raw)
            msg = str(item.message or "").strip()
            if truncated:
                note = f"raw_truncated@{max_raw}B"
                msg = f"{msg}; {note}" if msg else note
            cmd_row = BizStateBatchCommand(
                id=item.id,
                batch_id=batch_id,
                task_item_id=item.task_item_id,
                profile_id=item.profile_id,
                parser_id=item.parser_id,
                metric_id=item.metric_id,
                raw_command=str(item.raw_command or "")[:512],
                params_json=dict(item.params_json or {}),
                parse_status=item.parse_status,
                message=msg[:1020],
                raw_text=raw,
                row_count=int(item.row_count or 0),
                raw_line_count=int(line_count or 0),
                declared_total=int(getattr(item, "declared_total", 0) or 0),
                created_at=_utcnow(),
            )
            db.add(cmd_row)
            if item.persist_kind == "metric" and item.records_rel_path:
                records = read_records(item.records_rel_path)
                mid = str(item.metric_id or "").strip()
                if mid and records:
                    n = _persist_metric_rows(
                        db,
                        batch=batch,
                        cmd_row=cmd_row,
                        metric_id=mid,
                        records=records,
                    )
                    cmd_row.row_count = n
                    rows_n += n
            elif item.persist_kind == "lldp" and item.records_rel_path:
                records = read_records(item.records_rel_path)
                if records:
                    n = _persist_lldp_rows(
                        db, batch=batch, cmd_row=cmd_row, records=records
                    )
                    cmd_row.row_count = n
                    rows_n += n
        db.commit()
        return len(items), rows_n

    try:
        cmds, rows = _run_db_with_reconnect(_write, label="biz_state_flush_spool")
        if cmds or rows:
            _bump_batch_progress(batch_id, add_cmds=cmds, add_rows=rows)
        return int(cmds or 0), int(rows or 0)
    except Exception:
        _log.exception("biz_state flush spool failed batch=%s n=%s", batch_id, len(items))
        # Put back so a later flush / finalize can retry.
        pending.extend(items)
        raise


def _persist_metric_rows(
    db,
    *,
    batch: BizStateBatch,
    cmd_row: BizStateBatchCommand,
    metric_id: str,
    records: list[dict[str, Any]],
) -> int:
    """Bulk-insert generic metric rows (JSON payload per row)."""
    mid = str(metric_id or "").strip()
    if not mid or not records:
        return 0
    buf: list[dict[str, Any]] = []
    n = 0
    for i, rec in enumerate(records):
        if not isinstance(rec, dict) or not rec:
            continue
        buf.append(
            {
                "id": uuid4().hex,
                "batch_id": batch.id,
                "batch_command_id": cmd_row.id,
                "task_id": batch.task_id,
                "ne_id": batch.ne_id,
                "metric_id": mid,
                "seq": i,
                "data_json": dict(rec),
                "collected_at": _utcnow(),
            }
        )
        n += 1
        if len(buf) >= _METRIC_CHUNK:
            db.bulk_insert_mappings(BizStateMetricRow, buf)
            buf.clear()
    if buf:
        db.bulk_insert_mappings(BizStateMetricRow, buf)
    return n


def _finish_task(task_id: str, *, error: str = "") -> None:
    db = SessionLocal()
    try:
        task = db.get(BizStateTask, task_id)
        if not task:
            return
        task.collect_running = False
        if hasattr(task, "collect_queued_at"):
            task.collect_queued_at = None
        task.last_collect_ended_at = _utcnow()
        task.last_error = str(error or "")[:1020]
        task.updated_at = _utcnow()
        if error:
            _append_event(db, task_id=task_id, message=error, level="error")
        db.commit()
    finally:
        db.close()


def dispatch_collect(task_id: str, *, manual: bool = False) -> dict[str, Any]:
    """Enqueue a collect round; run inline when this process owns execution.

    Dedicated worker mode: only enqueue (claim loop runs the batch).
    Inline / non-dedicated: enqueue then atomically promote+execute (skip if
    another worker already claimed the batch).

    Returns the enqueue result dict (ok / queued / reason / batch_id / …).
    """
    from .claim import enqueue_collect

    result = enqueue_collect(task_id, manual=manual)
    if not result.get("queued"):
        return result
    batch_id = str(result.get("batch_id") or "")
    if not batch_id:
        return result
    execute_enqueued_batch(
        batch_id=batch_id,
        task_id=str(result.get("task_id") or task_id),
    )
    return result


def execute_enqueued_batch(*, batch_id: str, task_id: str) -> None:
    """Promote+run a queued batch when this process owns inline execution."""
    bid = str(batch_id or "").strip()
    if not bid:
        return
    if not _should_execute_inline():
        return
    # Atomic queued→running; if false, scheduler/worker already owns it.
    if not _try_claim_batch_for_execute(bid):
        return
    execute_claimed_batch(
        batch_id=bid,
        task_id=str(task_id or ""),
        source="",
        ne_id="",
        vendor="",
        device_type="",
    )


def _should_execute_inline() -> bool:
    """True when this process should run SSH after enqueue (not dedicated workers)."""
    if bool(getattr(settings, "run_inline_schedulers", True)):
        return True
    if not bool(getattr(settings, "biz_state_dedicated_workers", True)):
        return True
    return False


def _try_claim_batch_for_execute(batch_id: str) -> bool:
    """Promote queued→running only if still queued. Returns True iff we won the claim."""
    db = SessionLocal()
    try:
        batch = (
            db.query(BizStateBatch)
            .filter(BizStateBatch.id == batch_id)
            .with_for_update()
            .one_or_none()
        )
        if not batch or str(batch.status or "") != "queued":
            return False
        batch.status = "running"
        batch.message = ""
        db.commit()
        return True
    except Exception:
        _log.exception("biz_state claim-for-execute failed batch=%s", batch_id)
        try:
            db.rollback()
        except Exception:
            pass
        return False
    finally:
        db.close()


def _promote_queued_batch(batch_id: str) -> bool:
    """Backward-compatible alias for atomic claim. """
    return _try_claim_batch_for_execute(batch_id)

def execute_claimed_batch(
    *,
    batch_id: str,
    task_id: str,
    source: str = "",
    ne_id: str = "",
    vendor: str = "",
    device_type: str = "",
) -> None:
    """Run CLI+persist for an already-claimed (status=running) batch."""
    db = SessionLocal()
    try:
        batch = db.get(BizStateBatch, batch_id)
        task = db.get(BizStateTask, task_id) if task_id else None
        if not batch:
            return
        if not task_id:
            task_id = str(batch.task_id or "")
            task = db.get(BizStateTask, task_id) if task_id else None
        source = str(source or batch.source or (task.source if task else "") or "managed")
        ne_id = str(ne_id or batch.ne_id or (task.ne_id if task else "") or "")
        vendor = str(vendor or batch.vendor or (task.vendor if task else "") or "")
        device_type = str(device_type or (task.device_type if task else "") or "")
        if not task_id:
            return
    finally:
        db.close()

    error = ""
    try:
        if is_stop_requested(batch_id):
            _finalize_batch_status(
                batch_id=batch_id,
                task_id=task_id,
                cmd_count=0,
                total_rows=0,
                any_fail=False,
                any_ok=False,
                lane_errors=[],
                stopped=True,
            )
            error = STOP_USER_MESSAGE
        else:
            _run_collect_session(
                task_id=task_id,
                batch_id=batch_id,
                source=source,
                ne_id=ne_id,
                vendor=vendor,
                device_type=device_type,
            )
    except Exception as exc:
        _log.exception("biz_state collect failed task=%s batch=%s", task_id, batch_id)
        error = _format_error(exc)
        if "_stopped" in error or is_stop_requested(batch_id):
            error = STOP_USER_MESSAGE
        try:
            _fail_batch_status(batch_id, error)
        except Exception:
            _log.exception(
                "biz_state fail-batch after collect error failed batch=%s", batch_id
            )
    finally:
        _finish_task(task_id, error=error)


def _run_collect_lane(
    *,
    work: list[WorkItem],
    batch_id: str,
    creds: dict[str, Any],
    vendor_eff: str,
    device_type_eff: str,
    vendor_key: str,
    per_cmd: int,
    cap: int,
    label: str,
    shared_cache: dict[str, Any] | None = None,
    cache_lock: Any | None = None,
    cmd_locks: dict[str, Any] | None = None,
    aux_persisted: set[tuple[str, str]] | None = None,
) -> tuple[int, int, bool, bool]:
    """Run one SSH lane: collect+parse to spool, flush to DB in batches."""
    if not work:
        return 0, 0, False, False

    from .spool import (
        SpooledCommand,
        persist_every_cmds,
        write_meta,
        write_raw_text,
        write_records,
    )

    budget = min(int(cap), int(per_cmd) * max(1, len(work)) + 90)
    holder: dict[str, Any] = {}
    flush_every = persist_every_cmds()
    register_lane_holder(batch_id, holder)

    def _session() -> tuple[int, int, bool, bool]:
        from ..ne_netmiko import drain_read_channel

        if is_stop_requested(batch_id):
            raise TimeoutError(f"{label}_stopped")

        conn = open_netmiko_connection(creds, session_timeout=budget)
        holder["conn"] = conn
        total_rows = 0
        cmd_count = 0
        any_fail = False
        any_ok = False
        pending: list[SpooledCommand] = []
        task_id = ""
        from .parse_pool import (
            AuxRawCapture,
            PrimaryParseJob,
            get_parse_pool,
            parse_async_enabled,
        )
        from .persist_pool import get_persist_pool

        persist = get_persist_pool()
        parse_pool = get_parse_pool() if parse_async_enabled() else None
        parse_stats_lock = threading.Lock()
        parse_stats = {"ok": False, "fail": False, "pending": 0}

        def _on_parse_done(ok: bool, fail: bool) -> None:
            with parse_stats_lock:
                if ok:
                    parse_stats["ok"] = True
                if fail:
                    parse_stats["fail"] = True
                parse_stats["pending"] = max(0, int(parse_stats["pending"]) - 1)

        def _submit_pending() -> None:
            nonlocal pending
            if not pending:
                return
            chunk = list(pending)
            pending = []
            persist.submit(batch_id, chunk)

        def _queue(item: SpooledCommand, *, records: list[dict[str, Any]] | None = None) -> None:
            nonlocal cmd_count
            if records is not None and item.persist_kind:
                item.records_rel_path = write_records(batch_id, item.id, records)
                item.row_count = len(records)
            try:
                write_meta(batch_id, item.id, item.to_meta())
            except Exception:
                _log.exception("biz_state write meta failed cmd=%s", item.id)
            pending.append(item)
            cmd_count += 1
            if len(pending) >= flush_every:
                _submit_pending()

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

            # Lightweight lookup for task_id / batch existence (no long hold).
            sdb = SessionLocal()
            try:
                batch_row = sdb.get(BizStateBatch, batch_id)
                if not batch_row:
                    return 0, 0, True, False
                task_id = str(batch_row.task_id or "")
            finally:
                sdb.close()

            session = CollectSession(
                conn,
                vendor=vendor_eff,
                device_type=device_type_eff,
                vendor_key=vendor_key,
                read_timeout=per_cmd,
                shared_cache=shared_cache,
                cache_lock=cache_lock,
                cmd_locks=cmd_locks,
            )

            # Resolve expand_all → concrete per-VRF commands via discover profile.
            flat_work: list[WorkItem] = []
            persisted = aux_persisted if aux_persisted is not None else set()
            for concrete, params, profile_id, item_id, mode in work:
                if mode != "expand_all":
                    flat_work.append((concrete, params, profile_id, item_id, mode))
                    continue
                profile = get_profile(profile_id)
                if profile is None or not profile.placeholders:
                    any_fail = True
                    _emit_task_event(
                        task_id=task_id,
                        message=f"expand_all missing profile {profile_id}",
                        level="error",
                    )
                    continue
                ph = profile.placeholders[0]
                disc = get_profile(str(ph.discover_profile_id or "").strip())
                if disc is None:
                    any_fail = True
                    _emit_task_event(
                        task_id=task_id,
                        message=f"expand_all discover profile missing for {profile_id}",
                        level="error",
                    )
                    continue
                disc_cmd = normalize_command(disc.command_template)
                entry, _ = session.fetch_and_parse(
                    disc_cmd,
                    parser_id=disc.parser_id,
                    textfsm_command=disc.textfsm_command or disc_cmd,
                    params={},
                )
                if not entry.ok:
                    any_fail = True
                    _emit_task_event(
                        task_id=task_id,
                        message=f"expand_all discover failed: {entry.error}",
                        level="error",
                    )
                    continue
                try:
                    pairs = expand_bindings_from_discover_records(
                        profile=profile,
                        records=entry.records,
                    )
                except ValueError as exc:
                    any_fail = True
                    _emit_task_event(task_id=task_id, message=str(exc), level="error")
                    continue
                for cmd, p in pairs:
                    flat_work.append((cmd, p, profile_id, item_id, "normal"))

            for concrete, params, profile_id, item_id, mode in flat_work:
                if holder.get("timed_out") or holder.get("stop_requested") or is_stop_requested(
                    batch_id
                ):
                    raise TimeoutError(f"{label}_stopped")

                cmd_id = uuid4().hex
                raw_text = ""
                cache_hit_primary = False
                try:
                    cached = session.get_cached(concrete)
                    if cached is not None and str(cached.raw or "").strip():
                        raw_text = str(cached.raw or "")
                        cache_hit_primary = True
                    else:
                        raw_text = str(
                            send_show_command(conn, concrete, read_timeout=per_cmd) or ""
                        )
                except Exception as exc:
                    any_fail = True
                    sp = SpooledCommand(
                        id=cmd_id,
                        batch_id=batch_id,
                        task_item_id=item_id,
                        profile_id=profile_id,
                        raw_command=concrete[:512],
                        params_json=dict(params or {}),
                        parse_status="failed",
                        message=_format_error(exc),
                    )
                    _queue(sp)
                    continue

                raw_rel = ""
                raw_lines = 0
                try:
                    from .spool import count_text_lines, write_raw_text

                    raw_rel = write_raw_text(batch_id, cmd_id, raw_text)
                    raw_lines = count_text_lines(raw_text)
                except Exception:
                    _log.exception("biz_state spool raw failed cmd=%s", cmd_id)

                if mode == "custom":
                    any_ok = True
                    _queue(
                        SpooledCommand(
                            id=cmd_id,
                            batch_id=batch_id,
                            task_item_id=item_id,
                            profile_id=profile_id,
                            raw_command=concrete[:512],
                            params_json=dict(params or {}),
                            parse_status="skipped_custom",
                            message="custom_raw",
                            raw_rel_path=raw_rel,
                            raw_line_count=raw_lines,
                        )
                    )
                    continue

                hit = match_command(vendor_key=vendor_key, command=concrete)
                if not hit:
                    any_fail = True
                    _queue(
                        SpooledCommand(
                            id=cmd_id,
                            batch_id=batch_id,
                            task_item_id=item_id,
                            profile_id=profile_id,
                            raw_command=concrete[:512],
                            params_json=dict(params or {}),
                            parse_status="unmatched",
                            message="no profile matched concrete command",
                            raw_rel_path=raw_rel,
                            raw_line_count=raw_lines,
                        )
                    )
                    continue

                merged = {**params, **hit.params}
                if not get_parser(hit.profile.parser_id):
                    any_fail = True
                    _queue(
                        SpooledCommand(
                            id=cmd_id,
                            batch_id=batch_id,
                            task_item_id=item_id,
                            profile_id=hit.profile.profile_id,
                            parser_id=hit.profile.parser_id,
                            metric_id=hit.profile.metric_id,
                            raw_command=concrete[:512],
                            params_json=merged,
                            parse_status="failed",
                            message=f"unknown parser {hit.profile.parser_id}",
                            raw_rel_path=raw_rel,
                            raw_line_count=raw_lines,
                        )
                    )
                    continue

                if not cache_hit_primary:
                    session.remember(
                        concrete,
                        raw=raw_text,
                        ok=True,
                        cmd_row_id=cmd_id,
                    )

                # Collect aux raws on the SSH thread (no TextFSM); parse overlaps next CLI.
                aux_captures: list[AuxRawCapture] = []
                for aux in list(hit.profile.aux_commands or []):
                    try:
                        ra = resolve_aux_command(aux, params=merged)
                    except ValueError as exc:
                        _queue(
                            SpooledCommand(
                                id=uuid4().hex,
                                batch_id=batch_id,
                                task_item_id=item_id,
                                profile_id=str(aux.profile_id or "")[:128],
                                raw_command=str(aux.key or "")[:512],
                                parse_status="aux_failed",
                                message=f"aux_for={cmd_id};resolve:{exc}"[:1020],
                            )
                        )
                        continue
                    aux_id = uuid4().hex
                    entry, cache_hit = session.fetch_raw(ra.command, cmd_row_id=aux_id)
                    aux_mid = str(getattr(ra.profile, "metric_id", "") or "").strip()
                    aux_rel = ""
                    if entry.raw:
                        try:
                            aux_rel = write_raw_text(batch_id, aux_id, entry.raw or "")
                        except Exception:
                            _log.exception(
                                "biz_state spool aux raw failed cmd=%s", aux_id
                            )
                    if not entry.ok:
                        any_fail = True
                        _queue(
                            SpooledCommand(
                                id=aux_id,
                                batch_id=batch_id,
                                task_item_id=item_id,
                                profile_id=ra.profile_id,
                                parser_id=ra.parser_id,
                                metric_id=aux_mid,
                                raw_command=ra.command[:512],
                                parse_status="aux_failed",
                                message=f"aux_for={cmd_id};{entry.error}"[:1020],
                                raw_rel_path=aux_rel,
                            )
                        )
                        continue
                    # If cache already has parsed records, pass them through.
                    aux_captures.append(
                        AuxRawCapture(
                            key=ra.key,
                            aux_id=aux_id,
                            profile_id=ra.profile_id,
                            parser_id=ra.parser_id,
                            metric_id=aux_mid,
                            command=ra.command,
                            textfsm_command=ra.textfsm_command,
                            rule_keys=tuple(ra.rule_keys or ()),
                            raw=entry.raw,
                            raw_rel_path=aux_rel,
                            cache_hit=bool(cache_hit and entry.records),
                            records=list(entry.records or []),
                            fsm_tables=dict(entry.fsm_tables or {}),
                            ok=True,
                        )
                    )

                job = PrimaryParseJob(
                    batch_id=batch_id,
                    cmd_id=cmd_id,
                    task_item_id=item_id,
                    profile_id=hit.profile.profile_id,
                    parser_id=hit.profile.parser_id,
                    metric_id=hit.profile.metric_id,
                    concrete=concrete,
                    merged_params=dict(merged or {}),
                    raw_text=raw_text,
                    raw_rel_path=raw_rel,
                    raw_line_count=raw_lines,
                    textfsm_command=hit.profile.textfsm_command or concrete,
                    vendor=vendor_eff,
                    device_type=device_type_eff,
                    enrich_joins=list(hit.profile.enrich_joins or []),
                    aux_captures=aux_captures,
                    persisted=persisted,
                    cache_lock=cache_lock,
                    on_done=_on_parse_done if parse_pool is not None else None,
                )
                if parse_pool is not None:
                    with parse_stats_lock:
                        parse_stats["pending"] += 1
                    parse_pool.submit(job)
                    # Count primary (+ aux will be counted in parse worker via persist).
                    # cmd_count: bump for primary + each aux capture so progress is visible.
                    cmd_count += 1 + len(aux_captures)
                else:
                    ok, fail = _run_primary_parse_job(job)
                    if ok:
                        any_ok = True
                    if fail:
                        any_fail = True
                    # Sync path: parse job already submitted persist; count cmds.
                    cmd_count += 1 + len(aux_captures)

            # Drain CLI spool leftovers, then wait parse + persist pools.
            _submit_pending()
            if parse_pool is not None:
                if not parse_pool.wait_idle(timeout=max(30.0, float(budget))):
                    _log.warning(
                        "biz_state parse barrier timed out batch=%s lane=%s",
                        batch_id,
                        label,
                    )
                    any_fail = True
                    _emit_task_event(
                        task_id=task_id,
                        message=f"{label}: parse_barrier_timeout",
                        level="error",
                    )
                with parse_stats_lock:
                    if parse_stats["ok"]:
                        any_ok = True
                    if parse_stats["fail"]:
                        any_fail = True
            if not persist.wait_idle(timeout=max(30.0, float(budget))):
                _log.warning(
                    "biz_state persist barrier timed out batch=%s lane=%s",
                    batch_id,
                    label,
                )
                any_fail = True
                _emit_task_event(
                    task_id=task_id,
                    message=f"{label}: persist_barrier_timeout",
                    level="error",
                )
            # Do NOT read batch.row_count here — dual light+heavy lanes would each
            # see the cumulative DB total and _absorb would double-count.
            return total_rows, cmd_count, any_fail, any_ok
        finally:
            # Best-effort: enqueue leftover spool before connection teardown.
            try:
                _submit_pending()
                if parse_pool is not None:
                    parse_pool.wait_idle(timeout=60.0)
                persist.wait_idle(timeout=60.0)
            except Exception:
                _log.exception(
                    "biz_state persist drain on lane exit failed batch=%s", batch_id
                )
            holder.pop("conn", None)
            close_netmiko_connection(conn)

    try:
        return run_cli_with_timeout(
            _session,
            timeout_sec=budget,
            conn_holder=holder,
            label=label,
            acquire_budget=True,
        )
    except TimeoutError as exc:
        raise RuntimeError(str(exc)[:1020]) from exc
    finally:
        unregister_lane_holder(batch_id, holder)


def _absorb_lane_result(
    result: tuple[int, int, bool, bool] | BaseException,
    *,
    total_rows: int,
    cmd_count: int,
    any_fail: bool,
    any_ok: bool,
    lane_errors: list[str],
) -> tuple[int, int, bool, bool]:
    if isinstance(result, BaseException):
        lane_errors.append(_format_error(result))
        return total_rows, cmd_count, True, any_ok
    rows, cmds, fail, ok = result
    return (
        total_rows + int(rows or 0),
        cmd_count + int(cmds or 0),
        any_fail or bool(fail),
        any_ok or bool(ok),
    )


def _bump_batch_progress(batch_id: str, *, add_cmds: int = 0, add_rows: int = 0) -> None:
    """Atomically bump batch counters so UI can show progress while lanes still run."""
    cmds = int(add_cmds or 0)
    rows = int(add_rows or 0)
    if not batch_id or (cmds <= 0 and rows <= 0):
        return
    db = SessionLocal()
    try:
        batch = (
            db.query(BizStateBatch)
            .filter(BizStateBatch.id == batch_id)
            .with_for_update()
            .one_or_none()
        )
        if not batch:
            return
        if cmds > 0:
            batch.command_count = int(batch.command_count or 0) + cmds
        if rows > 0:
            batch.row_count = int(batch.row_count or 0) + rows
        db.commit()
    except Exception:
        _log.exception("biz_state bump batch progress failed batch=%s", batch_id)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()


def _is_stale_db_connection(exc: BaseException) -> bool:
    """True when PG/middleware closed an idle connection mid-collect."""
    if isinstance(exc, OperationalError):
        return True
    if isinstance(exc, DBAPIError) and bool(getattr(exc, "connection_invalidated", False)):
        return True
    msg = str(exc or "").lower()
    return (
        "server closed the connection" in msg
        or "connection not open" in msg
        or "connection already closed" in msg
        or "ssl connection has been closed" in msg
    )


def _invalidate_session(db) -> None:
    try:
        conn = db.connection()
        conn.invalidate()
    except Exception:
        pass
    try:
        db.close()
    except Exception:
        pass


def _run_db_with_reconnect(fn, *, label: str = "biz_state_db"):
    """Run ``fn(db)`` on a fresh Session; retry once after disconnect/OperationalError."""
    last: BaseException | None = None
    for attempt in range(2):
        db = SessionLocal()
        try:
            result = fn(db)
            try:
                db.close()
            except Exception:
                pass
            return result
        except Exception as exc:
            last = exc
            try:
                db.rollback()
            except Exception:
                pass
            _invalidate_session(db)
            if attempt == 0 and _is_stale_db_connection(exc):
                _log.warning("%s reconnect after stale connection: %s", label, exc)
                continue
            raise
    assert last is not None
    raise last


def _batch_has_progress(batch_id: str) -> bool:
    """Whether progressive bumps already persisted cmds/rows for this batch."""

    def _read(db) -> bool:
        live = db.get(BizStateBatch, batch_id)
        if not live:
            return False
        return int(live.command_count or 0) > 0 or int(live.row_count or 0) > 0

    return bool(_run_db_with_reconnect(_read, label="biz_state_batch_progress"))


def _is_fail_parse_status(status: str | None) -> bool:
    st = str(status or "").strip().lower()
    return st in ("failed", "error", "fail", "aux_failed") or st.endswith("_failed")


def _is_skip_parse_status(status: str | None) -> bool:
    st = str(status or "").strip().lower()
    return st.startswith("skipped")


def _batch_issue_summary(
    db,
    batch_id: str,
    lane_errors: list[str] | None = None,
) -> str:
    """Human-readable reason for partial/failed batches (never leave message empty)."""
    parts: list[str] = []
    for err in lane_errors or []:
        e = str(err or "").strip()
        if e and e not in parts:
            parts.append(e)

    cmds = (
        db.query(BizStateBatchCommand)
        .filter(BizStateBatchCommand.batch_id == batch_id)
        .order_by(BizStateBatchCommand.created_at.asc())
        .all()
    )
    n_fail = 0
    n_skip = 0
    n_aux_fail = 0
    samples: list[str] = []
    for c in cmds:
        st = str(c.parse_status or "").strip().lower()
        msg = str(c.message or "").strip()
        if st == "aux_failed" or (st.startswith("aux") and "fail" in st):
            n_aux_fail += 1
            if msg and len(samples) < 3:
                samples.append(f"aux:{c.raw_command}: {msg}"[:160])
        elif _is_fail_parse_status(st):
            n_fail += 1
            if msg and len(samples) < 3:
                samples.append(f"{c.raw_command}: {msg}"[:160])
        elif _is_skip_parse_status(st):
            n_skip += 1
            if msg and len(samples) < 3:
                samples.append(f"skip:{c.profile_id or c.raw_command}: {msg}"[:160])

    if n_fail:
        parts.append(f"{n_fail} command(s) failed")
    if n_aux_fail:
        parts.append(f"{n_aux_fail} aux command(s) failed")
    if n_skip:
        parts.append(f"{n_skip} item(s) skipped (e.g. missing bindings)")
    for s in samples:
        if s not in parts:
            parts.append(s)

    text = "; ".join(parts).strip()
    if not text:
        text = "partial success (some steps failed or were skipped)"
    return text[:1020]


def _finalize_batch_status(
    *,
    batch_id: str,
    task_id: str,
    cmd_count: int,
    total_rows: int,
    any_fail: bool,
    any_ok: bool,
    lane_errors: list[str],
    stopped: bool = False,
) -> str:
    """Write terminal batch status on a fresh Session (retry once on disconnect)."""

    def _write(db) -> str:
        batch = db.get(BizStateBatch, batch_id)
        if not batch:
            return ""
        # Prefer progressive counters (survive lane timeout) over in-memory lane totals.
        batch.command_count = max(int(batch.command_count or 0), int(cmd_count or 0))
        batch.row_count = max(int(batch.row_count or 0), int(total_rows or 0))
        batch.ended_at = _utcnow()
        if stopped:
            if any_ok or int(batch.command_count or 0) > 0 or int(batch.row_count or 0) > 0:
                batch.status = "partial"
                batch.message = STOP_USER_MESSAGE
            else:
                batch.status = "cancelled"
                batch.message = STOP_USER_MESSAGE
        elif any_fail and any_ok:
            batch.status = "partial"
            batch.message = _batch_issue_summary(db, batch_id, lane_errors)
        elif any_fail and not any_ok:
            batch.status = "failed"
            batch.message = _batch_issue_summary(db, batch_id, lane_errors) or (
                "; ".join(lane_errors)[:1020] if lane_errors else "all commands failed"
            )
        else:
            # Still surface skipped-only rows as partial when some cmds ran ok.
            statuses = [
                str(c.parse_status or "")
                for c in db.query(BizStateBatchCommand)
                .filter(BizStateBatchCommand.batch_id == batch_id)
                .all()
            ]
            n_skip = sum(1 for st in statuses if _is_skip_parse_status(st))
            if n_skip > 0 and any_ok:
                batch.status = "partial"
                batch.message = _batch_issue_summary(db, batch_id, lane_errors)
            else:
                batch.status = "success"
                batch.message = ""
        status = str(batch.status or "")
        db.commit()
        # Only full success triggers auto compare; never block the collect thread.
        if status == "success" and not stopped:
            try:
                from .compare_service import schedule_auto_compare_for_task

                schedule_auto_compare_for_task(task_id, batch_id)
            except Exception:
                _log.exception("biz_state auto compare schedule failed task=%s", task_id)
        return status

    try:
        return str(
            _run_db_with_reconnect(_write, label="biz_state_finalize") or ""
        )
    finally:
        clear_stop_requested(batch_id)


def _fail_batch_status(batch_id: str, error: str) -> None:
    """Mark batch failed on a fresh Session (retry once on disconnect)."""
    msg = str(error or "")[:1020]
    stopped = "_stopped" in msg or is_stop_requested(batch_id)

    def _write(db) -> None:
        batch = db.get(BizStateBatch, batch_id)
        if not batch:
            return
        st = str(batch.status or "")
        if st not in ("running", "queued"):
            return
        if stopped:
            has_progress = int(batch.command_count or 0) > 0 or int(batch.row_count or 0) > 0
            batch.status = "partial" if has_progress else "cancelled"
            batch.message = STOP_USER_MESSAGE
        else:
            batch.status = "failed"
            batch.message = msg
        batch.ended_at = _utcnow()
        db.commit()

    try:
        _run_db_with_reconnect(_write, label="biz_state_fail_batch")
    finally:
        clear_stop_requested(batch_id)

def _run_collect_session(
    *,
    task_id: str,
    batch_id: str,
    source: str,
    ne_id: str,
    vendor: str,
    device_type: str,
) -> None:
    light_per = int(settings.ne_collect_read_timeout_sec or 120)
    light_cap = int(settings.ne_collect_run_timeout_cap_sec or 600)
    heavy_per = int(getattr(settings, "biz_state_heavy_read_timeout_sec", 300) or 300)
    heavy_cap = int(getattr(settings, "biz_state_heavy_run_timeout_cap_sec", 900) or 900)

    # Phase 1: resolve target + build work list, then release the DB connection.
    # Holding one Session across heavy CLI (up to ~2400s) lets PG/middleware close
    # the idle connection; finalize would then hit OperationalError.
    creds: dict[str, Any]
    vendor_eff: str
    device_type_eff: str
    vendor_key: str
    light_work: list[WorkItem]
    heavy_work: list[WorkItem]

    db = SessionLocal()
    try:
        task = db.get(BizStateTask, task_id)
        batch = db.get(BizStateBatch, batch_id)
        if not task or not batch:
            raise RuntimeError(f"batch_or_task_missing batch={batch_id} task={task_id}")

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
        work: list[WorkItem] = []
        # Dedupe same CLI → same metric (e.g. legacy if_intf + config_interface).
        seen_work: set[tuple[str, str]] = set()
        for item in items:
            if item.kind == "custom_raw":
                cmd = normalize_command(item.command_override)
                if cmd:
                    key = (cmd, "__custom__")
                    if key in seen_work:
                        continue
                    seen_work.add(key)
                    work.append((cmd, {}, "", item.id, "custom"))
                continue
            profile = _resolve_collect_profile(item.source_profile_id)
            if profile is None:
                _append_event(
                    db,
                    task_id=task_id,
                    message=f"skip profile {item.source_profile_id} (missing or disabled)",
                    level="info",
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
                msg = str(exc)
                _append_event(db, task_id=task_id, message=msg, level="error")
                # Persist a visible skip row so UI / partial status can explain it.
                db.add(
                    BizStateBatchCommand(
                        id=uuid4().hex,
                        batch_id=batch_id,
                        task_item_id=item.id,
                        profile_id=profile.profile_id,
                        parser_id=str(profile.parser_id or ""),
                        metric_id=str(profile.metric_id or ""),
                        raw_command=str(profile.command_template or "")[:512],
                        params_json={},
                        parse_status="skipped",
                        message=msg[:1020],
                        row_count=0,
                        raw_text="",
                    )
                )
                batch.command_count = int(batch.command_count or 0) + 1
                continue
            for concrete, params in pairs:
                if concrete == EXPAND_ALL_COMMAND:
                    # Defer VRF list expansion until CollectSession is open.
                    work.append(("", dict(params or {}), profile.profile_id, item.id, "expand_all"))
                    continue
                cmd = normalize_command(concrete)
                # Prefer match_command metric so remapped if_intf shares key with config_interface
                hit = match_command(vendor_key=vendor_key, command=cmd)
                mid = str((hit.profile.metric_id if hit else profile.metric_id) or "").strip()
                pid = str((hit.profile.profile_id if hit else profile.profile_id) or "").strip()
                key = (cmd, mid or pid)
                if key in seen_work:
                    continue
                seen_work.add(key)
                work.append((cmd, params, pid or profile.profile_id, item.id, "normal"))

        if not work:
            batch.status = "failed"
            batch.message = "no commands to run"
            batch.ended_at = _utcnow()
            db.commit()
            raise RuntimeError("no commands to run")

        light_work, heavy_work = partition_work(work)
        db.commit()
    finally:
        db.close()

    # Fresh spool dir for this batch (collect → disk, then flush to DB).
    try:
        from .spool import clear_batch_spool

        clear_batch_spool(batch_id)
    except Exception:
        _log.exception("biz_state clear spool failed batch=%s", batch_id)

    # Phase 2: CLI lanes — no outer Session held across long timeouts.
    shared_cache: dict[str, Any] = {}
    cache_lock = threading.RLock()
    cmd_locks: dict[str, Any] = {}
    aux_persisted: set[tuple[str, str]] = set()
    lane_kwargs = dict(
        batch_id=batch_id,
        creds=creds,
        vendor_eff=vendor_eff,
        device_type_eff=device_type_eff,
        vendor_key=vendor_key,
        shared_cache=shared_cache,
        cache_lock=cache_lock,
        cmd_locks=cmd_locks,
        aux_persisted=aux_persisted,
    )

    def _run_light() -> tuple[int, int, bool, bool]:
        return _run_collect_lane(
            work=light_work,
            per_cmd=light_per,
            cap=light_cap,
            label="biz_state_light",
            **lane_kwargs,
        )

    def _run_heavy() -> tuple[int, int, bool, bool]:
        return _run_collect_lane(
            work=heavy_work,
            per_cmd=heavy_per,
            cap=heavy_cap,
            label="biz_state_heavy",
            **lane_kwargs,
        )

    total_rows = 0
    cmd_count = 0
    any_fail = False
    any_ok = False
    lane_errors: list[str] = []

    if light_work and heavy_work:
        heavy_fut = _heavy_cli_pool().submit(_run_heavy)
        light_res: tuple[int, int, bool, bool] | BaseException
        try:
            light_res = _run_light()
        except Exception as exc:
            light_res = exc
        heavy_res: tuple[int, int, bool, bool] | BaseException
        try:
            heavy_res = heavy_fut.result()
        except Exception as exc:
            heavy_res = exc
        total_rows, cmd_count, any_fail, any_ok = _absorb_lane_result(
            light_res,
            total_rows=total_rows,
            cmd_count=cmd_count,
            any_fail=any_fail,
            any_ok=any_ok,
            lane_errors=lane_errors,
        )
        total_rows, cmd_count, any_fail, any_ok = _absorb_lane_result(
            heavy_res,
            total_rows=total_rows,
            cmd_count=cmd_count,
            any_fail=any_fail,
            any_ok=any_ok,
            lane_errors=lane_errors,
        )
    elif heavy_work:
        try:
            total_rows, cmd_count, any_fail, any_ok = _run_heavy()
        except Exception as exc:
            total_rows, cmd_count, any_fail, any_ok = _absorb_lane_result(
                exc,
                total_rows=0,
                cmd_count=0,
                any_fail=False,
                any_ok=False,
                lane_errors=lane_errors,
            )
    else:
        try:
            total_rows, cmd_count, any_fail, any_ok = _run_light()
        except Exception as exc:
            total_rows, cmd_count, any_fail, any_ok = _absorb_lane_result(
                exc,
                total_rows=0,
                cmd_count=0,
                any_fail=False,
                any_ok=False,
                lane_errors=lane_errors,
            )

    if lane_errors and not any_ok and cmd_count == 0:
        # Progressive bumps may already have cmds; only hard-fail if nothing landed.
        if not _batch_has_progress(batch_id):
            if is_stop_requested(batch_id) or any("_stopped" in e for e in lane_errors):
                _finalize_batch_status(
                    batch_id=batch_id,
                    task_id=task_id,
                    cmd_count=cmd_count,
                    total_rows=total_rows,
                    any_fail=False,
                    any_ok=False,
                    lane_errors=lane_errors,
                    stopped=True,
                )
                return
            raise RuntimeError("; ".join(lane_errors)[:1020])
        any_fail = True

    # Ensure parse + persist pools drained before terminal status write.
    try:
        from .parse_pool import get_parse_pool, parse_async_enabled
        from .persist_pool import get_persist_pool

        if parse_async_enabled():
            if not get_parse_pool().wait_idle(timeout=120.0):
                _log.warning(
                    "biz_state parse barrier before finalize timed out batch=%s",
                    batch_id,
                )
                any_fail = True
                if "parse_barrier_timeout" not in lane_errors:
                    lane_errors.append("RuntimeError: parse_barrier_timeout")
        if not get_persist_pool().wait_idle(timeout=120.0):
            _log.warning(
                "biz_state persist barrier before finalize timed out batch=%s",
                batch_id,
            )
            any_fail = True
            if "persist_barrier_timeout" not in lane_errors:
                lane_errors.append("RuntimeError: persist_barrier_timeout")
    except Exception:
        _log.exception("biz_state parse/persist barrier before finalize failed batch=%s", batch_id)

    stopped = is_stop_requested(batch_id) or any("_stopped" in e for e in lane_errors)
    _finalize_batch_status(
        batch_id=batch_id,
        task_id=task_id,
        cmd_count=cmd_count,
        total_rows=total_rows,
        any_fail=any_fail,
        any_ok=any_ok,
        lane_errors=lane_errors,
        stopped=stopped,
    )

    def _purge(db) -> None:
        _purge_task_retention(db, task_id=task_id)

    try:
        _run_db_with_reconnect(_purge, label="biz_state_purge")
    except Exception:
        _log.exception("biz_state retention purge wrapper failed task=%s", task_id)


def _purge_task_retention(db, *, task_id: str) -> None:
    from .retention import purge_task_batches

    task = db.get(BizStateTask, task_id)
    if not task:
        return
    try:
        info = purge_task_batches(db, task)
        if info.get("dropped"):
            _log.info(
                "biz_state retention purged task=%s dropped=%s days=%s daily=%s",
                task_id,
                info.get("dropped"),
                info.get("retention_days"),
                info.get("daily_keep_enabled"),
            )
    except Exception:
        _log.exception("biz_state retention purge failed task=%s", task_id)


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
    # Manual: allow even when schedule is on (status=running) or paused/stopped.
    dispatch_collect(task_id, manual=True)
    return {"ok": True, "task_id": task_id}
