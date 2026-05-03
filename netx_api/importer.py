from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import AlarmBatch, AlarmNorm, ImportErrorRow
from .parser_config import ParserConfig


def _dt(v: Any) -> datetime | None:
    if v is None:
        return None
    text = str(v).strip()
    if not text or text.lower() == "nan":
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    dt = parsed.to_pydatetime()
    # Convention: Excel source time is local timezone time.
    # Convert to UTC before persisting; keep DB as naive UTC for compatibility.
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        dt = dt.replace(tzinfo=local_tz)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _str(v: Any) -> str:
    if v is None:
        return ""
    text = str(v).strip()
    if text.lower() == "nan":
        return ""
    return text


def _int(v: Any) -> int:
    if v is None:
        return 0
    text = str(v).strip()
    if not text or text.lower() == "nan":
        return 0
    try:
        return int(float(text))
    except Exception:
        return 0


def import_alarm_excel(db: Session, filename: str, content: bytes, parser: ParserConfig) -> AlarmBatch:
    df = pd.read_excel(BytesIO(content))
    headers = [str(h) for h in df.columns]

    col_alarm_time = parser.resolve_col(headers, "alarm_time")
    col_clear_time = parser.resolve_col(headers, "clear_time")
    col_severity = parser.resolve_col(headers, "severity_raw")
    col_alarm_code = parser.resolve_col(headers, "alarm_code")
    col_ne_name = parser.resolve_col(headers, "ne_name")
    col_ne_id = parser.resolve_col(headers, "ne_id")
    col_site_name = parser.resolve_col(headers, "site_name")
    col_desc = parser.resolve_col(headers, "description")
    col_ack = parser.resolve_col(headers, "ack_state")
    col_state = parser.resolve_col(headers, "clear_state")
    col_relevancy = parser.resolve_col(headers, "relevancy")
    col_l3vpn_peer_ne = parser.resolve_col(headers, "l3vpn_peer_ne")
    col_service = parser.resolve_col(headers, "service")
    col_affected = parser.resolve_col(headers, "affected_client_service_number")
    col_intermittence = parser.resolve_col(headers, "intermittence_count")
    col_me_level = parser.resolve_col(headers, "me_level")

    batch = AlarmBatch(
        source_file=filename,
        parser_version=parser.parser_version,
        dict_version=parser.dict_version,
        total_rows=int(len(df.index)),
        status="processing",
    )
    db.add(batch)
    db.flush()

    success_rows = 0
    failed_rows = 0

    for idx, row in df.iterrows():
        row_no = int(idx) + 2  # Excel header is row 1
        alarm_time = _dt(row.get(col_alarm_time)) if col_alarm_time else None
        raw_severity = _str(row.get(col_severity)) if col_severity else ""
        alarm_code = _str(row.get(col_alarm_code)) if col_alarm_code else ""
        ne_name = _str(row.get(col_ne_name)) if col_ne_name else ""
        ne_id = _str(row.get(col_ne_id)) if col_ne_id else ""
        description = _str(row.get(col_desc)) if col_desc else ""
        relevancy = _str(row.get(col_relevancy)) if col_relevancy else ""
        l3vpn_peer_ne = _str(row.get(col_l3vpn_peer_ne)) if col_l3vpn_peer_ne else ""
        service = _str(row.get(col_service)) if col_service else ""
        affected = _int(row.get(col_affected)) if col_affected else 0
        intermittence = _int(row.get(col_intermittence)) if col_intermittence else 0
        me_level = _str(row.get(col_me_level)) if col_me_level else ""

        if not alarm_time:
            failed_rows += 1
            db.add(
                ImportErrorRow(
                    batch_id=batch.batch_id,
                    row_no=row_no,
                    reason="missing_or_invalid_alarm_time",
                    raw_json=json.dumps(row.to_dict(), ensure_ascii=False, default=str),
                )
            )
            continue
        if not (ne_name or ne_id):
            failed_rows += 1
            db.add(
                ImportErrorRow(
                    batch_id=batch.batch_id,
                    row_no=row_no,
                    reason="missing_ne_name_and_ne_id",
                    raw_json=json.dumps(row.to_dict(), ensure_ascii=False, default=str),
                )
            )
            continue
        db.add(
            AlarmNorm(
                batch_id=batch.batch_id,
                row_no=row_no,
                alarm_time=alarm_time,
                clear_time=_dt(row.get(col_clear_time)) if col_clear_time else None,
                severity_raw=raw_severity,
                severity_norm=parser.normalize_severity(raw_severity),
                ne_name=ne_name,
                ne_id=ne_id,
                site_name=_str(row.get(col_site_name)) if col_site_name else "",
                alarm_code=alarm_code,
                alarm_name="",
                description=description,
                ack_state=_str(row.get(col_ack)) if col_ack else "",
                clear_state=_str(row.get(col_state)) if col_state else "",
                relevancy=relevancy,
                l3vpn_peer_ne=l3vpn_peer_ne,
                service=service,
                affected_client_service_number=affected,
                intermittence_count=intermittence,
                me_level=me_level,
                vendor=settings.vendor,
                source_type=settings.source_type,
                source_file=filename,
                raw_json=json.dumps(row.to_dict(), ensure_ascii=False, default=str),
            )
        )
        success_rows += 1

    batch.success_rows = success_rows
    batch.failed_rows = failed_rows
    batch.status = "done"
    db.commit()
    db.refresh(batch)
    return batch


def query_alarms(
    db: Session,
    *,
    batch_id: str | None,
    severity: str | None,
    alarm_code: str | None,
    ne_name: str | None,
    page: int,
    page_size: int,
) -> tuple[int, list[AlarmNorm]]:
    stmt = select(AlarmNorm)
    if batch_id:
        stmt = stmt.where(AlarmNorm.batch_id == batch_id)
    if severity:
        stmt = stmt.where(AlarmNorm.severity_norm == severity)
    if alarm_code:
        stmt = stmt.where(AlarmNorm.alarm_code.contains(alarm_code))
    if ne_name:
        stmt = stmt.where(AlarmNorm.ne_name.contains(ne_name))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = list(
        db.scalars(
            stmt.order_by(AlarmNorm.alarm_time.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    )
    return total, rows


def aggregate_alarms(db: Session, *, group_by: str, batch_id: str | None) -> list[tuple[str, int]]:
    if group_by not in {"severity_norm", "alarm_code", "ne_name"}:
        raise ValueError("unsupported_group_by")
    col = getattr(AlarmNorm, group_by)
    stmt = select(col, func.count()).group_by(col).order_by(func.count().desc())
    if batch_id:
        stmt = stmt.where(AlarmNorm.batch_id == batch_id)
    return [(str(key or ""), int(count)) for key, count in db.execute(stmt).all()]
