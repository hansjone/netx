"""Child-region icons on a parent canvas (folder === canvas drill-down).

Synthetic placements use fabric_node_id ``region:<folder_id>`` on the parent's
TopoViewNode rows — same shape as UME level region nodes, persisted so drag/save works.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from .models import TopoFolder, TopoView, TopoViewNode
from .topology_common import _utcnow
from .topology_membership import VIEW_KIND_PHYSICAL, normalize_view_kind
from .topology_schemas import ViewNodeOut

REGION_NODE_PREFIX = "region:"


def is_region_canvas_node_id(fabric_node_id: str | None) -> bool:
    return str(fabric_node_id or "").startswith(REGION_NODE_PREFIX)


def region_folder_id_from_node(fabric_node_id: str) -> str:
    return str(fabric_node_id or "")[len(REGION_NODE_PREFIX) :]


def region_canvas_node_id(folder_id: str) -> str:
    return f"{REGION_NODE_PREFIX}{folder_id}"


def _is_world_flat(view: TopoView) -> bool:
    filt = dict(view.filter or {})
    if filt.get("world_flat"):
        return True
    return str(view.name or "").strip() == "完整世界地图"


def primary_canvas_view(db: Session, folder_id: str) -> TopoView | None:
    """Parent canvas used for child-region icons (skip flat world map)."""
    fid = str(folder_id or "").strip()
    if not fid:
        return None
    views = (
        db.query(TopoView)
        .filter(TopoView.folder_id == fid)
        .order_by(TopoView.sort_order.asc(), TopoView.created_at.asc())
        .all()
    )
    usable = [v for v in views if not _is_world_flat(v)]
    if not usable:
        return None
    for v in usable:
        filt = dict(v.filter or {})
        if filt.get("ume_level") or filt.get("world"):
            return v
    for v in usable:
        if normalize_view_kind(v.kind) == VIEW_KIND_PHYSICAL:
            return v
    return usable[0]


def _child_primary_view(db: Session, folder_id: str) -> TopoView | None:
    return primary_canvas_view(db, folder_id)


def _default_region_xy(db: Session, view_id: str) -> tuple[float, float]:
    rows = db.query(TopoViewNode).filter(TopoViewNode.view_id == view_id).all()
    if not rows:
        return 160.0, 160.0
    max_x = max(float(r.x or 0) for r in rows)
    ys = [float(r.y or 0) for r in rows]
    avg_y = sum(ys) / len(ys) if ys else 160.0
    return max_x + 220.0, avg_y


def place_child_region_on_parent_canvas(
    db: Session,
    parent: TopoFolder,
    child: TopoFolder,
    *,
    x: float | None = None,
    y: float | None = None,
) -> TopoViewNode | None:
    """Put a region building icon on the parent's canvas. No-op for root parent."""
    if parent is None or child is None:
        return None
    if str(parent.kind or "") != "region":
        return None
    view = primary_canvas_view(db, parent.id)
    if view is None:
        return None
    nid = region_canvas_node_id(child.id)
    existing = (
        db.query(TopoViewNode)
        .filter(TopoViewNode.view_id == view.id, TopoViewNode.fabric_node_id == nid)
        .first()
    )
    if existing is not None:
        if child.name and existing.label != child.name:
            existing.label = str(child.name)[:256]
            existing.updated_at = _utcnow()
        return existing
    px, py = _default_region_xy(db, view.id) if x is None or y is None else (float(x), float(y))
    now = _utcnow()
    row = TopoViewNode(
        id=uuid4().hex,
        view_id=view.id,
        fabric_node_id=nid,
        x=px,
        y=py,
        label=str(child.name or "")[:256],
        locked=False,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    return row


def remove_region_canvas_placements(db: Session, folder_id: str) -> int:
    """Drop this folder's icon from any parent canvas."""
    nid = region_canvas_node_id(folder_id)
    return (
        db.query(TopoViewNode)
        .filter(TopoViewNode.fabric_node_id == nid)
        .delete(synchronize_session=False)
    )


def region_node_out(
    db: Session,
    *,
    folder: TopoFolder,
    x: float,
    y: float,
    locked: bool = False,
    label: str = "",
) -> ViewNodeOut:
    child_view = _child_primary_view(db, folder.id)
    kids = (
        db.query(TopoFolder)
        .filter(TopoFolder.parent_id == folder.id, TopoFolder.kind == "region")
        .count()
    )
    # node_count: prefer view membership count when available
    node_count = 0
    if child_view is not None:
        node_count = (
            db.query(TopoViewNode)
            .filter(TopoViewNode.view_id == child_view.id)
            .count()
        )
    display = (label or folder.name or folder.id)[:256]
    return ViewNodeOut(
        fabric_node_id=region_canvas_node_id(folder.id),
        managed_ne_id="",
        ume_ne_id="",
        label=display,
        x=float(x),
        y=float(y),
        locked=bool(locked),
        name=str(folder.name or "")[:256],
        ip="",
        vendor="",
        device_type="region",
        kind="region",
        folder_id=folder.id,
        view_id=child_view.id if child_view else "",
        node_count=int(node_count or kids or 0),
    )


def _is_ume_managed_folder(folder: TopoFolder) -> bool:
    """UME World / drill / SBN folders are drawn by ume_topology_world_graph, not here."""
    ext = str(getattr(folder, "external_ref", None) or "").strip()
    return bool(ext)


def child_region_nodes_for_view(db: Session, view: TopoView) -> list[ViewNodeOut]:
    """Manual child regions as canvas icons (skip UME-synced SBN folders)."""
    folder_id = str(view.folder_id or "").strip()
    if not folder_id or _is_world_flat(view):
        return []
    children = (
        db.query(TopoFolder)
        .filter(TopoFolder.parent_id == folder_id, TopoFolder.kind == "region")
        .order_by(TopoFolder.sort_order.asc(), TopoFolder.name.asc())
        .all()
    )
    children = [c for c in children if not _is_ume_managed_folder(c)]
    if not children:
        return []
    placements = {
        vn.fabric_node_id: vn
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
        if is_region_canvas_node_id(vn.fabric_node_id)
    }
    out: list[ViewNodeOut] = []
    auto_x, auto_y = _default_region_xy(db, view.id)
    for i, child in enumerate(children):
        nid = region_canvas_node_id(child.id)
        vn = placements.get(nid)
        if vn is not None:
            out.append(
                region_node_out(
                    db,
                    folder=child,
                    x=float(vn.x or 0),
                    y=float(vn.y or 0),
                    locked=bool(vn.locked),
                    label=str(vn.label or child.name or ""),
                )
            )
        else:
            out.append(
                region_node_out(
                    db,
                    folder=child,
                    x=auto_x + i * 40,
                    y=auto_y + i * 20,
                    locked=False,
                    label=str(child.name or ""),
                )
            )
    return out
