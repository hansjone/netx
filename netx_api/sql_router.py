"""Read-only SQL query routes (hardened)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .db import get_db
from .sql_guard import run_select

router = APIRouter(tags=["sql"])


@router.post("/v1/sql/query")
def sql_query(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)) -> dict:
    """
    Read-only SQL for legacy Excel batches.

    Safety: SELECT only, no CTE/WITH, no multi-statement, optional batch_id bind.
    """
    payload = payload or {}
    return run_select(
        db,
        str(payload.get("sql") or ""),
        params={"batch_id": str(payload.get("batch_id") or "").strip()},
        limit=int(payload.get("limit") or 200),
        require_batch_id_param=True,
    )


@router.post("/v1/sql/ume_query")
def sql_ume_query(payload: dict[str, Any] | None = None, db: Session = Depends(get_db)) -> dict:
    """
    Read-only SQL for UME current alarms / inventory tables only.
    """
    payload = payload or {}
    timeout = int(payload.get("statement_timeout_ms") or 0)
    timeout = max(0, min(timeout, 30000))
    return run_select(
        db,
        str(payload.get("sql") or ""),
        limit=int(payload.get("limit") or 200),
        statement_timeout_ms=timeout,
        allowed_tables={"ume_alarms_current", "ume_inventory_ne"},
    )
