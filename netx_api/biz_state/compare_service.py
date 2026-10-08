"""Compare templates, port mappings, jobs, and runs."""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import zipfile
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import (
    BizCompareDiff,
    BizCompareJob,
    BizCompareRun,
    BizCompareTemplate,
    BizPortMapping,
    BizPortMappingRow,
    BizStateBatch,
    BizStateBatchCommand,
    BizStateLldpNeighbor,
    BizStateTask,
)
from ..timeutil import utcnow_naive
from .compare_engine import compare_rows, mapping_stats
from .compare_rules import (
    ROW_FILTER_PRESETS,
    apply_row_filters,
    arp_dynamic_row_filters,
    effective_compare_fields,
    effective_display_fields,
    row_matches_filter,
)
from .iface_normalize import (
    apply_iface_normalize_rows,
    default_zte_iface_normalize_rules,
    normalize_iface_rules,
)
from .profiles import metric_field_map

_log = logging.getLogger("netx.biz_state.compare")

# Per-job mutex so auto + manual run_compare do not dual-write the same job.
_auto_compare_locks: dict[str, threading.Lock] = {}
_auto_compare_locks_guard = threading.Lock()


def _job_compare_lock(job_id: str) -> threading.Lock:
    jid = str(job_id or "").strip()
    with _auto_compare_locks_guard:
        lock = _auto_compare_locks.get(jid)
        if lock is None:
            lock = threading.Lock()
            _auto_compare_locks[jid] = lock
        return lock


def _utcnow() -> datetime:
    return utcnow_naive()


# parse_status values that mean the command produced usable (possibly empty) rows
_METRIC_OK_STATUSES = frozenset(
    {"ok", "unmatched", "skipped_custom", "ok_aux", "unmatched_aux"}
)
_METRIC_FAIL_STATUSES = frozenset({"failed", "failed_aux"})


def batch_metric_collect_ok(db: Session, batch_id: str, metric_id: str) -> bool:
    """True when this metric is safe to use from the batch.

    - Batch ``success``: OK (all commands finished).
    - Batch ``partial``/other: require at least one non-failed command for the metric.
    - No command row for the metric on a non-success batch → incomplete.
    """
    bid = str(batch_id or "").strip()
    mid = str(metric_id or "").strip()
    if not bid or not mid:
        return False
    batch = db.get(BizStateBatch, bid)
    if not batch:
        return False
    status = str(batch.status or "")
    if status == "success":
        return True
    cmds = (
        db.query(BizStateBatchCommand)
        .filter(
            BizStateBatchCommand.batch_id == bid,
            BizStateBatchCommand.metric_id == mid,
        )
        .all()
    )
    if not cmds:
        return False
    statuses = [str(c.parse_status or "").strip().lower() for c in cmds]
    if any(s in _METRIC_OK_STATUSES or s.startswith("ok") for s in statuses):
        return True
    if all(s in _METRIC_FAIL_STATUSES or s.startswith("failed") for s in statuses):
        return False
    # Unknown status with rows still present — allow; empty unknown on partial — deny
    return any(int(c.row_count or 0) > 0 for c in cmds)

def _compare_side(
    db: Session,
    batch_id: str,
    *,
    fallback_task_id: str = "",
) -> dict[str, Any]:
    """Human-readable before/after side for board header (who vs who)."""
    bid = str(batch_id or "").strip()
    fb_tid = str(fallback_task_id or "").strip()
    empty = {
        "batch_id": bid,
        "task_id": fb_tid,
        "ne_name": "",
        "ne_id": "",
        "ne_ip": "",
        "vendor": "",
        "status": "",
        "row_count": 0,
        "started_at": None,
        "label": "",
    }
    if not bid:
        # Still resolve device from job task when batch not chosen yet
        if fb_tid:
            task = db.get(BizStateTask, fb_tid)
            if task:
                ne_name = str(task.ne_name or "").strip()
                ne_ip = str(task.ne_ip or "").strip()
                label = ne_name or ne_ip or fb_tid[:12]
                return {
                    **empty,
                    "ne_name": ne_name,
                    "ne_id": str(task.ne_id or "").strip(),
                    "ne_ip": ne_ip,
                    "vendor": str(task.vendor or "").strip(),
                    "label": label,
                }
        return empty
    b = db.get(BizStateBatch, bid)
    if not b:
        task = db.get(BizStateTask, fb_tid) if fb_tid else None
        ne_name = str((task.ne_name if task else "") or "").strip()
        ne_ip = str((task.ne_ip if task else "") or "").strip()
        label = ne_name or ne_ip or bid[:12]
        return {
            **empty,
            "status": "missing",
            "ne_name": ne_name,
            "ne_id": str((task.ne_id if task else "") or "").strip(),
            "ne_ip": ne_ip,
            "vendor": str((task.vendor if task else "") or "").strip(),
            "label": label,
        }
    task = db.get(BizStateTask, b.task_id) if b.task_id else None
    if task is None and fb_tid:
        task = db.get(BizStateTask, fb_tid)
    ne_name = str(b.ne_name or (task.ne_name if task else "") or "").strip()
    ne_id = str(b.ne_id or (task.ne_id if task else "") or "").strip()
    ne_ip = str((task.ne_ip if task else "") or "").strip()
    vendor = str(b.vendor or (task.vendor if task else "") or "").strip()
    title = ne_name or ne_ip or ne_id or ""
    label = title if title else bid[:12]
    return {
        "batch_id": bid,
        "task_id": str(b.task_id or fb_tid or ""),
        "ne_name": ne_name,
        "ne_id": ne_id,
        "ne_ip": ne_ip,
        "vendor": vendor,
        "status": str(b.status or ""),
        "row_count": int(b.row_count or 0),
        "started_at": b.started_at.isoformat() + "Z" if b.started_at else None,
        "label": label,
    }

_DIFF_CHUNK = 2000
_LOAD_YIELD_PER = 5000
_SEARCH_TEXT_MAX = 4000
# Success-row persist policy (see resolve_unchanged_policy)
_STORE_UNCHANGED_MODES = frozenset({"auto", "always", "never", "sample", "keys"})
_UNCHANGED_FULL_MAX = 20_000
_UNCHANGED_SAMPLE_MAX = 5_000


def normalize_store_unchanged(raw: Any) -> str:
    m = str(raw or "auto").strip().lower()
    return m if m in _STORE_UNCHANGED_MODES else "auto"


def resolve_unchanged_policy(
    mode: str, *, before_n: int, after_n: int
) -> dict[str, Any]:
    """Decide whether / how many success rows to emit for one sheet.

    - always: full before/after for every match (slow on million-row sheets)
    - never: count only
    - sample: key + row_id sample (cap) — cutover spot-check default for large sheets
    - keys: all success as key + row_id (full browse; write still heavy)
    - auto: full when sheet is small; otherwise sample compact
    """
    m = normalize_store_unchanged(mode)
    n = max(int(before_n or 0), int(after_n or 0))
    if m == "never":
        return {"mode": m, "include": False, "limit": None, "compact": False}
    if m == "always":
        return {"mode": m, "include": True, "limit": None, "compact": False}
    if m == "keys":
        return {"mode": m, "include": True, "limit": None, "compact": True}
    if m == "sample":
        return {
            "mode": m,
            "include": True,
            "limit": _UNCHANGED_SAMPLE_MAX,
            "compact": True,
        }
    # auto — cutover-oriented: large sheets sample, not full key dump
    if n <= _UNCHANGED_FULL_MAX:
        return {"mode": m, "include": True, "limit": None, "compact": False}
    return {
        "mode": m,
        "include": True,
        "limit": _UNCHANGED_SAMPLE_MAX,
        "compact": True,
    }


def _strip_netx(row: Any) -> dict[str, Any]:
    """Drop collector provenance before persisting compare payloads."""
    if not isinstance(row, dict):
        return {}
    return {k: v for k, v in row.items() if k != "_netx"}


def _diff_search_text(d: dict[str, Any]) -> str:
    parts = [str(d.get("kind") or "")]
    for key in ("key", "before", "after", "mapped_before", "changes"):
        val = d.get(key)
        if val:
            try:
                parts.append(json.dumps(val, ensure_ascii=False, default=str, separators=(",", ":")))
            except Exception:
                parts.append(str(val))
    return " ".join(parts)[:_SEARCH_TEXT_MAX]


def _top_changed_fields(diffs: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    field_counts: dict[str, int] = {}
    for d in diffs:
        if str(d.get("kind") or "") != "changed":
            continue
        for fname in d.get("changes") or {}:
            field_counts[str(fname)] = field_counts.get(str(fname), 0) + 1
    return sorted(
        [{"field": k, "count": v} for k, v in field_counts.items()],
        key=lambda x: (-int(x["count"]), str(x["field"])),
    )[:limit]


def _persist_sheet_diffs(
    db: Session,
    *,
    run_id: str,
    metric_id: str,
    diffs: list[dict[str, Any]],
    seq_start: int = 0,
) -> int:
    """Bulk-insert diff rows already selected by the engine policy.

    Compact success rows carry key + before/after_row_id; JSON sides stay empty
    and are hydrated from metric tables on read.
    """
    buf: list[dict[str, Any]] = []
    seq = int(seq_start or 0)
    written = 0
    for d in diffs:
        kind = str(d.get("kind") or "")
        before = _strip_netx(d.get("before"))
        after = _strip_netx(d.get("after"))
        mapped = _strip_netx(d.get("mapped_before"))
        payload = {
            "kind": kind,
            "key": dict(d.get("key") or {}),
            "before": before,
            "after": after,
            "mapped_before": mapped,
            "changes": dict(d.get("changes") or {}),
        }
        # Compact success: search_text = kind + key only (no fat sides)
        search_src = (
            {"kind": kind, "key": payload["key"]}
            if kind == "unchanged" and bool(d.get("compact"))
            else payload
        )
        buf.append(
            {
                "id": uuid4().hex,
                "run_id": run_id,
                "metric_id": metric_id,
                "seq": seq,
                "kind": kind,
                "key_json": payload["key"],
                "before_json": before,
                "after_json": after,
                "mapped_before_json": mapped,
                "changes_json": payload["changes"],
                "before_row_id": str(d.get("before_row_id") or "")[:64],
                "after_row_id": str(d.get("after_row_id") or "")[:64],
                "search_text": _diff_search_text(search_src),
            }
        )
        seq += 1
        written += 1
        if len(buf) >= _DIFF_CHUNK:
            db.bulk_insert_mappings(BizCompareDiff, buf)
            buf.clear()
    if buf:
        db.bulk_insert_mappings(BizCompareDiff, buf)
    return written


def _metric_rows_by_ids(db: Session, ids: list[str]) -> dict[str, dict[str, Any]]:
    """Load BizStateMetricRow / LLDP rows by primary key for hydrate."""
    clean = [str(i).strip() for i in ids if str(i or "").strip()]
    if not clean:
        return {}
    from ..models import BizStateMetricRow

    out: dict[str, dict[str, Any]] = {}
    # Chunk IN lists for large pages
    for i in range(0, len(clean), 500):
        chunk = clean[i : i + 500]
        for r in db.query(BizStateMetricRow).filter(BizStateMetricRow.id.in_(chunk)).all():
            out[str(r.id)] = dict(r.data_json or {})
        missing = [x for x in chunk if x not in out]
        if missing:
            for n in (
                db.query(BizStateLldpNeighbor)
                .filter(BizStateLldpNeighbor.id.in_(missing))
                .all()
            ):
                out[str(n.id)] = {
                    "local_if": n.local_if,
                    "remote_sys": n.remote_sys,
                    "remote_if": n.remote_if,
                    "remote_ip": n.remote_ip,
                    "protocol": n.protocol,
                }
    return out


def _hydrate_diff_rows(db: Session, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill empty before/after from metric tables when row_ids are present."""
    need: list[str] = []
    for it in items:
        if not it.get("before") and it.get("before_row_id"):
            need.append(str(it["before_row_id"]))
        if not it.get("after") and it.get("after_row_id"):
            need.append(str(it["after_row_id"]))
    if not need:
        return items
    by_id = _metric_rows_by_ids(db, need)
    for it in items:
        brid = str(it.get("before_row_id") or "")
        arid = str(it.get("after_row_id") or "")
        if not it.get("before") and brid and brid in by_id:
            it["before"] = by_id[brid]
        if not it.get("after") and arid and arid in by_id:
            it["after"] = by_id[arid]
        # Success compact: no mapped_before stored — UI falls back to before
        if not it.get("mapped_before") and it.get("before"):
            it["mapped_before"] = dict(it["before"])
    return items


def _diff_row_out(r: BizCompareDiff) -> dict[str, Any]:
    return {
        "kind": r.kind,
        "key": r.key_json or {},
        "before": r.before_json or {},
        "after": r.after_json or {},
        "mapped_before": r.mapped_before_json or {},
        "changes": r.changes_json or {},
        "before_row_id": getattr(r, "before_row_id", "") or "",
        "after_row_id": getattr(r, "after_row_id", "") or "",
    }


def _run_has_diff_rows(db: Session, run_id: str) -> bool:
    return (
        db.query(BizCompareDiff.id).filter(BizCompareDiff.run_id == run_id).limit(1).first()
        is not None
    )


def _filter_inline_diffs(
    diffs: list[dict[str, Any]],
    *,
    kind: str,
    kw: str,
) -> list[dict[str, Any]]:
    kind_n = (kind or "diff").strip().lower()
    kw_n = (kw or "").strip().lower()
    out: list[dict[str, Any]] = []
    for d in diffs:
        dk = str(d.get("kind") or "")
        if kind_n == "diff":
            # Fail = missing + mismatch; added is a special bucket
            if dk not in ("removed", "changed"):
                continue
        elif kind_n != "all" and dk != kind_n:
            continue
        if kw_n:
            blob = _diff_search_text(d).lower()
            if kw_n not in blob:
                continue
        out.append(d)
    return out


def _sheet_meta_from_summary(summary: dict[str, Any], run: BizCompareRun, tpl: Any) -> list[dict[str, Any]]:
    sheets = list(summary.get("sheets") or [])
    if sheets:
        return sheets
    # Running / empty: do NOT invent a fake first-metric sheet (was showing
    # isis 100% with 0→0 while BGP was still loading).
    if str(run.status or "") in ("running", "queued"):
        return []
    return [
        {
            "metric_id": run.metric_id,
            "sheet_id": run.metric_id,
            "title": run.metric_id,
            "key_fields": list((tpl.key_fields if tpl else None) or []),
            "iface_fields": list((tpl.iface_fields if tpl else None) or []),
            "compare_fields": list((tpl.compare_fields if tpl else None) or []),
            "mode": "fields",
            "status": "done",
            "summary": {
                k: summary.get(k, 0)
                for k in ("added", "removed", "changed", "unchanged", "before_count", "after_count")
            },
            "diffs": list(run.diffs_json or []),
        }
    ]


def _metric_row_estimate(
    db: Session, batch_ids: list[str], metric_id: str
) -> int:
    """Cheap size hint from batch command row_count (max across sides)."""
    mid = str(metric_id or "").strip()
    if not mid:
        return 0
    best = 0
    for bid in batch_ids:
        bid = str(bid or "").strip()
        if not bid:
            continue
        rows = (
            db.query(BizStateBatchCommand)
            .filter(
                BizStateBatchCommand.batch_id == bid,
                BizStateBatchCommand.metric_id == mid,
            )
            .all()
        )
        if not rows:
            continue
        n = sum(int(c.row_count or 0) for c in rows)
        if n > best:
            best = n
    return best


def _order_sheets_small_first(
    db: Session,
    sheets: list[dict[str, Any]],
    *,
    before_batch_id: str,
    after_batch_id: str,
) -> list[dict[str, Any]]:
    """Run smaller metrics first so field engineers can review early results."""
    if len(sheets) <= 1:
        return list(sheets)
    batches = [before_batch_id, after_batch_id]
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for i, sheet in enumerate(sheets):
        n = _metric_row_estimate(db, batches, str(sheet.get("metric_id") or ""))
        scored.append((n, i, sheet))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [s for _, _, s in scored]


def _pending_sheet_meta(sheet: dict[str, Any]) -> dict[str, Any]:
    """Placeholder meta so the UI lists all check items while a run is in flight."""
    key_fields = list(sheet.get("key_fields") or [])
    compare_fields = list(sheet.get("compare_fields") or [])
    return {
        "sheet_id": sheet_key(sheet),
        "title": sheet_title(sheet),
        "metric_id": sheet.get("metric_id") or "",
        "key_fields": key_fields,
        "iface_fields": list(sheet.get("iface_fields") or []),
        "compare_fields": compare_fields,
        "display_fields": list(sheet.get("display_fields") or []),
        "field_rules": list(sheet.get("field_rules") or []),
        "ignore_port_changes": sheet.get("ignore_port_changes"),
        "mode": "presence" if not compare_fields else "fields",
        "status": "pending",
        "summary": {
            "added": 0,
            "removed": 0,
            "changed": 0,
            "unchanged": 0,
            "before_count": 0,
            "after_count": 0,
        },
    }


def _str_list(raw: Any) -> list[str]:
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _normalize_row_filters(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and item:
            out.append(dict(item))
    return out


def _normalize_field_rules(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("field") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rule: dict[str, Any] = {"field": name}
        compare = str(item.get("compare") or "").strip().lower()
        if compare:
            rule["compare"] = compare
        if item.get("ignore") is True:
            rule["ignore"] = True
            rule.setdefault("compare", "ignore")
        norm = str(item.get("normalize") or "").strip().lower()
        if norm and norm not in ("none", "strip"):
            rule["normalize"] = norm
        if item.get("tolerance") is not None and str(item.get("tolerance")).strip() != "":
            try:
                rule["tolerance"] = float(item.get("tolerance"))
            except (TypeError, ValueError):
                pass
        # Drop empty rules (only field name)
        if len(rule) > 1:
            out.append(rule)
    return out


def sheet_key(sheet: dict[str, Any] | None) -> str:
    """Unique compare-item id. Falls back to metric_id so old sheets stay valid."""
    data = sheet or {}
    return str(data.get("sheet_id") or data.get("metric_id") or "").strip()


def sheet_title(sheet: dict[str, Any] | None) -> str:
    data = sheet or {}
    return str(data.get("title") or "").strip() or sheet_key(data)


def _sheet_def(
    *,
    metric_id: str,
    key_fields: list[str],
    sheet_id: str | None = None,
    title: str | None = None,
    iface_fields: list[str] | None = None,
    compare_fields: list[str] | None = None,
    display_fields: list[str] | None = None,
    row_filters: list[dict[str, Any]] | None = None,
    field_rules: list[dict[str, Any]] | None = None,
    ignore_port_changes: bool | None = None,
) -> dict[str, Any]:
    mid = str(metric_id or "").strip()
    sid = str(sheet_id or "").strip() or mid
    ttl = str(title or "").strip() or sid
    keys = _str_list(key_fields)
    ifaces = _str_list(iface_fields)
    # compare_fields empty → presence-only mode (intentional)
    compare = _str_list(compare_fields) if compare_fields is not None else []
    # Keys are identity only; strip them from compare so UI/engine stay clear
    key_set = set(keys)
    compare = [f for f in compare if f not in key_set]
    rules = _normalize_field_rules(field_rules)
    # Drop ignored fields from compare list (single source of truth for UI)
    compare = effective_compare_fields(compare, rules)
    # None = legacy (derive key+compare); explicit list (even empty extras) preserved
    if display_fields is None:
        display = effective_display_fields(
            key_fields=keys,
            compare_fields=compare,
            display_fields=None,
        )
    else:
        display = effective_display_fields(
            key_fields=keys,
            compare_fields=compare,
            display_fields=_str_list(display_fields),
        )
    sheet: dict[str, Any] = {
        "sheet_id": sid,
        "title": ttl,
        "metric_id": mid,
        "key_fields": keys,
        "iface_fields": ifaces,
        "compare_fields": compare,
        "display_fields": display,
        "row_filters": _normalize_row_filters(row_filters),
        "field_rules": rules,
    }
    if ignore_port_changes is not None:
        sheet["ignore_port_changes"] = bool(ignore_port_changes)
    return sheet


def _default_lldp_sheet() -> dict[str, Any]:
    fields = metric_field_map().get("lldp_neighbor") or []
    keys = [f.name for f in fields if f.is_key] or ["local_if", "remote_sys", "remote_if"]
    ifaces = [f.name for f in fields if f.is_interface] or ["local_if"]
    # Value checks: non-key state/meta (e.g. remote_ip / protocol)
    compare = [f.name for f in fields if not f.is_key and f.role in ("state", "meta", "identity")]
    if not compare:
        compare = [n for n in ("remote_ip", "protocol") if n not in keys]
    return _sheet_def(
        metric_id="lldp_neighbor",
        key_fields=keys,
        iface_fields=ifaces,
        compare_fields=compare,
    )


def _default_sheet_for_metric(metric_id: str, *, compare_roles: tuple[str, ...] = ("state",)) -> dict[str, Any]:
    fields = metric_field_map().get(metric_id) or []
    keys = [f.name for f in fields if f.is_key]
    ifaces = [f.name for f in fields if f.is_interface]
    compare = [f.name for f in fields if (not f.is_key) and f.role in compare_roles]
    extra: dict[str, Any] = {}
    if metric_id == "arp":
        # Template-owned ARP filter (was hardcoded in _load_metric_rows)
        extra["row_filters"] = arp_dynamic_row_filters()
        # MAC often differs in format across vendors / reloads
        extra["field_rules"] = [{"field": "mac", "normalize": "mac"}] if "mac" in {
            f.name for f in fields
        } else []
        # Context columns: show but not necessarily compare
        ctx = [n for n in ("vrf", "entry_type", "age") if n not in keys and n not in compare]
        extra["display_fields"] = list(keys) + list(compare) + ctx
    elif metric_id in ("interface_detail", "optical_brief", "bgp_peer"):
        # Counters (rates / optical power / pfx_rcd) stay visible but out of default compare.
        counters = [f.name for f in fields if (not f.is_key) and f.role == "counter"]
        meta = [
            f.name
            for f in fields
            if (not f.is_key) and f.role == "meta" and f.name not in compare
        ]
        extra["display_fields"] = list(keys) + list(compare) + counters + meta
    return _sheet_def(
        metric_id=metric_id,
        key_fields=keys,
        iface_fields=ifaces,
        compare_fields=compare,
        display_fields=extra.get("display_fields"),
        row_filters=extra.get("row_filters"),
        field_rules=extra.get("field_rules"),
    )


def _sheets_split_by_field(
    metric_id: str,
    field: str,
    slices: tuple[tuple[str, str, str], ...],
    *,
    op: str = "eq",
    compare_roles: tuple[str, ...] = ("state",),
) -> list[dict[str, Any]]:
    """One collected metric → many compare sheets, each a row_filter slice.

    ``slices`` is ``(sheet_id, title, filter_value)``. Any metric can be split
    this way (BGP afi, ISIS af, …); the engine does not special-case names.
    """
    out: list[dict[str, Any]] = []
    for sid, title, value in slices:
        base = _default_sheet_for_metric(metric_id, compare_roles=compare_roles)
        out.append(
            _sheet_def(
                metric_id=metric_id,
                sheet_id=sid,
                title=title,
                key_fields=list(base.get("key_fields") or []),
                iface_fields=list(base.get("iface_fields") or []),
                compare_fields=list(base.get("compare_fields") or []),
                display_fields=list(base.get("display_fields") or []),
                row_filters=[{"field": field, "op": op, "value": value}],
                field_rules=list(base.get("field_rules") or []),
            )
        )
    return out


def _bgp_afi_sheets() -> list[dict[str, Any]]:
    return _sheets_split_by_field(
        "bgp_peer",
        "afi",
        (
            ("bgp_peer.ipv4", "BGP IPv4", "ipv4"),
            ("bgp_peer.ipv6", "BGP IPv6", "ipv6"),
            ("bgp_peer.vpnv4", "BGP VPNv4", "vpnv4"),
            ("bgp_peer.vpnv6", "BGP VPNv6", "vpnv6"),
            ("bgp_peer.evpn", "BGP EVPN", "evpn"),
            ("bgp_peer.vpls", "BGP VPLS", "vpls"),
        ),
        op="eq",
    )


def _vrrp_af_sheets() -> list[dict[str, Any]]:
    return _sheets_split_by_field(
        "vrrp",
        "af",
        (
            ("vrrp.ipv4", "VRRP IPv4", "ipv4"),
            ("vrrp.ipv6", "VRRP IPv6", "ipv6"),
        ),
        op="eq",
    )


def _isis_af_sheets() -> list[dict[str, Any]]:
    return _sheets_split_by_field(
        "isis_adjacency",
        "af",
        (
            ("isis_adjacency.ipv4", "ISIS IPv4", "IPv4"),
            ("isis_adjacency.ipv6", "ISIS IPv6", "IPv6"),
        ),
        op="contains",
    )


def _builtin_source_splits() -> dict[str, list[dict[str, Any]]]:
    return {
        "bgp_peer": _bgp_afi_sheets(),
        "isis_adjacency": _isis_af_sheets(),
        "vrrp": _vrrp_af_sheets(),
    }


def _packaged_zte_status_template_path():
    from pathlib import Path

    return Path(__file__).resolve().parent / "data" / "default_zte_status_template.json"


def _load_packaged_zte_status_template() -> dict[str, Any]:
    """IOH CN migration sheet set shipped as the built-in status default."""
    path = _packaged_zte_status_template_path()
    if not path.is_file():
        return {}
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")) or {})
    except Exception:
        _log.exception("failed to load packaged ZTE status template %s", path)
        return {}


def _default_zte_status_sheets() -> list[dict[str, Any]]:
    """Built-in status sheets — prefer packaged IOH CN migration rules."""
    raw = _load_packaged_zte_status_template()
    out: list[dict[str, Any]] = []
    for item in list(raw.get("metrics") or []):
        sheet = _normalize_sheet(item)
        if sheet:
            out.append(sheet)
    if out:
        return out
    # Fallback if package missing (tests / incomplete install)
    return [
        *_isis_af_sheets(),
        _default_sheet_for_metric("interface_brief", compare_roles=("state",)),
        _default_sheet_for_metric("interface_detail", compare_roles=("state",)),
        _default_sheet_for_metric("arp", compare_roles=("state",)),
        _default_sheet_for_metric("nd6_cache", compare_roles=("state",)),
        _default_sheet_for_metric("ospf_neighbor", compare_roles=("state",)),
        *_vrrp_af_sheets(),
        _default_sheet_for_metric("optical_brief", compare_roles=("state",)),
        *_bgp_afi_sheets(),
        _default_sheet_for_metric("l2vpn_pw", compare_roles=("state",)),
        _default_sheet_for_metric("l2vpn_pw_detail", compare_roles=("state",)),
        _default_lldp_sheet(),
    ]


def _builtin_status_needs_packaged_upgrade(existing: list[dict[str, Any]]) -> bool:
    """True when built-in template still lacks filtered BGP route sheets."""
    mids = {str(s.get("metric_id") or "") for s in existing}
    if "bgp_route" not in mids and "l2vpn_mac" not in mids:
        return True
    has_filtered_route = any(
        str(s.get("metric_id") or "") == "bgp_route" and list(s.get("row_filters") or [])
        for s in existing
    )
    if not has_filtered_route:
        return True
    packaged_keys = {sheet_key(s) for s in _default_zte_status_sheets()}
    have_keys = {sheet_key(s) for s in existing}
    # Missing several packaged sheet ids → sync to packaged default
    return len(packaged_keys - have_keys) >= 3


def _default_zte_config_sheets() -> list[dict[str, Any]]:
    """Config-intent metrics for cutover / intent-vs-intent compare."""
    return [
        _default_sheet_for_metric("config_vrf", compare_roles=("state",)),
        _default_sheet_for_metric("config_interface", compare_roles=("state",)),
        _default_sheet_for_metric("config_bgp_peer", compare_roles=("state",)),
        _default_sheet_for_metric("config_l2vpn_pw", compare_roles=("state",)),
        *_sheets_split_by_field(
            "config_static_route",
            "af",
            (
                ("config_static_route.ipv4", "Static IPv4", "ipv4"),
                ("config_static_route.ipv6", "Static IPv6", "ipv6"),
            ),
            compare_roles=("state",),
        ),
        *_sheets_split_by_field(
            "config_ospf",
            "af",
            (
                ("config_ospf.ipv4", "OSPF IPv4", "ipv4"),
                ("config_ospf.ipv6", "OSPF IPv6", "ipv6"),
            ),
            compare_roles=("state",),
        ),
        _default_sheet_for_metric("config_isis", compare_roles=("state",)),
    ]


def _normalize_sheet(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    mid = str(raw.get("metric_id") or "").strip()
    keys = _str_list(raw.get("key_fields"))
    if not mid or not keys:
        return None
    # Legacy ignore_fields → field_rules compare=ignore
    rules = list(_normalize_field_rules(raw.get("field_rules")))
    ignore = set(_str_list(raw.get("ignore_fields")))
    by_field = {str(r.get("field")): r for r in rules}
    for name in ignore:
        if name not in by_field:
            rules.append({"field": name, "compare": "ignore", "ignore": True})
    # display_fields: missing key → legacy derive; present → explicit
    disp_arg: list[str] | None
    if "display_fields" in raw:
        disp_arg = _str_list(raw.get("display_fields"))
    else:
        disp_arg = None
    ignore_ports: bool | None = None
    if "ignore_port_changes" in raw and raw.get("ignore_port_changes") is not None:
        ignore_ports = bool(raw.get("ignore_port_changes"))
    return _sheet_def(
        metric_id=mid,
        sheet_id=str(raw.get("sheet_id") or "").strip() or mid,
        title=str(raw.get("title") or "").strip() or None,
        key_fields=keys,
        iface_fields=_str_list(raw.get("iface_fields")),
        compare_fields=_str_list(raw.get("compare_fields")),
        display_fields=disp_arg,
        row_filters=_normalize_row_filters(raw.get("row_filters")),
        field_rules=rules,
        ignore_port_changes=ignore_ports,
    )


def _legacy_sheets(t: BizCompareTemplate) -> list[dict[str, Any]]:
    mid = str(t.metric_id or "").strip()
    keys = _str_list(t.key_fields)
    if not mid or not keys:
        return []
    ignore = set(_str_list(t.ignore_fields))
    compare = [f for f in _str_list(t.compare_fields) if f not in ignore]
    rules = [{"field": f, "compare": "ignore", "ignore": True} for f in sorted(ignore)]
    return [
        _sheet_def(
            metric_id=mid,
            key_fields=keys,
            iface_fields=_str_list(t.iface_fields),
            compare_fields=compare,
            field_rules=rules,
        )
    ]


def template_metrics(t: BizCompareTemplate) -> list[dict[str, Any]]:
    """Resolved metric sheets for a template (metrics_json or legacy single)."""
    raw = list(t.metrics_json or [])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        sheet = _normalize_sheet(item)
        if not sheet:
            continue
        sid = sheet_key(sheet)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sheet)
    if out:
        return out
    return _legacy_sheets(t)


def _apply_sheets_to_row(t: BizCompareTemplate, sheets: list[dict[str, Any]]) -> None:
    t.metrics_json = sheets
    first = sheets[0] if sheets else None
    if first:
        t.metric_id = first["metric_id"]
        t.key_fields = list(first["key_fields"])
        t.iface_fields = list(first["iface_fields"])
        t.compare_fields = list(first["compare_fields"])
        t.ignore_fields = []
    else:
        t.metric_id = ""
        t.key_fields = []
        t.iface_fields = []
        t.compare_fields = []
        t.ignore_fields = []


def _parse_metrics_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept ``metrics`` list or legacy single-metric fields."""
    if "metrics" in body and body.get("metrics") is not None:
        sheets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in list(body.get("metrics") or []):
            sheet = _normalize_sheet(raw)
            if not sheet:
                continue
            sid = sheet_key(sheet)
            if sid in seen:
                raise HTTPException(status_code=400, detail=f"duplicate_sheet:{sid}")
            seen.add(sid)
            sheets.append(sheet)
        if not sheets:
            raise HTTPException(status_code=400, detail="metrics_required")
        return sheets

    mid = str(body.get("metric_id") or "").strip()
    keys = _str_list(body.get("key_fields"))
    if not mid:
        raise HTTPException(status_code=400, detail="metric_id_required")
    if not keys:
        raise HTTPException(status_code=400, detail="key_fields_required")
    ignore = set(_str_list(body.get("ignore_fields")))
    compare = [f for f in _str_list(body.get("compare_fields")) if f not in ignore]
    rules = _normalize_field_rules(body.get("field_rules"))
    by_field = {str(r.get("field")): r for r in rules}
    for name in ignore:
        if name not in by_field:
            rules.append({"field": name, "compare": "ignore", "ignore": True})
    disp_arg: list[str] | None
    if "display_fields" in body:
        disp_arg = _str_list(body.get("display_fields"))
    else:
        disp_arg = None
    return [
        _sheet_def(
            metric_id=mid,
            sheet_id=str(body.get("sheet_id") or "").strip() or mid,
            title=str(body.get("title") or "").strip() or None,
            key_fields=keys,
            iface_fields=_str_list(body.get("iface_fields")),
            compare_fields=compare,
            display_fields=disp_arg,
            row_filters=_normalize_row_filters(body.get("row_filters")),
            field_rules=rules,
        )
    ]


def template_iface_normalize(t: BizCompareTemplate | None) -> list[dict[str, str]]:
    """Resolved iface type-alias rules for a compare template."""
    if t is None:
        return []
    return normalize_iface_rules(getattr(t, "iface_normalize_json", None) or [])


def _set_template_iface_normalize(t: BizCompareTemplate, raw: Any) -> None:
    t.iface_normalize_json = normalize_iface_rules(raw)


def _template_out(t: BizCompareTemplate) -> dict[str, Any]:
    sheets = template_metrics(t)
    first = sheets[0] if sheets else None
    return {
        "id": t.id,
        "name": t.name,
        "metrics": sheets,
        "metric_ids": list(dict.fromkeys(s["metric_id"] for s in sheets if s.get("metric_id"))),
        # legacy mirrors (first sheet)
        "metric_id": (first or {}).get("metric_id") or t.metric_id or "",
        "key_fields": list((first or {}).get("key_fields") or t.key_fields or []),
        "iface_fields": list((first or {}).get("iface_fields") or t.iface_fields or []),
        "compare_fields": list((first or {}).get("compare_fields") or t.compare_fields or []),
        "ignore_fields": [],
        "iface_normalize_rules": template_iface_normalize(t),
        "note": t.note,
        "updated_at": t.updated_at.isoformat() + "Z" if t.updated_at else None,
    }


def ensure_default_cutover_template(db: Session) -> BizCompareTemplate:
    row = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "Cutover default")
        .one_or_none()
    )
    if row:
        sheets = template_metrics(row)
        cleaned = [s for s in sheets if str(s.get("metric_id") or "") != "vrf_route_summary"]
        if not cleaned:
            cleaned = [_default_lldp_sheet()]
        if cleaned != sheets:
            _apply_sheets_to_row(row, cleaned)
            row.note = "Built-in cutover template (LLDP)"
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    sheets = [_default_lldp_sheet()]
    row = BizCompareTemplate(
        id=uuid4().hex,
        name="Cutover default",
        note="Built-in cutover template (LLDP)",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, sheets)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_lldp_template(db: Session) -> BizCompareTemplate:
    row = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "LLDP default")
        .one_or_none()
    )
    if row:
        if not template_metrics(row):
            _apply_sheets_to_row(row, [_default_lldp_sheet()])
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name="LLDP default",
        note="Built-in template for LLDP neighbor cutover compare",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, [_default_lldp_sheet()])
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_zte_status_template(db: Session) -> BizCompareTemplate:
    name = "ZTE status default"
    row = db.query(BizCompareTemplate).filter(BizCompareTemplate.name == name).one_or_none()
    packaged = _load_packaged_zte_status_template()
    sheets = _default_zte_status_sheets()
    note = str(
        packaged.get("note")
        or "Built-in ZTE status cutover (ISIS/IF/ARP/ND6/BGP route AF sheets)"
    )[:512]
    iface_rules = list(packaged.get("iface_normalize_rules") or [])
    if row:
        existing = template_metrics(row)
        if _builtin_status_needs_packaged_upgrade(existing) and sheets:
            _apply_sheets_to_row(row, sheets)
            row.note = note
            row.updated_at = _utcnow()
            if iface_rules:
                _set_template_iface_normalize(row, iface_rules)
            elif not template_iface_normalize(row):
                # Packaged IOH rules use empty normalize; leave empty when explicit
                _set_template_iface_normalize(row, [])
            db.commit()
            db.refresh(row)
            return row
        # Incremental patches for already-upgraded installs
        changed = False
        upgraded: list[dict[str, Any]] = []
        by_sid = {sheet_key(s): s for s in sheets}
        for s in existing:
            cur = dict(s)
            mid = str(cur.get("metric_id") or "")
            sid = sheet_key(cur)
            if mid == "arp" and not cur.get("row_filters"):
                src = by_sid.get(sid) or next(
                    (x for x in sheets if x.get("metric_id") == "arp"), None
                )
                cur["row_filters"] = list(
                    (src or {}).get("row_filters") or arp_dynamic_row_filters()
                )
                if not cur.get("field_rules") and src and src.get("field_rules"):
                    cur["field_rules"] = list(src["field_rules"])
                changed = True
            upgraded.append(_normalize_sheet(cur) or cur)
        have_mids = {str(s.get("metric_id") or "") for s in upgraded}
        for s in sheets:
            if str(s.get("metric_id") or "") not in have_mids:
                # Only append wholly missing metrics (e.g. bgp_route family)
                if str(s.get("metric_id") or "") == "bgp_route" and "bgp_route" not in have_mids:
                    upgraded.extend(
                        [x for x in sheets if x.get("metric_id") == "bgp_route"]
                    )
                    have_mids.add("bgp_route")
                    changed = True
                elif str(s.get("metric_id") or "") not in have_mids:
                    upgraded.append(s)
                    have_mids.add(str(s.get("metric_id") or ""))
                    changed = True
        if changed:
            _apply_sheets_to_row(row, upgraded if upgraded else sheets)
            row.note = note
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name=name,
        note=note,
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, sheets)
    _set_template_iface_normalize(row, iface_rules)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_zte_config_template(db: Session) -> BizCompareTemplate:
    name = "ZTE config intent default"
    row = db.query(BizCompareTemplate).filter(BizCompareTemplate.name == name).one_or_none()
    sheets = _default_zte_config_sheets()
    if row:
        existing = template_metrics(row)
        want = {s["metric_id"] for s in sheets}
        have = {s["metric_id"] for s in existing}
        if want - have:
            upgraded = list(existing)
            for s in sheets:
                if s["metric_id"] not in have:
                    upgraded.append(s)
            _apply_sheets_to_row(row, upgraded)
            row.note = "Built-in ZTE config intent (VRF/IF/BGP/L2VPN PW)"
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        if not template_iface_normalize(row):
            _set_template_iface_normalize(row, default_zte_iface_normalize_rules())
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name=name,
        note="Built-in ZTE config intent (VRF/IF/BGP/L2VPN PW)",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, sheets)
    _set_template_iface_normalize(row, default_zte_iface_normalize_rules())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_templates(db: Session) -> None:
    """Seed the single built-in compare template (most complete status sheet set).

    Operators own additional templates after that — do not recreate deleted
    siblings (port-only / LLDP / VRF / config) on every list call.
    """
    ensure_default_zte_status_template(db)


def upgrade_builtin_split_sheets(db: Session) -> None:
    """Upgrade built-in ZTE status template to packaged AF / BGP route sheets.

    Custom templates are left alone. Built-in is replaced wholesale when it
    still lacks filtered ``bgp_route`` sheets (IOH CN migration default).
    """
    row = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "ZTE status default")
        .one_or_none()
    )
    if not row:
        return
    existing = template_metrics(row)
    packaged_sheets = _default_zte_status_sheets()
    if _builtin_status_needs_packaged_upgrade(existing) and packaged_sheets:
        packaged = _load_packaged_zte_status_template()
        _apply_sheets_to_row(row, packaged_sheets)
        row.note = str(
            packaged.get("note")
            or "Built-in ZTE status cutover (ISIS/IF/ARP/ND6/BGP route AF sheets)"
        )[:512]
        _set_template_iface_normalize(row, list(packaged.get("iface_normalize_rules") or []))
        row.updated_at = _utcnow()
        db.commit()
        return
    splits = _builtin_source_splits()
    out: list[dict[str, Any]] = []
    changed = False
    replaced: set[str] = set()
    for s in existing:
        mid = str(s.get("metric_id") or "")
        if mid in splits and sheet_key(s) == mid and not list(s.get("row_filters") or []):
            if mid not in replaced:
                out.extend(splits[mid])
                replaced.add(mid)
            changed = True
            continue
        out.append(s)
    if not changed or not out:
        return
    _apply_sheets_to_row(row, out)
    row.note = "Built-in ZTE status cutover (ISIS/IF/ARP/ND6/BGP, address-family sheets)"
    row.updated_at = _utcnow()
    db.commit()


def list_templates(db: Session) -> list[dict[str, Any]]:
    ensure_default_templates(db)
    upgrade_builtin_split_sheets(db)
    rows = db.query(BizCompareTemplate).order_by(BizCompareTemplate.name.asc()).all()
    return [_template_out(t) for t in rows]


def list_metric_schemas() -> list[dict[str, Any]]:
    """Field catalog for template editors (key / iface / compare pickers)."""
    out: list[dict[str, Any]] = []
    for metric_id, fields in sorted(metric_field_map().items()):
        if metric_id in ("vrf_list",):
            continue
        out.append(
            {
                "metric_id": metric_id,
                "fields": [
                    {
                        "name": f.name,
                        "display_name": f.display_name or f.name,
                        "dtype": f.dtype,
                        "is_key": bool(f.is_key),
                        "is_interface": bool(f.is_interface),
                        "role": f.role,
                    }
                    for f in fields
                ],
            }
        )
    return out


def list_row_filter_presets() -> list[dict[str, Any]]:
    """Named row_filter bundles for the template UI (ARP dynamic, BGP Established, …)."""
    return [
        {"id": pid, "label": pid, "row_filters": filters}
        for pid, filters in ROW_FILTER_PRESETS.items()
    ]


def create_template(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    sheets = _parse_metrics_body(body)
    t = BizCompareTemplate(
        id=uuid4().hex,
        name=str(body.get("name") or sheets[0]["metric_id"])[:256],
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(t, sheets)
    if "iface_normalize_rules" in body or "iface_normalize_json" in body:
        _set_template_iface_normalize(
            t, body.get("iface_normalize_rules", body.get("iface_normalize_json"))
        )
    db.add(t)
    db.commit()
    return _template_out(t)


def update_template(db: Session, template_id: str, body: dict[str, Any]) -> dict[str, Any]:
    t = db.get(BizCompareTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="template_not_found")
    if "name" in body:
        t.name = str(body.get("name") or "")[:256]
    if "note" in body:
        t.note = str(body.get("note") or "")[:512]
    if "iface_normalize_rules" in body or "iface_normalize_json" in body:
        _set_template_iface_normalize(
            t, body.get("iface_normalize_rules", body.get("iface_normalize_json"))
        )
        t.updated_at = _utcnow()
    if any(
        k in body
        for k in (
            "metrics",
            "metric_id",
            "key_fields",
            "iface_fields",
            "compare_fields",
            "ignore_fields",
            "display_fields",
            "row_filters",
            "field_rules",
        )
    ):
        # Prefer explicit metrics; otherwise merge into current sheets from legacy keys
        if "metrics" in body and body.get("metrics") is not None:
            sheets = _parse_metrics_body(body)
        else:
            # Patch first sheet (or create) from legacy fields
            sheets = list(template_metrics(t))
            if not sheets:
                sheets = _parse_metrics_body(body)
            else:
                first = dict(sheets[0])
                if "metric_id" in body and body.get("metric_id") is not None:
                    mid = str(body.get("metric_id") or "").strip()
                    if mid:
                        first["metric_id"] = mid
                if "key_fields" in body:
                    keys = _str_list(body.get("key_fields"))
                    if not keys:
                        raise HTTPException(status_code=400, detail="key_fields_required")
                    first["key_fields"] = keys
                if "iface_fields" in body:
                    first["iface_fields"] = _str_list(body.get("iface_fields"))
                if "compare_fields" in body or "ignore_fields" in body:
                    ignore = set(_str_list(body.get("ignore_fields"))) if "ignore_fields" in body else set()
                    compare = (
                        _str_list(body.get("compare_fields"))
                        if "compare_fields" in body
                        else list(first.get("compare_fields") or [])
                    )
                    first["compare_fields"] = [f for f in compare if f not in ignore]
                if "display_fields" in body:
                    first["display_fields"] = _str_list(body.get("display_fields"))
                if "row_filters" in body:
                    first["row_filters"] = _normalize_row_filters(body.get("row_filters"))
                if "field_rules" in body:
                    first["field_rules"] = _normalize_field_rules(body.get("field_rules"))
                sheets[0] = _normalize_sheet(first) or first
        _apply_sheets_to_row(t, sheets)
    t.updated_at = _utcnow()
    db.commit()
    return _template_out(t)


def delete_template(db: Session, template_id: str) -> None:
    t = db.get(BizCompareTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="template_not_found")
    db.delete(t)
    db.commit()


def _mapping_out(db: Session, m: BizPortMapping) -> dict[str, Any]:
    rows = (
        db.query(BizPortMappingRow)
        .filter(BizPortMappingRow.mapping_id == m.id)
        .order_by(BizPortMappingRow.before_if.asc())
        .all()
    )
    return {
        "id": m.id,
        "name": m.name,
        "note": m.note,
        "rows": [{"id": r.id, "before_if": r.before_if, "after_if": r.after_if} for r in rows],
        "updated_at": m.updated_at.isoformat() + "Z" if m.updated_at else None,
    }


def list_mappings(db: Session) -> list[dict[str, Any]]:
    rows = db.query(BizPortMapping).order_by(BizPortMapping.name.asc()).all()
    return [_mapping_out(db, m) for m in rows]


def create_mapping(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    m = BizPortMapping(
        id=uuid4().hex,
        name=str(body.get("name") or "port map")[:256],
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(m)
    db.flush()
    _replace_mapping_rows(db, m.id, list(body.get("rows") or []))
    db.commit()
    return _mapping_out(db, m)


def update_mapping(db: Session, mapping_id: str, body: dict[str, Any]) -> dict[str, Any]:
    m = db.get(BizPortMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="mapping_not_found")
    if "name" in body:
        m.name = str(body.get("name") or "")[:256]
    if "note" in body:
        m.note = str(body.get("note") or "")[:512]
    if "rows" in body:
        _replace_mapping_rows(db, m.id, list(body.get("rows") or []))
    m.updated_at = _utcnow()
    db.commit()
    return _mapping_out(db, m)


def _replace_mapping_rows(db: Session, mapping_id: str, rows_in: list[dict[str, Any]]) -> None:
    db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).delete()
    seen: set[str] = set()
    for raw in rows_in:
        before = str(raw.get("before_if") or "").strip()
        after = str(raw.get("after_if") or "").strip()
        if not before or not after:
            continue
        if before in seen:
            raise HTTPException(status_code=400, detail=f"duplicate_before_if:{before}")
        seen.add(before)
        db.add(
            BizPortMappingRow(
                id=uuid4().hex,
                mapping_id=mapping_id,
                before_if=before[:128],
                after_if=after[:128],
            )
        )


def delete_mapping(db: Session, mapping_id: str) -> None:
    m = db.get(BizPortMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="mapping_not_found")
    db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).delete()
    db.delete(m)
    db.commit()


def _port_map_dict(db: Session, mapping_id: str) -> dict[str, str]:
    if not mapping_id:
        return {}
    rows = db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).all()
    return {str(r.before_if): str(r.after_if) for r in rows if r.before_if and r.after_if}


def _load_metric_rows(
    db: Session,
    *,
    batch_id: str,
    metric_id: str,
    on_chunk: Callable[[int], None] | None = None,
    row_filters: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Load metric rows in keyset chunks (stable on million-row sheets).

    When ``row_filters`` are SQL-pushdown-safe on PostgreSQL, they are applied in
    the SELECT (critical for BGP afi/vrf sheet splits — avoids loading 1M+ then
    discarding). Otherwise filters are applied in Python after each chunk.
    """
    from .compare_sql import (
        _dialect_is_postgres,
        _filters_sql_compatible,
        compile_row_filters_sql,
    )

    filters = [f for f in (row_filters or []) if isinstance(f, dict)]
    pushdown = bool(
        filters and _dialect_is_postgres(db) and _filters_sql_compatible(filters)
    )
    filter_sql, filter_params = ("TRUE", {})
    if pushdown:
        filter_sql, filter_params = compile_row_filters_sql(filters)

    if metric_id == "lldp_neighbor":
        out: list[dict[str, Any]] = []
        last_id = ""
        while True:
            q = db.query(BizStateLldpNeighbor).filter(BizStateLldpNeighbor.batch_id == batch_id)
            if last_id:
                q = q.filter(BizStateLldpNeighbor.id > last_id)
            chunk = q.order_by(BizStateLldpNeighbor.id.asc()).limit(_LOAD_YIELD_PER).all()
            if not chunk:
                break
            for n in chunk:
                row = {
                    "local_if": n.local_if,
                    "remote_sys": n.remote_sys,
                    "remote_if": n.remote_if,
                    "remote_ip": n.remote_ip,
                    "protocol": n.protocol,
                    "_netx": {
                        "batch_id": batch_id,
                        "batch_command_id": n.batch_command_id or "",
                        "task_id": n.task_id or "",
                        "ne_id": n.ne_id or "",
                        "collected_at": n.collected_at.isoformat() + "Z"
                        if n.collected_at
                        else None,
                        "row_id": n.id,
                    },
                }
                if filters and not pushdown and not all(
                    row_matches_filter(row, f) for f in filters
                ):
                    db.expunge(n)
                    continue
                out.append(row)
                db.expunge(n)
            last_id = str(chunk[-1].id)
            if on_chunk:
                on_chunk(len(out))
            if len(chunk) < _LOAD_YIELD_PER:
                break
        if filters and not pushdown:
            return apply_row_filters(out, filters)
        return out

    # Generic tabular metrics — PG + pushdown uses SQL keyset with JSON filters
    from ..models import BizStateMetricRow
    from sqlalchemy import text as sql_text

    out: list[dict[str, Any]] = []
    last_seq = -1
    last_id = ""
    while True:
        if pushdown:
            params = {
                "bid": batch_id,
                "mid": metric_id,
                "last_seq": last_seq,
                "last_id": last_id,
                "lim": int(_LOAD_YIELD_PER),
                **filter_params,
            }
            keyset = (
                "(seq > :last_seq OR (seq = :last_seq AND id > :last_id))"
                if last_id
                else "TRUE"
            )
            rows = db.execute(
                sql_text(
                    f"""
                    SELECT id, batch_command_id, task_id, ne_id, seq, data_json, collected_at
                    FROM biz_state_metric_row
                    WHERE batch_id = :bid
                      AND metric_id = :mid
                      AND ({filter_sql})
                      AND ({keyset})
                    ORDER BY seq ASC, id ASC
                    LIMIT :lim
                    """
                ),
                params,
            ).mappings().all()
            if not rows:
                break
            for r in rows:
                data = dict(r["data_json"] or {})
                collected = r["collected_at"]
                out.append(
                    {
                        **data,
                        "_netx": {
                            "batch_id": batch_id,
                            "batch_command_id": str(r["batch_command_id"] or ""),
                            "task_id": str(r["task_id"] or ""),
                            "ne_id": str(r["ne_id"] or ""),
                            "collected_at": collected.isoformat() + "Z"
                            if collected is not None
                            else None,
                            "row_id": str(r["id"]),
                        },
                    }
                )
            last_seq = int(rows[-1]["seq"] or 0)
            last_id = str(rows[-1]["id"])
            if on_chunk:
                on_chunk(len(out))
            if len(rows) < _LOAD_YIELD_PER:
                break
            continue

        q = db.query(BizStateMetricRow).filter(
            BizStateMetricRow.batch_id == batch_id,
            BizStateMetricRow.metric_id == metric_id,
        )
        if last_id:
            q = q.filter(
                or_(
                    BizStateMetricRow.seq > last_seq,
                    and_(
                        BizStateMetricRow.seq == last_seq,
                        BizStateMetricRow.id > last_id,
                    ),
                )
            )
        chunk = (
            q.order_by(BizStateMetricRow.seq.asc(), BizStateMetricRow.id.asc())
            .limit(_LOAD_YIELD_PER)
            .all()
        )
        if not chunk:
            break
        for r in chunk:
            row = {
                **dict(r.data_json or {}),
                "_netx": {
                    "batch_id": batch_id,
                    "batch_command_id": r.batch_command_id or "",
                    "task_id": r.task_id or "",
                    "ne_id": r.ne_id or "",
                    "collected_at": r.collected_at.isoformat() + "Z"
                    if r.collected_at
                    else None,
                    "row_id": r.id,
                },
            }
            if filters and not all(row_matches_filter(row, f) for f in filters):
                db.expunge(r)
                continue
            out.append(row)
            db.expunge(r)
        last_seq = int(chunk[-1].seq or 0)
        last_id = str(chunk[-1].id)
        if on_chunk:
            on_chunk(len(out))
        if len(chunk) < _LOAD_YIELD_PER:
            break
    if out:
        return out
    # Known metric with zero rows is OK; unknown metric still errors
    if metric_id in metric_field_map():
        return []
    raise HTTPException(status_code=400, detail=f"unsupported_metric:{metric_id}")


def validate_mapping(
    db: Session,
    *,
    mapping_id: str,
    before_batch_id: str,
    after_batch_id: str,
    template_id: str = "",
) -> dict[str, Any]:
    ensure_default_templates(db)
    tpl = db.get(BizCompareTemplate, template_id) if template_id else ensure_default_cutover_template(db)
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    sheets = template_metrics(tpl)
    if not sheets:
        raise HTTPException(status_code=400, detail="template_has_no_metrics")
    pmap = _port_map_dict(db, mapping_id)
    # Validate against first sheet that has iface fields (or first sheet)
    target = next((s for s in sheets if s.get("iface_fields")), sheets[0])
    iface_fields = list(target.get("iface_fields") or [])
    norm = template_iface_normalize(tpl)
    before = apply_iface_normalize_rows(
        _load_metric_rows(db, batch_id=before_batch_id, metric_id=target["metric_id"]),
        iface_fields=iface_fields,
        rules=norm,
    )
    after = apply_iface_normalize_rows(
        _load_metric_rows(db, batch_id=after_batch_id, metric_id=target["metric_id"]),
        iface_fields=iface_fields,
        rules=norm,
    )
    return mapping_stats(
        before_rows=before,
        after_rows=after,
        iface_fields=iface_fields,
        port_map=pmap,
    )


def _job_out(j: BizCompareJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "name": j.name,
        "template_id": j.template_id,
        "mapping_id": j.mapping_id,
        "before_task_id": j.before_task_id,
        "after_task_id": j.after_task_id,
        "before_batch_id": j.before_batch_id,
        "after_batch_id": j.after_batch_id,
        "mode": j.mode,
        "status": j.status,
        "enabled_sheet_ids": _str_list(getattr(j, "enabled_sheet_ids", None)),
        "store_unchanged": normalize_store_unchanged(getattr(j, "store_unchanged", None)),
        "note": j.note,
        "updated_at": j.updated_at.isoformat() + "Z" if j.updated_at else None,
    }


def _filter_enabled_sheets(
    sheets_cfg: list[dict[str, Any]], enabled_sheet_ids: list[str] | None
) -> list[dict[str, Any]]:
    """Empty enabled list → all sheets; else keep matching sheet_id only."""
    allowed = set(_str_list(enabled_sheet_ids))
    if not allowed:
        return sheets_cfg
    return [s for s in sheets_cfg if sheet_key(s) in allowed]


def list_jobs(db: Session) -> list[dict[str, Any]]:
    rows = db.query(BizCompareJob).order_by(BizCompareJob.updated_at.desc()).all()
    return [_job_out(j) for j in rows]


def create_job(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    ensure_default_templates(db)
    template_id = str(body.get("template_id") or "").strip()
    if not template_id:
        tpl = ensure_default_cutover_template(db)
        template_id = tpl.id
    else:
        if not db.get(BizCompareTemplate, template_id):
            raise HTTPException(status_code=404, detail="template_not_found")
    enabled = _str_list(body.get("enabled_sheet_ids"))
    j = BizCompareJob(
        id=uuid4().hex,
        name=str(body.get("name") or "compare")[:256],
        template_id=template_id,
        mapping_id=str(body.get("mapping_id") or ""),
        before_task_id=str(body.get("before_task_id") or ""),
        after_task_id=str(body.get("after_task_id") or ""),
        before_batch_id=str(body.get("before_batch_id") or ""),
        after_batch_id=str(body.get("after_batch_id") or ""),
        mode=str(body.get("mode") or "manual")[:16],
        status="ready",
        enabled_sheet_ids=enabled,
        store_unchanged=normalize_store_unchanged(body.get("store_unchanged")),
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    if j.mode == "auto":
        j.status = "auto"
    db.add(j)
    db.commit()
    return _job_out(j)


def update_job(db: Session, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    for key in (
        "name",
        "template_id",
        "mapping_id",
        "before_task_id",
        "after_task_id",
        "before_batch_id",
        "after_batch_id",
        "mode",
        "status",
        "note",
    ):
        if key in body and body.get(key) is not None:
            setattr(j, key, str(body.get(key) or ""))
    if "enabled_sheet_ids" in body:
        j.enabled_sheet_ids = _str_list(body.get("enabled_sheet_ids"))
    if "store_unchanged" in body:
        j.store_unchanged = normalize_store_unchanged(body.get("store_unchanged"))
    j.updated_at = _utcnow()
    db.commit()
    return _job_out(j)


def delete_job(db: Session, job_id: str) -> None:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    run_ids = [
        rid for (rid,) in db.query(BizCompareRun.id).filter(BizCompareRun.job_id == job_id).all()
    ]
    if run_ids:
        db.query(BizCompareDiff).filter(BizCompareDiff.run_id.in_(run_ids)).delete(
            synchronize_session=False
        )
    db.query(BizCompareRun).filter(BizCompareRun.job_id == job_id).delete()
    db.delete(j)
    db.commit()


def delete_run(db: Session, run_id: str) -> dict[str, Any]:
    """Delete one compare run and its diffs; leave the job intact."""
    r = db.get(BizCompareRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    job_id = str(r.job_id or "")
    db.query(BizCompareDiff).filter(BizCompareDiff.run_id == run_id).delete(
        synchronize_session=False
    )
    db.delete(r)
    db.commit()
    return {"ok": True, "job_id": job_id, "run_id": run_id}


def _resolve_after_batch(db: Session, job: BizCompareJob) -> str:
    if job.mode != "auto":
        return str(job.after_batch_id or "")
    task_id = str(job.after_task_id or "")
    if not task_id:
        return str(job.after_batch_id or "")
    latest = (
        db.query(BizStateBatch)
        .filter(
            BizStateBatch.task_id == task_id,
            BizStateBatch.status == "success",
        )
        .order_by(BizStateBatch.started_at.desc())
        .first()
    )
    return str(latest.id) if latest else ""


def _sheet_result_envelope(
    sheet: dict[str, Any],
    *,
    key_fields: list[str],
    iface_fields: list[str],
    compare_fields: list[str],
    display_fields: list[str],
    row_filters: list[Any],
    field_rules: list[Any],
    ignore_ports: bool | None,
    mode: str,
    summary: dict[str, Any],
    diffs: list[Any],
    mapping_stats: dict[str, Any],
) -> dict[str, Any]:
    return {
        "sheet_id": sheet_key(sheet),
        "title": sheet_title(sheet),
        "metric_id": sheet["metric_id"],
        "key_fields": key_fields,
        "iface_fields": iface_fields,
        "compare_fields": compare_fields,
        "display_fields": display_fields,
        "row_filters": row_filters,
        "field_rules": field_rules,
        "ignore_port_changes": ignore_ports,
        "mode": mode,
        "status": "done",
        "summary": summary,
        "diffs": diffs,
        "mapping_stats": mapping_stats,
    }


def _run_sheet(
    db: Session,
    *,
    sheet: dict[str, Any],
    before_batch_id: str,
    after_batch_id: str,
    port_map: dict[str, str],
    iface_normalize_rules: list[dict[str, str]] | None = None,
    store_unchanged: str = "auto",
    on_load_progress: Callable[..., None] | None = None,
) -> dict[str, Any]:
    key_fields = list(sheet.get("key_fields") or [])
    iface_fields = list(sheet.get("iface_fields") or [])
    field_rules = list(sheet.get("field_rules") or [])
    compare_fields = effective_compare_fields(
        list(sheet.get("compare_fields") or []),
        field_rules,
    )
    display_fields = effective_display_fields(
        key_fields=key_fields,
        compare_fields=compare_fields,
        display_fields=list(sheet.get("display_fields"))
        if "display_fields" in sheet
        else None,
    )
    row_filters = list(sheet.get("row_filters") or [])
    mode = "presence" if not compare_fields else "fields"
    ignore_ports = sheet.get("ignore_port_changes")
    if ignore_ports is not None:
        ignore_ports = bool(ignore_ports)
    mid = sheet["metric_id"]

    def _emit_load(side: str, n: int, **meta: Any) -> None:
        if not on_load_progress:
            return
        try:
            on_load_progress(side, n, **meta)
        except TypeError:
            on_load_progress(side, n)

    # PostgreSQL path: pushdown-safe sheets join in-DB (BGP-scale).
    from .compare_sql import SqlCompareSkip, run_sql_sheet_compare, sql_compare_skip_reason

    skip_reason = sql_compare_skip_reason(
        db,
        sheet,
        port_map=port_map,
        iface_normalize_rules=iface_normalize_rules,
    )
    if not skip_reason:
        try:
            result = run_sql_sheet_compare(
                db,
                sheet=sheet,
                before_batch_id=before_batch_id,
                after_batch_id=after_batch_id,
                store_unchanged=store_unchanged,
                on_progress=_emit_load,
            )
            summary = dict(result["summary"])
            # Engine already sets raw counts / policy; keep keys stable
            if "unchanged_policy" not in summary:
                summary["unchanged_policy"] = resolve_unchanged_policy(
                    store_unchanged,
                    before_n=int(summary.get("before_count") or 0),
                    after_n=int(summary.get("after_count") or 0),
                )
            return _sheet_result_envelope(
                sheet,
                key_fields=key_fields,
                iface_fields=iface_fields,
                compare_fields=compare_fields,
                display_fields=display_fields,
                row_filters=row_filters,
                field_rules=field_rules,
                ignore_ports=ignore_ports,
                mode=mode,
                summary=summary,
                diffs=list(result.get("diffs") or []),
                mapping_stats=dict(result.get("mapping_stats") or {}),
            )
        except SqlCompareSkip as skip:
            skip_reason = skip.reason or "skip"
            _log.info(
                "python compare sheet=%s metric=%s skip_sql=%s",
                sheet_key(sheet),
                mid,
                skip_reason,
            )
            try:
                db.rollback()
            except Exception:
                pass
        except Exception:
            _log.exception(
                "sql compare fallback sheet=%s metric=%s — using Python engine",
                sheet_key(sheet),
                mid,
            )
            skip_reason = "sql_error_fallback"
            # Roll back aborted SQL transaction so Python path can use the session
            try:
                db.rollback()
            except Exception:
                pass
    else:
        _log.info(
            "python compare sheet=%s metric=%s skip_sql=%s",
            sheet_key(sheet),
            mid,
            skip_reason,
        )

    _emit_load("before", 0, engine="python", note=skip_reason or "python", phase="loading")

    def _before_chunk(n: int) -> None:
        _emit_load("before", n, engine="python", note=skip_reason or "python", phase="loading")

    def _after_chunk(n: int) -> None:
        _emit_load("after", n, engine="python", note=skip_reason or "python", phase="loading")

    before_raw = _load_metric_rows(
        db,
        batch_id=before_batch_id,
        metric_id=mid,
        on_chunk=_before_chunk,
        row_filters=row_filters,
    )
    after_raw = _load_metric_rows(
        db,
        batch_id=after_batch_id,
        metric_id=mid,
        on_chunk=_after_chunk,
        row_filters=row_filters,
    )
    # Filters already applied in load when pushdown-safe; keep apply for safety
    before_rows = apply_row_filters(before_raw, row_filters) if row_filters else before_raw
    after_rows = apply_row_filters(after_raw, row_filters) if row_filters else after_raw
    policy = resolve_unchanged_policy(
        store_unchanged, before_n=len(before_rows), after_n=len(after_rows)
    )
    result = compare_rows(
        before_rows=before_rows,
        after_rows=after_rows,
        key_fields=key_fields,
        iface_fields=iface_fields,
        compare_fields=compare_fields,
        port_map=port_map,
        field_rules=field_rules,
        iface_normalize_rules=iface_normalize_rules,
        ignore_port_changes=ignore_ports,
        include_unchanged=bool(policy["include"]),
        unchanged_limit=policy.get("limit"),
        compact_unchanged=bool(policy.get("compact")),
    )
    summary = dict(result["summary"])
    summary["before_raw_count"] = len(before_raw)
    summary["after_raw_count"] = len(after_raw)
    summary["row_filters"] = len(row_filters)
    summary["unchanged_policy"] = policy
    summary.setdefault("engine", "python")
    return _sheet_result_envelope(
        sheet,
        key_fields=key_fields,
        iface_fields=iface_fields,
        compare_fields=compare_fields,
        display_fields=display_fields,
        row_filters=row_filters,
        field_rules=field_rules,
        ignore_ports=ignore_ports,
        mode=mode,
        summary=summary,
        diffs=list(result.get("diffs") or []),
        mapping_stats=dict(result.get("mapping_stats") or {}),
    )


def _validate_compare_job(
    db: Session, job_id: str, *, force_after_batch_id: str = ""
) -> dict[str, Any]:
    """Resolve job/template/batches/sheets; raises HTTPException on bad input."""
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    tpl = db.get(BizCompareTemplate, j.template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    before_batch_id = str(j.before_batch_id or "")
    after_batch_id = str(force_after_batch_id or "").strip() or _resolve_after_batch(db, j)
    if not before_batch_id or not after_batch_id:
        raise HTTPException(status_code=400, detail="before_and_after_batch_required")
    if not db.get(BizStateBatch, before_batch_id) or not db.get(BizStateBatch, after_batch_id):
        raise HTTPException(status_code=404, detail="batch_not_found")

    sheets_cfg = template_metrics(tpl)
    if not sheets_cfg:
        raise HTTPException(status_code=400, detail="template_has_no_metrics")
    sheets_cfg = _filter_enabled_sheets(sheets_cfg, getattr(j, "enabled_sheet_ids", None))
    if not sheets_cfg:
        raise HTTPException(status_code=400, detail="no_enabled_sheets")
    sheets_cfg = _order_sheets_small_first(
        db,
        sheets_cfg,
        before_batch_id=before_batch_id,
        after_batch_id=after_batch_id,
    )
    return {
        "job": j,
        "template": tpl,
        "before_batch_id": before_batch_id,
        "after_batch_id": after_batch_id,
        "sheets_cfg": sheets_cfg,
    }


def _create_running_run(
    db: Session,
    *,
    job: BizCompareJob,
    tpl: BizCompareTemplate,
    before_batch_id: str,
    after_batch_id: str,
    sheets_cfg: list[dict[str, Any]],
) -> BizCompareRun:
    first_metric = str(sheets_cfg[0].get("metric_id") or "")
    pending_sheets = [_pending_sheet_meta(s) for s in sheets_cfg]
    run = BizCompareRun(
        id=uuid4().hex,
        job_id=job.id,
        template_id=tpl.id,
        mapping_id=job.mapping_id,
        before_batch_id=before_batch_id,
        after_batch_id=after_batch_id,
        metric_id=first_metric,
        status="running",
        summary_json={
            "progress": {
                "phase": "queued",
                "sheet_index": 0,
                "sheet_total": len(sheets_cfg),
                "sheet_id": "",
                "sheet_title": "",
                "elapsed_ms": 0,
            },
            "sheet_count": len(sheets_cfg),
            "added": 0,
            "removed": 0,
            "changed": 0,
            "unchanged": 0,
            "duplicate": 0,
            "before_count": 0,
            "after_count": 0,
            "sheets": pending_sheets,
        },
        diffs_json=[],
        mapping_stats_json={},
        message="queued",
        created_at=_utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _set_run_progress(
    db: Session,
    run: BizCompareRun,
    *,
    phase: str,
    sheet_index: int,
    sheet_total: int,
    sheet: dict[str, Any] | None,
    started_mono: float,
    extra: dict[str, Any] | None = None,
    detach: bool = False,
) -> None:
    """Update run progress.

    ``detach=True`` writes via a fresh session so SQL compare can keep an open
    transaction (TEMP CTAS) without mid-flight commits on the worker ``db``.
    """
    elapsed_ms = int((time.monotonic() - started_mono) * 1000)
    progress = {
        "phase": phase,
        "sheet_index": sheet_index,
        "sheet_total": sheet_total,
        "sheet_id": sheet_key(sheet) if sheet else "",
        "sheet_title": sheet_title(sheet) if sheet else "",
        "elapsed_ms": elapsed_ms,
    }
    if extra:
        progress.update(extra)
    title = progress["sheet_title"] or progress["sheet_id"] or ""
    message = (
        f"{phase} {sheet_index}/{sheet_total}"
        + (f" · {title}" if title else "")
        + f" · {elapsed_ms // 1000}s"
    )[:1024]

    if detach:
        from ..db import SessionLocal

        s = SessionLocal()
        try:
            r = s.get(BizCompareRun, str(run.id))
            if not r:
                return
            prev = dict(r.summary_json or {})
            prev["progress"] = progress
            r.summary_json = prev
            if str(r.status or "") != "cancelled":
                r.status = "running"
                r.message = message
            s.commit()
            # Mirror into worker instance for later in-memory reads (do not commit db)
            prev_w = dict(run.summary_json or {})
            prev_w["progress"] = progress
            run.summary_json = prev_w
            if str(run.status or "") != "cancelled":
                run.message = message
        finally:
            s.close()
        return

    prev = dict(run.summary_json or {})
    prev["progress"] = progress
    run.summary_json = prev
    # Re-read status from DB — cancel may have been committed by another session
    # (UI cancel / startup recovery) while this worker still holds a stale "running".
    db.expire(run, ["status", "message"])
    if str(run.status or "") != "cancelled":
        run.status = "running"
        run.message = message
    db.commit()


def _execute_compare_into_run(db: Session, run_id: str) -> dict[str, Any]:
    """Run compare into an existing ``running`` BizCompareRun; persist sheet-by-sheet."""
    run = db.get(BizCompareRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run_not_found")
    j = db.get(BizCompareJob, run.job_id)
    if not j:
        run.status = "failed"
        run.message = "job_not_found"
        db.commit()
        raise HTTPException(status_code=404, detail="job_not_found")
    tpl = db.get(BizCompareTemplate, run.template_id)
    if not tpl:
        run.status = "failed"
        run.message = "template_not_found"
        db.commit()
        raise HTTPException(status_code=404, detail="template_not_found")

    before_batch_id = str(run.before_batch_id or "")
    after_batch_id = str(run.after_batch_id or "")
    sheets_cfg = _filter_enabled_sheets(
        template_metrics(tpl), getattr(j, "enabled_sheet_ids", None)
    )
    if not sheets_cfg:
        run.status = "failed"
        run.message = "no_enabled_sheets"
        db.commit()
        raise HTTPException(status_code=400, detail="no_enabled_sheets")
    sheets_cfg = _order_sheets_small_first(
        db,
        sheets_cfg,
        before_batch_id=before_batch_id,
        after_batch_id=after_batch_id,
    )

    started_mono = time.monotonic()
    store_mode = normalize_store_unchanged(getattr(j, "store_unchanged", None))
    pmap = _port_map_dict(db, run.mapping_id)
    norm_rules = template_iface_normalize(tpl)
    unchanged_listed_total = 0
    unchanged_truncated_any = False
    unchanged_compact_any = False
    agg = {
        "before_count": 0,
        "after_count": 0,
        "added": 0,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
        "duplicate": 0,
    }
    mapping_by_metric: dict[str, Any] = {}
    field_counts: dict[str, int] = {}
    total = len(sheets_cfg)

    # Prefer seeded pending sheets from create; realign to small-first order
    prev_metas = list((run.summary_json or {}).get("sheets") or [])
    by_key = {sheet_key(m): m for m in prev_metas}
    sheet_metas = [
        by_key.get(sheet_key(s)) or _pending_sheet_meta(s) for s in sheets_cfg
    ]

    def _publish_sheets() -> None:
        prev = dict(run.summary_json or {})
        prev["sheets"] = list(sheet_metas)
        prev.update({k: agg[k] for k in agg})
        run.summary_json = prev
        db.expire(run, ["status"])
        # Never resurrect cancelled while publishing incremental sheet metas
        if str(run.status or "") == "cancelled":
            db.commit()
            return
        db.commit()

    try:
        for idx, sheet in enumerate(sheets_cfg, start=1):
            if _run_is_cancelled(db, run_id):
                run = db.get(BizCompareRun, run_id) or run
                return get_run(db, run.id)
            sid = sheet_key(sheet)
            # Mark current sheet running in the sidebar list
            for meta in sheet_metas:
                if sheet_key(meta) == sid:
                    meta["status"] = "running"
                    break
            _publish_sheets()

            _load_pub = {"t": 0.0, "n": -1, "engine": ""}

            def _on_load(
                side: str,
                n: int,
                *,
                engine: str = "python",
                note: str = "",
                phase: str | None = None,
                _idx: int = idx,
                _sheet: dict = sheet,
            ) -> None:
                now = time.monotonic()
                eng = str(engine or "python")
                # SQL emits sparse updates; Python still throttle chunk spam
                if eng != "sql":
                    if n - _load_pub["n"] < 25_000 and now - _load_pub["t"] < 2.0:
                        return
                _load_pub["t"] = now
                _load_pub["n"] = n
                _load_pub["engine"] = eng
                extra: dict[str, Any] = {
                    "load_side": side,
                    "rows_loaded": n,
                    "engine": eng,
                }
                if note:
                    extra["engine_note"] = str(note)[:128]
                # SQL path: detach progress commits so TEMP CTAS stays in one txn
                _set_run_progress(
                    db,
                    run,
                    phase=str(phase or ("loading" if eng != "sql" else "sql_count")),
                    sheet_index=_idx,
                    sheet_total=total,
                    sheet=_sheet,
                    started_mono=started_mono,
                    extra=extra,
                    detach=(eng == "sql"),
                )

            _set_run_progress(
                db,
                run,
                phase="loading",
                sheet_index=idx,
                sheet_total=total,
                sheet=sheet,
                started_mono=started_mono,
                extra={"engine": "", "engine_note": ""},
            )
            one = _run_sheet(
                db,
                sheet=sheet,
                before_batch_id=before_batch_id,
                after_batch_id=after_batch_id,
                port_map=pmap,
                iface_normalize_rules=norm_rules,
                store_unchanged=store_mode,
                on_load_progress=_on_load,
            )
            s = one["summary"]
            listed = int(s.get("unchanged_listed") or 0)
            unchanged_listed_total += listed
            if s.get("unchanged_truncated"):
                unchanged_truncated_any = True
            if s.get("unchanged_compact"):
                unchanged_compact_any = True
            _set_run_progress(
                db,
                run,
                phase="comparing",
                sheet_index=idx,
                sheet_total=total,
                sheet=sheet,
                started_mono=started_mono,
                extra={
                    "before_count": int(s.get("before_count") or 0),
                    "after_count": int(s.get("after_count") or 0),
                    "diff_rows": int(s.get("added") or 0)
                    + int(s.get("removed") or 0)
                    + int(s.get("changed") or 0)
                    + int(s.get("duplicate") or 0)
                    + listed,
                },
            )
            diffs = list(one.get("diffs") or [])
            for d in diffs:
                if str(d.get("kind") or "") != "changed":
                    continue
                for fname in d.get("changes") or {}:
                    field_counts[str(fname)] = field_counts.get(str(fname), 0) + 1
            # Cutover-first: persist fails so the UI can open the fail tab ASAP,
            # then write success slim keys (sample / keys mode).
            fail_diffs = [d for d in diffs if str(d.get("kind") or "") != "unchanged"]
            ok_diffs = [d for d in diffs if str(d.get("kind") or "") == "unchanged"]
            mid = sheet_key(one)
            _set_run_progress(
                db,
                run,
                phase="persisting_fail",
                sheet_index=idx,
                sheet_total=total,
                sheet=sheet,
                started_mono=started_mono,
                extra={"fail_rows": len(fail_diffs)},
            )
            n_fail = _persist_sheet_diffs(
                db, run_id=run.id, metric_id=mid, diffs=fail_diffs, seq_start=0
            )
            db.commit()
            if ok_diffs:
                _set_run_progress(
                    db,
                    run,
                    phase="persisting_ok",
                    sheet_index=idx,
                    sheet_total=total,
                    sheet=sheet,
                    started_mono=started_mono,
                    extra={"ok_rows": len(ok_diffs)},
                )
                _persist_sheet_diffs(
                    db,
                    run_id=run.id,
                    metric_id=mid,
                    diffs=ok_diffs,
                    seq_start=n_fail,
                )
            for k in agg:
                agg[k] += int(s.get(k) or 0)
            mapping_by_metric[mid] = one["mapping_stats"]
            done_meta = {
                "sheet_id": one.get("sheet_id") or one["metric_id"],
                "title": one.get("title") or one.get("sheet_id") or one["metric_id"],
                "metric_id": one["metric_id"],
                "key_fields": one["key_fields"],
                "iface_fields": one["iface_fields"],
                "compare_fields": one["compare_fields"],
                "display_fields": one.get("display_fields") or [],
                "field_rules": one.get("field_rules") or [],
                "ignore_port_changes": one.get("ignore_port_changes"),
                "mode": one["mode"],
                "status": "done",
                "summary": one["summary"],
            }
            replaced = False
            for i, meta in enumerate(sheet_metas):
                if sheet_key(meta) == mid:
                    sheet_metas[i] = done_meta
                    replaced = True
                    break
            if not replaced:
                sheet_metas.append(done_meta)
            _publish_sheets()
            # Drop heavy diffs before next sheet
            one.clear()
            diffs.clear()
            fail_diffs.clear()
            ok_diffs.clear()
            db.commit()
            if _run_is_cancelled(db, run_id):
                run = db.get(BizCompareRun, run_id) or run
                return get_run(db, run.id)

        if _run_is_cancelled(db, run_id):
            run = db.get(BizCompareRun, run_id) or run
            return get_run(db, run.id)

        duration_ms = int((time.monotonic() - started_mono) * 1000)
        top_fields = sorted(
            [{"field": k, "count": v} for k, v in field_counts.items()],
            key=lambda x: (-int(x["count"]), str(x["field"])),
        )[:8]
        summary_payload = {
            **agg,
            "sheet_count": len(sheet_metas),
            "top_changed_fields": top_fields,
            "duration_ms": duration_ms,
            "store_unchanged": store_mode,
            "unchanged_stored": unchanged_listed_total > 0,
            "unchanged_listed": unchanged_listed_total,
            "unchanged_truncated": unchanged_truncated_any,
            "unchanged_compact": unchanged_compact_any,
            "progress": {
                "phase": "done",
                "sheet_index": total,
                "sheet_total": total,
                "sheet_id": "",
                "sheet_title": "",
                "elapsed_ms": duration_ms,
            },
            "sheets": sheet_metas,
        }
        run = db.get(BizCompareRun, run_id) or run
        if str(run.status or "") == "cancelled":
            return get_run(db, run.id)
        run.status = "success"
        run.summary_json = summary_payload
        run.mapping_stats_json = mapping_by_metric
        run.metric_id = str(sheet_metas[0]["metric_id"]) if sheet_metas else run.metric_id
        run.message = f"done · {duration_ms // 1000}s"
        run.diffs_json = []
        j.updated_at = _utcnow()
        if j.mode == "manual":
            j.after_batch_id = after_batch_id
        db.commit()
        return get_run(db, run.id)
    except HTTPException as exc:
        run = db.get(BizCompareRun, run_id) or run
        if str(run.status or "") != "cancelled":
            run.status = "failed"
            run.message = str(getattr(exc, "detail", "") or exc)[:1024]
            prev = dict(run.summary_json or {})
            prog = dict(prev.get("progress") or {})
            prog["phase"] = "failed"
            prog["elapsed_ms"] = int((time.monotonic() - started_mono) * 1000)
            prev["progress"] = prog
            prev["duration_ms"] = prog["elapsed_ms"]
            run.summary_json = prev
            db.commit()
        raise
    except Exception as exc:
        _log.exception("compare run failed run=%s job=%s", run_id, j.id)
        run = db.get(BizCompareRun, run_id) or run
        if str(run.status or "") != "cancelled":
            run.status = "failed"
            run.message = str(exc)[:1024]
            prev = dict(run.summary_json or {})
            prog = dict(prev.get("progress") or {})
            prog["phase"] = "failed"
            prog["elapsed_ms"] = int((time.monotonic() - started_mono) * 1000)
            prev["progress"] = prog
            prev["duration_ms"] = prog["elapsed_ms"]
            run.summary_json = prev
            db.commit()
        raise


_INTERRUPT_MARK = "interrupted_by_restart"


def _job_has_active_run(db: Session, job_id: str) -> BizCompareRun | None:
    return (
        db.query(BizCompareRun)
        .filter(
            BizCompareRun.job_id == str(job_id or ""),
            BizCompareRun.status.in_(("running", "queued")),
        )
        .order_by(BizCompareRun.created_at.desc())
        .first()
    )


def _run_is_cancelled(db: Session, run_id: str) -> bool:
    """Re-read status so user/startup cancel is visible to the worker thread."""
    db.expire_all()
    r = db.get(BizCompareRun, run_id)
    return bool(r and str(r.status or "") == "cancelled")


def recover_interrupted_compares_on_startup(db: Session) -> dict[str, Any]:
    """Mark orphaned running/queued compare runs as cancelled after process restart.

    In-memory job locks die with the process; without this, the UI stays on
    「比对中」and blocks a new run.
    """
    now = _utcnow()
    rows = (
        db.query(BizCompareRun)
        .filter(BizCompareRun.status.in_(("running", "queued")))
        .all()
    )
    n = 0
    for r in rows:
        r.status = "cancelled"
        msg = str(r.message or "").strip()
        if _INTERRUPT_MARK not in msg:
            r.message = f"{msg} | {_INTERRUPT_MARK}".strip(" |")[:1024]
        prev = dict(r.summary_json or {})
        prog = dict(prev.get("progress") or {})
        prog["phase"] = "cancelled"
        prog["elapsed_ms"] = int(prog.get("elapsed_ms") or 0)
        prev["progress"] = prog
        # Mark in-flight sheet placeholders so UI does not show fake pass
        sheets = list(prev.get("sheets") or [])
        for sh in sheets:
            st = str(sh.get("status") or "")
            if st in ("pending", "running", "queued"):
                sh["status"] = "cancelled"
        prev["sheets"] = sheets
        r.summary_json = prev
        n += 1
    if n:
        db.commit()
        _log.info("startup: cancelled %s interrupted compare run(s)", n)
    return {"runs": n, "at": now.isoformat() + "Z"}


def cancel_compare_run(db: Session, run_id: str) -> dict[str, Any]:
    """Cancel a running/queued compare so a new run can start."""
    r = db.get(BizCompareRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    st = str(r.status or "")
    if st not in ("running", "queued"):
        return get_run(db, run_id)
    r.status = "cancelled"
    msg = str(r.message or "").strip()
    r.message = f"{msg} | cancelled_by_user".strip(" |")[:1024]
    prev = dict(r.summary_json or {})
    prog = dict(prev.get("progress") or {})
    prog["phase"] = "cancelled"
    prev["progress"] = prog
    for sh in list(prev.get("sheets") or []):
        if str(sh.get("status") or "") in ("pending", "running", "queued"):
            sh["status"] = "cancelled"
    r.summary_json = prev
    db.commit()
    return get_run(db, run_id)


def run_compare(db: Session, job_id: str, *, force_after_batch_id: str = "") -> dict[str, Any]:
    """Synchronous compare (auto-compare / tests). Blocks the caller until done."""
    if _job_has_active_run(db, job_id):
        raise HTTPException(status_code=409, detail="compare_already_running")
    lock = _job_compare_lock(job_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="compare_already_running")
    try:
        ctx = _validate_compare_job(db, job_id, force_after_batch_id=force_after_batch_id)
        run = _create_running_run(
            db,
            job=ctx["job"],
            tpl=ctx["template"],
            before_batch_id=ctx["before_batch_id"],
            after_batch_id=ctx["after_batch_id"],
            sheets_cfg=ctx["sheets_cfg"],
        )
        return _execute_compare_into_run(db, run.id)
    finally:
        lock.release()


def enqueue_compare(
    db: Session, job_id: str, *, force_after_batch_id: str = ""
) -> dict[str, Any]:
    """Create a ``running`` run and execute compare on a daemon thread.

    Returns immediately so the HTTP worker / UI stay responsive. Poll
    ``GET /compare/runs/{id}`` for progress (``summary.progress``).
    """
    if _job_has_active_run(db, job_id):
        raise HTTPException(status_code=409, detail="compare_already_running")
    lock = _job_compare_lock(job_id)
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="compare_already_running")
    run_id = ""
    try:
        ctx = _validate_compare_job(db, job_id, force_after_batch_id=force_after_batch_id)
        run = _create_running_run(
            db,
            job=ctx["job"],
            tpl=ctx["template"],
            before_batch_id=ctx["before_batch_id"],
            after_batch_id=ctx["after_batch_id"],
            sheets_cfg=ctx["sheets_cfg"],
        )
        run_id = run.id
    except Exception:
        lock.release()
        raise

    def _bg() -> None:
        from ..db import SessionLocal

        s = SessionLocal()
        try:
            _execute_compare_into_run(s, run_id)
        except Exception:
            _log.exception("bg compare failed job=%s run=%s", job_id, run_id)
        finally:
            s.close()
            lock.release()

    threading.Thread(
        target=_bg,
        name=f"biz-cmp-{run_id[:8]}",
        daemon=True,
    ).start()
    return get_run(db, run_id)


def _csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    if any(ch in s for ch in ",\"\n\r"):
        return '"' + s.replace('"', '""') + '"'
    return s


def _sheet_csv(sheet: dict[str, Any]) -> str:
    keys = list(sheet.get("key_fields") or [])
    key_set = set(keys)
    compare = [f for f in list(sheet.get("compare_fields") or []) if f not in key_set]
    compare_set = set(compare)
    display = effective_display_fields(
        key_fields=keys,
        compare_fields=compare,
        display_fields=list(sheet.get("display_fields") or []) or None,
    )
    # Non-key columns already ordered Key→Compare→Display by effective_display_fields
    extra = [f for f in display if f not in key_set]
    headers = ["kind", *keys]
    for f in extra:
        if f in compare_set:
            headers.append(f"{f}__pre")
            headers.append(f"{f}__post")
        else:
            headers.append(f)
    lines = [",".join(_csv_cell(h) for h in headers)]
    for d in list(sheet.get("diffs") or []):
        kind = str(d.get("kind") or "")
        pre = dict(d.get("mapped_before") or d.get("before") or {})
        post = dict(d.get("after") or {})
        key = dict(d.get("key") or {})
        row = [kind]
        for k in keys:
            row.append(key.get(k, pre.get(k, post.get(k, ""))))
        for f in extra:
            if f in compare_set:
                if kind == "added":
                    row.append("")
                    row.append(post.get(f, ""))
                elif kind == "removed":
                    row.append(pre.get(f, ""))
                    row.append("")
                else:
                    row.append(pre.get(f, ""))
                    row.append(post.get(f, ""))
            else:
                # Display-only: prefer after, then before
                if kind == "removed":
                    row.append(pre.get(f, ""))
                else:
                    row.append(post.get(f, pre.get(f, "")))
        lines.append(",".join(_csv_cell(x) for x in row))
    return "\ufeff" + "\n".join(lines) + "\n"


def _enrich_summary(summary: dict[str, Any], sheets: list[dict[str, Any]]) -> dict[str, Any]:
    added = int(summary.get("added") or 0)
    removed = int(summary.get("removed") or 0)
    changed = int(summary.get("changed") or 0)
    unchanged = int(summary.get("unchanged") or 0)
    before_count = int(summary.get("before_count") or 0)
    after_count = int(summary.get("after_count") or 0)
    total = added + removed + changed + unchanged
    # Compare verdict: fail = missing + mismatch; success = match; added is special
    fail_count = removed + changed
    success_count = unchanged
    judged = fail_count + success_count
    matched = changed + unchanged
    diff_count = fail_count
    pass_rate = (
        round((success_count / judged) * 100, 1) if judged else (100.0 if total == 0 else 0.0)
    )
    diff_rate = round((fail_count / judged) * 100, 1) if judged else 0.0

    sheet_cards: list[dict[str, Any]] = []
    for sh in sheets:
        ss = dict(sh.get("summary") or {})
        sa = int(ss.get("added") or 0)
        sr = int(ss.get("removed") or 0)
        sc = int(ss.get("changed") or 0)
        su = int(ss.get("unchanged") or 0)
        st = sa + sr + sc + su
        sf = sr + sc
        sj = sf + su
        status = str(sh.get("status") or "done")
        pending = status in ("pending", "running", "queued")
        sheet_cards.append(
            {
                "sheet_id": sheet_key(sh),
                "title": sheet_title(sh),
                "metric_id": sh.get("metric_id") or "",
                "mode": sh.get("mode") or ("presence" if not sh.get("compare_fields") else "fields"),
                "status": status,
                "added": sa,
                "removed": sr,
                "changed": sc,
                "unchanged": su,
                "before_count": int(ss.get("before_count") or 0),
                "after_count": int(ss.get("after_count") or 0),
                "fail_count": sf,
                "success_count": su,
                "diff_count": sf,
                # Pending sheets must not look like "100% pass"
                "pass_rate": None
                if pending
                else (round((su / sj) * 100, 1) if sj else (100.0 if st == 0 else 0.0)),
            }
        )

    top_fields = list(summary.get("top_changed_fields") or [])
    if not top_fields:
        # Legacy runs that still embed diffs in summary_json
        field_counts: dict[str, int] = {}
        for sh in sheets:
            for d in list(sh.get("diffs") or []):
                if str(d.get("kind") or "") != "changed":
                    continue
                for fname in d.get("changes") or {}:
                    field_counts[str(fname)] = field_counts.get(str(fname), 0) + 1
        top_fields = sorted(
            [{"field": k, "count": v} for k, v in field_counts.items()],
            key=lambda x: (-int(x["count"]), str(x["field"])),
        )[:8]

    any_pending = any(
        str(sh.get("status") or "") in ("pending", "running", "queued") for sh in sheets
    )
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "before_count": before_count,
        "after_count": after_count,
        "sheet_count": int(summary.get("sheet_count") or len(sheets) or 0),
        "total_rows": total,
        "matched_rows": matched,
        "fail_count": fail_count,
        "success_count": success_count,
        "diff_count": diff_count,
        "pass_rate": None if any_pending else pass_rate,
        "diff_rate": diff_rate,
        "ok": False if any_pending else fail_count == 0,
        "sheet_cards": sheet_cards,
        "top_changed_fields": top_fields,
        "duration_ms": int(summary.get("duration_ms") or 0),
        "store_unchanged": str(summary.get("store_unchanged") or ""),
        "unchanged_stored": bool(summary.get("unchanged_stored", True)),
        "unchanged_listed": int(summary.get("unchanged_listed") or 0),
        "unchanged_truncated": bool(summary.get("unchanged_truncated")),
        "unchanged_compact": bool(summary.get("unchanged_compact")),
        "progress": dict(summary.get("progress") or {}),
    }


def get_run(db: Session, run_id: str) -> dict[str, Any]:
    r = db.get(BizCompareRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    tpl = db.get(BizCompareTemplate, r.template_id) if r.template_id else None
    summary = dict(r.summary_json or {})
    raw_sheets = _sheet_meta_from_summary(summary, r, tpl)
    # Never return full diffs in run detail (million-row safe)
    sheets = [
        {
            "sheet_id": sh.get("sheet_id") or sh.get("metric_id") or "",
            "title": sh.get("title") or sh.get("sheet_id") or sh.get("metric_id") or "",
            "metric_id": sh.get("metric_id") or "",
            "key_fields": list(sh.get("key_fields") or []),
            "iface_fields": list(sh.get("iface_fields") or []),
            "compare_fields": list(sh.get("compare_fields") or []),
            "display_fields": list(sh.get("display_fields") or []),
            "field_rules": list(sh.get("field_rules") or []),
            "mode": sh.get("mode") or ("presence" if not sh.get("compare_fields") else "fields"),
            "status": str(sh.get("status") or "done"),
            "summary": dict(sh.get("summary") or {}),
        }
        for sh in raw_sheets
    ]
    enriched = _enrich_summary(summary, raw_sheets)
    stored = "rows" if _run_has_diff_rows(db, run_id) else "inline"
    job = db.get(BizCompareJob, r.job_id) if r.job_id else None
    mapping = db.get(BizPortMapping, r.mapping_id) if r.mapping_id else None
    before_side = _compare_side(
        db, r.before_batch_id, fallback_task_id=(job.before_task_id if job else "")
    )
    after_side = _compare_side(
        db, r.after_batch_id, fallback_task_id=(job.after_task_id if job else "")
    )
    return {
        "id": r.id,
        "job_id": r.job_id,
        "job_name": (job.name if job else "") or "",
        "template_id": r.template_id,
        "template_name": (tpl.name if tpl else "") or "",
        "mapping_id": r.mapping_id,
        "mapping_name": (mapping.name if mapping else "") or "",
        "before_batch_id": r.before_batch_id,
        "after_batch_id": r.after_batch_id,
        "before": before_side,
        "after": after_side,
        "metric_id": r.metric_id,
        "status": r.status,
        "summary": enriched,
        "sheets": sheets,
        "diffs": [],
        "diffs_stored": stored,
        "mapping_stats": r.mapping_stats_json or {},
        "message": r.message,
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "template": _template_out(tpl) if tpl else None,
    }


def _lookup_sheet(sheets: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """Find a result sheet by sheet_id, or by metric_id when that source is unique."""
    k = str(key or "").strip()
    if not k:
        return sheets[0] if sheets else None
    for s in sheets:
        if sheet_key(s) == k:
            return s
    hits = [s for s in sheets if str(s.get("metric_id") or "") == k]
    if len(hits) == 1:
        return hits[0]
    return None


def list_run_diffs(
    db: Session,
    run_id: str,
    *,
    metric_id: str = "",
    kind: str = "diff",
    kw: str = "",
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    r = db.get(BizCompareRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    page_n = max(1, int(page or 1))
    size_n = max(1, min(500, int(page_size or 100)))
    kind_n = (kind or "diff").strip().lower()
    kw_n = (kw or "").strip()

    summary = dict(r.summary_json or {})
    tpl = db.get(BizCompareTemplate, r.template_id) if r.template_id else None
    sheets = _sheet_meta_from_summary(summary, r, tpl)
    asked = (metric_id or "").strip()
    sheet = _lookup_sheet(sheets, asked) if asked else (sheets[0] if sheets else None)
    mid = sheet_key(sheet) if sheet else (asked or str(r.metric_id or ""))

    if _run_has_diff_rows(db, run_id):
        q = db.query(BizCompareDiff).filter(
            BizCompareDiff.run_id == run_id,
            BizCompareDiff.metric_id == mid,
        )
        if kind_n == "diff":
            q = q.filter(BizCompareDiff.kind.in_(("removed", "changed")))
        elif kind_n != "all":
            q = q.filter(BizCompareDiff.kind == kind_n)
        if kw_n:
            q = q.filter(BizCompareDiff.search_text.ilike(f"%{kw_n}%"))
        total = q.count()
        rows = (
            q.order_by(BizCompareDiff.seq.asc(), BizCompareDiff.id.asc())
            .offset((page_n - 1) * size_n)
            .limit(size_n)
            .all()
        )
        items = _hydrate_diff_rows(db, [_diff_row_out(x) for x in rows])
        return {
            "total": total,
            "page": page_n,
            "page_size": size_n,
            "metric_id": mid,
            "items": items,
        }

    # Legacy: diffs embedded in summary_json / diffs_json
    if sheet is None and sheets:
        sheet = sheets[0]
        mid = sheet_key(sheet)
    inline = list((sheet or {}).get("diffs") or [])
    if not inline and mid == r.metric_id:
        inline = list(r.diffs_json or [])
    filtered = _filter_inline_diffs(inline, kind=kind_n, kw=kw_n)
    total = len(filtered)
    start = (page_n - 1) * size_n
    page_items = filtered[start : start + size_n]
    return {
        "total": total,
        "page": page_n,
        "page_size": size_n,
        "metric_id": mid,
        "items": page_items,
    }


def _iter_sheet_diffs(db: Session, run_id: str, metric_id: str) -> list[dict[str, Any]]:
    """Load all diffs for one sheet (export). Prefer row table; fall back to inline.

    Compact success rows are hydrated in chunks from metric tables.
    """
    if _run_has_diff_rows(db, run_id):
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            rows = (
                db.query(BizCompareDiff)
                .filter(BizCompareDiff.run_id == run_id, BizCompareDiff.metric_id == metric_id)
                .order_by(BizCompareDiff.seq.asc(), BizCompareDiff.id.asc())
                .offset(offset)
                .limit(_DIFF_CHUNK)
                .all()
            )
            if not rows:
                break
            chunk = _hydrate_diff_rows(db, [_diff_row_out(x) for x in rows])
            out.extend(chunk)
            offset += len(rows)
            if len(rows) < _DIFF_CHUNK:
                break
        return out
    r = db.get(BizCompareRun, run_id)
    if not r:
        return []
    summary = dict(r.summary_json or {})
    sheets = list(summary.get("sheets") or [])
    for sh in sheets:
        if sheet_key(sh) == metric_id or (
            str(sh.get("metric_id") or "") == metric_id and sheet_key(sh) == metric_id
        ):
            return list(sh.get("diffs") or [])
    if metric_id == r.metric_id:
        return list(r.diffs_json or [])
    return []


def export_run_zip(db: Session, run_id: str) -> bytes:
    detail = get_run(db, run_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        s = detail.get("summary") or {}
        manifest = [
            f"run_id={detail.get('id')}",
            f"job_id={detail.get('job_id')}",
            f"before_batch_id={detail.get('before_batch_id')}",
            f"after_batch_id={detail.get('after_batch_id')}",
            f"created_at={detail.get('created_at')}",
            f"pass_rate={s.get('pass_rate')}%",
            f"diff_count={s.get('diff_count')}",
            f"added={s.get('added')} removed={s.get('removed')} "
            f"changed={s.get('changed')} unchanged={s.get('unchanged')}",
            f"before_count={s.get('before_count')} after_count={s.get('after_count')}",
            "",
            "sheets:",
        ]
        for card in list(s.get("sheet_cards") or []):
            manifest.append(
                f"- {card.get('title') or card.get('sheet_id') or card.get('metric_id')}: diff={card.get('diff_count')} "
                f"pass={card.get('pass_rate')}% "
                f"+{card.get('added')}/-{card.get('removed')}/~{card.get('changed')}/= {card.get('unchanged')}"
            )
        zf.writestr("manifest.txt", "\n".join(manifest) + "\n")
        for sheet in list(detail.get("sheets") or []):
            sid = sheet_key(sheet) or "sheet"
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in sid)[:80] or "sheet"
            sheet_full = {
                **sheet,
                "diffs": _iter_sheet_diffs(db, run_id, sid),
            }
            zf.writestr(f"tables/{safe}.csv", _sheet_csv(sheet_full))
        sum_lines = ["metric_id,mode,before,after,added,removed,changed,unchanged,diff_count,pass_rate"]
        for card in list(s.get("sheet_cards") or []):
            sum_lines.append(
                ",".join(
                    _csv_cell(x)
                    for x in (
                        card.get("title") or card.get("sheet_id") or card.get("metric_id"),
                        card.get("mode"),
                        card.get("before_count"),
                        card.get("after_count"),
                        card.get("added"),
                        card.get("removed"),
                        card.get("changed"),
                        card.get("unchanged"),
                        card.get("diff_count"),
                        card.get("pass_rate"),
                    )
                )
            )
        zf.writestr("tables/_summary.csv", "\ufeff" + "\n".join(sum_lines) + "\n")
    return buf.getvalue()


def list_runs(db: Session, job_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(BizCompareRun)
        .filter(BizCompareRun.job_id == job_id)
        .order_by(BizCompareRun.created_at.desc())
        .limit(max(1, min(100, int(limit))))
        .all()
    )
    job = db.get(BizCompareJob, job_id)
    before_tid = str(job.before_task_id or "") if job else ""
    after_tid = str(job.after_task_id or "") if job else ""
    out: list[dict[str, Any]] = []
    for r in rows:
        summary = dict(r.summary_json or {})
        out.append(
            {
                "id": r.id,
                "before_batch_id": r.before_batch_id,
                "after_batch_id": r.after_batch_id,
                "before": _compare_side(db, r.before_batch_id, fallback_task_id=before_tid),
                "after": _compare_side(db, r.after_batch_id, fallback_task_id=after_tid),
                "status": r.status,
                "message": r.message or "",
                "summary": {
                    k: summary.get(k, 0)
                    for k in (
                        "added",
                        "removed",
                        "changed",
                        "unchanged",
                        "sheet_count",
                        "duration_ms",
                    )
                },
                "progress": dict(summary.get("progress") or {}),
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            }
        )
    return out


def try_auto_compare_for_task(db: Session, task_id: str, batch_id: str) -> int:
    """When a new *success* after batch lands, run auto jobs pinned to that task.

    Skips jobs already being compared (non-blocking lock). Call from a background
    thread so the collect finalize path is not blocked.
    """
    batch = db.get(BizStateBatch, batch_id)
    if not batch or str(batch.status or "") != "success":
        return 0
    jobs = (
        db.query(BizCompareJob)
        .filter(BizCompareJob.mode == "auto", BizCompareJob.after_task_id == task_id)
        .all()
    )
    n = 0
    for j in jobs:
        if not j.before_batch_id:
            continue
        try:
            run_compare(db, j.id, force_after_batch_id=batch_id)
            n += 1
        except HTTPException as exc:
            if int(getattr(exc, "status_code", 0) or 0) == 409:
                _log.info("auto compare skipped (busy) job=%s", j.id)
            continue
        except Exception:
            _log.exception("auto compare failed job=%s task=%s", j.id, task_id)
            continue
    return n


def schedule_auto_compare_for_task(task_id: str, batch_id: str) -> None:
    """Fire-and-forget auto compare on a daemon thread (own DB session)."""

    def _run() -> None:
        from ..db import SessionLocal

        db = SessionLocal()
        try:
            try_auto_compare_for_task(db, task_id, batch_id)
        except Exception:
            _log.exception(
                "bg auto compare failed task=%s batch=%s", task_id, batch_id
            )
        finally:
            db.close()

    threading.Thread(
        target=_run,
        name=f"biz-auto-cmp-{str(batch_id)[:8]}",
        daemon=True,
    ).start()
