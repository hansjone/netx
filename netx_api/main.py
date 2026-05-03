from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from io import StringIO
import time
import re
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session
from typing import Any
import uvicorn

from .ap_client import analyze_with_oclaw, health_with_oclaw
from .config import settings
from .db import Base, SessionLocal, engine
from .importer import aggregate_alarms, import_alarm_excel, query_alarms
from .models import AiAnalyzeHistory, AlarmBatch, AlarmNorm, ImportErrorRow
from .models import ImportJob
from .parser_config import load_parser_config
from .schemas import (
    AlarmAggregateBucket,
    AlarmAggregateResponse,
    AiAnalyzeHistoryItem,
    AiAnalyzeHistoryResponse,
    AlarmItem,
    AlarmQueryResponse,
    BatchSummary,
    ImportJobItem,
    ImportJobListResponse,
)

app = FastAPI(title="netx ops tool", version="0.1.0")
parser_cfg = load_parser_config()

_SQL_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|call|copy|vacuum|analyze)\b",
    flags=re.IGNORECASE,
)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    # All timestamps are stored as UTC in DB (naive). Treat naive as UTC.
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    try:
        return dt.astimezone(timezone.utc)
    except Exception:
        return dt


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/v1/sql/query")
def sql_query(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)) -> dict:
    """
    Read-only SQL query endpoint for AI power users.

    Safety constraints:
    - SELECT only, single statement (no ';')
    - forbid DDL/DML keywords
    - enforce max rows (server-side LIMIT wrapper)
    - require batch_id param and require SQL contains ':batch_id'
    """
    payload = payload or {}
    sql = str(payload.get("sql") or "").strip()
    batch_id = str(payload.get("batch_id") or "").strip()
    limit = int(payload.get("limit") or 200)
    limit = max(1, min(limit, 2000))
    if not sql:
        raise HTTPException(status_code=400, detail="sql_required")
    if ";" in sql:
        raise HTTPException(status_code=400, detail="single_statement_only")
    low = sql.lower().lstrip()
    if not low.startswith("select"):
        raise HTTPException(status_code=400, detail="select_only")
    if _SQL_FORBIDDEN_RE.search(sql):
        raise HTTPException(status_code=400, detail="forbidden_keyword")
    if not batch_id:
        raise HTTPException(status_code=400, detail="batch_id_required")
    if ":batch_id" not in sql:
        raise HTTPException(status_code=400, detail="batch_id_param_required(:batch_id)")
    wrapped = f"select * from ({sql}) as q limit {limit}"
    try:
        res = db.execute(sql_text(wrapped), {"batch_id": batch_id})
        cols = list(res.keys())
        raw_rows = res.fetchall()
        rows: list[list[Any]] = []
        for r in raw_rows:
            out_row: list[Any] = []
            for v in list(r):
                if isinstance(v, datetime):
                    out_row.append(((_ensure_utc(v) or v).isoformat().replace("+00:00", "Z")))
                else:
                    out_row.append(v)
            rows.append(out_row)
        return {"ok": True, "columns": cols, "rows": rows, "limit": limit}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"sql_failed:{str(exc)[:240]}") from exc


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    # Best-effort schema evolution for new columns (no migrations framework).
    # Safe for Postgres (IF NOT EXISTS); ignored on failure.
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS relevancy VARCHAR(128) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS l3vpn_peer_ne VARCHAR(256) DEFAULT ''")
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS service VARCHAR(256) DEFAULT ''")
            conn.exec_driver_sql(
                "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS affected_client_service_number INTEGER DEFAULT 0"
            )
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS intermittence_count INTEGER DEFAULT 0")
            conn.exec_driver_sql("ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS me_level VARCHAR(128) DEFAULT ''")
    except Exception:
        pass


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/integrations/status")
def integrations_status(db: Session = Depends(get_db)) -> dict:
    # netx api is up if this handler executes; still verify DB + oclaw bridge separately.
    netx_api = {"status": "up"}

    db_status: dict = {"status": "unknown"}
    try:
        t0 = time.monotonic()
        db.execute(sql_text("select 1"))
        db_status = {"status": "up", "latency_ms": int((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        db_status = {"status": "down", "error": str(exc)[:240]}

    oclaw_status: dict = {"status": "unknown"}
    try:
        t0 = time.monotonic()
        data = health_with_oclaw()
        oclaw_status = {
            "status": "up",
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "http_status": int(data.get("status_code") or 200),
            "detail": data.get("data") or {},
        }
    except Exception as exc:
        msg = str(exc)
        http_status = None
        kind = "unknown"
        if " 401 " in msg or "401" in msg:
            kind = "auth"
            http_status = 401
        elif " 404 " in msg or "404" in msg:
            kind = "not_found"
            http_status = 404
        elif "timeout" in msg.lower():
            kind = "timeout"
        elif "connect" in msg.lower():
            kind = "connect"
        else:
            kind = "other"
        oclaw_status = {"status": "down", "error_kind": kind, "http_status": http_status, "error": msg[:240]}

    return {"netx_api": netx_api, "db": db_status, "oclaw_bridge": oclaw_status}


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "mode": "api_only",
        "message": "netx UI is served by Vite frontend only",
        "frontend_url": settings.frontend_url,
        "api_health": "/health",
        "api_status": "/v1/integrations/status",
    }


@app.post("/v1/alarms/import", response_model=BatchSummary)
async def import_alarms(file: UploadFile = File(...), db: Session = Depends(get_db)) -> BatchSummary:
    filename = str(file.filename or "alarm.xlsx")
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="only_excel_supported_in_phase1")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty_file")
    batch = import_alarm_excel(db, filename=filename, content=content, parser=parser_cfg)

    try:
        job = ImportJob(
            kind="alarms",
            file_name=filename,
            batch_id=str(batch.batch_id),
            ok=1,
            summary=f"success={int(batch.success_rows)} failed={int(batch.failed_rows)}",
        )
        db.add(job)
        db.commit()
    except Exception:
        db.rollback()
    return BatchSummary(
        batch_id=str(batch.batch_id),
        total_rows=int(batch.total_rows or 0),
        success_rows=int(batch.success_rows or 0),
        failed_rows=int(batch.failed_rows or 0),
        status=str(batch.status or ""),
        created_at=_ensure_utc(batch.created_at) or datetime.now(timezone.utc),
    )


@app.post("/v1/logs/import")
async def import_logs(file: UploadFile = File(...)) -> dict:
    # Placeholder for Phase 2: logs parsing + storage + query.
    filename = str(file.filename or "logs.zip")
    if not filename:
        raise HTTPException(status_code=400, detail="filename_required")
    raise HTTPException(status_code=501, detail="logs_import_not_implemented")


@app.get("/v1/jobs", response_model=ImportJobListResponse)
def list_jobs(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> ImportJobListResponse:
    rows = db.query(ImportJob).order_by(ImportJob.created_at.desc()).limit(limit).all()
    items = [
        ImportJobItem(
            id=int(x.id),
            kind=str(x.kind),
            file_name=str(x.file_name or ""),
            batch_id=str(x.batch_id) if x.batch_id else None,
            ok=bool(int(x.ok or 0)),
            summary=str(x.summary or ""),
            created_at=_ensure_utc(x.created_at) or datetime.now(timezone.utc),
        )
        for x in rows
    ]
    return ImportJobListResponse(items=items)


@app.get("/v1/batches")
def list_batches(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> dict:
    rows = db.query(AlarmBatch).order_by(AlarmBatch.created_at.desc()).limit(limit).all()
    return {
        "items": [
            {
                "batch_id": x.batch_id,
                "source_file": x.source_file,
                "status": x.status,
                "total_rows": x.total_rows,
                "success_rows": x.success_rows,
                "failed_rows": x.failed_rows,
                "created_at": (_ensure_utc(x.created_at) or datetime.now(timezone.utc)).isoformat(),
            }
            for x in rows
        ]
    }


@app.get("/v1/batches/{batch_id}/errors.csv")
def download_batch_errors(batch_id: str, db: Session = Depends(get_db)):
    rows = (
        db.query(ImportErrorRow)
        .filter(ImportErrorRow.batch_id == batch_id)
        .order_by(ImportErrorRow.id.asc())
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="batch_or_errors_not_found")
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["row_no", "reason", "raw_json"])
    for r in rows:
        writer.writerow([r.row_no, r.reason, r.raw_json])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"content-disposition": f'attachment; filename="batch_{batch_id}_errors.csv"'},
    )


@app.delete("/v1/batches/{batch_id}")
def delete_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = db.get(AlarmBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    try:
        alarms_deleted = int(
            db.query(AlarmNorm).filter(AlarmNorm.batch_id == batch_id).delete(synchronize_session=False)
        )
        errors_deleted = int(
            db.query(ImportErrorRow).filter(ImportErrorRow.batch_id == batch_id).delete(synchronize_session=False)
        )
        jobs_deleted = int(
            db.query(ImportJob).filter(ImportJob.batch_id == batch_id).delete(synchronize_session=False)
        )
        db.delete(batch)
        db.commit()
        return {
            "ok": True,
            "batch_id": batch_id,
            "deleted": {
                "batch": 1,
                "alarms": alarms_deleted,
                "errors": errors_deleted,
                "jobs": jobs_deleted,
            },
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"delete_batch_failed: {exc}") from exc


@app.delete("/v1/batches")
def delete_all_batches(db: Session = Depends(get_db)) -> dict:
    try:
        alarms_deleted = int(db.query(AlarmNorm).delete(synchronize_session=False))
        errors_deleted = int(db.query(ImportErrorRow).delete(synchronize_session=False))
        jobs_deleted = int(db.query(ImportJob).delete(synchronize_session=False))
        batches_deleted = int(db.query(AlarmBatch).delete(synchronize_session=False))
        db.commit()
        return {
            "ok": True,
            "deleted": {
                "batches": batches_deleted,
                "alarms": alarms_deleted,
                "errors": errors_deleted,
                "jobs": jobs_deleted,
            },
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"delete_all_batches_failed: {exc}") from exc


@app.get("/v1/diagnostics")
def diagnostics(
    batch_id: str = Query(...),
    db: Session = Depends(get_db),
) -> dict:
    sev_rows = aggregate_alarms(db, group_by="severity_norm", batch_id=batch_id)
    code_rows = aggregate_alarms(db, group_by="alarm_code", batch_id=batch_id)[:10]
    ne_rows = aggregate_alarms(db, group_by="ne_name", batch_id=batch_id)[:10]
    total = sum(count for _, count in sev_rows)

    def _protocol_bucket(text: str) -> str:
        t = (text or "").upper()
        # IP/MPLS control plane
        if any(x in t for x in ("BGP", "OSPF", "ISIS", "LDP", "MPLS", "L3VPN", "VPN")):
            return "IP/MPLS"
        # Ethernet / packet
        if any(x in t for x in ("ETH", "GE", "10GE", "25GE", "40GE", "100GE", "XGE")):
            return "ETH"
        # OTN / optical
        if any(x in t for x in ("OTN", "ODU", "OCH", "OMS", "OSC", "DWDM", "WDM", "ROADM")):
            return "OTN/光"
        # Timing / clock
        if any(x in t for x in ("CLOCK", "SYNC", "PTP", "1588", "BITS", "TOD")):
            return "时钟"
        # Power
        if any(x in t for x in ("PWR", "POWER", "PSU", "BAT", "BATT")):
            return "电源"
        return "其他"

    proto_counts: dict[str, int] = {}
    for name, desc, code, raw in (
        db.query(AlarmNorm.alarm_name, AlarmNorm.description, AlarmNorm.alarm_code, AlarmNorm.raw_json)
        .filter(AlarmNorm.batch_id == batch_id)
        .all()
    ):
        blob = " | ".join([str(code or ""), str(name or ""), str(desc or ""), str(raw or "")])
        k = _protocol_bucket(blob)
        proto_counts[k] = int(proto_counts.get(k, 0)) + 1
    protocol_summary = sorted(proto_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return {
        "batch_id": batch_id,
        "total_alarms": int(total),
        "severity_summary": [{"key": k, "count": v} for k, v in sev_rows],
        "top_alarm_codes": [{"key": k, "count": v} for k, v in code_rows],
        "top_ne": [{"key": k, "count": v} for k, v in ne_rows],
        "protocol_summary": [{"key": k, "count": v} for k, v in protocol_summary],
    }


@app.post("/v1/ap/analyze")
def ap_analyze(payload: dict, db: Session = Depends(get_db)) -> dict:
    batch_id = str(payload.get("batch_id") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not batch_id or not question:
        raise HTTPException(status_code=400, detail="batch_id_and_question_required")
    diag = diagnostics(batch_id=batch_id, db=db)
    analysis_request_id = str(payload.get("analysis_request_id") or "").strip()
    filters_obj = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    req = {
        "analysis_request_id": analysis_request_id,
        "question": question,
        "dataset_ref": {
            "batch_id": batch_id,
            "filters": filters_obj or {},
        },
        "context": {
            "severity_summary": diag["severity_summary"],
            "top_alarm_codes": diag["top_alarm_codes"],
            "top_ne": diag["top_ne"],
            "protocol_summary": diag.get("protocol_summary", []),
            "findings": diag.get("findings", []),
        },
        "constraints": payload.get("constraints") or {"language": "zh-CN", "max_points": 6},
        "interaction_mode": "expert",
        "specialist": "ops",
    }
    ok = False
    err = ""
    oclaw_resp: dict[str, Any] | None = None
    try:
        oclaw_resp = analyze_with_oclaw(req)
        ok = bool(oclaw_resp.get("ok")) if isinstance(oclaw_resp, dict) else False
    except Exception as exc:
        err = str(exc)
    # Persist Q&A history (best-effort; never block response).
    try:
        answer = ""
        if isinstance(oclaw_resp, dict):
            answer = str(oclaw_resp.get("answer") or "").strip()
        row = AiAnalyzeHistory(
            analysis_request_id=analysis_request_id,
            batch_id=batch_id,
            question=question,
            filters_json=json.dumps(filters_obj or {}, ensure_ascii=False),
            ok=1 if ok else 0,
            answer=answer,
            error=err,
            evidence_json=json.dumps(diag or {}, ensure_ascii=False),
            created_at=datetime.utcnow(),
        )
        db.add(row)
        db.commit()
    except Exception:
        db.rollback()
    if not ok:
        return {
            "ok": False,
            "error": err or "oclaw_bridge_unavailable",
            "fallback_diagnostics": diag,
            "batch_id": batch_id,
            "question": question,
        }
    return {"ok": True, "batch_id": batch_id, "question": question, "diagnostics": diag, "oclaw": oclaw_resp}


@app.get("/v1/ap/history", response_model=AiAnalyzeHistoryResponse)
def ap_history(
    batch_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AiAnalyzeHistoryResponse:
    q = db.query(AiAnalyzeHistory)
    if batch_id and str(batch_id).strip():
        q = q.filter(AiAnalyzeHistory.batch_id == str(batch_id).strip())
    total = int(q.count())
    rows = (
        q.order_by(AiAnalyzeHistory.id.desc())
        .offset((int(page) - 1) * int(page_size))
        .limit(int(page_size))
        .all()
    )
    items: list[AiAnalyzeHistoryItem] = []
    for r in rows:
        try:
            filters = json.loads(str(r.filters_json or "{}"))
        except Exception:
            filters = {}
        items.append(
            AiAnalyzeHistoryItem(
                id=int(r.id),
                analysis_request_id=str(r.analysis_request_id or ""),
                batch_id=str(r.batch_id or ""),
                question=str(r.question or ""),
                filters=filters if isinstance(filters, dict) else {},
                ok=bool(int(r.ok or 0) == 1),
                answer=str(r.answer or ""),
                error=str(r.error or ""),
                created_at=_ensure_utc(r.created_at) or datetime.now(timezone.utc),
            )
        )
    return AiAnalyzeHistoryResponse(total=total, page=page, page_size=page_size, items=items)


@app.get("/v1/alarms", response_model=AlarmQueryResponse)
def list_alarms(
    batch_id: str | None = Query(default=None),
    alarm_code: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    ne_name: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> AlarmQueryResponse:
    total, rows = query_alarms(
        db,
        batch_id=batch_id,
        alarm_code=alarm_code,
        severity=severity,
        ne_name=ne_name,
        page=page,
        page_size=page_size,
    )
    items = [
        AlarmItem(
            id=x.id,
            batch_id=x.batch_id,
            row_no=x.row_no,
            alarm_time=_ensure_utc(x.alarm_time) or datetime.now(timezone.utc),
            severity_norm=x.severity_norm,
            severity_raw=x.severity_raw,
            ne_name=x.ne_name,
            alarm_code=x.alarm_code,
            description=x.description,
            ack_state=x.ack_state,
        )
        for x in rows
    ]
    return AlarmQueryResponse(total=total, page=page, page_size=page_size, items=items)


@app.get("/v1/alarms/fields")
def alarms_fields() -> dict:
    """List all columns in alarms_norm for power querying."""
    cols = []
    try:
        cols = [str(c.name) for c in AlarmNorm.__table__.columns]  # type: ignore[attr-defined]
    except Exception:
        cols = []
    return {"items": cols}


def _serialize_alarm_row(row: AlarmNorm) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in AlarmNorm.__table__.columns:  # type: ignore[attr-defined]
        name = str(c.name)
        v = getattr(row, name, None)
        if hasattr(v, "isoformat"):
            try:
                if isinstance(v, datetime):
                    out[name] = (_ensure_utc(v) or v).isoformat()
                else:
                    out[name] = v.isoformat()  # datetime/date
                continue
            except Exception:
                pass
        out[name] = v
    return out


@app.get("/v1/alarms/raw")
def alarms_raw(
    batch_id: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    alarm_code: str | None = Query(default=None),
    ne_name: str | None = Query(default=None),
    q: str | None = Query(default=None, description="free text contains on alarm_code/ne_name/description/service"),
    order_by: str = Query(default="alarm_time"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    """
    Power query: return **all columns** for alarms_norm rows.

    Safety constraints:
    - batch_id is required (avoid unbounded scans)
    - order_by is whitelisted
    - page_size capped
    """
    bid = str(batch_id or "").strip()
    if not bid:
        raise HTTPException(status_code=400, detail="batch_id_required")
    stmt = db.query(AlarmNorm).filter(AlarmNorm.batch_id == bid)
    if severity and str(severity).strip():
        stmt = stmt.filter(AlarmNorm.severity_norm == str(severity).strip())
    if alarm_code and str(alarm_code).strip():
        stmt = stmt.filter(AlarmNorm.alarm_code.contains(str(alarm_code).strip()))
    if ne_name and str(ne_name).strip():
        stmt = stmt.filter(AlarmNorm.ne_name.contains(str(ne_name).strip()))
    if q and str(q).strip():
        qw = str(q).strip()
        stmt = stmt.filter(
            (AlarmNorm.alarm_code.contains(qw))
            | (AlarmNorm.ne_name.contains(qw))
            | (AlarmNorm.description.contains(qw))
            | (AlarmNorm.service.contains(qw))
        )
    allowed_order_by = {
        "id": AlarmNorm.id,
        "alarm_time": AlarmNorm.alarm_time,
        "severity_norm": AlarmNorm.severity_norm,
        "ne_name": AlarmNorm.ne_name,
        "alarm_code": AlarmNorm.alarm_code,
    }
    col = allowed_order_by.get(str(order_by or "").strip(), AlarmNorm.alarm_time)
    if str(order or "").strip().lower() == "asc":
        stmt = stmt.order_by(col.asc())
    else:
        stmt = stmt.order_by(col.desc())
    total = int(stmt.count())
    rows = (
        stmt.offset((int(page) - 1) * int(page_size))
        .limit(int(page_size))
        .all()
    )
    return {
        "total": total,
        "page": int(page),
        "page_size": int(page_size),
        "items": [_serialize_alarm_row(r) for r in rows],
    }


@app.get("/v1/alarms/aggregate", response_model=AlarmAggregateResponse)
def alarms_aggregate(
    group_by: str = Query(default="severity_norm"),
    batch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AlarmAggregateResponse:
    try:
        rows = aggregate_alarms(db, group_by=group_by, batch_id=batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AlarmAggregateResponse(
        group_by=group_by,
        buckets=[AlarmAggregateBucket(key=k, count=v) for k, v in rows],
    )


@app.get("/v1/batches/{batch_id}")
def get_batch(batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = db.get(AlarmBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch_not_found")
    errors = (
        db.query(ImportErrorRow)
        .filter(ImportErrorRow.batch_id == batch_id)
        .order_by(ImportErrorRow.id.asc())
        .limit(20)
        .all()
    )
    return {
        "batch": BatchSummary.model_validate(batch, from_attributes=True).model_dump(),
        "errors_preview": [
            {"row_no": e.row_no, "reason": e.reason, "raw_json": e.raw_json}
            for e in errors
        ],
    }


if __name__ == "__main__":
    uvicorn.run("netx_api.main:app", host=settings.host, port=settings.port, reload=False)
