"""Collect runner: expand task items → CLI → match → parse → batch rows."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
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
    BizStateMetricRow,
    BizStateTask,
    BizStateTaskItem,
    BizStateTaskItemBinding,
)
from ..ne_netmiko import disable_target_paging, send_show_command
from ..ne_session_factory import close_netmiko_connection, open_netmiko_connection
from .collect_session import (
    CollectSession,
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
from .parsers import get_parser
from .profiles import get_profile

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
        task.last_collect_ended_at = _utcnow()
        task.last_error = str(error or "")[:1020]
        task.updated_at = _utcnow()
        if error:
            _append_event(db, task_id=task_id, message=error, level="error")
        db.commit()
    finally:
        db.close()


def dispatch_collect(task_id: str, *, manual: bool = False) -> None:
    """Claim and run one collect round.

    Scheduler calls with ``manual=False`` (only when task status is ``running``).
    Collect-now calls with ``manual=True`` (any status, as long as not already collecting).
    """
    db = SessionLocal()
    batch_id = ""
    try:
        task = db.get(BizStateTask, task_id)
        if not task:
            return
        if task.collect_running:
            return
        st = str(task.status or "").strip()
        if manual:
            # Idle manual trigger: allow scheduled / paused / draft / stopped
            if st in ("", "deleted"):
                return
        else:
            if st != "running":
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
) -> tuple[int, int, bool, bool]:
    """Run one SSH lane (own connection + CollectSession + timeout budget)."""
    if not work:
        return 0, 0, False, False

    budget = min(int(cap), int(per_cmd) * max(1, len(work)) + 90)
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
            session = CollectSession(
                conn,
                vendor=vendor_eff,
                device_type=device_type_eff,
                vendor_key=vendor_key,
                read_timeout=per_cmd,
            )
            try:
                batch_row = sdb.get(BizStateBatch, batch_id)
                if not batch_row:
                    return 0, 0, True, False

                # Resolve expand_all → concrete per-VRF commands via discover profile.
                flat_work: list[WorkItem] = []
                aux_persisted: set[tuple[str, str]] = set()
                for concrete, params, profile_id, item_id, mode in work:
                    if mode != "expand_all":
                        flat_work.append((concrete, params, profile_id, item_id, mode))
                        continue
                    profile = get_profile(profile_id)
                    if profile is None or not profile.placeholders:
                        any_fail = True
                        _append_event(
                            sdb,
                            task_id=str(batch_row.task_id or ""),
                            message=f"expand_all missing profile {profile_id}",
                            level="error",
                        )
                        continue
                    ph = profile.placeholders[0]
                    disc = get_profile(str(ph.discover_profile_id or "").strip())
                    if disc is None:
                        any_fail = True
                        _append_event(
                            sdb,
                            task_id=str(batch_row.task_id or ""),
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
                        _append_event(
                            sdb,
                            task_id=str(batch_row.task_id or ""),
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
                        _append_event(
                            sdb,
                            task_id=str(batch_row.task_id or ""),
                            message=str(exc),
                            level="error",
                        )
                        continue
                    for cmd, p in pairs:
                        flat_work.append((cmd, p, profile_id, item_id, "normal"))

                for concrete, params, profile_id, item_id, mode in flat_work:
                    if holder.get("timed_out"):
                        raise TimeoutError(f"{label}_aborted")
                    cmd_count += 1
                    # Persist "running" before CLI so UI shows cmd progress during long reads.
                    cmd_row = BizStateBatchCommand(
                        id=uuid4().hex,
                        batch_id=batch_id,
                        task_item_id=item_id,
                        profile_id=profile_id,
                        raw_command=concrete[:512],
                        params_json=dict(params or {}),
                        parse_status="running",
                        message="collecting",
                        created_at=_utcnow(),
                    )
                    sdb.add(cmd_row)
                    sdb.commit()
                    _bump_batch_progress(batch_id, add_cmds=1)

                    cache_hit_primary = False
                    try:
                        cached = session.get_cached(concrete)
                        if cached is not None and str(cached.raw or "").strip():
                            cmd_row.raw_text = str(cached.raw or "")
                            cache_hit_primary = True
                        else:
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

                    if not get_parser(hit.profile.parser_id):
                        any_fail = True
                        cmd_row.parse_status = "failed"
                        cmd_row.message = f"unknown parser {hit.profile.parser_id}"
                        sdb.add(cmd_row)
                        sdb.commit()
                        continue

                    if cache_hit_primary:
                        pass
                    else:
                        session.remember(
                            concrete,
                            raw=cmd_row.raw_text or "",
                            ok=True,
                            cmd_row_id=cmd_row.id,
                        )

                    resolved_aux = []
                    aux_results: dict[str, Any] = {}
                    for aux in list(hit.profile.aux_commands or []):
                        try:
                            ra = resolve_aux_command(aux, params=merged)
                        except ValueError as exc:
                            aux_row = BizStateBatchCommand(
                                id=uuid4().hex,
                                batch_id=batch_id,
                                task_item_id=item_id,
                                profile_id=str(aux.profile_id or "")[:128],
                                raw_command=str(aux.key or "")[:512],
                                params_json={},
                                parse_status="aux_failed",
                                message=f"aux_for={cmd_row.id};resolve:{exc}"[:1020],
                                created_at=_utcnow(),
                            )
                            cmd_count += 1
                            sdb.add(aux_row)
                            sdb.commit()
                            _bump_batch_progress(batch_id, add_cmds=1)
                            continue
                        resolved_aux.append(ra)
                        aux_row = BizStateBatchCommand(
                            id=uuid4().hex,
                            batch_id=batch_id,
                            task_item_id=item_id,
                            profile_id=ra.profile_id,
                            parser_id=ra.parser_id,
                            metric_id="",
                            raw_command=ra.command[:512],
                            params_json={},
                            parse_status="running",
                            message=f"aux_for={cmd_row.id};collecting"[:1020],
                            created_at=_utcnow(),
                        )
                        cmd_count += 1
                        sdb.add(aux_row)
                        sdb.commit()
                        _bump_batch_progress(batch_id, add_cmds=1)
                        entry, cache_hit = session.fetch_and_parse(
                            ra.command,
                            parser_id=ra.parser_id,
                            textfsm_command=ra.textfsm_command,
                            params=merged,
                            cmd_row_id=aux_row.id,
                        )
                        aux_results[ra.key] = entry
                        aux_mid = str(getattr(ra.profile, "metric_id", "") or "").strip()
                        if aux_mid:
                            aux_row.metric_id = aux_mid
                        if cache_hit:
                            aux_row.parse_status = "aux_cached"
                            aux_row.message = (
                                f"aux_for={cmd_row.id};cache_hit;src={entry.cmd_row_id}"
                            )[:1020]
                            aux_row.raw_text = ""
                            aux_row.row_count = len(entry.records or [])
                        elif not entry.ok:
                            aux_row.parse_status = "aux_failed"
                            aux_row.message = (
                                f"aux_for={cmd_row.id};{entry.error}"
                            )[:1020]
                            aux_row.raw_text = entry.raw
                        else:
                            aux_row.parse_status = "aux"
                            aux_row.message = f"aux_for={cmd_row.id}"[:1020]
                            aux_row.raw_text = entry.raw
                            aux_row.row_count = len(entry.records or [])
                            entry.cmd_row_id = aux_row.id
                        # Persist aux metrics once per CLI (config_vrf / FIB shared across VRFs).
                        if (
                            entry.ok
                            and entry.records
                            and aux_mid in _GENERIC_METRICS
                        ):
                            persist_key = (normalize_command(ra.command), aux_mid)
                            if persist_key not in aux_persisted:
                                n_aux = _persist_metric_rows(
                                    sdb,
                                    batch=batch_row,
                                    cmd_row=aux_row,
                                    metric_id=aux_mid,
                                    records=entry.records,
                                )
                                aux_row.row_count = n_aux
                                total_rows += n_aux
                                aux_persisted.add(persist_key)
                                if n_aux:
                                    _bump_batch_progress(batch_id, add_rows=n_aux)
                        sdb.add(aux_row)
                        sdb.commit()

                    bundle = build_parse_bundle(
                        primary_raw=cmd_row.raw_text or "",
                        primary_parser_id=hit.profile.parser_id,
                        aux_results=aux_results,
                        resolved_aux=resolved_aux,
                    )
                    try:
                        records, fsm_tables, rule_keys = run_primary_with_bundle(
                            hit.profile.parser_id,
                            bundle=bundle,
                            vendor=vendor_eff,
                            device_type=device_type_eff,
                            command=hit.profile.textfsm_command or concrete,
                            textfsm_command=hit.profile.textfsm_command or "",
                            params=merged,
                            enrich_joins=list(hit.profile.enrich_joins or []),
                        )
                        session.remember(
                            concrete,
                            raw=cmd_row.raw_text or "",
                            fsm_tables=fsm_tables,
                            records=records,
                            ok=True,
                            cmd_row_id=cmd_row.id,
                        )
                        hints = []
                        if rule_keys:
                            nonempty = [k for k in rule_keys if fsm_tables.get(k)]
                            hints.append(
                                f"fsm_keys={','.join(rule_keys)};hit={','.join(nonempty)}"
                            )
                        if bundle.aux_records:
                            hints.append(
                                "aux="
                                + ",".join(
                                    f"{k}:{len(v)}" for k, v in bundle.aux_records.items()
                                )
                            )
                        if hit.profile.enrich_joins:
                            hints.append(
                                "enrich="
                                + ",".join(j.from_aux for j in hit.profile.enrich_joins)
                            )
                        if hints:
                            cmd_row.message = ";".join(hints)[:1020]
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
                    elif hit.profile.metric_id in _GENERIC_METRICS:
                        n = _persist_metric_rows(
                            sdb,
                            batch=batch_row,
                            cmd_row=cmd_row,
                            metric_id=hit.profile.metric_id,
                            records=records,
                        )
                    cmd_row.row_count = n
                    cmd_row.parse_status = "ok"
                    total_rows += n
                    any_ok = True
                    sdb.add(cmd_row)
                    sdb.commit()
                    if n:
                        _bump_batch_progress(batch_id, add_rows=n)
            finally:
                sdb.close()
            return total_rows, cmd_count, any_fail, any_ok
        finally:
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
                _append_event(db, task_id=task_id, message=str(exc), level="error")
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
        lane_kwargs = dict(
            batch_id=batch_id,
            creds=creds,
            vendor_eff=vendor_eff,
            device_type_eff=device_type_eff,
            vendor_key=vendor_key,
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
            live = db.get(BizStateBatch, batch_id)
            if not live or (int(live.command_count or 0) == 0 and int(live.row_count or 0) == 0):
                raise RuntimeError("; ".join(lane_errors)[:1020])
            any_fail = True

        batch = db.get(BizStateBatch, batch_id)
        if batch:
            # Prefer progressive counters (survive lane timeout) over in-memory lane totals.
            batch.command_count = max(int(batch.command_count or 0), int(cmd_count or 0))
            batch.row_count = max(int(batch.row_count or 0), int(total_rows or 0))
            batch.ended_at = _utcnow()
            if any_fail and any_ok:
                batch.status = "partial"
                if lane_errors:
                    batch.message = "; ".join(lane_errors)[:1020]
            elif any_fail and not any_ok:
                batch.status = "failed"
                batch.message = (
                    "; ".join(lane_errors)[:1020] if lane_errors else "all commands failed"
                )
            else:
                batch.status = "success"
                batch.message = ""
            db.commit()
            if batch.status in ("success", "partial"):
                try:
                    from .compare_service import try_auto_compare_for_task

                    try_auto_compare_for_task(db, task_id, batch_id)
                except Exception:
                    _log.exception("biz_state auto compare hook failed task=%s", task_id)

        _purge_task_retention(db, task_id=task_id)
    finally:
        db.close()


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
