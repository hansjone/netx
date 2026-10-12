"""Collection quality of a compare side: is a missing row a business loss or a data problem?

Compare results are only as good as the two batches behind them. This module
reads *receipts* (``BizStateBatchCommand``: counts and status, never ``raw_text``)
to tell apart:

- ``not_collected``  – a side never ran a command (or the slice) for the sheet
- ``suspect_parse``  – the device printed lines but the parser produced no rows
- ``no_data``        – both sides ran fine and are really empty
- ``ok``             – ordinary comparison
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy.orm import Session

from ..models import BizStateBatchCommand
from .compare_rules import scalar_text

# More than this many CLI lines with zero parsed rows is not "an empty table".
SUSPECT_RAW_LINES = 5
DATA_STATES = ("ok", "no_data", "suspect_parse", "not_collected")
UNJUDGED_STATES = ("no_data", "not_collected")


def _has_receipts(db: Session, batch_id: str) -> bool:
    return bool(batch_id) and db.query(BizStateBatchCommand.id).filter(
        BizStateBatchCommand.batch_id == batch_id).limit(1).first() is not None


def side_receipts(db: Session, batch_id: str, metric_id: str) -> list[dict[str, Any]]:
    if not batch_id or not metric_id:
        return []
    rows = (
        db.query(
            BizStateBatchCommand.profile_id, BizStateBatchCommand.parse_status,
            BizStateBatchCommand.row_count, BizStateBatchCommand.raw_line_count,
            BizStateBatchCommand.declared_total,
        )
        .filter(BizStateBatchCommand.batch_id == batch_id, BizStateBatchCommand.metric_id == metric_id)
        .all()
    )
    return [
        {
            "profile_id": str(r[0] or ""), "parse_status": str(r[1] or ""),
            "row_count": int(r[2] or 0), "raw_line_count": int(r[3] or 0),
            "declared_total": int(r[4] or 0),
        }
        for r in rows
    ]


def collect_quality(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for r in receipts:
        key = str(r.get("parse_status") or "unknown")
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "commands": len(receipts),
        "parse_status": statuses,
        "row_count": sum(int(r.get("row_count") or 0) for r in receipts),
        "raw_line_count": sum(int(r.get("raw_line_count") or 0) for r in receipts),
        "declared_total": sum(int(r.get("declared_total") or 0) for r in receipts),
        "profile_ids": sorted({str(r.get("profile_id") or "") for r in receipts if r.get("profile_id")}),
    }


def sheet_slice(sheet: Mapping[str, Any]) -> dict[str, str]:
    """``{field: value}`` the sheet pins with top-level ``eq`` row filters."""
    out: dict[str, str] = {}
    for f in sheet.get("row_filters") or []:
        if not isinstance(f, Mapping) or "any" in f or "all" in f:
            continue
        if str(f.get("op") or "eq").strip().lower() not in ("eq", "==", "ci_eq"):
            continue
        field = str(f.get("field") or "").strip()
        value = scalar_text(f.get("value")).strip().lower()
        if field and value:
            out[field] = value
    return out


def covers_slice(
    receipts: Sequence[Mapping[str, Any]], wanted: Mapping[str, str], *, profile_slice: Any = None,
) -> bool:
    """True when at least one successful command could have produced ``wanted``.

    A command without a declared slice (or unknown profile) covers everything.
    """
    if profile_slice is None:
        from .profiles import get_profile

        def profile_slice(pid: str) -> dict[str, str]:  # noqa: F811
            prof = get_profile(pid) if pid else None
            return dict(prof.slice) if prof is not None and prof.slice else {}

    for r in receipts:
        if str(r.get("parse_status") or "") in ("failed", "skipped_custom"):
            continue
        declared = profile_slice(str(r.get("profile_id") or ""))
        if all(
            f not in declared or str(declared[f]).strip().lower() == v
            for f, v in wanted.items()
        ):
            return True
    return False


def classify_sheet(
    *,
    before: Sequence[Mapping[str, Any]] | None,
    after: Sequence[Mapping[str, Any]] | None,
    before_rows: int,
    after_rows: int,
    wanted_slice: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """``(data_state, reasons)`` for one sheet; reasons name the offending side.

    ``None`` receipts mean the batch carries no receipts at all (imported or
    legacy data): that side cannot be judged and is trusted as-is.
    """
    wanted = dict(wanted_slice or {})
    reasons: list[str] = []
    for side, receipts in (("before", before), ("after", after)):
        if receipts is None:
            continue
        if not receipts:
            reasons.append(f"{side}:no_command")
        elif wanted and not covers_slice(receipts, wanted):
            reasons.append(f"{side}:slice_not_collected")
    if reasons:
        return "not_collected", reasons
    for side, receipts in (("before", before), ("after", after)):
        if receipts is None:
            continue
        q = collect_quality(receipts)
        if q["row_count"] == 0 and q["raw_line_count"] > SUSPECT_RAW_LINES:
            reasons.append(f"{side}:parsed_0_of_{q['raw_line_count']}_lines")
        elif q["commands"] and q["parse_status"].get("failed", 0) == q["commands"]:
            reasons.append(f"{side}:all_commands_failed")
    if reasons:
        return "suspect_parse", reasons
    if before_rows == 0 and after_rows == 0:
        return "no_data", ["both_sides_empty"]
    return "ok", []


def assess_sheet(
    db: Session, sheet: Mapping[str, Any], *, before_batch_id: str, after_batch_id: str,
    before_rows: int, after_rows: int,
) -> dict[str, Any]:
    """Quality block stored on a sheet summary."""
    metric_id = str(sheet.get("metric_id") or "")
    before = side_receipts(db, before_batch_id, metric_id) if _has_receipts(db, before_batch_id) else None
    after = side_receipts(db, after_batch_id, metric_id) if _has_receipts(db, after_batch_id) else None
    state, reasons = classify_sheet(
        before=before, after=after, before_rows=before_rows, after_rows=after_rows,
        wanted_slice=sheet_slice(sheet),
    )
    return {
        "data_state": state,
        "data_state_reasons": reasons,
        "collect_quality": {
            "before": collect_quality(before or []), "after": collect_quality(after or []),
            "unknown_sides": [n for n, r in (("before", before), ("after", after)) if r is None],
        },
    }


def command_coverage(
    db: Session, *, before_batch_id: str, after_batch_id: str,
) -> dict[str, Any]:
    """Collected "metric{slice}" labels per side and their difference.

    Profile ids differ per vendor (``zte.x`` vs ``huawei.x``), so the comparison
    is on what a command *produces*: its metric and slice.
    """
    from .profiles import get_profile

    def labels(batch_id: str) -> set[str]:
        if not batch_id:
            return set()
        rows = db.query(BizStateBatchCommand.metric_id, BizStateBatchCommand.profile_id).filter(
            BizStateBatchCommand.batch_id == batch_id, BizStateBatchCommand.metric_id != "",
        ).distinct().all()
        out: set[str] = set()
        for metric_id, profile_id in rows:
            prof = get_profile(str(profile_id or "")) if profile_id else None
            sl = dict(prof.slice) if prof is not None and prof.slice else {}
            suffix = ",".join(f"{k}={v}" for k, v in sorted(sl.items()))
            out.add(f"{metric_id}{{{suffix}}}" if suffix else str(metric_id))
        return out

    b, a = labels(before_batch_id), labels(after_batch_id)
    return {"before": sorted(b), "after": sorted(a),
            "only_before": sorted(b - a), "only_after": sorted(a - b)}
