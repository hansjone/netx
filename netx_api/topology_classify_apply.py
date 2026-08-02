"""Topology classify preview/apply and fabric node tagging."""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import TopoClassifyRule, TopoFabricNode, TopoFolder
from .topology_classify_common import (
    _MATCH_FIELDS,
    _ROLE_VALUES,
    _compile_pattern,
    _enabled_rules,
    _ensure_region_by_name,
    _match_text,
    _resolve_region_hit,
    _resolve_role_hit,
    _utcnow,
)
from .topology_membership import normalize_view_role
from .topology_schemas import (
    ClassifyApplyOut,
    ClassifyPreviewOut,
    FabricNodeOut,
    FabricNodesBulkTagOut,
    FabricNodesBulkTagRequest,
    FabricNodesMatchOut,
    FabricNodesMatchRequest,
    FabricNodeTagPatch,
)

def preview_classify(db: Session, *, sample_limit: int = 20) -> ClassifyPreviewOut:
    role_rules = _enabled_rules(db, "role")
    region_rules = _enabled_rules(db, "region")
    nodes = db.query(TopoFabricNode).order_by(TopoFabricNode.name.asc()).all()
    role_matched = role_unmatched = role_conflict = 0
    region_matched = region_unmatched = region_conflict = 0
    role_samples: list[dict[str, Any]] = []
    region_samples: list[dict[str, Any]] = []
    unmatched_samples: list[dict[str, Any]] = []

    for n in nodes:
        role, _rid, multi_r = _resolve_role_hit(n, role_rules)
        if role is None:
            role_unmatched += 1
        else:
            role_matched += 1
            if multi_r:
                role_conflict += 1
            if len(role_samples) < sample_limit:
                role_samples.append(
                    {
                        "fabric_node_id": n.id,
                        "name": n.name,
                        "ip": n.ip,
                        "role": role,
                        "multi_hit": multi_r,
                    }
                )

        region_id, _rrid, multi_reg = _resolve_region_hit(
            db, n, region_rules, create_missing=False
        )
        if region_id is None:
            region_unmatched += 1
        else:
            region_matched += 1
            if multi_reg:
                region_conflict += 1
            if len(region_samples) < sample_limit:
                region_samples.append(
                    {
                        "fabric_node_id": n.id,
                        "name": n.name,
                        "ip": n.ip,
                        "region": region_id,
                        "multi_hit": multi_reg,
                    }
                )

        if role is None and region_id is None and len(unmatched_samples) < sample_limit:
            unmatched_samples.append(
                {"fabric_node_id": n.id, "name": n.name, "ip": n.ip, "vendor": n.vendor}
            )

    return ClassifyPreviewOut(
        total_nodes=len(nodes),
        role_matched=role_matched,
        role_unmatched=role_unmatched,
        role_conflicts=role_conflict,
        region_matched=region_matched,
        region_unmatched=region_unmatched,
        region_conflicts=region_conflict,
        role_samples=role_samples,
        region_samples=region_samples,
        unmatched_samples=unmatched_samples,
    )


def apply_classify(
    db: Session,
    *,
    skip_manual: bool = True,
    fill_empty_only: bool = False,
) -> ClassifyApplyOut:
    role_rules = _enabled_rules(db, "role")
    region_rules = _enabled_rules(db, "region")
    nodes = db.query(TopoFabricNode).all()
    role_updated = region_updated = skipped_manual = 0

    for n in nodes:
        role, _, _ = _resolve_role_hit(n, role_rules)
        if role is not None:
            if skip_manual and str(n.role_source or "") == "manual":
                skipped_manual += 1
            elif fill_empty_only and str(n.role or "").strip():
                pass
            else:
                n.role = role
                n.role_source = "rule"
                n.updated_at = _utcnow()
                role_updated += 1
        elif not str(n.role or "").strip() and str(n.role_source or "") != "manual":
            n.role = "unknown"
            n.role_source = "rule"
            n.updated_at = _utcnow()

        region_id, _, _ = _resolve_region_hit(db, n, region_rules, create_missing=True)
        if region_id is not None and not str(region_id).startswith("new:"):
            if skip_manual and str(n.region_source or "") == "manual":
                skipped_manual += 1
            elif fill_empty_only and str(n.region_folder_id or "").strip():
                pass
            else:
                n.region_folder_id = region_id
                n.region_source = "rule"
                n.updated_at = _utcnow()
                region_updated += 1

    db.commit()
    return ClassifyApplyOut(
        role_updated=role_updated,
        region_updated=region_updated,
        skipped_manual=skipped_manual,
        total_nodes=len(nodes),
    )


def list_unmatched(
    db: Session,
    *,
    kind: str = "any",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    from sqlalchemy import or_

    q = db.query(TopoFabricNode)
    k = str(kind or "any").strip().lower()
    role_miss = or_(TopoFabricNode.role == "", TopoFabricNode.role == "unknown")
    region_miss = or_(
        TopoFabricNode.region_folder_id.is_(None),
        TopoFabricNode.region_folder_id == "",
    )
    if k == "role":
        q = q.filter(role_miss)
    elif k == "region":
        q = q.filter(region_miss)
    else:
        q = q.filter(or_(role_miss, region_miss))
    total = q.count()
    rows = (
        q.order_by(TopoFabricNode.name.asc())
        .offset(max(0, (page - 1) * page_size))
        .limit(page_size)
        .all()
    )
    from .topology_service import _node_out

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_node_out(n).model_dump() for n in rows],
    }


def patch_fabric_node_tags(
    db: Session, fabric_node_id: str, body: FabricNodeTagPatch
) -> FabricNodeOut:
    from .topology_service import _node_out

    n = db.get(TopoFabricNode, fabric_node_id)
    if n is None:
        raise HTTPException(status_code=404, detail="fabric_node_not_found")
    if body.role is not None:
        role = str(body.role or "").strip().lower()
        if role and role not in _ROLE_VALUES:
            raise HTTPException(status_code=400, detail="role_invalid")
        n.role = role
        n.role_source = "manual"
    if body.region_folder_id is not None:
        fid = str(body.region_folder_id or "").strip()
        if fid:
            folder = db.get(TopoFolder, fid)
            if folder is None or str(folder.kind or "") != "region":
                raise HTTPException(status_code=400, detail="folder_not_found")
            n.region_folder_id = fid
        else:
            n.region_folder_id = None
        n.region_source = "manual"
    n.updated_at = _utcnow()
    db.commit()
    db.refresh(n)
    return _node_out(n)


def _iter_regex_matches(
    db: Session, *, pattern: str, match_field: str
) -> list[TopoFabricNode]:
    cre = _compile_pattern(pattern)
    mf = str(match_field or "name").strip().lower()
    if mf not in _MATCH_FIELDS:
        raise HTTPException(status_code=400, detail="match_field_invalid")
    out: list[TopoFabricNode] = []
    for n in db.query(TopoFabricNode).order_by(TopoFabricNode.name.asc()).all():
        text = _match_text(n, mf)
        if text and cre.search(text):
            out.append(n)
    return out


def match_fabric_nodes(db: Session, body: FabricNodesMatchRequest) -> FabricNodesMatchOut:
    """Ephemeral regex lookup — does not persist rules."""
    from .topology_inventory_lifecycle import fabric_link_status

    matched = _iter_regex_matches(
        db, pattern=body.pattern, match_field=body.match_field
    )
    limit = max(1, min(200, int(body.sample_limit or 50)))
    samples = [
        {
            "fabric_node_id": n.id,
            "name": n.name,
            "ip": n.ip,
            "role": n.role or "",
            "region_folder_id": n.region_folder_id or "",
            "link_status": fabric_link_status(n),
        }
        for n in matched[:limit]
    ]
    return FabricNodesMatchOut(
        pattern=str(body.pattern or "").strip(),
        match_field=str(body.match_field or "name"),
        total_matched=len(matched),
        samples=samples,
        fabric_node_ids=[n.id for n in matched],
    )


def bulk_tag_fabric_nodes(
    db: Session, body: FabricNodesBulkTagRequest
) -> FabricNodesBulkTagOut:
    """Assign role/region after user confirms a regex or explicit selection."""
    if body.role is None and body.region_folder_id is None:
        raise HTTPException(status_code=400, detail="role_or_region_required")

    role_v: str | None = None
    if body.role is not None:
        role_v = str(body.role or "").strip().lower()
        if role_v and role_v not in _ROLE_VALUES:
            raise HTTPException(status_code=400, detail="role_invalid")

    region_v: str | None = None
    if body.region_folder_id is not None:
        region_v = str(body.region_folder_id or "").strip()
        if region_v:
            folder = db.get(TopoFolder, region_v)
            if folder is None or str(folder.kind or "") != "region":
                raise HTTPException(status_code=400, detail="folder_not_found")
        else:
            region_v = ""

    ids = [str(x).strip() for x in (body.fabric_node_ids or []) if str(x).strip()]
    if str(body.pattern or "").strip():
        matched_nodes = _iter_regex_matches(
            db, pattern=body.pattern, match_field=body.match_field
        )
    elif ids:
        matched_nodes = (
            db.query(TopoFabricNode)
            .filter(TopoFabricNode.id.in_(ids))
            .order_by(TopoFabricNode.name.asc())
            .all()
        )
    else:
        raise HTTPException(status_code=400, detail="ids_or_pattern_required")

    samples = [
        {
            "fabric_node_id": n.id,
            "name": n.name,
            "ip": n.ip,
            "role": n.role or "",
            "region_folder_id": n.region_folder_id or "",
        }
        for n in matched_nodes[:50]
    ]
    if body.dry_run:
        return FabricNodesBulkTagOut(
            dry_run=True,
            matched=len(matched_nodes),
            updated=0,
            role=role_v,
            region_folder_id=region_v,
            samples=samples,
        )

    now = _utcnow()
    updated = 0
    for n in matched_nodes:
        if role_v is not None:
            n.role = role_v
            n.role_source = "manual"
        if region_v is not None:
            n.region_folder_id = region_v or None
            n.region_source = "manual"
        n.updated_at = now
        updated += 1
    db.commit()
    return FabricNodesBulkTagOut(
        dry_run=False,
        matched=len(matched_nodes),
        updated=updated,
        role=role_v,
        region_folder_id=region_v,
        samples=samples,
    )


def apply_classify_empty_only(db: Session) -> ClassifyApplyOut:
    """Incremental classify for newly synced nodes (fill empty tags only)."""
    return apply_classify(db, skip_manual=True, fill_empty_only=True)


