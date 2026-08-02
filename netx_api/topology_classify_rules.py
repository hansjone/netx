"""Topology classify rule CRUD."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import TopoClassifyRule, TopoFolder
from .topology_classify_common import (
    _MATCH_FIELDS,
    _MAX_PATTERN_LEN,
    _SCOPES,
    _compile_pattern,
    _rule_out,
    _utcnow,
    _validate_payload,
)
from .topology_schemas import ClassifyRuleCreate, ClassifyRuleOut, ClassifyRuleUpdate

def list_rules(db: Session, *, scope: str = "") -> list[ClassifyRuleOut]:
    q = db.query(TopoClassifyRule)
    if scope.strip():
        q = q.filter(TopoClassifyRule.scope == scope.strip().lower())
    rows = q.order_by(
        TopoClassifyRule.scope.asc(),
        TopoClassifyRule.priority.asc(),
        TopoClassifyRule.name.asc(),
    ).all()
    return [_rule_out(r) for r in rows]


def create_rule(db: Session, body: ClassifyRuleCreate) -> ClassifyRuleOut:
    scope = str(body.scope or "").strip().lower()
    if scope not in _SCOPES:
        raise HTTPException(status_code=400, detail="scope_invalid")
    match_field = str(body.match_field or "name").strip().lower()
    if match_field not in _MATCH_FIELDS:
        raise HTTPException(status_code=400, detail="match_field_invalid")
    _compile_pattern(body.pattern)
    payload = _validate_payload(scope, dict(body.payload or {}))
    if scope == "region" and payload.get("folder_id"):
        folder = db.get(TopoFolder, payload["folder_id"])
        if folder is None or str(folder.kind or "") != "region":
            raise HTTPException(status_code=400, detail="folder_not_found")
    now = _utcnow()
    row = TopoClassifyRule(
        id=uuid4().hex,
        scope=scope,
        name=str(body.name or "").strip()[:256] or f"{scope}-rule",
        pattern=str(body.pattern or "").strip()[:_MAX_PATTERN_LEN],
        match_field=match_field,
        priority=int(body.priority if body.priority is not None else 100),
        enabled=bool(body.enabled if body.enabled is not None else True),
        payload=payload,
        remark=str(body.remark or "")[:512],
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _rule_out(row)


def update_rule(db: Session, rule_id: str, body: ClassifyRuleUpdate) -> ClassifyRuleOut:
    row = db.get(TopoClassifyRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="rule_not_found")
    if body.name is not None:
        row.name = str(body.name or "").strip()[:256]
    if body.pattern is not None:
        _compile_pattern(body.pattern)
        row.pattern = str(body.pattern or "").strip()[:_MAX_PATTERN_LEN]
    if body.match_field is not None:
        mf = str(body.match_field or "name").strip().lower()
        if mf not in _MATCH_FIELDS:
            raise HTTPException(status_code=400, detail="match_field_invalid")
        row.match_field = mf
    if body.priority is not None:
        row.priority = int(body.priority)
    if body.enabled is not None:
        row.enabled = bool(body.enabled)
    if body.remark is not None:
        row.remark = str(body.remark or "")[:512]
    if body.payload is not None:
        row.payload = _validate_payload(str(row.scope or "role"), dict(body.payload or {}))
        if str(row.scope) == "region" and row.payload.get("folder_id"):
            folder = db.get(TopoFolder, row.payload["folder_id"])
            if folder is None or str(folder.kind or "") != "region":
                raise HTTPException(status_code=400, detail="folder_not_found")
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _rule_out(row)


def delete_rule(db: Session, rule_id: str) -> dict[str, Any]:
    row = db.get(TopoClassifyRule, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="rule_not_found")
    db.delete(row)
    db.commit()
    return {"ok": True, "id": rule_id, "deleted": True}


