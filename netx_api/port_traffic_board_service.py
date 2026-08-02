"""Named traffic wall boards (multi-panel ops views)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import PortTrafficBoard, PortTrafficPanel, PortTrafficTarget
from .port_traffic_schemas import (
    PortTrafficBoardCreate,
    PortTrafficBoardOut,
    PortTrafficBoardPanelsPut,
    PortTrafficBoardSummaryOut,
    PortTrafficBoardUpdate,
    PortTrafficPanelIn,
    PortTrafficPanelOut,
)
from .port_traffic_service import _target_out, _utcnow


def _panel_out(db: Session, row: PortTrafficPanel) -> PortTrafficPanelOut:
    target = db.get(PortTrafficTarget, str(row.target_id or "")) if row.target_id else None
    baseline = (
        db.get(PortTrafficTarget, str(row.baseline_target_id or ""))
        if row.baseline_target_id
        else None
    )
    stale = not target or str(target.status or "") != "active"
    return PortTrafficPanelOut(
        id=str(row.id),
        board_id=str(row.board_id or ""),
        title=str(row.title or ""),
        target_id=str(row.target_id or ""),
        range_hours=int(row.range_hours or 24),
        baseline=str(row.baseline or "off"),
        offset_hours=int(row.offset_hours or 0),
        ahead_hours=max(0, int(row.ahead_hours if getattr(row, "ahead_hours", None) is not None else 1)),
        baseline_target_id=str(row.baseline_target_id or ""),
        y_mode=str(row.y_mode or "auto"),
        ord=int(row.ord or 0),
        col_span=max(1, int(row.col_span or 1)),
        row_span=max(1, int(row.row_span or 1)),
        stale=stale,
        target=_target_out(target) if target else None,
        baseline_target=_target_out(baseline) if baseline else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _board_summary(db: Session, board: PortTrafficBoard) -> PortTrafficBoardSummaryOut:
    count = (
        db.query(func.count(PortTrafficPanel.id))
        .filter(PortTrafficPanel.board_id == board.id)
        .scalar()
        or 0
    )
    return PortTrafficBoardSummaryOut(
        id=str(board.id),
        name=str(board.name or ""),
        remark=str(board.remark or ""),
        cols=max(1, min(4, int(board.cols or 2))),
        panel_count=int(count),
        created_by=str(board.created_by or ""),
        updated_by=str(board.updated_by or ""),
        created_at=board.created_at,
        updated_at=board.updated_at,
    )


def _board_out(db: Session, board: PortTrafficBoard) -> PortTrafficBoardOut:
    panels = (
        db.query(PortTrafficPanel)
        .filter(PortTrafficPanel.board_id == board.id)
        .order_by(PortTrafficPanel.ord, PortTrafficPanel.created_at)
        .all()
    )
    summary = _board_summary(db, board)
    return PortTrafficBoardOut(
        **summary.model_dump(),
        panels=[_panel_out(db, p) for p in panels],
    )


def _validate_panel_target(db: Session, target_id: str, *, allow_missing: bool = False) -> None:
    tid = str(target_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="target_id_required")
    row = db.get(PortTrafficTarget, tid)
    if not row:
        if allow_missing:
            return
        raise HTTPException(status_code=400, detail="target_not_found")


def _apply_panels(
    db: Session,
    board: PortTrafficBoard,
    panels: list[PortTrafficPanelIn],
) -> None:
    cols = max(1, min(4, int(board.cols or 2)))
    db.query(PortTrafficPanel).filter(PortTrafficPanel.board_id == board.id).delete(
        synchronize_session=False
    )
    now = _utcnow()
    for i, item in enumerate(panels):
        _validate_panel_target(db, item.target_id)
        if item.baseline_target_id:
            _validate_panel_target(db, item.baseline_target_id, allow_missing=True)
        pid = str(item.id or "").strip() or uuid4().hex
        db.add(
            PortTrafficPanel(
                id=pid,
                board_id=str(board.id),
                title=str(item.title or "")[:256],
                target_id=str(item.target_id).strip(),
                range_hours=int(item.range_hours),
                baseline=str(item.baseline or "off"),
                offset_hours=int(item.offset_hours or 0),
                ahead_hours=max(0, min(24, int(item.ahead_hours if item.ahead_hours is not None else 1))),
                baseline_target_id=str(item.baseline_target_id or "").strip(),
                y_mode=str(item.y_mode or "auto"),
                ord=int(item.ord if item.ord is not None else i),
                col_span=max(1, min(cols, int(item.col_span or 1))),
                row_span=max(1, min(4, int(item.row_span or 1))),
                created_at=now,
                updated_at=now,
            )
        )


def list_boards(db: Session) -> list[PortTrafficBoardSummaryOut]:
    rows = db.query(PortTrafficBoard).order_by(PortTrafficBoard.updated_at.desc()).all()
    return [_board_summary(db, r) for r in rows]


def get_board(db: Session, board_id: str) -> PortTrafficBoardOut:
    board = db.get(PortTrafficBoard, board_id)
    if not board:
        raise HTTPException(status_code=404, detail="board_not_found")
    return _board_out(db, board)


def create_board(
    db: Session,
    body: PortTrafficBoardCreate,
    *,
    actor_user_id: str = "",
) -> PortTrafficBoardOut:
    now = _utcnow()
    board = PortTrafficBoard(
        id=uuid4().hex,
        name=str(body.name).strip()[:256],
        remark=str(body.remark or "")[:1024],
        cols=max(1, min(4, int(body.cols or 2))),
        created_by=str(actor_user_id or ""),
        updated_by=str(actor_user_id or ""),
        created_at=now,
        updated_at=now,
    )
    db.add(board)
    db.flush()
    if body.panels:
        _apply_panels(db, board, body.panels)
    db.commit()
    db.refresh(board)
    return _board_out(db, board)


def update_board(
    db: Session,
    board_id: str,
    body: PortTrafficBoardUpdate,
    *,
    actor_user_id: str = "",
) -> PortTrafficBoardOut:
    board = db.get(PortTrafficBoard, board_id)
    if not board:
        raise HTTPException(status_code=404, detail="board_not_found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        board.name = str(data["name"]).strip()[:256]
    if "remark" in data and data["remark"] is not None:
        board.remark = str(data["remark"])[:1024]
    if "cols" in data and data["cols"] is not None:
        board.cols = max(1, min(4, int(data["cols"])))
    board.updated_by = str(actor_user_id or "")
    board.updated_at = _utcnow()
    db.commit()
    db.refresh(board)
    return _board_out(db, board)


def put_board_panels(
    db: Session,
    board_id: str,
    body: PortTrafficBoardPanelsPut,
    *,
    actor_user_id: str = "",
) -> PortTrafficBoardOut:
    board = db.get(PortTrafficBoard, board_id)
    if not board:
        raise HTTPException(status_code=404, detail="board_not_found")
    _apply_panels(db, board, body.panels or [])
    board.updated_by = str(actor_user_id or "")
    board.updated_at = _utcnow()
    db.commit()
    db.refresh(board)
    return _board_out(db, board)


def delete_board(db: Session, board_id: str) -> dict[str, Any]:
    board = db.get(PortTrafficBoard, board_id)
    if not board:
        raise HTTPException(status_code=404, detail="board_not_found")
    db.query(PortTrafficPanel).filter(PortTrafficPanel.board_id == board_id).delete(
        synchronize_session=False
    )
    db.delete(board)
    db.commit()
    return {"ok": True, "id": board_id}


def delete_panels_for_targets(db: Session, target_ids: list[str]) -> None:
    ids = [str(x) for x in target_ids if x]
    if not ids:
        return
    db.query(PortTrafficPanel).filter(PortTrafficPanel.target_id.in_(ids)).delete(
        synchronize_session=False
    )
    db.query(PortTrafficPanel).filter(PortTrafficPanel.baseline_target_id.in_(ids)).update(
        {PortTrafficPanel.baseline_target_id: ""},
        synchronize_session=False,
    )
