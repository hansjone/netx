"""UME current/history alarms, aggregates, diagnostics."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .key_alert_config import (
    get_key_alert_monitor_config,
    invalidate_key_alert_config_cache,
    set_key_alert_monitor_config,
)
from .key_alert_matcher import (
    invalidate_key_alert_rule_cache,
    normalize_match_type,
    parse_rule_ne_types_payload,
    rule_match_type,
    rule_match_value,
    rule_ne_types,
    rule_storage_key,
    serialize_rule_ne_types,
)
from .models import (
    UmeAlarmCurrent,
    UmeAlarmHistory,
    UmeInventoryNE,
    UmeKeyAlertForwardLog,
    UmeKeyAlertRule,
    UmeSyncJob,
)
from .oclaw_alarm_forwarder import (
    forwarder_status,
    request_forwarder_reconnect,
)
from .ume_alarm_ws import (
    cancel_alarm_subscription_manual,
    clear_local_alarm_subscription_manual,
    establish_alarm_subscription_manual,
    get_alarms_coordination_status,
    get_subscription_status,
    get_ws_connection_status,
    get_ws_logs,
    request_ws_reconnect,
)
from .ume_support import (
    UME_KNOWN_RUNTIME_TASKS,
    _aggregate_rows,
    _ensure_utc,
    _list_runtime_tasks,
    _normalize_netx_lang,
    _parse_time,
    _protocol_bucket_label,
    _request_force_sync_after_resume,
    _runtime_pause_task,
    _runtime_resume_task,
    _ume_alarm_host_name,
    _ume_alarm_ne_group_key,
    _ume_client,
    _ume_error_kind,
    _clear_force_resume_hints,
)
from .ume_sync_service import sync_alarms_current, sync_alarms_history_full, sync_inventory_full
from .ume_token_store import clear_shared_token

_log = logging.getLogger("netx.ume.router")
router = APIRouter(tags=["ume"])

@router.get("/v1/ume/alarms")
def ume_list_alarms(
    severity: str | None = Query(default=None),
    is_cleared: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    host_name: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    time_from: str | None = Query(default=None, description="Filter by last_seen_at >="),
    time_to: str | None = Query(default=None, description="Filter by last_seen_at <="),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmCurrent.perceived_severity == str(severity).strip())
    if is_cleared and str(is_cleared).strip():
        stmt = stmt.filter(UmeAlarmCurrent.is_cleared == str(is_cleared).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmCurrent.ne_id == str(ne_id).strip())
    hn = str(host_name or "").strip()
    if hn:
        stmt = stmt.filter(
            UmeAlarmCurrent.host_name.contains(hn) | UmeInventoryNE.host_name.contains(hn)
        )
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmCurrent.alarm_key.contains(kw)
            | UmeAlarmCurrent.object_name.contains(kw)
            | UmeAlarmCurrent.native_probable_cause.contains(kw)
            | UmeAlarmCurrent.notification_id.contains(kw)
            | UmeAlarmCurrent.host_name.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
            | UmeInventoryNE.host_name.contains(kw)
        )
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    if dt_from:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))
    total = int(stmt.count())
    rows = (
        stmt.order_by(
            UmeAlarmCurrent.time_created.desc(),
            UmeAlarmCurrent.last_seen_at.desc(),
            UmeAlarmCurrent.alarm_key.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    items = [
        {
            "alarm_key": str(alarm.alarm_key or ""),
            "ne_id": str(alarm.ne_id or ""),
            "ne_name": str((ne.ne_name if ne else "") or ""),
            "user_label": str((ne.user_label if ne else "") or ""),
            "host_name": _ume_alarm_host_name(alarm, ne),
            "ne_type": str((ne.ne_type if ne else "") or ""),
            "object_name": str(alarm.object_name or ""),
            "event_type": str(alarm.event_type or ""),
            "native_probable_cause": str(alarm.native_probable_cause or ""),
            "notification_id": str(alarm.notification_id or ""),
            "perceived_severity": str(alarm.perceived_severity or ""),
            "is_cleared": str(alarm.is_cleared or ""),
            "time_created": str(alarm.time_created or ""),
            "last_seen_at": (_ensure_utc(alarm.last_seen_at) or datetime.now(timezone.utc)).isoformat(),
        }
        for alarm, ne in rows
    ]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "meta": {"time_filter_field": "last_seen_at"},
        "items": items,
    }


@router.get("/v1/ume/alarms/fields")
def ume_alarms_fields() -> dict[str, Any]:
    """List all queryable field names for UME raw alarm query."""
    alarm_cols = [str(c.name) for c in UmeAlarmCurrent.__table__.columns]  # type: ignore[attr-defined]
    ne_cols = [str(c.name) for c in UmeInventoryNE.__table__.columns]  # type: ignore[attr-defined]
    selectable_fields = [f"alarm_{x}" for x in alarm_cols] + [f"ne_{x}" for x in ne_cols] + ["ne_exists"]
    order_by_allowed = ["last_seen_at", "time_created", "perceived_severity", "event_type", "ne_id"]
    return {
        "alarm_fields": alarm_cols,
        "ne_fields": ne_cols,
        "selectable_fields": selectable_fields,
        "order_by_allowed": order_by_allowed,
    }


def _serialize_ume_alarm_raw_row(
    alarm: UmeAlarmCurrent, ne: UmeInventoryNE | None, selected_fields: set[str] | None = None
) -> dict[str, Any]:
    selected = selected_fields or set()
    use_all = len(selected) == 0
    out: dict[str, Any] = {}
    for c in UmeAlarmCurrent.__table__.columns:  # type: ignore[attr-defined]
        name = str(c.name)
        v = getattr(alarm, name, None)
        key = f"alarm_{name}"
        if not use_all and key not in selected:
            continue
        if hasattr(v, "isoformat"):
            try:
                if isinstance(v, datetime):
                    out[key] = (_ensure_utc(v) or v).isoformat()
                else:
                    out[key] = v.isoformat()
                continue
            except Exception:
                pass
        out[key] = v
    if ne is None:
        if use_all or "ne_exists" in selected:
            out["ne_exists"] = False
        return out
    if use_all or "ne_exists" in selected:
        out["ne_exists"] = True
    for c in UmeInventoryNE.__table__.columns:  # type: ignore[attr-defined]
        name = str(c.name)
        v = getattr(ne, name, None)
        key = f"ne_{name}"
        if not use_all and key not in selected:
            continue
        if hasattr(v, "isoformat"):
            try:
                if isinstance(v, datetime):
                    out[key] = (_ensure_utc(v) or v).isoformat()
                else:
                    out[key] = v.isoformat()
                continue
            except Exception:
                pass
        out[key] = v
    return out


def _extract_ume_raw_group_field(alarm: UmeAlarmCurrent, ne: UmeInventoryNE | None, field: str) -> str:
    key = str(field or "").strip()
    if not key:
        return ""
    if key.startswith("alarm_"):
        attr = key[len("alarm_") :]
        return str(getattr(alarm, attr, "") or "")
    if key.startswith("ne_"):
        attr = key[len("ne_") :]
        if key == "ne_exists":
            return "1" if ne is not None else "0"
        if key == "ne_host_name":
            hn = str(getattr(alarm, "host_name", "") or "").strip()
            if hn:
                return hn
        if ne is None:
            return ""
        return str(getattr(ne, attr, "") or "")
    return ""


@router.get("/v1/ume/alarms/raw")
def ume_alarms_raw(
    severity: str | None = Query(default=None),
    is_cleared: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    time_from: str | None = Query(default=None),
    time_to: str | None = Query(default=None),
    order_by: str = Query(default="last_seen_at"),
    order: str = Query(default="desc"),
    select_fields: str | None = Query(default=None, description="comma-separated alarm_*/ne_* fields"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmCurrent.perceived_severity == str(severity).strip())
    if is_cleared and str(is_cleared).strip():
        stmt = stmt.filter(UmeAlarmCurrent.is_cleared == str(is_cleared).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmCurrent.ne_id == str(ne_id).strip())
    if event_type and str(event_type).strip():
        stmt = stmt.filter(UmeAlarmCurrent.event_type.contains(str(event_type).strip()))
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmCurrent.alarm_key.contains(kw)
            | UmeAlarmCurrent.object_name.contains(kw)
            | UmeAlarmCurrent.native_probable_cause.contains(kw)
            | UmeAlarmCurrent.event_type.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
        )
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    if dt_from:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))

    allowed_order_by = {
        "last_seen_at": UmeAlarmCurrent.last_seen_at,
        "time_created": UmeAlarmCurrent.time_created,
        "perceived_severity": UmeAlarmCurrent.perceived_severity,
        "event_type": UmeAlarmCurrent.event_type,
        "ne_id": UmeAlarmCurrent.ne_id,
    }
    col = allowed_order_by.get(str(order_by or "").strip(), UmeAlarmCurrent.last_seen_at)
    if str(order or "").strip().lower() == "asc":
        stmt = stmt.order_by(col.asc())
    else:
        stmt = stmt.order_by(col.desc())

    selected_fields: set[str] = set()
    fields_meta = ume_alarms_fields()
    selectable_fields = set(str(x) for x in (fields_meta.get("selectable_fields") or []))
    order_by_allowed = [str(x) for x in (fields_meta.get("order_by_allowed") or [])]
    if select_fields and str(select_fields).strip():
        selected_fields = {x.strip() for x in str(select_fields).split(",") if x.strip()}
        invalid = [x for x in selected_fields if x not in selectable_fields]
        if invalid:
            raise HTTPException(status_code=400, detail=f"invalid_select_fields:{','.join(sorted(invalid)[:20])}")

    total = int(stmt.count())
    rows = stmt.offset((int(page) - 1) * int(page_size)).limit(int(page_size)).all()
    return {
        "total": total,
        "page": int(page),
        "page_size": int(page_size),
        "select_fields": sorted(selected_fields) if selected_fields else [],
        "meta": {
            "available_fields": sorted(selectable_fields),
            "order_by_allowed": order_by_allowed,
            "time_filter_field": "last_seen_at",
        },
        "items": [_serialize_ume_alarm_raw_row(alarm, ne, selected_fields) for alarm, ne in rows],
    }


_HOST_MISSING_LABEL = "(host_name missing)"
_HOST_GROUP_FIELDS = frozenset({"alarm_host_name", "ne_host_name", "ne_user_label"})


def _normalize_ne_bucket_key(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s or s.lower() in {"unknown", "none", "null"}:
        return _HOST_MISSING_LABEL
    # Bare UUID or ME{uuid}
    if len(s) >= 32 and s.count("-") >= 4 and " " not in s:
        return _HOST_MISSING_LABEL
    if s.startswith("ME{") and s.endswith("}"):
        return _HOST_MISSING_LABEL
    return s


def _raw_group_bucket_key(field: str, value: str) -> str:
    if field in _HOST_GROUP_FIELDS:
        return _normalize_ne_bucket_key(value)
    s = str(value or "").strip()
    return s if s else "(empty)"


@router.get("/v1/ume/alarms/aggregate/raw")
def ume_alarms_aggregate_raw(
    group_by: str = Query(default="alarm_perceived_severity"),
    group_by2: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    is_cleared: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    time_from: str | None = Query(default=None),
    time_to: str | None = Query(default=None),
    exclude_missing_host: bool = Query(
        default=True,
        description="When grouping by host/user_label fields, omit (host_name missing) from buckets.",
    ),
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    fields_meta = ume_alarms_fields()
    selectable_fields = set(str(x) for x in (fields_meta.get("selectable_fields") or []))
    g1 = str(group_by or "").strip()
    g2 = str(group_by2 or "").strip()
    if g1 not in selectable_fields:
        raise HTTPException(status_code=400, detail=f"invalid_group_by:{g1}")
    if g2 and g2 not in selectable_fields:
        raise HTTPException(status_code=400, detail=f"invalid_group_by2:{g2}")

    stmt = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmCurrent.perceived_severity == str(severity).strip())
    if is_cleared and str(is_cleared).strip():
        stmt = stmt.filter(UmeAlarmCurrent.is_cleared == str(is_cleared).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmCurrent.ne_id == str(ne_id).strip())
    if event_type and str(event_type).strip():
        stmt = stmt.filter(UmeAlarmCurrent.event_type.contains(str(event_type).strip()))
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmCurrent.alarm_key.contains(kw)
            | UmeAlarmCurrent.object_name.contains(kw)
            | UmeAlarmCurrent.native_probable_cause.contains(kw)
            | UmeAlarmCurrent.event_type.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
        )
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    if dt_from:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        stmt = stmt.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))

    rows = stmt.order_by(UmeAlarmCurrent.last_seen_at.desc()).all()
    merged: dict[tuple[str, str], int] = {}
    by_ne_missing = 0
    for alarm, ne in rows:
        nk1 = _raw_group_bucket_key(g1, _extract_ume_raw_group_field(alarm, ne, g1))
        nk2 = _raw_group_bucket_key(g2, _extract_ume_raw_group_field(alarm, ne, g2)) if g2 else ""
        missing_hit = False
        if g1 in _HOST_GROUP_FIELDS and nk1 == _HOST_MISSING_LABEL:
            missing_hit = True
        if g2 and g2 in _HOST_GROUP_FIELDS and nk2 == _HOST_MISSING_LABEL:
            missing_hit = True
        if missing_hit:
            by_ne_missing += 1
            if exclude_missing_host:
                continue
        kk = (nk1, nk2)
        merged[kk] = int(merged.get(kk, 0)) + 1
    buckets = sorted(merged.items(), key=lambda x: x[1], reverse=True)[: int(limit)]

    return {
        "total": len(rows),
        "group_by": g1,
        "group_by2": g2 or None,
        "by_ne_missing": by_ne_missing,
        "exclude_missing_host": bool(exclude_missing_host),
        "meta": {
            "available_fields": sorted(selectable_fields),
            "group_by_allowed": sorted(selectable_fields),
            "applied_filters": {
                "severity": str(severity or "").strip() or None,
                "is_cleared": str(is_cleared or "").strip() or None,
                "ne_id": str(ne_id or "").strip() or None,
                "event_type": str(event_type or "").strip() or None,
                "keyword": str(keyword or "").strip() or None,
                "time_from": str(time_from or "").strip() or None,
                "time_to": str(time_to or "").strip() or None,
            },
            "time_filter_field": "last_seen_at",
            "limit": int(limit),
            "host_missing_label": _HOST_MISSING_LABEL,
        },
        "buckets": [
            {
                "key": k1,
                "key2": (k2 if g2 else None),
                "count": int(v),
            }
            for (k1, k2), v in buckets
        ],
    }


@router.get("/v1/ume/alarms/aggregate")
def ume_alarms_aggregate(
    top_ne: int = Query(
        default=50,
        ge=0,
        le=5000,
        description="Max NE buckets to return (0 = all). Severity buckets are always complete.",
    ),
    exclude_missing_host: bool = Query(
        default=True,
        description="When true, omit (host_name missing) from by_ne ranking (count still in by_ne_missing).",
    ),
    severity: str | None = Query(
        default=None,
        description="Optional perceived_severity filter (e.g. critical) for top-NE ranking.",
    ),
    time_from: str | None = Query(default=None, description="Filter by last_seen_at >="),
    time_to: str | None = Query(default=None, description="Filter by last_seen_at <="),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Aggregate current alarms by severity and NE (SQL group-by; top_ne capped)."""
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    sev = str(severity or "").strip() or None

    base = db.query(UmeAlarmCurrent)
    if sev:
        base = base.filter(UmeAlarmCurrent.perceived_severity == sev)
    if dt_from:
        base = base.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        base = base.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))
    # Prefer .count() — with_entities(func.count()) without select_from can return 1.
    total = int(base.count())

    sev_q = db.query(UmeAlarmCurrent.perceived_severity, func.count())
    if sev:
        sev_q = sev_q.filter(UmeAlarmCurrent.perceived_severity == sev)
    if dt_from:
        sev_q = sev_q.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        sev_q = sev_q.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))
    sev_rows = (
        sev_q.group_by(UmeAlarmCurrent.perceived_severity).order_by(func.count().desc()).all()
    )
    by_severity = [
        {"key": (str(k).strip() if k is not None and str(k).strip() else "unknown"), "count": int(v)}
        for k, v in sev_rows
    ]

    # Display key: host_name / inventory host / user_label only (never bare UUID).
    ne_key = func.coalesce(
        func.nullif(func.trim(UmeAlarmCurrent.host_name), ""),
        func.nullif(func.trim(UmeInventoryNE.host_name), ""),
        func.nullif(func.trim(UmeInventoryNE.user_label), ""),
        _HOST_MISSING_LABEL,
    )
    ne_q = (
        db.query(ne_key.label("ne_key"), func.count().label("cnt"))
        .select_from(UmeAlarmCurrent)
        .outerjoin(UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id)
    )
    if sev:
        ne_q = ne_q.filter(UmeAlarmCurrent.perceived_severity == sev)
    if dt_from:
        ne_q = ne_q.filter(UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        ne_q = ne_q.filter(UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None))
    ne_q = ne_q.group_by(ne_key).order_by(func.count().desc())

    all_ne_rows = ne_q.all()
    by_ne_missing = 0
    named_counts: dict[str, int] = {}
    for k, v in all_ne_rows:
        label = _normalize_ne_bucket_key(k)
        cnt = int(v)
        if label == _HOST_MISSING_LABEL:
            by_ne_missing += cnt
        else:
            named_counts[label] = int(named_counts.get(label, 0)) + cnt
    named_rows = sorted(named_counts.items(), key=lambda kv: kv[1], reverse=True)
    by_ne_total = len(named_rows) + (1 if by_ne_missing else 0)
    ranked = list(named_rows)
    if not exclude_missing_host and by_ne_missing:
        ranked.append((_HOST_MISSING_LABEL, by_ne_missing))
        ranked.sort(key=lambda kv: int(kv[1]), reverse=True)
    if top_ne > 0:
        ranked = ranked[: int(top_ne)]
    by_ne = [{"key": str(k), "count": int(v)} for k, v in ranked]

    seen_bounds = db.query(
        func.min(UmeAlarmCurrent.last_seen_at),
        func.max(UmeAlarmCurrent.last_seen_at),
    )
    if sev:
        seen_bounds = seen_bounds.filter(UmeAlarmCurrent.perceived_severity == sev)
    if dt_from:
        seen_bounds = seen_bounds.filter(
            UmeAlarmCurrent.last_seen_at >= dt_from.replace(tzinfo=None)
        )
    if dt_to:
        seen_bounds = seen_bounds.filter(
            UmeAlarmCurrent.last_seen_at <= dt_to.replace(tzinfo=None)
        )
    min_seen, max_seen = seen_bounds.one()

    return {
        "total": total,
        "by_severity": by_severity,
        "by_ne": by_ne,
        "by_ne_total": by_ne_total,
        "by_ne_missing": by_ne_missing,
        "top_ne": int(top_ne),
        "exclude_missing_host": bool(exclude_missing_host),
        "severity": sev,
        "meta": {
            "time_filter_field": "last_seen_at",
            "last_seen_min": (_ensure_utc(min_seen).isoformat() if min_seen else None),
            "last_seen_max": (_ensure_utc(max_seen).isoformat() if max_seen else None),
        },
    }


@router.get("/v1/ume/diagnostics")
def ume_diagnostics(
    lang: str | None = Query(default=None),
    top_n: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Fast SQL diagnostics; top_ne excludes missing host_name; includes data freshness."""
    total = int(db.query(func.count()).select_from(UmeAlarmCurrent).scalar() or 0)
    min_seen, max_seen = db.query(
        func.min(UmeAlarmCurrent.last_seen_at),
        func.max(UmeAlarmCurrent.last_seen_at),
    ).one()

    sev_rows = (
        db.query(UmeAlarmCurrent.perceived_severity, func.count())
        .group_by(UmeAlarmCurrent.perceived_severity)
        .order_by(func.count().desc())
        .all()
    )
    by_severity = [
        {"key": (str(k).strip() if k is not None and str(k).strip() else "unknown"), "count": int(v)}
        for k, v in sev_rows
    ]

    event_rows = (
        db.query(UmeAlarmCurrent.event_type, func.count())
        .group_by(UmeAlarmCurrent.event_type)
        .order_by(func.count().desc())
        .limit(top_n)
        .all()
    )
    top_event_types = [
        {"key": (str(k).strip() if k is not None and str(k).strip() else "unknown"), "count": int(v)}
        for k, v in event_rows
    ]

    # Prefer real UME alarmCode from raw_json when present (Postgres JSON text).
    top_alarm_codes: list[dict[str, Any]] = []
    try:
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSON

        code_expr = func.coalesce(
            func.nullif(
                func.json_extract_path_text(cast(UmeAlarmCurrent.raw_json, JSON), "alarmCode"),
                "",
            ),
            "(none)",
        )
        code_rows = (
            db.query(code_expr.label("code"), func.count())
            .group_by(code_expr)
            .order_by(func.count().desc())
            .limit(top_n)
            .all()
        )
        top_alarm_codes = [{"key": str(k), "count": int(v)} for k, v in code_rows]
    except Exception:
        # SQLite / non-JSON: fall back to native_probable_cause tops.
        cause_rows = (
            db.query(UmeAlarmCurrent.native_probable_cause, func.count())
            .group_by(UmeAlarmCurrent.native_probable_cause)
            .order_by(func.count().desc())
            .limit(top_n)
            .all()
        )
        top_alarm_codes = [
            {
                "key": (str(k).strip() if k is not None and str(k).strip() else "unknown"),
                "count": int(v),
            }
            for k, v in cause_rows
        ]

    ne_key = func.coalesce(
        func.nullif(func.trim(UmeAlarmCurrent.host_name), ""),
        func.nullif(func.trim(UmeInventoryNE.host_name), ""),
        func.nullif(func.trim(UmeInventoryNE.user_label), ""),
        _HOST_MISSING_LABEL,
    )
    ne_rows = (
        db.query(ne_key.label("ne_key"), func.count().label("cnt"))
        .select_from(UmeAlarmCurrent)
        .outerjoin(UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id)
        .group_by(ne_key)
        .order_by(func.count().desc())
        .all()
    )
    by_ne_missing = 0
    named_counts: dict[str, int] = {}
    for k, v in ne_rows:
        label = _normalize_ne_bucket_key(k)
        cnt = int(v)
        if label == _HOST_MISSING_LABEL:
            by_ne_missing += cnt
            continue
        named_counts[label] = int(named_counts.get(label, 0)) + cnt
    top_ne = [
        {"key": label, "count": cnt}
        for label, cnt in sorted(named_counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    ]

    # Protocol buckets still need text classify; stream rows lightly (cause+event only).
    lang_norm = _normalize_netx_lang(lang)
    proto_counts: dict[str, int] = {}
    light = db.query(
        UmeAlarmCurrent.event_type,
        UmeAlarmCurrent.native_probable_cause,
        UmeAlarmCurrent.object_name,
    ).yield_per(2000)
    for event_type, cause, obj in light:
        blob = " | ".join([str(event_type or ""), str(cause or ""), str(obj or "")])
        bucket = _protocol_bucket_label(blob, lang=lang_norm)
        proto_counts[bucket] = int(proto_counts.get(bucket, 0)) + 1
    protocol_summary = sorted(proto_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

    return {
        "source": "ume_alarms_current",
        "total_alarms": total,
        "severity_summary": by_severity,
        "top_event_types": top_event_types,
        # Backward-compatible alias — historically event_type, now real alarmCode when possible.
        "top_alarm_codes": top_alarm_codes,
        "top_ne": top_ne,
        "by_ne_missing": by_ne_missing,
        "protocol_summary": [{"key": k, "count": v} for k, v in protocol_summary],
        "meta": {
            "last_seen_min": (_ensure_utc(min_seen).isoformat() if min_seen else None),
            "last_seen_max": (_ensure_utc(max_seen).isoformat() if max_seen else None),
            "time_filter_field": "last_seen_at",
            "host_missing_label": _HOST_MISSING_LABEL,
        },
    }


@router.get("/v1/ume/alarms/history")
def ume_list_alarms_history(
    severity: str | None = Query(default=None),
    ne_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    time_from: str | None = Query(default=None),
    time_to: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    stmt = db.query(UmeAlarmHistory, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmHistory.ne_id == UmeInventoryNE.ne_id
    )
    if severity and str(severity).strip():
        stmt = stmt.filter(UmeAlarmHistory.perceived_severity == str(severity).strip())
    if ne_id and str(ne_id).strip():
        stmt = stmt.filter(UmeAlarmHistory.ne_id == str(ne_id).strip())
    kw = str(keyword or "").strip()
    if kw:
        stmt = stmt.filter(
            UmeAlarmHistory.alarm_key.contains(kw)
            | UmeAlarmHistory.object_name.contains(kw)
            | UmeAlarmHistory.native_probable_cause.contains(kw)
            | UmeInventoryNE.ne_name.contains(kw)
            | UmeInventoryNE.user_label.contains(kw)
            | UmeInventoryNE.ip_address.contains(kw)
        )
    dt_from = _parse_time(time_from)
    dt_to = _parse_time(time_to)
    if dt_from:
        stmt = stmt.filter(UmeAlarmHistory.last_seen_at >= dt_from.replace(tzinfo=None))
    if dt_to:
        stmt = stmt.filter(UmeAlarmHistory.last_seen_at <= dt_to.replace(tzinfo=None))
    total = int(stmt.count())
    rows = stmt.order_by(UmeAlarmHistory.last_seen_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [
        {
            "alarm_key": str(alarm.alarm_key or ""),
            "ne_id": str(alarm.ne_id or ""),
            "ne_name": str((ne.ne_name if ne else "") or ""),
            "user_label": str((ne.user_label if ne else "") or ""),
            "object_name": str(alarm.object_name or ""),
            "event_type": str(alarm.event_type or ""),
            "native_probable_cause": str(alarm.native_probable_cause or ""),
            "perceived_severity": str(alarm.perceived_severity or ""),
            "is_cleared": str(alarm.is_cleared or ""),
            "time_created": str(alarm.time_created or ""),
            "last_seen_at": (_ensure_utc(alarm.last_seen_at) or datetime.now(timezone.utc)).isoformat(),
        }
        for alarm, ne in rows
    ]
    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/v1/ume/alarms/history/aggregate")
def ume_alarms_history_aggregate(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(UmeAlarmHistory, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmHistory.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_ne = _aggregate_rows(rows, lambda x: (x[1].user_label if x[1] else "") or (x[1].ne_name if x[1] else "") or x[0].ne_id)
    by_date = _aggregate_rows(rows, lambda x: str(x[0].time_created or "")[:10])
    return {"total": len(rows), "by_severity": by_severity, "by_ne": by_ne, "by_date": by_date}


