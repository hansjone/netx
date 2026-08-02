"""Regex-based fabric role/region classification + slice map generation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import TopoClassifyRule, TopoFabricEdge, TopoFabricNode, TopoFolder, TopoView, TopoViewNode
from .topology_membership import (
    VIEW_KIND_CUSTOM,
    VIEW_ROLE_ACCESS,
    VIEW_ROLE_AGGREGATION,
    VIEW_ROLE_CORE,
    VIEW_ROLES,
    merge_filter_with_membership,
    normalize_view_role,
)
from .topology_schemas import (
    ClassifyApplyOut,
    ClassifyPreviewOut,
    ClassifyRuleCreate,
    ClassifyRuleOut,
    ClassifyRuleUpdate,
    FabricNodeOut,
    FabricNodesBulkTagOut,
    FabricNodesBulkTagRequest,
    FabricNodesMatchOut,
    FabricNodesMatchRequest,
    FabricNodeTagPatch,
    SliceGenerateOut,
    SliceGenerateRequest,
    SliceMapPlan,
    TopologyFolderCreate,
    TopologyViewCreate,
)

_MAX_PATTERN_LEN = 512
_ROLE_VALUES = VIEW_ROLES | {"unknown"}
_MATCH_FIELDS = frozenset({"name", "ip", "name_ip"})
_SCOPES = frozenset({"role", "region"})
_SLICE_TEMPLATES = frozenset({"core_only", "core_agg", "agg_access"})


def _utcnow() -> datetime:
    return datetime.utcnow()


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
    return ClassifyRuleOut(
        id=row.id,
        scope=str(row.scope or "role"),
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
    if scope == "role":
        role = normalize_view_role(str(out.get("role") or ""))
        if str(out.get("role") or "").strip().lower() not in VIEW_ROLES:
            raise HTTPException(status_code=400, detail="role_payload_invalid")
        return {"role": role}
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


def _enabled_rules(db: Session, scope: str) -> list[tuple[TopoClassifyRule, re.Pattern[str]]]:
    rows = (
        db.query(TopoClassifyRule)
        .filter(TopoClassifyRule.scope == scope, TopoClassifyRule.enabled.is_(True))
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


def _resolve_role_hit(
    node: TopoFabricNode, rules: list[tuple[TopoClassifyRule, re.Pattern[str]]]
) -> tuple[str | None, str | None, bool]:
    """Return (role, rule_id, multi_hit)."""
    hits: list[tuple[str, str]] = []
    for rule, cre in rules:
        text = _match_text(node, rule.match_field)
        if not text:
            continue
        if cre.search(text):
            role = normalize_view_role(str((rule.payload or {}).get("role") or ""))
            hits.append((role, rule.id))
    if not hits:
        return None, None, False
    return hits[0][0], hits[0][1], len(hits) > 1


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


# --- Slice generation -------------------------------------------------------


def _active_neighbors(db: Session, seed_ids: set[str], *, hops: int = 1) -> set[str]:
    if not seed_ids or hops <= 0:
        return set()
    frontier = set(seed_ids)
    found: set[str] = set()
    for _ in range(hops):
        if not frontier:
            break
        rows = (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.layer == "physical",
                TopoFabricEdge.status == "active",
                (TopoFabricEdge.a_node_id.in_(frontier) | TopoFabricEdge.b_node_id.in_(frontier)),
            )
            .all()
        )
        nxt: set[str] = set()
        for e in rows:
            for a, b in ((e.a_node_id, e.b_node_id), (e.b_node_id, e.a_node_id)):
                if a in frontier and b not in seed_ids and b not in found:
                    nxt.add(str(b))
        found |= nxt
        frontier = nxt
    return found


def _connected_components(db: Session, node_ids: list[str]) -> list[list[str]]:
    ids = [str(x) for x in node_ids if str(x)]
    if not ids:
        return []
    id_set = set(ids)
    adj: dict[str, set[str]] = {i: set() for i in ids}
    rows = (
        db.query(TopoFabricEdge)
        .filter(
            TopoFabricEdge.layer == "physical",
            TopoFabricEdge.status == "active",
            TopoFabricEdge.a_node_id.in_(ids),
            TopoFabricEdge.b_node_id.in_(ids),
        )
        .all()
    )
    for e in rows:
        a, b = str(e.a_node_id), str(e.b_node_id)
        if a in id_set and b in id_set:
            adj[a].add(b)
            adj[b].add(a)
    seen: set[str] = set()
    comps: list[list[str]] = []
    for nid in ids:
        if nid in seen:
            continue
        stack = [nid]
        seen.add(nid)
        comp: list[str] = []
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nb in adj.get(cur, ()):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        comps.append(sorted(comp))
    return comps


def _nodes_in_region(db: Session, folder_id: str, *, role: str = "") -> list[TopoFabricNode]:
    q = db.query(TopoFabricNode).filter(TopoFabricNode.region_folder_id == folder_id)
    if role:
        q = q.filter(TopoFabricNode.role == role)
    return q.order_by(TopoFabricNode.name.asc()).all()


def preview_slices(db: Session, body: SliceGenerateRequest) -> SliceGenerateOut:
    folder = db.get(TopoFolder, body.folder_id)
    if folder is None or str(folder.kind or "") != "region":
        raise HTTPException(status_code=400, detail="folder_not_found")
    template = str(body.template or "").strip().lower()
    if template not in _SLICE_TEMPLATES:
        raise HTTPException(status_code=400, detail="template_invalid")
    max_nodes = max(1, min(2000, int(body.max_nodes or 300)))
    plans: list[SliceMapPlan] = []
    overlap_ids: set[str] = set()
    seen_in_maps: dict[str, int] = {}

    def _track(ids: list[str]) -> None:
        for i in ids:
            seen_in_maps[i] = seen_in_maps.get(i, 0) + 1

    if template == "core_only":
        cores = _nodes_in_region(db, folder.id, role=VIEW_ROLE_CORE)
        comps = _connected_components(db, [n.id for n in cores]) or [
            [n.id] for n in cores
        ]
        for idx, comp in enumerate(comps, start=1):
            if len(comp) > max_nodes:
                raise HTTPException(
                    status_code=400,
                    detail=f"slice_exceeds_max_nodes:{len(comp)}>{max_nodes}",
                )
            name = f"Core-{idx}" if len(comps) > 1 else "Core"
            plans.append(
                SliceMapPlan(
                    name=name,
                    role=VIEW_ROLE_CORE,
                    seed_fabric_node_ids=comp,
                    member_fabric_node_ids=comp,
                    node_count=len(comp),
                )
            )
            _track(comp)
    elif template == "core_agg":
        cores = _nodes_in_region(db, folder.id, role=VIEW_ROLE_CORE)
        comps = _connected_components(db, [n.id for n in cores]) or [
            [n.id] for n in cores
        ]
        for idx, comp in enumerate(comps, start=1):
            peers = _active_neighbors(db, set(comp), hops=1)
            agg_ids = [
                p
                for p in peers
                if (fn := db.get(TopoFabricNode, p)) is not None
                and str(fn.role or "") == VIEW_ROLE_AGGREGATION
                and str(fn.region_folder_id or "") == folder.id
            ]
            members = sorted(set(comp) | set(agg_ids))
            if len(members) > max_nodes:
                raise HTTPException(
                    status_code=400,
                    detail=f"slice_exceeds_max_nodes:{len(members)}>{max_nodes}",
                )
            name = f"CoreAgg-{idx}" if len(comps) > 1 else "Core+Agg"
            plans.append(
                SliceMapPlan(
                    name=name,
                    role=VIEW_ROLE_CORE,
                    seed_fabric_node_ids=comp,
                    member_fabric_node_ids=members,
                    node_count=len(members),
                )
            )
            _track(members)
    else:  # agg_access
        aggs = _nodes_in_region(db, folder.id, role=VIEW_ROLE_AGGREGATION)
        comps = _connected_components(db, [n.id for n in aggs]) or [
            [n.id] for n in aggs
        ]
        for idx, comp in enumerate(comps, start=1):
            peers = _active_neighbors(db, set(comp), hops=1)
            acc_ids = [
                p
                for p in peers
                if (fn := db.get(TopoFabricNode, p)) is not None
                and str(fn.role or "") == VIEW_ROLE_ACCESS
                and str(fn.region_folder_id or "") == folder.id
            ]
            members = sorted(set(comp) | set(acc_ids))
            if len(members) > max_nodes:
                raise HTTPException(
                    status_code=400,
                    detail=f"slice_exceeds_max_nodes:{len(members)}>{max_nodes}",
                )
            name = f"AggAccess-{idx}" if len(comps) > 1 else "Agg+Access"
            plans.append(
                SliceMapPlan(
                    name=name,
                    role=VIEW_ROLE_AGGREGATION,
                    seed_fabric_node_ids=comp,
                    member_fabric_node_ids=members,
                    node_count=len(members),
                )
            )
            _track(members)

    overlap_ids = {nid for nid, cnt in seen_in_maps.items() if cnt > 1}
    return SliceGenerateOut(
        folder_id=folder.id,
        template=template,
        dry_run=True,
        maps=plans,
        map_count=len(plans),
        overlap_node_count=len(overlap_ids),
        created_view_ids=[],
    )


def generate_slices(db: Session, body: SliceGenerateRequest) -> SliceGenerateOut:
    from .topology_service import create_view, _place_fabric_ids_on_view

    preview = preview_slices(db, body)
    if body.dry_run:
        return preview

    created: list[str] = []
    for plan in preview.maps:
        view = create_view(
            db,
            TopologyViewCreate(
                name=plan.name,
                folder_id=body.folder_id,
                kind=VIEW_KIND_CUSTOM,
                role=plan.role,
                remark=f"slice:{body.template}",
            ),
        )
        mem = {
            "mode": "hybrid",
            "seed_fabric_node_ids": list(plan.member_fabric_node_ids),
            "expand_hops": 0,
            "max_nodes": int(body.max_nodes or 300),
            "frozen": True,
            "managed_ne_ids": [],
            "tags_any": [],
            "vendors": [],
            "device_types": [],
            "keyword": "",
        }
        row = db.get(TopoView, view.id)
        assert row is not None
        row.filter = merge_filter_with_membership(
            dict(row.filter or {}), role=normalize_view_role(plan.role), membership=mem
        )
        _place_fabric_ids_on_view(db, row, list(plan.member_fabric_node_ids), existing=set())
        row.updated_at = _utcnow()
        db.commit()
        created.append(view.id)

    # Optionally seed physical overview with cores only
    if body.seed_physical_cores:
        from .topology_service import ensure_region_physical_view

        phys = ensure_region_physical_view(db, body.folder_id, commit=True)
        cores = [n.id for n in _nodes_in_region(db, body.folder_id, role=VIEW_ROLE_CORE)]
        existing = {
            vn.fabric_node_id
            for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == phys.id).all()
        }
        to_add = [c for c in cores if c not in existing][: int(body.max_nodes or 300)]
        if to_add:
            _place_fabric_ids_on_view(db, phys, to_add, existing=existing)
            mem = merge_filter_with_membership(
                dict(phys.filter or {}),
                role=VIEW_ROLE_CORE,
                kind="physical",
                membership={
                    **dict((phys.filter or {}).get("membership") or {}),
                    "frozen": True,
                    "max_nodes": int(body.max_nodes or 500),
                },
            )
            phys.filter = mem
            phys.updated_at = _utcnow()
            db.commit()

    return SliceGenerateOut(
        folder_id=body.folder_id,
        template=str(body.template),
        dry_run=False,
        maps=preview.maps,
        map_count=len(preview.maps),
        overlap_node_count=preview.overlap_node_count,
        created_view_ids=created,
    )


def search_fabric_nodes_with_views(
    db: Session,
    *,
    keyword: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    from .topology_service import _node_out

    q = db.query(TopoFabricNode)
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            (TopoFabricNode.name.ilike(like))
            | (TopoFabricNode.ip.ilike(like))
            | (TopoFabricNode.vendor.ilike(like))
        )
    total = q.count()
    rows = (
        q.order_by(TopoFabricNode.name.asc())
        .offset(max(0, (page - 1) * page_size))
        .limit(page_size)
        .all()
    )
    node_ids = [n.id for n in rows]
    placements: dict[str, list[dict[str, Any]]] = {nid: [] for nid in node_ids}
    if node_ids:
        vnodes = (
            db.query(TopoViewNode, TopoView, TopoFolder)
            .join(TopoView, TopoView.id == TopoViewNode.view_id)
            .outerjoin(TopoFolder, TopoFolder.id == TopoView.folder_id)
            .filter(TopoViewNode.fabric_node_id.in_(node_ids))
            .all()
        )
        for vn, view, folder in vnodes:
            placements.setdefault(vn.fabric_node_id, []).append(
                {
                    "view_id": view.id,
                    "view_name": view.name,
                    "folder_id": view.folder_id or "",
                    "folder_name": (folder.name if folder else "") or "",
                    "kind": view.kind or "custom",
                }
            )
    items = []
    for n in rows:
        d = _node_out(n).model_dump()
        d["views"] = placements.get(n.id, [])
        items.append(d)
    return {"total": total, "page": page, "page_size": page_size, "items": items}
