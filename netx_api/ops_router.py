"""Ops overview APIs (live task board)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .auth_deps import AuthContext, require_user
from .db import get_db
from .ops_tasks_service import list_ops_tasks

router = APIRouter(prefix="/v1/ops", tags=["ops"])


@router.get("/tasks")
def api_ops_tasks(
    ctx: Annotated[AuthContext, Depends(require_user)],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _ = ctx
    return list_ops_tasks(db)
