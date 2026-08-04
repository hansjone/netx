"""Topology folder tree and view CRUD."""
from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .models import (
    ManagedNE,
    TopoFabricEdge,
    TopoFabricNode,
    TopoFolder,
    TopoView,
    TopoViewEdgeStyle,
    TopoViewNode,
    UmeInventoryNE,
)
from .topology_common import (
    PHYSICAL_VIEW_NAME,
    ROOT_FOLDER_NAME,
    VIEW_GRAPH_EDGE_HARD_CAP,
    VIEW_GRAPH_NODE_HARD_CAP,
    _LEGACY_UNASSIGNED_NAME,
    _normalize_edge_status,
    _utcnow,
)
from .topology_fabric import (
    _edge_out,
    _fabric_match_score,
    _is_inventory_node,
    _node_out,
    _nodes_by_ids,
    ensure_fabric_node_for_managed,
    ensure_fabric_node_for_ume,
    merge_duplicate_fabric_nodes,
)
from .cli_resolve import get_default_profile, infer_device_type_vendor
from .topology_membership import (
    VIEW_KIND_CUSTOM,
    VIEW_KIND_PHYSICAL,
    has_hard_scope,
    merge_filter_with_membership,
    normalize_view_kind,
    normalize_view_role,
    parse_membership,
)
from .topology_schemas import (
    TopologyFolderCreate,
    TopologyFolderOut,
    TopologyFolderUpdate,
    TopologyTreeFolderOut,
    TopologyTreeOut,
    TopologyTreeViewOut,
    TopologyViewCreate,
    TopologyViewGraphOut,
    TopologyViewOut,
    TopologyViewUpdate,
    ViewEdgeOut,
    ViewEdgeStylePatch,
    ViewNodeIn,
    ViewNodeOut,
    ViewNodesAdd,
    ViewPopulateOut,
    ViewPopulateRequest,
    ViewPositionsPatch,
)

# ---------------------------------------------------------------------------
# Folders (tree) + Views (leaf canvases)
# ---------------------------------------------------------------------------


def _folder_out(f: TopoFolder) -> TopologyFolderOut:
    return TopologyFolderOut(
        id=f.id,
        parent_id=str(f.parent_id or ""),
        kind=str(f.kind or "region"),
        name=f.name or "",
        sort_order=int(f.sort_order or 0),
        is_system=bool(f.is_system),
        created_at=f.created_at,
        updated_at=f.updated_at,
    )


def _view_out(v: TopoView, *, node_count: int = 0) -> TopologyViewOut:
    return TopologyViewOut(
        id=v.id,
        name=v.name,
        remark=v.remark or "",
        folder_id=str(v.folder_id or ""),
        kind=normalize_view_kind(getattr(v, "kind", None)),
        role=normalize_view_role(v.role),
        sort_order=int(v.sort_order or 0),
        filter=dict(v.filter or {}),
        viewport=dict(v.viewport or {}),
        node_count=node_count,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


def _get_view_or_404(db: Session, view_id: str) -> TopoView:
    vid = str(view_id or "").strip()
    row = db.get(TopoView, vid) if vid else None
    if row is None:
        raise HTTPException(status_code=404, detail="topology_view_not_found")
    return row


def _get_folder_or_404(db: Session, folder_id: str) -> TopoFolder:
    fid = str(folder_id or "").strip()
    row = db.get(TopoFolder, fid) if fid else None
    if row is None:
        raise HTTPException(status_code=404, detail="topology_folder_not_found")
    return row


def ensure_region_physical_view(db: Session, folder_id: str, *, commit: bool = True) -> TopoView:
    """Ensure a site has exactly one default physical topology map."""
    fid = str(folder_id or "").strip()
    folder = _get_folder_or_404(db, fid)
    if str(folder.kind or "") == "root":
        raise HTTPException(status_code=400, detail="view_must_hang_under_region")
    existing = (
        db.query(TopoView)
        .filter(TopoView.folder_id == folder.id, TopoView.kind == VIEW_KIND_PHYSICAL)
        .order_by(TopoView.sort_order.asc(), TopoView.created_at.asc())
        .first()
    )
    if existing is not None:
        return existing
    now = _utcnow()
    role = "core"
    row = TopoView(
        id=uuid4().hex,
        folder_id=folder.id,
        parent_view_id=None,
        kind=VIEW_KIND_PHYSICAL,
        role=role,
        name=PHYSICAL_VIEW_NAME,
        remark="",
        sort_order=0,
        filter=merge_filter_with_membership({}, role=role, kind=VIEW_KIND_PHYSICAL),
        viewport={},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def bootstrap_topology_tree(db: Session) -> dict[str, str]:
    """Ensure hidden system root; flatten legacy nesting."""
    now = _utcnow()
    root = (
        db.query(TopoFolder)
        .filter(TopoFolder.kind == "root")
        .order_by(TopoFolder.created_at.asc())
        .first()
    )
    if root is None:
        root = TopoFolder(
            id=uuid4().hex,
            parent_id=None,
            kind="root",
            name=ROOT_FOLDER_NAME,
            sort_order=0,
            is_system=True,
            created_at=now,
            updated_at=now,
        )
        db.add(root)
        db.flush()

    # Drop legacy auto-created Unassigned region when empty; otherwise demote to normal region.
    legacy = (
        db.query(TopoFolder)
        .filter(
            TopoFolder.kind == "region",
            TopoFolder.name == _LEGACY_UNASSIGNED_NAME,
        )
        .all()
    )
    for folder in legacy:
        view_cnt = db.query(TopoView).filter(TopoView.folder_id == folder.id).count()
        if view_cnt == 0:
            db.delete(folder)
        elif bool(folder.is_system):
            folder.is_system = False
            folder.updated_at = now

    # Flatten nesting + normalize kind for all views.
    for v in db.query(TopoView).all():
        changed = False
        if v.parent_view_id:
            v.parent_view_id = None
            changed = True
        kind = normalize_view_kind(getattr(v, "kind", None))
        if str(getattr(v, "kind", "") or "") != kind:
            v.kind = kind
            changed = True
        if not str(v.role or "").strip():
            v.role = "core"
            changed = True
        filt = dict(v.filter or {})
        if "membership" not in filt:
            v.filter = merge_filter_with_membership(
                filt, role=normalize_view_role(v.role), kind=kind
            )
            changed = True
        if changed:
            v.updated_at = now

    db.commit()
    return {"root_id": root.id}


def create_folder(db: Session, body: TopologyFolderCreate) -> TopologyFolderOut:
    bootstrap_topology_tree(db)
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    kind = str(body.kind or "region").strip().lower()
    if kind != "region":
        raise HTTPException(status_code=400, detail="folder_kind_must_be_region")
    root = db.query(TopoFolder).filter(TopoFolder.kind == "root").first()
    if root is None:
        raise HTTPException(status_code=500, detail="topology_root_missing")
    parent_id = str(body.parent_id or "").strip() or root.id
    parent = _get_folder_or_404(db, parent_id)
    if str(parent.kind or "") != "root":
        raise HTTPException(status_code=400, detail="region_must_hang_under_root")
    now = _utcnow()
    row = TopoFolder(
        id=uuid4().hex,
        parent_id=root.id,
        kind="region",
        name=name[:256],
        sort_order=int(body.sort_order or 0),
        is_system=False,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    db.commit()
    db.refresh(row)
    return _folder_out(row)


def update_folder(db: Session, folder_id: str, body: TopologyFolderUpdate) -> TopologyFolderOut:
    row = _get_folder_or_404(db, folder_id)
    if str(row.kind or "") == "root":
        if body.parent_id is not None:
            raise HTTPException(status_code=400, detail="cannot_reparent_root")
    if body.name is not None:
        name = str(body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name_required")
        if bool(row.is_system) and str(row.kind or "") == "root":
            row.name = name[:256]
        elif bool(row.is_system):
            raise HTTPException(status_code=400, detail="cannot_rename_system_folder")
        else:
            row.name = name[:256]
    if body.sort_order is not None:
        row.sort_order = int(body.sort_order)
    if body.parent_id is not None and str(row.kind or "") == "region":
        parent = _get_folder_or_404(db, body.parent_id)
        if str(parent.kind or "") != "root":
            raise HTTPException(status_code=400, detail="region_must_hang_under_root")
        row.parent_id = parent.id
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _folder_out(row)


def delete_folder(db: Session, folder_id: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a region and cascade-delete its maps.

    ``force`` is accepted for API compatibility; cascade always runs.
    """
    row = _get_folder_or_404(db, folder_id)
    if str(row.kind or "") == "root" or bool(row.is_system):
        raise HTTPException(status_code=400, detail="cannot_delete_system_folder")
    _ = force
    views = db.query(TopoView).filter(TopoView.folder_id == row.id).all()
    for v in views:
        db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.view_id == v.id).delete(
            synchronize_session=False
        )
        db.query(TopoViewNode).filter(TopoViewNode.view_id == v.id).delete(
            synchronize_session=False
        )
        db.delete(v)
    db.flush()
    db.delete(row)
    db.commit()
    return {"ok": True, "folder_id": folder_id, "deleted": True}


def get_topology_tree(db: Session) -> TopologyTreeOut:
    bootstrap_topology_tree(db)
    folders = db.query(TopoFolder).order_by(TopoFolder.sort_order.asc(), TopoFolder.name.asc()).all()
    views = db.query(TopoView).order_by(TopoView.sort_order.asc(), TopoView.name.asc()).all()
    nc_map: dict[str, int] = {}
    for vid, cnt in (
        db.query(TopoViewNode.view_id, func.count(TopoViewNode.id))
        .group_by(TopoViewNode.view_id)
        .all()
    ):
        nc_map[str(vid)] = int(cnt or 0)

    by_parent: dict[str, list[TopoFolder]] = {}
    root: TopoFolder | None = None
    for f in folders:
        if str(f.kind or "") == "root":
            root = f
            continue
        pid = str(f.parent_id or "")
        by_parent.setdefault(pid, []).append(f)

    views_by_folder: dict[str, list[TopoView]] = {}
    for v in views:
        views_by_folder.setdefault(str(v.folder_id or ""), []).append(v)

    def _flat_views(folder_views: list[TopoView]) -> list[TopologyTreeViewOut]:
        # physical first, then custom; stable by sort_order/name.
        ordered = sorted(
            folder_views,
            key=lambda x: (
                0 if normalize_view_kind(getattr(x, "kind", None)) == VIEW_KIND_PHYSICAL else 1,
                int(x.sort_order or 0),
                x.name or "",
                x.id,
            ),
        )
        return [
            TopologyTreeViewOut(
                id=v.id,
                name=v.name or "",
                kind=normalize_view_kind(getattr(v, "kind", None)),
                role=normalize_view_role(v.role),
                sort_order=int(v.sort_order or 0),
                node_count=nc_map.get(v.id, 0),
                updated_at=v.updated_at,
            )
            for v in ordered
        ]

    def _build(folder: TopoFolder) -> TopologyTreeFolderOut:
        kids = [_build(c) for c in by_parent.get(folder.id, [])]
        return TopologyTreeFolderOut(
            id=folder.id,
            parent_id=str(folder.parent_id or ""),
            kind=str(folder.kind or "region"),
            name=folder.name or "",
            sort_order=int(folder.sort_order or 0),
            is_system=bool(folder.is_system),
            views=_flat_views(views_by_folder.get(folder.id, [])),
            children=kids,
        )

    if root is None:
        return TopologyTreeOut(root=None)
    return TopologyTreeOut(root=_build(root))


def list_views(db: Session) -> dict[str, Any]:
    bootstrap_topology_tree(db)
    rows = db.query(TopoView).order_by(TopoView.updated_at.desc()).all()
    items = []
    for v in rows:
        nc = db.query(TopoViewNode).filter(TopoViewNode.view_id == v.id).count()
        items.append(_view_out(v, node_count=nc).model_dump())
    return {"total": len(items), "items": items}


def create_view(db: Session, body: TopologyViewCreate) -> TopologyViewOut:
    name = str(body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    role = normalize_view_role(body.role)
    kind = normalize_view_kind(body.kind)
    folder_id = str(body.folder_id or "").strip()
    if not folder_id:
        raise HTTPException(status_code=400, detail="folder_id_required")
    folder = _get_folder_or_404(db, folder_id)
    if str(folder.kind or "") == "root":
        raise HTTPException(status_code=400, detail="view_must_hang_under_region")
    if kind == VIEW_KIND_PHYSICAL:
        existing = (
            db.query(TopoView)
            .filter(TopoView.folder_id == folder.id, TopoView.kind == VIEW_KIND_PHYSICAL)
            .first()
        )
        if existing is not None:
            raise HTTPException(status_code=400, detail="region_already_has_physical_view")
    filt = merge_filter_with_membership(dict(body.filter or {}), role=role, kind=kind)
    now = _utcnow()
    row = TopoView(
        id=uuid4().hex,
        folder_id=folder_id,
        parent_view_id=None,
        kind=kind,
        role=role,
        name=name[:256],
        remark=str(body.remark or "")[:1024],
        sort_order=int(body.sort_order or 0),
        filter=filt,
        viewport={},
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _view_out(row)


def update_view(db: Session, view_id: str, body: TopologyViewUpdate) -> TopologyViewOut:
    row = _get_view_or_404(db, view_id)
    if body.name is not None:
        name = str(body.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name_required")
        row.name = name[:256]
    if body.remark is not None:
        row.remark = str(body.remark or "")[:1024]
    if body.role is not None:
        row.role = normalize_view_role(body.role)
    if body.sort_order is not None:
        row.sort_order = int(body.sort_order)
    if body.folder_id is not None:
        fid = str(body.folder_id or "").strip()
        folder = _get_folder_or_404(db, fid)
        if str(folder.kind or "") == "root":
            raise HTTPException(status_code=400, detail="view_must_hang_under_region")
        row.folder_id = folder.id
    if body.kind is not None:
        new_kind = normalize_view_kind(body.kind)
        if new_kind == VIEW_KIND_PHYSICAL and normalize_view_kind(row.kind) != VIEW_KIND_PHYSICAL:
            clash = (
                db.query(TopoView)
                .filter(
                    TopoView.folder_id == row.folder_id,
                    TopoView.kind == VIEW_KIND_PHYSICAL,
                    TopoView.id != row.id,
                )
                .first()
            )
            if clash is not None:
                raise HTTPException(status_code=400, detail="region_already_has_physical_view")
        row.kind = new_kind
    row.parent_view_id = None
    if body.filter is not None:
        row.filter = merge_filter_with_membership(
            dict(body.filter or {}),
            role=normalize_view_role(row.role),
            kind=normalize_view_kind(row.kind),
        )
    if body.viewport is not None:
        row.viewport = dict(body.viewport or {})
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    nc = db.query(TopoViewNode).filter(TopoViewNode.view_id == row.id).count()
    return _view_out(row, node_count=nc)


def delete_view(db: Session, view_id: str, *, force: bool = False) -> dict[str, Any]:
    row = _get_view_or_404(db, view_id)
    is_physical = normalize_view_kind(row.kind) == VIEW_KIND_PHYSICAL
    if is_physical and not force:
        raise HTTPException(status_code=400, detail="cannot_delete_physical_view")
    db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.view_id == row.id).delete(
        synchronize_session=False
    )
    db.query(TopoViewNode).filter(TopoViewNode.view_id == row.id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True, "view_id": view_id, "deleted": True}

