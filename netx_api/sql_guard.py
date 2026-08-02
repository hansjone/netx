"""Hardened read-only SQL helpers for AI / power-user query endpoints."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import settings

_SQL_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|call|copy|vacuum|analyze|"
    r"execute|prepare|deallocate|listen|notify|load|reindex|cluster|refresh|security|"
    r"set\s+role|set\s+session|into\s+outfile|pg_read_file|lo_import|lo_export)\b",
    flags=re.IGNORECASE,
)
_WITH_RE = re.compile(r"^\s*with\b", flags=re.IGNORECASE)
_COMMENT_RE = re.compile(r"/\*.*?\*/|--.*?$", flags=re.IGNORECASE | re.DOTALL | re.MULTILINE)
_FROM_JOIN_RE = re.compile(r"\b(?:from|join)\s+([a-zA-Z0-9_\"\.]+)", flags=re.IGNORECASE)

_readonly_engine: Engine | None = None
_ReadonlySession: sessionmaker | None = None


def _ensure_utc(value: datetime) -> datetime | None:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _strip_sql_comments(sql: str) -> str:
    return _COMMENT_RE.sub(" ", sql)


def validate_select_sql(sql: str, *, allowed_tables: set[str] | None = None) -> str:
    cleaned = _strip_sql_comments(str(sql or "")).strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="sql_required")
    if ";" in cleaned:
        raise HTTPException(status_code=400, detail="single_statement_only")
    if _WITH_RE.search(cleaned):
        raise HTTPException(status_code=400, detail="with_cte_not_allowed")
    low = cleaned.lower().lstrip()
    if not low.startswith("select"):
        raise HTTPException(status_code=400, detail="select_only")
    if _SQL_FORBIDDEN_RE.search(cleaned):
        raise HTTPException(status_code=400, detail="forbidden_keyword")
    # Block obvious catalog / other-schema probes in the text.
    if re.search(r"\bpg_catalog\b|\binformation_schema\b|\bpg_toast\b", cleaned, re.I):
        raise HTTPException(status_code=400, detail="catalog_not_allowed")
    if allowed_tables is not None:
        refs = _FROM_JOIN_RE.findall(cleaned)
        if not refs:
            raise HTTPException(status_code=400, detail="from_required")
        for ref in refs:
            normalized = str(ref).strip().strip('"')
            if "." in normalized:
                normalized = normalized.split(".")[-1]
            if normalized.lower() not in allowed_tables:
                raise HTTPException(status_code=400, detail=f"ume_table_not_allowed:{normalized}")
    return cleaned


def get_sql_session(db: Session) -> Session:
    """Prefer dedicated read-only engine when configured; else reuse request session."""
    url = str(getattr(settings, "sql_readonly_database_url", "") or "").strip()
    if not url:
        return db
    global _readonly_engine, _ReadonlySession
    if _readonly_engine is None:
        _readonly_engine = create_engine(url, pool_pre_ping=True)
        _ReadonlySession = sessionmaker(bind=_readonly_engine, autoflush=False, autocommit=False)
    assert _ReadonlySession is not None
    return _ReadonlySession()


def run_select(
    db: Session,
    sql: str,
    *,
    params: dict[str, Any] | None = None,
    limit: int = 200,
    statement_timeout_ms: int = 0,
    allowed_tables: set[str] | None = None,
    require_batch_id_param: bool = False,
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 200), 2000))
    cleaned = validate_select_sql(sql, allowed_tables=allowed_tables)
    bind_params = dict(params or {})
    if require_batch_id_param:
        batch_id = str(bind_params.get("batch_id") or "").strip()
        if not batch_id:
            raise HTTPException(status_code=400, detail="batch_id_required")
        if ":batch_id" not in cleaned:
            raise HTTPException(status_code=400, detail="batch_id_param_required(:batch_id)")
    wrapped = f"select * from ({cleaned}) as q limit {limit}"
    own_session = False
    session = db
    url = str(getattr(settings, "sql_readonly_database_url", "") or "").strip()
    if url:
        session = get_sql_session(db)
        own_session = session is not db
    try:
        if statement_timeout_ms > 0:
            try:
                if str(getattr(getattr(session, "bind", None), "dialect", None).name).lower().startswith("postgres"):
                    session.execute(
                        sql_text("SET LOCAL statement_timeout = :ms"),
                        {"ms": int(statement_timeout_ms)},
                    )
                    session.execute(sql_text("SET LOCAL search_path TO public"))
            except Exception:
                pass
        res = session.execute(sql_text(wrapped), bind_params)
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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"sql_failed:{str(exc)[:240]}") from exc
    finally:
        if own_session:
            session.close()
