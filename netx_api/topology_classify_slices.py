"""Topology slice map preview/generation and fabric search."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import TopoFabricEdge, TopoFabricNode, TopoFolder, TopoView, TopoViewNode
from .topology_classify_common import _SLICE_TEMPLATES, _utcnow
from .topology_membership import (
    VIEW_KIND_CUSTOM,
    VIEW_ROLE_ACCESS,
    VIEW_ROLE_AGGREGATION,
    VIEW_ROLE_CORE,
    merge_filter_with_membership,
    normalize_view_role,
)
from .topology_schemas import (
    FabricNodeOut,
    SliceGenerateOut,
    SliceGenerateRequest,
    SliceMapPlan,
    TopologyViewCreate,
)

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

