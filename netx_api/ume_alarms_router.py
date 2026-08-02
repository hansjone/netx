"""UME current/history alarms, aggregates, diagnostics."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
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
    return {"total": total, "page": page, "page_size": page_size, "items": items}


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
    counts: dict[tuple[str, str], int] = {}
    for alarm, ne in rows:
        k1 = _extract_ume_raw_group_field(alarm, ne, g1)
        k2 = _extract_ume_raw_group_field(alarm, ne, g2) if g2 else ""
        kk = (k1, k2)
        counts[kk] = int(counts.get(kk, 0)) + 1
    buckets = sorted(counts.items(), key=lambda x: x[1], reverse=True)[: int(limit)]
    return {
        "total": len(rows),
        "group_by": g1,
        "group_by2": g2 or None,
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
        },
        "buckets": [
            {"key": k1, "key2": (k2 if g2 else None), "count": int(v)}
            for (k1, k2), v in buckets
        ],
    }


@router.get("/v1/ume/alarms/aggregate")
def ume_alarms_aggregate(db: Session = Depends(get_db)) -> dict[str, Any]:
    rows = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_ne = _aggregate_rows(rows, lambda x: _ume_alarm_ne_group_key(x[0], x[1]))
    return {"total": len(rows), "by_severity": by_severity, "by_ne": by_ne}


@router.get("/v1/ume/diagnostics")
def ume_diagnostics(
    lang: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.query(UmeAlarmCurrent, UmeInventoryNE).outerjoin(
        UmeInventoryNE, UmeAlarmCurrent.ne_id == UmeInventoryNE.ne_id
    ).all()
    by_severity = _aggregate_rows(rows, lambda x: x[0].perceived_severity)
    by_alarm_code = _aggregate_rows(rows, lambda x: x[0].event_type)[:10]
    by_ne = _aggregate_rows(rows, lambda x: _ume_alarm_ne_group_key(x[0], x[1]))[:10]

    lang_norm = _normalize_netx_lang(lang)
    proto_counts: dict[str, int] = {}
    for alarm, ne in rows:
        blob = " | ".join(
            [
                str(alarm.event_type or ""),
                str(alarm.native_probable_cause or ""),
                str(alarm.object_name or ""),
                str(ne.ne_name if ne else ""),
                str(ne.user_label if ne else ""),
                str(ne.ip_address if ne else ""),
            ]
        )
        bucket = _protocol_bucket_label(blob, lang=lang_norm)
        proto_counts[bucket] = int(proto_counts.get(bucket, 0)) + 1
    protocol_summary = sorted(proto_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "source": "ume_alarms_current",
        "total_alarms": len(rows),
        "severity_summary": [{"key": k, "count": v} for k, v in by_severity],
        "top_alarm_codes": [{"key": k, "count": v} for k, v in by_alarm_code],
        "top_ne": [{"key": k, "count": v} for k, v in by_ne],
        "protocol_summary": [{"key": k, "count": v} for k, v in protocol_summary],
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


