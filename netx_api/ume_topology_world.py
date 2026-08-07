"""Seed UME hierarchical canvases: World (level) + World Flat + per-SBN views."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from .models import TopoFabricNode, TopoFolder, TopoView, UmeTopoNode
from .topology_common import _utcnow
from .topology_membership import VIEW_KIND_PHYSICAL
from .topology_views_tree import bootstrap_topology_tree

_log = logging.getLogger("netx.ume.topo_world")

WORLD_FOLDER_NAME = "UME World"
WORLD_DRILL_FOLDER_NAME = "World"
WORLD_FOLDER_REF = "ume:world"
WORLD_DRILL_REF = "ume:world:drill"
WORLD_VIEW_NAME = "World"
WORLD_FLAT_VIEW_NAME = "世界地图"
# Pre-rename / locale labels kept so existing TopoView rows still match.
WORLD_FLAT_VIEW_NAME_LEGACY = "完整世界地图"
WORLD_FLAT_VIEW_NAME_EN = "World map"
WORLD_FLAT_VIEW_NAMES = frozenset(
    {WORLD_FLAT_VIEW_NAME, WORLD_FLAT_VIEW_NAME_LEGACY, WORLD_FLAT_VIEW_NAME_EN}
)
# Drill-down root: children of MD (root SBNs + rare direct MEs).
WORLD_LEVEL_FILTER = {"ume_level": True, "parent": "md"}
WORLD_FLAT_FILTER = {"world_flat": True}


def is_world_flat_view_name(name: str | None) -> bool:
    return str(name or "").strip() in WORLD_FLAT_VIEW_NAMES


def _filt(view: TopoView | None) -> dict[str, Any]:
    if view is None:
        return {}
    from .topology_common import view_filter_dict

    return view_filter_dict(getattr(view, "filter", None))


def is_ume_level_view(view: TopoView | None) -> bool:
    return bool(_filt(view).get("ume_level"))


def is_world_flat_view(view: TopoView | None) -> bool:
    filt = _filt(view)
    return bool(filt.get("world_flat")) or is_world_flat_view_name(str(getattr(view, "name", "") or ""))


def is_ume_world_container(folder: TopoFolder | None) -> bool:
    if folder is None:
        return False
    ext = str(getattr(folder, "external_ref", None) or "").strip()
    if ext == WORLD_FOLDER_REF:
        return True
    return str(folder.name or "").strip() == WORLD_FOLDER_NAME and bool(folder.is_system)


def is_world_drill_folder(folder: TopoFolder | None) -> bool:
    if folder is None:
        return False
    ext = str(getattr(folder, "external_ref", None) or "").strip()
    return ext == WORLD_DRILL_REF or (
        str(folder.name or "") == WORLD_DRILL_FOLDER_NAME and bool(folder.is_system)
    )


def is_world_view(view: TopoView | None) -> bool:
    """True for UME World drill-root (legacy `world` key or ume_level parent=md)."""
    if view is None:
        return False
    filt = _filt(view)
    if filt.get("world_flat"):
        return False
    if filt.get("ume_level") and str(filt.get("parent") or "") == "md":
        return True
    # Legacy flat-world filter — treat as flat, not drill root.
    if bool(filt.get("world")) and not filt.get("ume_level"):
        return False
    return str(view.name or "") == WORLD_VIEW_NAME and not is_world_flat_view(view)


def is_ume_canvas_view(view: TopoView | None) -> bool:
    """Any UME-driven virtual canvas (level or flat) — bypass membership graph."""
    if view is None:
        return False
    filt = _filt(view)
    if filt.get("ume_level") or filt.get("world_flat"):
        return True
    # Legacy world viewport
    return bool(filt.get("world"))


def get_world_container_folder(db: Session) -> TopoFolder | None:
    for f in db.query(TopoFolder).filter(TopoFolder.kind == "region").all():
        if is_ume_world_container(f):
            return f
    return None


def get_world_drill_folder(db: Session) -> TopoFolder | None:
    row = (
        db.query(TopoFolder)
        .filter(TopoFolder.external_ref == WORLD_DRILL_REF)
        .first()
    )
    if row is not None:
        return row
    for f in db.query(TopoFolder).filter(TopoFolder.kind == "region").all():
        if is_world_drill_folder(f):
            return f
    return None


def get_world_view(db: Session) -> TopoView | None:
    """Drill-down World root canvas (not the flat all-NE map)."""
    for v in db.query(TopoView).all():
        if is_world_view(v) or (
            is_ume_level_view(v) and str(_filt(v).get("parent") or "") == "md"
        ):
            return v
    return None


def get_world_flat_view(db: Session) -> TopoView | None:
    for v in db.query(TopoView).all():
        if is_world_flat_view(v):
            return v
    return None


def _folder_subtree_ids(
    db: Session,
    folder_id: str,
    *,
    children_map: dict[str | None, list[str]] | None = None,
) -> set[str]:
    if children_map is None:
        children_map = {}
        for fid, parent_id in db.query(TopoFolder.id, TopoFolder.parent_id).all():
            children_map.setdefault(parent_id, []).append(fid)
    ids = {folder_id}
    stack = [folder_id]
    while stack:
        cur = stack.pop()
        for cid in children_map.get(cur, []):
            if cid not in ids:
                ids.add(cid)
                stack.append(cid)
    return ids


def _child_region_has_nes(db: Session, child: TopoFolder) -> bool:
    """True when the child region subtree owns at least one fabric NE."""
    ids = _folder_subtree_ids(db, child.id)
    if ids and (
        db.query(TopoFabricNode.id)
        .filter(TopoFabricNode.region_folder_id.in_(list(ids)))
        .limit(1)
        .first()
        is not None
    ):
        return True
    # Cheap fallback: direct UME ME under this SBN (never scan all fabric attrs).
    ref = str(getattr(child, "external_ref", None) or "").strip()
    if ref and not ref.startswith("ume:"):
        if (
            db.query(UmeTopoNode.node_id)
            .filter(
                UmeTopoNode.node_type == "TOPO_NODE_ME",
                UmeTopoNode.parent_node == ref,
            )
            .limit(1)
            .first()
            is not None
        ):
            return True
    return False


def world_map_should_exist(db: Session) -> bool:
    """Rule 2A: L2 has nested child regions and at least one of those has NEs.

    If the unique L2 canvas only has direct NEs (no child regions), world map is
    redundant with the L2 canvas and must not appear.
    """
    drill = get_world_drill_folder(db)
    if drill is None:
        return False
    children = (
        db.query(TopoFolder)
        .filter(TopoFolder.parent_id == drill.id, TopoFolder.kind == "region")
        .all()
    )
    if not children:
        return False
    children_map: dict[str | None, list[str]] = {}
    for fid, parent_id in db.query(TopoFolder.id, TopoFolder.parent_id).all():
        children_map.setdefault(parent_id, []).append(fid)
    all_ids: set[str] = set()
    for c in children:
        all_ids |= _folder_subtree_ids(db, c.id, children_map=children_map)
    if all_ids and (
        db.query(TopoFabricNode.id)
        .filter(TopoFabricNode.region_folder_id.in_(list(all_ids)))
        .limit(1)
        .first()
        is not None
    ):
        return True
    refs = [
        str(getattr(c, "external_ref", None) or "").strip()
        for c in children
        if str(getattr(c, "external_ref", None) or "").strip()
        and not str(getattr(c, "external_ref", None) or "").startswith("ume:")
    ]
    if refs and (
        db.query(UmeTopoNode.node_id)
        .filter(
            UmeTopoNode.node_type == "TOPO_NODE_ME",
            UmeTopoNode.parent_node.in_(refs),
        )
        .limit(1)
        .first()
        is not None
    ):
        return True
    return False


def reconcile_world_flat_view(db: Session) -> TopoView | None:
    """Create flat view when 2A holds; keep row but mark suppressed when not.

    Soft-hide via filter.suppressed so drag overrides on TopoViewNode survive.
    """
    container = get_world_container_folder(db)
    if container is None:
        return None
    should = world_map_should_exist(db)
    existing = get_world_flat_view(db)
    now = _utcnow()
    if should:
        if existing is None:
            existing = _ensure_view(
                db,
                folder_id=container.id,
                name=WORLD_FLAT_VIEW_NAME,
                filt=dict(WORLD_FLAT_FILTER),
                remark="All NEs with composed flat-world coordinates (no regions)",
                sort_order=1,
                match=is_world_flat_view,
            )
        else:
            existing.folder_id = container.id
            filt = dict(existing.filter or {})
            filt.update(WORLD_FLAT_FILTER)
            filt.pop("suppressed", None)
            existing.filter = filt
            existing.name = WORLD_FLAT_VIEW_NAME
            existing.updated_at = now
        return existing
    if existing is not None:
        filt = dict(existing.filter or {})
        filt.update(WORLD_FLAT_FILTER)
        filt["suppressed"] = True
        existing.filter = filt
        existing.updated_at = now
    return existing


def is_world_flat_visible(view: TopoView | None) -> bool:
    """Tree/hex should show flat only when not suppressed and 2A would hold."""
    if view is None or not is_world_flat_view(view):
        return False
    if bool(_filt(view).get("suppressed")):
        return False
    return True


def _ensure_view(
    db: Session,
    *,
    folder_id: str,
    name: str,
    filt: dict[str, Any],
    remark: str,
    sort_order: int,
    match,
) -> TopoView:
    """Find view under folder matching predicate, or create."""
    now = _utcnow()
    found: TopoView | None = None
    for v in db.query(TopoView).filter(TopoView.folder_id == folder_id).all():
        if match(v):
            found = v
            break
    if found is None:
        found = TopoView(
            id=uuid4().hex,
            folder_id=folder_id,
            kind=VIEW_KIND_PHYSICAL,
            role="core",
            name=name,
            remark=remark,
            sort_order=sort_order,
            filter=dict(filt),
            viewport={},
            created_at=now,
            updated_at=now,
        )
        db.add(found)
        db.flush()
        return found
    found.kind = VIEW_KIND_PHYSICAL
    found.name = name
    found.remark = remark
    found.sort_order = sort_order
    cur = dict(found.filter or {})
    cur.update(filt)
    found.filter = cur
    found.updated_at = now
    return found


def ensure_ume_world_and_sbn_folders(db: Session) -> dict[str, Any]:
    """Create/update UME World container + unique L2 World drill + conditional Flat.

    Tree shape::

        UME World          (nav only — hex browse)
          ├─ World         (unique L2 folder ume:world:drill — SBNs hang here)
          │    └─ <root SBNs…>
          └─ 世界地图  (flat view — only when rule 2A holds)
    """
    now = _utcnow()
    stats: dict[str, Any] = {
        "world_folder_id": "",
        "world_drill_folder_id": "",
        "world_view_id": "",
        "world_flat_view_id": "",
        "world_map_visible": False,
        "sbn_folders": 0,
        "sbn_updated": 0,
        "sbn_views": 0,
    }
    bootstrap_topology_tree(db)
    root = db.query(TopoFolder).filter(TopoFolder.kind == "root").first()
    if root is None:
        raise RuntimeError("topology_root_missing")

    world_folder = (
        db.query(TopoFolder)
        .filter(TopoFolder.kind == "region", TopoFolder.name == WORLD_FOLDER_NAME)
        .first()
    )
    if world_folder is None:
        world_folder = TopoFolder(
            id=uuid4().hex,
            parent_id=root.id,
            kind="region",
            name=WORLD_FOLDER_NAME,
            sort_order=0,
            is_system=True,
            external_ref=WORLD_FOLDER_REF,
            created_at=now,
            updated_at=now,
        )
        db.add(world_folder)
        db.flush()
    else:
        world_folder.is_system = True
        world_folder.external_ref = world_folder.external_ref or WORLD_FOLDER_REF
        world_folder.updated_at = now

    # Drill folder "World" under UME World — owns the level canvas + SBN children.
    drill_folder = (
        db.query(TopoFolder)
        .filter(TopoFolder.external_ref == WORLD_DRILL_REF)
        .first()
    )
    if drill_folder is None:
        drill_folder = (
            db.query(TopoFolder)
            .filter(
                TopoFolder.parent_id == world_folder.id,
                TopoFolder.name == WORLD_DRILL_FOLDER_NAME,
                TopoFolder.is_system.is_(True),
            )
            .first()
        )
    if drill_folder is None:
        drill_folder = TopoFolder(
            id=uuid4().hex,
            parent_id=world_folder.id,
            kind="region",
            name=WORLD_DRILL_FOLDER_NAME,
            sort_order=0,
            is_system=True,
            external_ref=WORLD_DRILL_REF,
            created_at=now,
            updated_at=now,
        )
        db.add(drill_folder)
        db.flush()
    else:
        drill_folder.parent_id = world_folder.id
        drill_folder.name = WORLD_DRILL_FOLDER_NAME
        drill_folder.is_system = True
        drill_folder.external_ref = WORLD_DRILL_REF
        drill_folder.sort_order = 0
        drill_folder.updated_at = now

    world_view = _ensure_view(
        db,
        folder_id=drill_folder.id,
        name=WORLD_VIEW_NAME,
        filt=dict(WORLD_LEVEL_FILTER),
        remark="UME drill-down root (child regions + direct MEs)",
        sort_order=0,
        match=lambda v: is_world_view(v)
        or (is_ume_level_view(v) and str(_filt(v).get("parent") or "") == "md")
        or (bool(_filt(v).get("world")) and not _filt(v).get("world_flat")),
    )
    # Migrate legacy World view that still hangs on UME World container.
    for v in db.query(TopoView).filter(TopoView.folder_id == world_folder.id).all():
        if is_world_flat_view(v):
            continue
        if is_world_view(v) or (
            is_ume_level_view(v) and str(_filt(v).get("parent") or "") == "md"
        ):
            if v.id != world_view.id:
                v.folder_id = drill_folder.id
                vf = dict(v.filter or {})
                vf.pop("world", None)
                vf.pop("mode", None)
                vf.update(WORLD_LEVEL_FILTER)
                v.filter = vf
                v.updated_at = now
    wf = dict(world_view.filter or {})
    wf.pop("world", None)
    wf.pop("mode", None)
    wf.update(WORLD_LEVEL_FILTER)
    world_view.filter = wf
    world_view.folder_id = drill_folder.id

    stats["world_folder_id"] = world_folder.id
    stats["world_drill_folder_id"] = drill_folder.id
    stats["world_view_id"] = world_view.id

    sbns = (
        db.query(UmeTopoNode)
        .filter(UmeTopoNode.node_type == "TOPO_NODE_SBN")
        .all()
    )
    sbn_ids = {str(s.node_id) for s in sbns if str(s.node_id or "").strip()}
    by_ref: dict[str, TopoFolder] = {
        str(f.external_ref): f
        for f in db.query(TopoFolder)
        .filter(TopoFolder.external_ref.isnot(None), TopoFolder.external_ref != "")
        .all()
        if str(f.external_ref or "").strip() and not str(f.external_ref).startswith("ume:")
    }

    for sbn in sbns:
        sid = str(sbn.node_id or "").strip()
        if not sid:
            continue
        label = (sbn.user_label or sid)[:256]
        folder = by_ref.get(sid)
        if folder is None:
            folder = TopoFolder(
                id=uuid4().hex,
                parent_id=drill_folder.id,
                kind="region",
                name=label,
                sort_order=100,
                is_system=True,
                external_ref=sid,
                created_at=now,
                updated_at=now,
            )
            db.add(folder)
            by_ref[sid] = folder
            stats["sbn_folders"] += 1
        else:
            folder.name = label
            folder.is_system = True
            folder.updated_at = now
            stats["sbn_updated"] += 1

    db.flush()

    for sbn in sbns:
        sid = str(sbn.node_id or "").strip()
        folder = by_ref.get(sid)
        if folder is None:
            continue
        parent = str(sbn.parent_node or "").strip()
        if parent in sbn_ids and parent in by_ref:
            folder.parent_id = by_ref[parent].id
        else:
            # Root SBNs hang under World (drill), not UME World container.
            folder.parent_id = drill_folder.id

        label = (sbn.user_label or sid)[:256]
        level_filt = {
            "ume_level": True,
            "sbn_id": sid,
            "ume_x": float(sbn.x_pos) if sbn.x_pos is not None else None,
            "ume_y": float(sbn.y_pos) if sbn.y_pos is not None else None,
        }
        _ensure_view(
            db,
            folder_id=folder.id,
            name=label[:200] or "Topology",
            filt=level_filt,
            remark=f"UME SBN level canvas ({sid})",
            sort_order=0,
            match=lambda v, _sid=sid: is_ume_level_view(v)
            and str(_filt(v).get("sbn_id") or "") == _sid,
        )
        stats["sbn_views"] += 1

    # Tag fabric ME nodes with direct SBN for region filter / flat packing.
    me_parent = {
        str(n.ume_ne_id or n.node_id): str(n.parent_node or "")
        for n in db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_ME").all()
        if str(n.ume_ne_id or n.node_id or "").strip()
    }
    for fn in db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id.isnot(None)).all():
        uid = str(fn.ume_ne_id or "").strip()
        parent = me_parent.get(uid, "")
        if not parent:
            continue
        attrs = dict(fn.attrs or {})
        attrs["ume_sbn_id"] = parent[:128]
        folder = by_ref.get(parent)
        if folder is not None:
            fn.region_folder_id = folder.id
            fn.region_source = "ume"
        fn.attrs = attrs

    db.flush()
    flat_view = reconcile_world_flat_view(db)
    stats["world_map_visible"] = world_map_should_exist(db)
    if flat_view is not None and stats["world_map_visible"]:
        stats["world_flat_view_id"] = flat_view.id
    elif flat_view is not None:
        stats["world_flat_view_id"] = flat_view.id

    db.commit()
    try:
        from .ume_topology_flat_coords import recompute_flat_world_coords

        stats["flat_coords"] = recompute_flat_world_coords(db)
    except Exception:
        _log.exception("recompute_flat_world_coords after world ensure failed")
    _log.info("ume world/sbn folders ready: %s", stats)
    return stats


def folder_bbox(db: Session, folder_id: str) -> dict[str, float | int] | None:
    """Bounding box of fabric nodes tagged with this SBN folder (or descendants)."""
    folder = db.get(TopoFolder, folder_id)
    if folder is None:
        return None
    all_folders = db.query(TopoFolder).all()
    children: dict[str | None, list[str]] = {}
    for f in all_folders:
        children.setdefault(f.parent_id, []).append(f.id)
    ids = {folder_id}
    stack = [folder_id]
    while stack:
        cur = stack.pop()
        for cid in children.get(cur, []):
            if cid not in ids:
                ids.add(cid)
                stack.append(cid)

    q = (
        db.query(TopoFabricNode)
        .filter(
            TopoFabricNode.region_folder_id.in_(list(ids)),
            TopoFabricNode.world_x.isnot(None),
            TopoFabricNode.world_y.isnot(None),
        )
        .all()
    )
    if not q:
        ref = str(folder.external_ref or "").strip()
        if ref:
            q = [
                n
                for n in db.query(TopoFabricNode)
                .filter(TopoFabricNode.world_x.isnot(None), TopoFabricNode.world_y.isnot(None))
                .all()
                if str((n.attrs or {}).get("ume_sbn_id") or "") == ref
            ]
    if not q:
        return None
    xs = [float(n.world_x) for n in q]
    ys = [float(n.world_y) for n in q]
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
        "n": len(q),
    }
