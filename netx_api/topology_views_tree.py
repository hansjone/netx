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
    UmeTopoNode,
)
from .topology_common import (
    PHYSICAL_VIEW_NAME,
    ROOT_FOLDER_NAME,
    VIEW_GRAPH_EDGE_HARD_CAP,
    VIEW_GRAPH_NODE_HARD_CAP,
    _LEGACY_UNASSIGNED_NAME,
    _normalize_edge_status,
    _utcnow,
    view_filter_dict,
)

# Manual top-level「根」auto-spawns this unique L2 canvas (mirrors UME World / World).
MANUAL_ROOT_MAP_NAME = "根图"
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
        external_ref=str(getattr(f, "external_ref", None) or ""),
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
        filter=view_filter_dict(v.filter),
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


def _ensure_manual_root_map(
    db: Session, top: TopoFolder, *, now: Any | None = None
) -> tuple[TopoFolder, bool]:
    """Ensure a top-level manual「根」has unique L2「根图」; migrate legacy L1 canvas onto it.

    Pre-architecture shape: region === canvas (physical view hangs on the L1 folder).
    Current shape: L1 nav-only + system L2「根图」holds the canvas (and any prior L2 kids).
    """
    stamp = now or _utcnow()
    changed = False
    kids = (
        db.query(TopoFolder)
        .filter(TopoFolder.parent_id == top.id, TopoFolder.kind == "region")
        .order_by(TopoFolder.sort_order.asc(), TopoFolder.created_at.asc())
        .all()
    )
    root_map = next((k for k in kids if str(k.name or "") == MANUAL_ROOT_MAP_NAME), None)
    if root_map is None:
        root_map = TopoFolder(
            id=uuid4().hex,
            parent_id=top.id,
            kind="region",
            name=MANUAL_ROOT_MAP_NAME,
            sort_order=0,
            is_system=True,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(root_map)
        db.flush()
        changed = True
        for kid in kids:
            kid.parent_id = root_map.id
            kid.updated_at = stamp
            changed = True
    elif not bool(root_map.is_system):
        root_map.is_system = True
        root_map.updated_at = stamp
        changed = True

    l1_views = db.query(TopoView).filter(TopoView.folder_id == top.id).all()
    for v in l1_views:
        v.folder_id = root_map.id
        v.updated_at = stamp
        changed = True
    if l1_views:
        db.flush()

    has_phys = (
        db.query(TopoView.id)
        .filter(TopoView.folder_id == root_map.id, TopoView.kind == VIEW_KIND_PHYSICAL)
        .first()
        is not None
    )
    if not has_phys:
        db.add(
            TopoView(
                id=uuid4().hex,
                folder_id=root_map.id,
                kind=VIEW_KIND_PHYSICAL,
                role="core",
                name=MANUAL_ROOT_MAP_NAME,
                remark="",
                sort_order=0,
                filter={},
                viewport={},
                created_at=stamp,
                updated_at=stamp,
            )
        )
        changed = True
    return root_map, changed


def _heal_manual_root_canvases(db: Session, root: TopoFolder) -> bool:
    """Adopt orphan regions and upgrade legacy single-map roots to 根/根图."""
    from .ume_topology_world import is_ume_world_container

    now = _utcnow()
    changed = False
    # Floating regions (no parent) from older installs — hang under system root.
    for orphan in (
        db.query(TopoFolder)
        .filter(
            TopoFolder.kind == "region",
            or_(TopoFolder.parent_id.is_(None), TopoFolder.parent_id == ""),
        )
        .all()
    ):
        orphan.parent_id = root.id
        orphan.updated_at = now
        changed = True

    tops = (
        db.query(TopoFolder)
        .filter(TopoFolder.parent_id == root.id, TopoFolder.kind == "region")
        .order_by(TopoFolder.sort_order.asc(), TopoFolder.created_at.asc())
        .all()
    )
    for top in tops:
        if is_ume_world_container(top):
            continue
        kids = (
            db.query(TopoFolder)
            .filter(TopoFolder.parent_id == top.id, TopoFolder.kind == "region")
            .all()
        )
        has_root_map = any(str(k.name or "") == MANUAL_ROOT_MAP_NAME for k in kids)
        l1_view_cnt = (
            db.query(func.count(TopoView.id)).filter(TopoView.folder_id == top.id).scalar() or 0
        )
        # Already correct: unique 根图, no stray L1 views.
        if has_root_map and int(l1_view_cnt) == 0:
            # Still mark 根图 system if needed.
            rm = next(k for k in kids if str(k.name or "") == MANUAL_ROOT_MAP_NAME)
            if not bool(rm.is_system):
                rm.is_system = True
                rm.updated_at = now
                changed = True
            continue
        _, did = _ensure_manual_root_map(db, top, now=now)
        changed = changed or did
    return changed


def bootstrap_topology_tree(db: Session) -> dict[str, str]:
    """Ensure hidden system root; heal legacy Unassigned / single-map roots when needed."""
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
        db.commit()
        return {"root_id": root.id}

    dirty = False
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
            dirty = True
        elif bool(folder.is_system):
            folder.is_system = False
            folder.updated_at = now
            dirty = True

    # Cheap normalize only for rows that still look pre-migration.
    stale_views = (
        db.query(TopoView)
        .filter(
            or_(
                TopoView.parent_view_id.isnot(None),
                TopoView.role.is_(None),
                TopoView.role == "",
            )
        )
        .all()
    )
    for v in stale_views:
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
        filt = view_filter_dict(v.filter)
        if "membership" not in filt:
            v.filter = merge_filter_with_membership(
                filt, role=normalize_view_role(v.role), kind=kind
            )
            changed = True
        if changed:
            v.updated_at = now
            dirty = True

    if _heal_manual_root_canvases(db, root):
        dirty = True

    if dirty:
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
    parent_kind = str(parent.kind or "")
    if parent_kind not in ("root", "region"):
        raise HTTPException(status_code=400, detail="invalid_parent_folder")

    # UME World container: only one L2 (system drill). New regions remount under drill.
    from .ume_topology_world import (
        ensure_ume_world_and_sbn_folders,
        get_world_drill_folder,
        is_ume_world_container,
        reconcile_world_flat_view,
    )

    if is_ume_world_container(parent):
        drill = get_world_drill_folder(db)
        if drill is None:
            ensure_ume_world_and_sbn_folders(db)
            drill = get_world_drill_folder(db)
        if drill is None:
            raise HTTPException(status_code=500, detail="world_drill_missing")
        parent = drill
        parent_kind = "region"

    # Manual 根 (top-level nav): unique L2「根图」only — further creates remount under it.
    if (
        parent_kind == "region"
        and str(parent.parent_id or "") == str(root.id)
        and not is_ume_world_container(parent)
    ):
        root_map, _ = _ensure_manual_root_map(db, parent)
        parent = root_map
        parent_kind = "region"
    now = _utcnow()
    row = TopoFolder(
        id=uuid4().hex,
        parent_id=parent.id,
        kind="region",
        name=name[:256],
        sort_order=int(body.sort_order or 0),
        is_system=False,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()

    if parent_kind == "root":
        # 「根」nav container + auto unique「根图」canvas (UME World / World pattern).
        root_map = TopoFolder(
            id=uuid4().hex,
            parent_id=row.id,
            kind="region",
            name=MANUAL_ROOT_MAP_NAME,
            sort_order=0,
            is_system=True,
            created_at=now,
            updated_at=now,
        )
        db.add(root_map)
        db.flush()
        db.add(
            TopoView(
                id=uuid4().hex,
                folder_id=root_map.id,
                kind=VIEW_KIND_PHYSICAL,
                role="core",
                name=MANUAL_ROOT_MAP_NAME,
                remark="",
                sort_order=0,
                filter={},
                viewport={},
                created_at=now,
                updated_at=now,
            )
        )
    elif parent_kind == "region":
        # Nested under 根图 / deeper canvas: region === canvas + icon on parent.
        phys = TopoView(
            id=uuid4().hex,
            folder_id=row.id,
            kind=VIEW_KIND_PHYSICAL,
            role="core",
            name=name[:200] or "Topology",
            remark="",
            sort_order=0,
            filter={},
            viewport={},
            created_at=now,
            updated_at=now,
        )
        db.add(phys)
        db.flush()
        parent_has_phys = (
            db.query(TopoView.id)
            .filter(TopoView.folder_id == parent.id, TopoView.kind == VIEW_KIND_PHYSICAL)
            .first()
            is not None
        )
        if parent_has_phys:
            from .topology_region_canvas import place_child_region_on_parent_canvas

            place_child_region_on_parent_canvas(db, parent, row)
        else:
            ensure_region_physical_view(db, parent.id, commit=False)
            from .topology_region_canvas import place_child_region_on_parent_canvas

            place_child_region_on_parent_canvas(db, parent, row)
    db.flush()
    try:
        reconcile_world_flat_view(db)
    except Exception:
        # Creating a manual root must not fail because UME world reconcile hiccups.
        import logging

        logging.getLogger("netx.topology").exception(
            "reconcile_world_flat_view failed after create_folder id=%s", row.id
        )
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
            from .topology_region_canvas import region_canvas_node_id

            nid = region_canvas_node_id(row.id)
            for vn in (
                db.query(TopoViewNode).filter(TopoViewNode.fabric_node_id == nid).all()
            ):
                vn.label = name[:256]
                vn.updated_at = _utcnow()
    if body.sort_order is not None:
        row.sort_order = int(body.sort_order)
    if body.parent_id is not None and str(row.kind or "") == "region":
        parent = _get_folder_or_404(db, body.parent_id)
        if str(parent.kind or "") not in ("root", "region"):
            raise HTTPException(status_code=400, detail="invalid_parent_folder")
        row.parent_id = parent.id
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    return _folder_out(row)


def delete_folder(db: Session, folder_id: str, *, force: bool = False) -> dict[str, Any]:
    """Delete a region and cascade-delete nested regions + maps.

    System L2「根图」(manual, no UME external_ref) may be removed only as part of
    cascading from its parent「根」.

    UME World and UME-synced SBN folders (``external_ref`` set) are user-deletable —
    they are synced inventory, not structural locks; a later UME apply can recreate them.
    """
    row = _get_folder_or_404(db, folder_id)
    if str(row.kind or "") == "root":
        raise HTTPException(status_code=400, detail="cannot_delete_system_folder")

    from .ume_topology_world import is_ume_world_container

    ext = str(getattr(row, "external_ref", None) or "").strip()
    ume_synced = bool(ext)  # ume:world / ume:world:drill / SBN id
    # Protect manual system folders (根图); allow UME World + synced children.
    if bool(row.is_system) and not is_ume_world_container(row) and not ume_synced:
        raise HTTPException(status_code=400, detail="cannot_delete_system_folder")
    _ = force
    from .topology_region_canvas import remove_region_canvas_placements

    def _purge_folder(folder: TopoFolder) -> None:
        kids = (
            db.query(TopoFolder)
            .filter(TopoFolder.parent_id == folder.id)
            .all()
        )
        for kid in kids:
            _purge_folder(kid)
        remove_region_canvas_placements(db, folder.id)
        views = db.query(TopoView).filter(TopoView.folder_id == folder.id).all()
        for v in views:
            db.query(TopoViewEdgeStyle).filter(TopoViewEdgeStyle.view_id == v.id).delete(
                synchronize_session=False
            )
            db.query(TopoViewNode).filter(TopoViewNode.view_id == v.id).delete(
                synchronize_session=False
            )
            db.delete(v)
        db.flush()
        db.delete(folder)

    _purge_folder(row)
    from .ume_topology_world import reconcile_world_flat_view

    try:
        reconcile_world_flat_view(db)
    except Exception:
        import logging

        logging.getLogger("netx.topology").exception(
            "reconcile_world_flat_view failed after delete_folder id=%s", folder_id
        )
    db.commit()
    return {"ok": True, "folder_id": folder_id, "deleted": True}


def get_topology_tree(db: Session) -> TopologyTreeOut:
    bootstrap_topology_tree(db)
    folders = db.query(TopoFolder).order_by(TopoFolder.sort_order.asc(), TopoFolder.name.asc()).all()
    views = db.query(TopoView).order_by(TopoView.sort_order.asc(), TopoView.name.asc()).all()
    # Cheap directory counts — never hydrate 15k fabric rows (attrs JSON) on tree load.
    # Full inventory scans belong only to the world flat map graph.
    nc_map: dict[str, int] = {
        str(vid): int(cnt or 0)
        for vid, cnt in (
            db.query(TopoViewNode.view_id, func.count(TopoViewNode.id))
            .group_by(TopoViewNode.view_id)
            .all()
        )
    }
    region_icon_map: dict[str, int] = {
        str(vid): int(cnt or 0)
        for vid, cnt in (
            db.query(TopoViewNode.view_id, func.count(TopoViewNode.id))
            .filter(TopoViewNode.fabric_node_id.like("region:%"))
            .group_by(TopoViewNode.view_id)
            .all()
        )
    }
    # Skip heavy UME rollups on fresh installs (no UME folders / level views yet).
    has_ume_dirs = any(
        str(getattr(f, "external_ref", None) or "").strip()
        for f in folders
        if str(f.kind or "") == "region"
    ) or any(
        bool(
            view_filter_dict(v.filter).get("ume_level")
            or view_filter_dict(v.filter).get("world")
            or view_filter_dict(v.filter).get("world_flat")
        )
        for v in views
    )
    ume_ne_by_folder: dict[str, int] = {}
    ume_ne_by_sbn: dict[str, int] = {}
    ume_ne_total = 0
    if has_ume_dirs:
        ume_ne_by_folder = {
            str(fid): int(cnt or 0)
            for fid, cnt in (
                db.query(TopoFabricNode.region_folder_id, func.count(TopoFabricNode.id))
                .filter(
                    TopoFabricNode.ume_ne_id.isnot(None),
                    TopoFabricNode.ume_ne_id != "",
                    TopoFabricNode.region_folder_id.isnot(None),
                    TopoFabricNode.region_folder_id != "",
                )
                .group_by(TopoFabricNode.region_folder_id)
                .all()
            )
            if str(fid or "").strip()
        }
        ume_ne_by_sbn = {
            str(parent): int(cnt or 0)
            for parent, cnt in (
                db.query(UmeTopoNode.parent_node, func.count(UmeTopoNode.node_id))
                .filter(
                    UmeTopoNode.node_type == "TOPO_NODE_ME",
                    UmeTopoNode.parent_node.isnot(None),
                    UmeTopoNode.parent_node != "",
                )
                .group_by(UmeTopoNode.parent_node)
                .all()
            )
            if str(parent or "").strip()
        }
        ume_ne_total = int(
            db.query(func.count(TopoFabricNode.id))
            .filter(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != "")
            .scalar()
            or 0
        )

    def _view_node_count(v: TopoView) -> int:
        raw = int(nc_map.get(v.id, 0) or 0)
        ne_nc = max(0, raw - int(region_icon_map.get(v.id, 0) or 0))
        if ne_nc:
            return ne_nc
        filt = view_filter_dict(v.filter)
        if filt.get("world_flat") or (
            bool(filt.get("world")) and not filt.get("ume_level")
        ):
            return ume_ne_total
        if filt.get("ume_level"):
            sid = str(filt.get("sbn_id") or "").strip()
            if sid:
                return int(ume_ne_by_sbn.get(sid, 0))
            # World drill root: directory hint only (open canvas for real graph).
            if str(filt.get("parent") or "") == "md":
                return ume_ne_total
            if v.folder_id:
                return int(ume_ne_by_folder.get(str(v.folder_id), 0))
        if v.folder_id:
            return int(ume_ne_by_folder.get(str(v.folder_id), 0))
        return 0

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

    def _flat_views(folder: TopoFolder, folder_views: list[TopoView]) -> list[TopologyTreeViewOut]:
        from .ume_topology_world import (
            is_ume_world_container,
            is_world_flat_view,
            is_world_flat_visible,
            world_map_should_exist,
        )

        # Soft-hide world map on UME World when rule 2A is not met.
        show_flat = True
        if is_ume_world_container(folder):
            try:
                show_flat = world_map_should_exist(db)
            except Exception:
                show_flat = False

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
        out: list[TopologyTreeViewOut] = []
        for v in ordered:
            if is_world_flat_view(v):
                if not show_flat or not is_world_flat_visible(v):
                    continue
            out.append(
                TopologyTreeViewOut(
                    id=v.id,
                    name=v.name or "",
                    kind=normalize_view_kind(getattr(v, "kind", None)),
                    role=normalize_view_role(v.role),
                    sort_order=int(v.sort_order or 0),
                    node_count=_view_node_count(v),
                    updated_at=v.updated_at,
                )
            )
        return out

    def _build(folder: TopoFolder) -> TopologyTreeFolderOut:
        kids = [_build(c) for c in by_parent.get(folder.id, [])]
        return TopologyTreeFolderOut(
            id=folder.id,
            parent_id=str(folder.parent_id or ""),
            kind=str(folder.kind or "region"),
            name=folder.name or "",
            sort_order=int(folder.sort_order or 0),
            is_system=bool(folder.is_system),
            external_ref=str(getattr(folder, "external_ref", None) or ""),
            views=_flat_views(folder, views_by_folder.get(folder.id, [])),
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

