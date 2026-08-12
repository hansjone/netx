"""Shared helpers for topology classify rules and apply."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import TopoClassifyRule, TopoFabricNode, TopoFolder
from .timeutil import utcnow_naive
from .topology_level import LEVEL_PRESETS, level_to_role, normalize_level, role_to_level
from .topology_schemas import (
    ClassifyRuleOut,
    TopologyFolderCreate,
)

_MAX_PATTERN_LEN = 512
_MATCH_FIELDS = frozenset({"name", "ip", "name_ip"})
_SCOPES = frozenset({"level", "region", "role"})  # role = legacy alias of level
_SLICE_TEMPLATES = frozenset({"core_only", "core_agg", "agg_access"})
_ROLE_VALUES = frozenset(LEVEL_PRESETS) | {"unknown", "edge", ""}


def _utcnow() -> datetime:
    return utcnow_naive()


def _normalize_scope(scope: str) -> str:
    s = str(scope or "level").strip().lower()
    if s == "role":
        return "level"
    return s


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    p = str(pattern or "").strip()
    if not p:
        raise HTTPException(status_code=400, detail="pattern_required")
    if len(p) > _MAX_PATTERN_LEN:
        raise HTTPException(status_code=400, detail="pattern_too_long")
    try:
        return re.compile(p, re.IGNORECASE)
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"invalid_pattern:{exc}") from exc


def _match_text(node: TopoFabricNode, match_field: str) -> str:
    name = str(node.name or "")
    ip = str(node.ip or "")
    mf = str(match_field or "name").strip().lower()
    if mf == "ip":
        return ip
    if mf == "name_ip":
        return f"{name} {ip}".strip()
    return name


def _rule_out(row: TopoClassifyRule) -> ClassifyRuleOut:
    scope = _normalize_scope(str(row.scope or "level"))
    return ClassifyRuleOut(
        id=row.id,
        scope=scope,
        name=str(row.name or ""),
        pattern=str(row.pattern or ""),
        match_field=str(row.match_field or "name"),
        priority=int(row.priority or 100),
        enabled=bool(row.enabled),
        payload=dict(row.payload or {}),
        remark=str(row.remark or ""),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate_payload(scope: str, payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload or {})
    scope_n = _normalize_scope(scope)
    if scope_n == "level":
        if "level" in out and out.get("level") is not None:
            try:
                lv = normalize_level(out.get("level"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if lv is None:
                raise HTTPException(status_code=400, detail="level_payload_invalid")
            return {"level": lv}
        if "role" in out:
            try:
                lv = role_to_level(str(out.get("role") or ""))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if lv is None:
                raise HTTPException(status_code=400, detail="level_payload_invalid")
            return {"level": lv, "role": level_to_role(lv)}
        raise HTTPException(status_code=400, detail="level_payload_invalid")
    if "folder_id" in out and str(out.get("folder_id") or "").strip():
        return {"folder_id": str(out["folder_id"]).strip()}
    if "region_name_from_group" in out:
        try:
            g = int(out.get("region_name_from_group"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="region_group_invalid") from exc
        if g < 1:
            raise HTTPException(status_code=400, detail="region_group_invalid")
        return {"region_name_from_group": g}
    raise HTTPException(status_code=400, detail="region_payload_invalid")


def _enabled_rules(db: Session, scope: str) -> list[tuple[TopoClassifyRule, re.Pattern[str]]]:
    scope_n = _normalize_scope(scope)
    scopes = ("level", "role") if scope_n == "level" else (scope_n,)
    rows = (
        db.query(TopoClassifyRule)
        .filter(TopoClassifyRule.scope.in_(scopes), TopoClassifyRule.enabled.is_(True))
        .order_by(TopoClassifyRule.priority.asc(), TopoClassifyRule.name.asc())
        .all()
    )
    out: list[tuple[TopoClassifyRule, re.Pattern[str]]] = []
    for r in rows:
        try:
            out.append((r, _compile_pattern(r.pattern)))
        except HTTPException:
            continue
    return out


def _ensure_region_by_name(db: Session, name: str) -> TopoFolder:
    from .topology_service import bootstrap_topology_tree, create_folder

    name = str(name or "").strip()[:256]
    if not name:
        raise HTTPException(status_code=400, detail="region_name_empty")
    existing = (
        db.query(TopoFolder)
        .filter(TopoFolder.kind == "region", TopoFolder.name == name)
        .first()
    )
    if existing is not None:
        return existing
    bootstrap_topology_tree(db)
    created = create_folder(db, TopologyFolderCreate(name=name, kind="region"))
    folder = db.get(TopoFolder, created.id)
    assert folder is not None
    return folder


def _payload_level(payload: dict[str, Any] | None) -> float | None:
    p = dict(payload or {})
    if "level" in p and p.get("level") is not None:
        try:
            return normalize_level(p.get("level"))
        except ValueError:
            return None
    if "role" in p:
        try:
            return role_to_level(str(p.get("role") or ""))
        except ValueError:
            return None
    return None


def _resolve_level_hit(
    node: TopoFabricNode, rules: list[tuple[TopoClassifyRule, re.Pattern[str]]]
) -> tuple[float | None, str | None, bool]:
    """Return (level, rule_id, multi_hit)."""
    hits: list[tuple[float, str]] = []
    for rule, cre in rules:
        text = _match_text(node, rule.match_field)
        if not text:
            continue
        if cre.search(text):
            lv = _payload_level(rule.payload)
            if lv is None:
                continue
            hits.append((lv, rule.id))
    if not hits:
        return None, None, False
    return hits[0][0], hits[0][1], len(hits) > 1


# Back-compat name used by older imports
def _resolve_role_hit(
    node: TopoFabricNode, rules: list[tuple[TopoClassifyRule, re.Pattern[str]]]
) -> tuple[str | None, str | None, bool]:
    lv, rid, multi = _resolve_level_hit(node, rules)
    if lv is None:
        return None, None, False
    return level_to_role(lv), rid, multi


def _resolve_region_hit(
    db: Session,
    node: TopoFabricNode,
    rules: list[tuple[TopoClassifyRule, re.Pattern[str]]],
    *,
    create_missing: bool,
) -> tuple[str | None, str | None, bool]:
    hits: list[tuple[str, str]] = []
    for rule, cre in rules:
        text = _match_text(node, rule.match_field)
        if not text:
            continue
        m = cre.search(text)
        if not m:
            continue
        payload = dict(rule.payload or {})
        folder_id = str(payload.get("folder_id") or "").strip()
        if folder_id:
            hits.append((folder_id, rule.id))
            continue
        g = int(payload.get("region_name_from_group") or 0)
        try:
            region_name = m.group(g)
        except IndexError:
            continue
        region_name = str(region_name or "").strip()
        if not region_name:
            continue
        if create_missing:
            folder = _ensure_region_by_name(db, region_name)
            hits.append((folder.id, rule.id))
        else:
            existing = (
                db.query(TopoFolder)
                .filter(TopoFolder.kind == "region", TopoFolder.name == region_name)
                .first()
            )
            hits.append((existing.id if existing else f"new:{region_name}", rule.id))
    if not hits:
        return None, None, False
    return hits[0][0], hits[0][1], len(hits) > 1


def apply_level_fields(node: TopoFabricNode, level: float | None, *, source: str) -> None:
    """Write level + synced role alias."""
    node.level = level
    node.role = level_to_role(level)
    node.role_source = source
