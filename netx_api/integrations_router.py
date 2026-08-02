"""Thin integrations / health routes (liveness vs readiness)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from .db import get_db

router = APIRouter(tags=["health"])


@router.get("/health/live", status_code=200)
def health_live() -> dict[str, str]:
    """Process liveness — no DB or upstream checks."""
    return {"status": "ok", "probe": "live"}


@router.get("/health/ready", status_code=200)
def health_ready(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Readiness — verifies database connectivity."""
    try:
        db.execute(sql_text("select 1"))
        return {"status": "ok", "probe": "ready", "db": "up"}
    except Exception as exc:
        return {"status": "down", "probe": "ready", "db": "down", "error": str(exc)[:240]}
