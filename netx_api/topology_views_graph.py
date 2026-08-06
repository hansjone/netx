"""Topology view graph, populate, positions, and edge styles."""
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
    _EDGE_STATUS_MISSING,
    _EDGE_STATUS_MISSING_COMPAT,
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
    TopologyPlaceholderCreate,
    TopologyTreeFolderOut,
    TopologyTreeOut,
    TopologyTreeViewOut,
    TopologyViewCreate,
    TopologyViewGraphOut,
    TopologyViewOut,
    TopologyViewUpdate,
    ViewEdgeOut,
    ViewEdgeStylePatch,
    ViewMutationOut,
    ViewNodeIn,
    ViewNodeOut,
    ViewNodesAdd,
    ViewNodesRemove,
    ViewPopulateOut,
    ViewPopulateRequest,
    ViewPositionsPatch,
    ViewProjectNeighborsRequest,
)

# ---------------------------------------------------------------------------
# Folders (tree) + Views (leaf canvases)
# ---------------------------------------------------------------------------



from .topology_views_tree import (
    _get_folder_or_404,
    _get_view_or_404,
    _view_out,
    ensure_region_physical_view,
)

def _connect_status_for_node(db: Session, n: TopoFabricNode) -> str:
    if n.managed_ne_id:
        ne = db.get(ManagedNE, n.managed_ne_id)
        if ne is not None:
            return ne.connect_status or ""
    if n.ume_ne_id:
        ume = (
            db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == n.ume_ne_id).one_or_none()
        )
        if ume is not None:
            return ume.connection_status or ""
    return ""


def _managed_source_for_node(db: Session, n: TopoFabricNode) -> str:
    if n.managed_ne_id:
        ne = db.get(ManagedNE, n.managed_ne_id)
        if ne is not None:
            return str(ne.source or "").strip()
    return ""


def _batch_ne_lookups(
    db: Session, fabric_nodes: dict[str, TopoFabricNode]
) -> tuple[dict[str, ManagedNE], dict[str, UmeInventoryNE]]:
    mids = sorted(
        {
            str(fn.managed_ne_id or "").strip()
            for fn in fabric_nodes.values()
            if str(fn.managed_ne_id or "").strip()
        }
    )
    uids = sorted(
        {
            str(fn.ume_ne_id or "").strip()
            for fn in fabric_nodes.values()
            if str(fn.ume_ne_id or "").strip()
        }
    )
    managed = (
        {str(n.id): n for n in db.query(ManagedNE).filter(ManagedNE.id.in_(mids)).all()}
        if mids
        else {}
    )
    ume = (
        {
            str(u.ne_id): u
            for u in db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id.in_(uids)).all()
        }
        if uids
        else {}
    )
    return managed, ume


def get_view_graph(db: Session, view_id: str) -> TopologyViewGraphOut:
    from .topology_region_canvas import (
        child_region_nodes_for_view,
        is_region_canvas_node_id,
        region_folder_id_from_node,
        region_node_out,
    )
    from .ume_topology_world import is_ume_canvas_view
    from .ume_topology_world_graph import get_ume_canvas_graph

    view = _get_view_or_404(db, view_id)
    # Save/mutate return_graph must use the same UME synthetic graph as GET.
    if is_ume_canvas_view(view):
        return get_ume_canvas_graph(db, view_id)
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    truncated = False
    reason = ""
    if len(vnodes) > VIEW_GRAPH_NODE_HARD_CAP:
        vnodes = vnodes[:VIEW_GRAPH_NODE_HARD_CAP]
        truncated = True
        reason = "too_many_view_nodes"
    fids = [vn.fabric_node_id for vn in vnodes if not is_region_canvas_node_id(vn.fabric_node_id)]
    fabric_nodes = {
        n.id: n for n in db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(fids)).all()
    } if fids else {}
    managed_by_id, ume_by_id = _batch_ne_lookups(db, fabric_nodes)
    filt = dict(view.filter or {})
    layer = str(filt.get("layer") or "physical").strip() or "physical"
    status = str(filt.get("status") or "").strip().lower()
    nodes_out: list[ViewNodeOut] = []
    region_ids_seen: set[str] = set()
    for vn in vnodes:
        if is_region_canvas_node_id(vn.fabric_node_id):
            folder = db.get(TopoFolder, region_folder_id_from_node(vn.fabric_node_id))
            if folder is None or str(folder.kind or "") != "region":
                continue
            region_ids_seen.add(folder.id)
            nodes_out.append(
                region_node_out(
                    db,
                    folder=folder,
                    x=float(vn.x or 0),
                    y=float(vn.y or 0),
                    locked=bool(vn.locked),
                    label=str(vn.label or folder.name or ""),
                )
            )
            continue
        fn = fabric_nodes.get(vn.fabric_node_id)
        label = (vn.label or "").strip()
        if not label and fn is not None:
            label = (fn.name or fn.ip or vn.fabric_node_id)[:256]
        connect_status = ""
        managed_source = ""
        if fn is not None:
            mid = str(fn.managed_ne_id or "").strip()
            uid = str(fn.ume_ne_id or "").strip()
            if mid and mid in managed_by_id:
                mne = managed_by_id[mid]
                connect_status = mne.connect_status or ""
                managed_source = str(mne.source or "").strip()
            elif uid and uid in ume_by_id:
                connect_status = ume_by_id[uid].connection_status or ""
        nodes_out.append(
            ViewNodeOut(
                fabric_node_id=vn.fabric_node_id,
                managed_ne_id=(fn.managed_ne_id if fn else "") or "",
                ume_ne_id=(fn.ume_ne_id if fn else "") or "",
                label=label,
                x=float(vn.x or 0),
                y=float(vn.y or 0),
                locked=bool(vn.locked),
                name=(fn.name if fn else "") or "",
                ip=(fn.ip if fn else "") or "",
                vendor=(fn.vendor if fn else "") or "",
                device_type=(fn.device_type if fn else "") or "",
                connect_status=connect_status,
                managed_source=managed_source,
            )
        )
    # Child regions without a placement row still appear (legacy / missed create).
    for rn in child_region_nodes_for_view(db, view):
        fid = str(rn.folder_id or "").strip()
        if fid and fid not in region_ids_seen:
            nodes_out.append(rn)
            region_ids_seen.add(fid)
    edges_out: list[ViewEdgeOut] = []
    if fids:
        q = db.query(TopoFabricEdge).filter(
            TopoFabricEdge.layer == layer,
            TopoFabricEdge.a_node_id.in_(fids),
            TopoFabricEdge.b_node_id.in_(fids),
        )
        if status:
            st_norm = _normalize_edge_status(status)
            if st_norm == _EDGE_STATUS_MISSING:
                q = q.filter(TopoFabricEdge.status.in_(list(_EDGE_STATUS_MISSING_COMPAT)))
            else:
                q = q.filter(TopoFabricEdge.status == st_norm)
        edges = q.limit(VIEW_GRAPH_EDGE_HARD_CAP + 1).all()
        if len(edges) > VIEW_GRAPH_EDGE_HARD_CAP:
            edges = edges[:VIEW_GRAPH_EDGE_HARD_CAP]
            truncated = True
            reason = reason or "too_many_edges"
        styles = {
            s.fabric_edge_id: s
            for s in db.query(TopoViewEdgeStyle)
            .filter(
                TopoViewEdgeStyle.view_id == view.id,
                TopoViewEdgeStyle.fabric_edge_id.in_([e.id for e in edges]),
            )
            .all()
        }
        for e in edges:
            st = styles.get(e.id)
            src = str(e.source or "lldp").strip().lower() or "lldp"
            if src == "stale":
                src = "lldp"
            edges_out.append(
                ViewEdgeOut(
                    id=e.id,
                    a_node_id=e.a_node_id,
                    b_node_id=e.b_node_id,
                    a_port=e.a_port or "",
                    b_port=e.b_port or "",
                    source=src,
                    status=_normalize_edge_status(e.status or "active"),
                    layer=e.layer or "physical",
                    stroke_color=(st.stroke_color if st else "") or "",
                    stroke_width=int(st.stroke_width if st else 0) or 0,
                    line_style=(st.line_style if st else "") or "",
                    discovered_at=e.discovered_at,
                )
            )
    outside = _outside_peers_for_view(db, view, member_ids=set(fids), layer=layer)
    return TopologyViewGraphOut(
        view=_view_out(view, node_count=len(nodes_out)),
        nodes=nodes_out,
        edges=edges_out,
        truncated=truncated,
        truncate_reason=reason,
        outside_peers=outside,
    )


def _membership_for_view(view: TopoView) -> dict[str, Any]:
    return parse_membership(dict(view.filter or {}), role=normalize_view_role(view.role))


def _fabric_in_hard_scope(db: Session, fn: TopoFabricNode, mem: dict[str, Any]) -> bool:
    """If hard scope filters are set, node must match ALL set dimensions (AND)."""
    if not has_hard_scope(mem):
        return True
    mid = str(fn.managed_ne_id or "").strip()
    allowed_mids = set(mem.get("managed_ne_ids") or [])
    if allowed_mids and mid not in allowed_mids:
        return False
    vendors = {str(x).lower() for x in (mem.get("vendors") or [])}
    if vendors and str(fn.vendor or "").strip().lower() not in vendors:
        return False
    dtypes = {str(x).lower() for x in (mem.get("device_types") or [])}
    if dtypes and str(fn.device_type or "").strip().lower() not in dtypes:
        return False
    keyword = str(mem.get("keyword") or "").strip().lower()
    if keyword:
        blob = f"{fn.name or ''} {fn.ip or ''}".lower()
        if keyword not in blob:
            return False
    tags_any = {str(x).lower() for x in (mem.get("tags_any") or [])}
    if tags_any:
        ne = db.get(ManagedNE, mid) if mid else None
        tag_blob = str(getattr(ne, "tags", "") or "").lower() if ne else ""
        if not any(t in tag_blob for t in tags_any):
            return False
    return True


def _outside_peers_for_view(
    db: Session,
    view: TopoView,
    *,
    member_ids: set[str],
    layer: str,
    limit: int = 50,
) -> list[dict[str, str]]:
    if not member_ids:
        return []
    edges = (
        db.query(TopoFabricEdge)
        .filter(
            TopoFabricEdge.layer == layer,
            or_(
                TopoFabricEdge.a_node_id.in_(list(member_ids)),
                TopoFabricEdge.b_node_id.in_(list(member_ids)),
            ),
        )
        .limit(5000)
        .all()
    )
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for e in edges:
        for peer_id, local_id in ((e.b_node_id, e.a_node_id), (e.a_node_id, e.b_node_id)):
            if local_id not in member_ids or peer_id in member_ids:
                continue
            if peer_id in seen:
                continue
            seen.add(peer_id)
            fn = db.get(TopoFabricNode, peer_id)
            out.append(
                {
                    "fabric_node_id": peer_id,
                    "name": (fn.name if fn else "") or "",
                    "ip": (fn.ip if fn else "") or "",
                    "via_node_id": local_id,
                }
            )
            if len(out) >= limit:
                return out
    return out


def _place_fabric_ids_on_view(
    db: Session,
    view: TopoView,
    fabric_ids: list[str],
    *,
    existing: set[str],
    near_fabric_ids: set[str] | None = None,
) -> int:
    now = _utcnow()
    added = 0
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    anchor = None
    near = {str(x).strip() for x in (near_fabric_ids or set()) if str(x).strip()}
    if near:
        for vn in vnodes:
            if vn.fabric_node_id in near:
                anchor = vn
                break
    if anchor is not None:
        base_x = float(anchor.x or 0.0) + 200.0
        base_y = float(anchor.y or 0.0)
    else:
        max_x = max((float(vn.x or 0) for vn in vnodes), default=40.0)
        base_x = max_x + 200.0
        base_y = 40.0
    cols = max(1, int(len(fabric_ids) ** 0.5) or 1)
    for i, fid in enumerate(fabric_ids):
        if fid in existing or db.get(TopoFabricNode, fid) is None:
            continue
        x = base_x + (i % cols) * 180.0
        y = base_y + (i // cols) * 120.0
        db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=view.id,
                fabric_node_id=fid,
                x=x,
                y=y,
                label="",
                locked=False,
                created_at=now,
                updated_at=now,
            )
        )
        existing.add(fid)
        added += 1
    if added:
        view.updated_at = now
    return added


def _has_fabric_filter(
    *,
    keyword: str = "",
    role: str = "",
    vendor: str = "",
    link_status: str = "",
) -> bool:
    return bool(
        str(keyword or "").strip()
        or str(role or "").strip()
        or str(vendor or "").strip()
        or str(link_status or "").strip()
    )


def _apply_fabric_filters(
    q: Any,
    *,
    keyword: str = "",
    role: str = "",
    vendor: str = "",
    link_status: str = "",
) -> Any:
    kw = str(keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        q = q.filter(
            or_(
                TopoFabricNode.name.ilike(like),
                TopoFabricNode.ip.ilike(like),
                TopoFabricNode.managed_ne_id.ilike(like),
                TopoFabricNode.ume_ne_id.ilike(like),
            )
        )
    role_v = str(role or "").strip().lower()
    if role_v:
        q = q.filter(TopoFabricNode.role == role_v)
    vendor_v = str(vendor or "").strip()
    if vendor_v:
        q = q.filter(TopoFabricNode.vendor.ilike(f"%{vendor_v}%"))
    ls = str(link_status or "").strip().lower()
    if ls == "orphaned":
        q = q.filter(
            or_(TopoFabricNode.managed_ne_id.is_(None), TopoFabricNode.managed_ne_id == ""),
            or_(TopoFabricNode.ume_ne_id.is_(None), TopoFabricNode.ume_ne_id == ""),
        )
    elif ls == "linked":
        q = q.filter(
            or_(
                and_(TopoFabricNode.managed_ne_id.isnot(None), TopoFabricNode.managed_ne_id != ""),
                and_(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != ""),
            )
        )
    elif ls == "managed":
        q = q.filter(
            and_(TopoFabricNode.managed_ne_id.isnot(None), TopoFabricNode.managed_ne_id != ""),
            or_(TopoFabricNode.ume_ne_id.is_(None), TopoFabricNode.ume_ne_id == ""),
        )
    elif ls == "ume":
        q = q.filter(
            and_(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != ""),
            or_(TopoFabricNode.managed_ne_id.is_(None), TopoFabricNode.managed_ne_id == ""),
        )
    elif ls == "both":
        q = q.filter(
            and_(TopoFabricNode.managed_ne_id.isnot(None), TopoFabricNode.managed_ne_id != ""),
            and_(TopoFabricNode.ume_ne_id.isnot(None), TopoFabricNode.ume_ne_id != ""),
        )
    return q


def select_fabric_ids(
    db: Session,
    *,
    keyword: str = "",
    role: str = "",
    vendor: str = "",
    link_status: str = "",
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[str], int]:
    """Server-side fabric id selection for bulk add (paged)."""
    lim = max(1, min(VIEW_GRAPH_NODE_HARD_CAP, int(limit or 500)))
    off = max(0, int(offset or 0))
    q = _apply_fabric_filters(
        db.query(TopoFabricNode),
        keyword=keyword,
        role=role,
        vendor=vendor,
        link_status=link_status,
    )
    total = int(q.count())
    rows = q.order_by(TopoFabricNode.name.asc()).offset(off).limit(lim).all()
    return [str(r.id) for r in rows], total


def select_view_fabric_ids(
    db: Session,
    view_id: str,
    *,
    fabric_node_ids: list[str] | None = None,
    keyword: str = "",
    role: str = "",
    vendor: str = "",
    link_status: str = "",
) -> list[str]:
    """Select fabric ids already placed on a view, optionally narrowed by filter/ids."""
    explicit = [str(x).strip() for x in (fabric_node_ids or []) if str(x).strip()]
    q = (
        db.query(TopoFabricNode.id)
        .join(TopoViewNode, TopoViewNode.fabric_node_id == TopoFabricNode.id)
        .filter(TopoViewNode.view_id == view_id)
    )
    if explicit:
        q = q.filter(TopoFabricNode.id.in_(explicit))
    if _has_fabric_filter(keyword=keyword, role=role, vendor=vendor, link_status=link_status):
        q = _apply_fabric_filters(
            q, keyword=keyword, role=role, vendor=vendor, link_status=link_status
        )
    rows = q.order_by(TopoFabricNode.name.asc()).all()
    return [str(r[0] if isinstance(r, tuple) else r.id if hasattr(r, "id") else r) for r in rows]


def _layout_coords(
    count: int,
    *,
    layout: str,
    origin_x: float,
    origin_y: float,
    gap_x: float,
    gap_y: float,
    cols: int,
) -> list[tuple[float, float]]:
    kind = str(layout or "grid").strip().lower() or "grid"
    if count <= 0:
        return []
    if kind == "stack":
        return [(float(origin_x), float(origin_y) + i * float(gap_y)) for i in range(count)]
    c = int(cols or 0)
    if c <= 0:
        c = max(1, int(count**0.5) or 1)
    return [
        (float(origin_x) + (i % c) * float(gap_x), float(origin_y) + (i // c) * float(gap_y))
        for i in range(count)
    ]


def _view_node_count(db: Session, view_id: str) -> int:
    return int(
        db.query(func.count(TopoViewNode.id)).filter(TopoViewNode.view_id == view_id).scalar() or 0
    )


def _mutation_result(
    db: Session,
    view_id: str,
    *,
    max_nodes: int,
    return_graph: bool,
    matched: int = 0,
    added: int = 0,
    updated: int = 0,
    removed: int = 0,
    skipped_existing: int = 0,
    skipped_missing: int = 0,
    skipped_locked: int = 0,
    truncated: bool = False,
    next_offset: int | None = None,
) -> ViewMutationOut | TopologyViewGraphOut:
    if return_graph:
        return get_view_graph(db, view_id)
    return ViewMutationOut(
        ok=True,
        view_id=view_id,
        matched=matched,
        added=added,
        updated=updated,
        removed=removed,
        skipped_existing=skipped_existing,
        skipped_missing=skipped_missing,
        skipped_locked=skipped_locked,
        view_node_count=_view_node_count(db, view_id),
        max_nodes=max_nodes,
        truncated=truncated,
        next_offset=next_offset,
        graph=None,
    )


def patch_view_positions(
    db: Session, view_id: str, body: ViewPositionsPatch
) -> ViewMutationOut | TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    mem = _membership_for_view(view)
    max_nodes = int(mem.get("max_nodes") or 300)
    now = _utcnow()
    layout = str(body.layout or "").strip().lower()
    updated = 0
    skipped_locked = 0
    matched = 0

    existing = {
        vn.fabric_node_id: vn
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }

    if layout in {"grid", "offset", "stack"}:
        ids = select_view_fabric_ids(
            db,
            view.id,
            fabric_node_ids=list(body.fabric_node_ids or []),
            keyword=body.keyword,
            role=body.role,
            vendor=body.vendor,
            link_status=body.link_status,
        )
        if not ids and not _has_fabric_filter(
            keyword=body.keyword,
            role=body.role,
            vendor=body.vendor,
            link_status=body.link_status,
        ) and not (body.fabric_node_ids or []):
            # layout with no filter/ids → all nodes on view
            ids = sorted(existing.keys())
        matched = len(ids)
        if layout == "offset":
            for fid in ids:
                row = existing.get(fid)
                if row is None:
                    continue
                if row.locked:
                    skipped_locked += 1
                    continue
                row.x = float(row.x or 0) + float(body.dx or 0)
                row.y = float(row.y or 0) + float(body.dy or 0)
                row.updated_at = now
                updated += 1
        else:
            coords = _layout_coords(
                len(ids),
                layout=layout,
                origin_x=float(body.origin_x),
                origin_y=float(body.origin_y),
                gap_x=float(body.gap_x),
                gap_y=float(body.gap_y),
                cols=int(body.cols or 0),
            )
            for fid, (x, y) in zip(ids, coords):
                row = existing.get(fid)
                if row is None:
                    continue
                if row.locked:
                    skipped_locked += 1
                    continue
                row.x = x
                row.y = y
                row.updated_at = now
                updated += 1
    else:
        positions = list(body.positions or [])
        if len(positions) > VIEW_GRAPH_NODE_HARD_CAP:
            raise HTTPException(status_code=400, detail="too_many_positions")
        matched = len(positions)
        for p in positions:
            fid = str(p.fabric_node_id or "").strip()
            if not fid:
                continue
            from .topology_region_canvas import is_region_canvas_node_id

            if not is_region_canvas_node_id(fid) and db.get(TopoFabricNode, fid) is None:
                raise HTTPException(status_code=400, detail=f"fabric_node_not_found:{fid}")
            row = existing.get(fid)
            if row is None:
                row = TopoViewNode(
                    id=uuid4().hex,
                    view_id=view.id,
                    fabric_node_id=fid,
                    x=float(p.x or 0),
                    y=float(p.y or 0),
                    label=str(p.label or "")[:256],
                    locked=bool(p.locked),
                    created_at=now,
                    updated_at=now,
                )
                db.add(row)
                existing[fid] = row
                updated += 1
            else:
                if row.locked and bool(p.locked):
                    skipped_locked += 1
                    continue
                row.x = float(p.x or 0)
                row.y = float(p.y or 0)
                if p.label is not None:
                    row.label = str(p.label or "")[:256]
                row.locked = bool(p.locked)
                row.updated_at = now
                updated += 1

    view.updated_at = now
    db.commit()
    return _mutation_result(
        db,
        view.id,
        max_nodes=max_nodes,
        return_graph=bool(body.return_graph),
        matched=matched,
        updated=updated,
        skipped_locked=skipped_locked,
    )


def add_nodes_to_view(
    db: Session, view_id: str, body: ViewNodesAdd
) -> ViewMutationOut | TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    from .ume_topology_world import is_world_flat_view

    if is_world_flat_view(view):
        raise HTTPException(status_code=400, detail="world_map_no_direct_nes")
    mem = _membership_for_view(view)
    max_nodes = int(mem.get("max_nodes") or 300)
    now = _utcnow()
    existing = {
        vn.fabric_node_id
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    original_count = len(existing)
    if original_count >= max_nodes:
        if body.return_graph:
            raise HTTPException(status_code=400, detail="membership_max_nodes")
        return ViewMutationOut(
            ok=False,
            view_id=view.id,
            matched=0,
            view_node_count=original_count,
            max_nodes=max_nodes,
            truncated=True,
        )

    candidate_ids: list[str] = []
    matched_total = 0
    next_offset: int | None = None
    filter_mode = _has_fabric_filter(
        keyword=body.keyword,
        role=body.role,
        vendor=body.vendor,
        link_status=body.link_status,
    )

    if filter_mode:
        page_ids, matched_total = select_fabric_ids(
            db,
            keyword=body.keyword,
            role=body.role,
            vendor=body.vendor,
            link_status=body.link_status,
            offset=int(body.offset or 0),
            limit=int(body.limit or 500),
        )
        candidate_ids.extend(page_ids)
        end = int(body.offset or 0) + len(page_ids)
        if end < matched_total:
            next_offset = end
    else:
        for fid in body.fabric_node_ids or []:
            fid_s = str(fid or "").strip()
            if fid_s:
                candidate_ids.append(fid_s)
        matched_total = len(candidate_ids)

    # UI path: managed / ume may still create fabric nodes.
    for mid in body.managed_ne_ids or []:
        mid_s = str(mid or "").strip()
        if not mid_s:
            continue
        ne = db.get(ManagedNE, mid_s)
        if ne is None:
            continue
        fn = ensure_fabric_node_for_managed(db, ne)
        candidate_ids.append(fn.id)
        matched_total += 1
    default_profile = get_default_profile(db)
    for uid in body.ume_ne_ids or []:
        uid_s = str(uid or "").strip()
        if not uid_s:
            continue
        ume = db.query(UmeInventoryNE).filter(UmeInventoryNE.ne_id == uid_s).one_or_none()
        if ume is None:
            continue
        if default_profile is not None:
            dtype, vendor = infer_device_type_vendor(str(ume.ne_type or ""), default_profile)
        else:
            dtype, vendor = "zte_zxros", (ume.vendor or "ZTE")
        fn = ensure_fabric_node_for_ume(db, ume, device_type=dtype, vendor=vendor)
        candidate_ids.append(fn.id)
        matched_total += 1

    # Dedupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for fid in candidate_ids:
        if fid in seen:
            continue
        seen.add(fid)
        ordered.append(fid)

    skipped_existing = 0
    skipped_missing = 0
    to_add: list[str] = []
    for fid in ordered:
        if fid in existing:
            skipped_existing += 1
            continue
        if db.get(TopoFabricNode, fid) is None:
            skipped_missing += 1
            continue
        to_add.append(fid)

    room = max(0, max_nodes - original_count)
    truncated = len(to_add) > room
    if truncated:
        to_add = to_add[:room]
        next_offset = None  # capped by membership; caller should open another view

    keep_layout = str(body.layout or "grid").strip().lower() == "keep"
    if keep_layout:
        coords = [(40.0, 40.0)] * len(to_add)
    else:
        # Place new nodes to the right of existing content when possible.
        max_x = max((float(vn.x or 0) for vn in db.query(TopoViewNode).filter(
            TopoViewNode.view_id == view.id
        ).all()), default=40.0)
        origin_x = (max_x + 200.0) if original_count else 40.0
        coords = _layout_coords(
            len(to_add),
            layout="grid",
            origin_x=origin_x,
            origin_y=40.0,
            gap_x=180.0,
            gap_y=120.0,
            cols=0,
        )

    for fid, (x, y) in zip(to_add, coords):
        db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=view.id,
                fabric_node_id=fid,
                x=x,
                y=y,
                label="",
                locked=False,
                created_at=now,
                updated_at=now,
            )
        )
        existing.add(fid)

    if to_add:
        view.updated_at = now
        db.commit()
    elif filter_mode or body.fabric_node_ids or body.managed_ne_ids or body.ume_ne_ids:
        db.commit()

    return _mutation_result(
        db,
        view.id,
        max_nodes=max_nodes,
        return_graph=bool(body.return_graph),
        matched=matched_total if filter_mode else len(ordered),
        added=len(to_add),
        skipped_existing=skipped_existing,
        skipped_missing=skipped_missing,
        truncated=truncated or (next_offset is not None),
        next_offset=next_offset,
    )


def create_topology_placeholder_on_view(
    db: Session,
    view_id: str,
    body: TopologyPlaceholderCreate,
) -> TopologyViewGraphOut:
    """Create a ManagedNE with source=topology, ensure fabric node, place on the view."""
    from .device_types import TOPOLOGY_NE_SOURCE
    from .ne_service_common import _normalize_ip

    view = _get_view_or_404(db, view_id)
    from .ume_topology_world import is_world_flat_view

    if is_world_flat_view(view):
        raise HTTPException(status_code=400, detail="world_map_no_direct_nes")
    mem = _membership_for_view(view)
    max_nodes = int(mem.get("max_nodes") or 300)
    existing_count = (
        db.query(func.count(TopoViewNode.id)).filter(TopoViewNode.view_id == view.id).scalar() or 0
    )
    if int(existing_count) >= max_nodes:
        raise HTTPException(status_code=400, detail="membership_max_nodes")

    display = str(body.name or "").strip()[:256]
    if not display:
        raise HTTPException(status_code=400, detail="name_required")
    ip = _normalize_ip(body.ip_address)[:128]
    now = _utcnow()
    ne = ManagedNE(
        id=uuid4().hex,
        name=display,
        vendor="Other",
        device_type="generic",
        ip_address=ip,
        port=22,
        protocol="ssh",
        username="",
        password_enc="",
        enable_secret_enc="",
        connect_status="unknown",
        tags="",
        remark="Created on topology canvas",
        source=TOPOLOGY_NE_SOURCE,
        source_ref="",
        created_at=now,
        updated_at=now,
    )
    db.add(ne)
    db.flush()
    fabric = ensure_fabric_node_for_managed(db, ne)

    already = (
        db.query(TopoViewNode)
        .filter(TopoViewNode.view_id == view.id, TopoViewNode.fabric_node_id == fabric.id)
        .one_or_none()
    )
    if already is None:
        db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=view.id,
                fabric_node_id=fabric.id,
                x=float(body.x or 0.0),
                y=float(body.y or 0.0),
                label="",
                locked=False,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        already.x = float(body.x or already.x or 0.0)
        already.y = float(body.y or already.y or 0.0)
        already.updated_at = now
    view.updated_at = now
    db.commit()
    return get_view_graph(db, view.id)


def _neighbor_ids(
    db: Session, *, seed_ids: set[str], layer: str, hops: int
) -> set[str]:
    frontier = set(seed_ids)
    found: set[str] = set()
    for _ in range(max(0, hops)):
        if not frontier:
            break
        rows = (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.layer == layer,
                or_(
                    TopoFabricEdge.a_node_id.in_(list(frontier)),
                    TopoFabricEdge.b_node_id.in_(list(frontier)),
                ),
            )
            .all()
        )
        nxt: set[str] = set()
        for e in rows:
            for a, b in ((e.a_node_id, e.b_node_id), (e.b_node_id, e.a_node_id)):
                if a in frontier and b not in seed_ids and b not in found:
                    nxt.add(b)
        found |= nxt
        frontier = nxt
    return found


def project_fabric_neighbors_to_view(
    db: Session,
    view_id: str,
    body: ViewProjectNeighborsRequest | None = None,
) -> TopologyViewGraphOut:
    """Add in-scope fabric neighbors onto the leaf view (bounded by membership).

    Optional seeds limit expansion to neighbors of those fabric nodes (must
    already be on the view). Empty seeds → expand from every canvas node.

    dry_run=True returns the projected graph without persisting placements.
    """
    merge_duplicate_fabric_nodes(db)
    view = _get_view_or_404(db, view_id)
    mem = _membership_for_view(view)
    req = body or ViewProjectNeighborsRequest()
    dry_run = bool(req.dry_run)
    if bool(mem.get("frozen")):
        g = get_view_graph(db, view.id)
        g.truncated = True
        g.truncate_reason = "membership_frozen"
        return g

    max_nodes = int(mem.get("max_nodes") or 300)
    hops = int(mem.get("expand_hops") or 1)
    filt = dict(view.filter or {})
    layer = str(filt.get("layer") or "physical").strip() or "physical"

    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    fids_on_view = [vn.fabric_node_id for vn in vnodes]
    fabric_on_view = (
        {
            n.id: n
            for n in db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(fids_on_view)).all()
        }
        if fids_on_view
        else {}
    )
    # Drop placements pointing at missing fabric rows only (keep LLDP placeholders).
    orphan_vns = [vn for vn in vnodes if vn.fabric_node_id not in fabric_on_view]
    if orphan_vns and not dry_run:
        for vn in orphan_vns:
            db.delete(vn)
        view.updated_at = _utcnow()
        db.commit()
        vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
        fids_on_view = [vn.fabric_node_id for vn in vnodes]
        fabric_on_view = (
            {
                n.id: n
                for n in db.query(TopoFabricNode)
                .filter(TopoFabricNode.id.in_(fids_on_view))
                .all()
            }
            if fids_on_view
            else {}
        )

    existing = {vn.fabric_node_id for vn in vnodes}
    if not existing:
        return get_view_graph(db, view.id)
    if len(existing) >= max_nodes:
        g = get_view_graph(db, view.id)
        g.truncated = True
        g.truncate_reason = g.truncate_reason or "membership_cap"
        return g

    seed_ids: set[str] = {
        str(x).strip() for x in (req.seed_fabric_node_ids or []) if str(x).strip()
    }
    want_mids = {
        str(mid or "").strip() for mid in (req.managed_ne_ids or []) if str(mid or "").strip()
    }
    if want_mids:
        for fid, fn in fabric_on_view.items():
            if str(fn.managed_ne_id or "").strip() in want_mids:
                seed_ids.add(fid)
    if seed_ids:
        seed_ids &= existing
        if not seed_ids:
            return get_view_graph(db, view.id)
    else:
        seed_ids = set(existing)

    peer_ids = _neighbor_ids(db, seed_ids=seed_ids, layer=layer, hops=hops)
    peer_rows = (
        {
            n.id: n
            for n in db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(list(peer_ids))).all()
        }
        if peer_ids
        else {}
    )
    eligible: list[str] = []
    for peer in sorted(peer_ids):
        if peer in existing:
            continue
        fn = peer_rows.get(peer)
        if fn is None or not _is_inventory_node(fn):
            continue
        if _fabric_match_score(db, fn) < 2:
            continue
        if not _fabric_in_hard_scope(db, fn, mem):
            continue
        eligible.append(peer)

    room = max(0, max_nodes - len(existing))
    to_add = eligible[:room]
    truncated = len(eligible) > len(to_add)

    if dry_run:
        if to_add:
            nested = db.begin_nested()
            try:
                _place_fabric_ids_on_view(
                    db, view, to_add, existing=set(existing), near_fabric_ids=seed_ids
                )
                db.flush()
                g = get_view_graph(db, view.id)
            finally:
                nested.rollback()
        else:
            g = get_view_graph(db, view.id)
        if truncated:
            g.truncated = True
            g.truncate_reason = g.truncate_reason or "membership_cap"
        return g

    if to_add:
        _place_fabric_ids_on_view(
            db, view, to_add, existing=existing, near_fabric_ids=seed_ids
        )
        db.commit()
    g = get_view_graph(db, view.id)
    if truncated:
        g.truncated = True
        g.truncate_reason = g.truncate_reason or "membership_cap"
    return g


def populate_view(db: Session, view_id: str, body: ViewPopulateRequest) -> ViewPopulateOut:
    """Resolve membership candidates and optionally place them on the leaf view."""
    view = _get_view_or_404(db, view_id)
    role = normalize_view_role(view.role)
    if body.membership is not None:
        filt = merge_filter_with_membership(
            dict(view.filter or {}), role=role, membership=parse_membership(
                {"membership": body.membership}, role=role
            )
        )
        if not body.dry_run:
            view.filter = filt
    mem = parse_membership(dict(view.filter or {}), role=role)
    max_nodes = int(mem.get("max_nodes") or 300)
    hops = int(mem.get("expand_hops") or 1)
    layer = str((view.filter or {}).get("layer") or "physical").strip() or "physical"

    existing = {
        vn.fabric_node_id
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    seeds = set(mem.get("seed_fabric_node_ids") or []) | set(existing)

    # Seed from managed_ne_ids
    for mid in mem.get("managed_ne_ids") or []:
        ne = db.get(ManagedNE, mid)
        if ne is None:
            continue
        fn = ensure_fabric_node_for_managed(db, ne)
        seeds.add(fn.id)

    # Hard-scope scan when filters present
    candidates: set[str] = set(seeds)
    if has_hard_scope(mem):
        for fn in db.query(TopoFabricNode).all():
            if not _is_inventory_node(fn):
                continue
            if _fabric_in_hard_scope(db, fn, mem):
                candidates.add(fn.id)

    if hops > 0 and seeds:
        for peer in _neighbor_ids(db, seed_ids=seeds, layer=layer, hops=hops):
            fn = db.get(TopoFabricNode, peer)
            if fn is None or not _is_inventory_node(fn):
                continue
            if has_hard_scope(mem) and not _fabric_in_hard_scope(db, fn, mem):
                continue
            if _fabric_match_score(db, fn) < 2:
                continue
            candidates.add(peer)

    ordered = sorted(candidates)
    truncated = len(ordered) > max_nodes
    ordered = ordered[:max_nodes]
    would_add = [fid for fid in ordered if fid not in existing]

    outside = _outside_peers_for_view(db, view, member_ids=set(ordered), layer=layer)
    if body.dry_run:
        return ViewPopulateOut(
            view_id=view.id,
            dry_run=True,
            candidate_count=len(candidates),
            would_add=len(would_add),
            added=0,
            max_nodes=max_nodes,
            truncated=truncated,
            outside_peers=outside,
            graph=None,
        )

    added = _place_fabric_ids_on_view(db, view, would_add, existing=existing)
    if body.freeze_after:
        mem["frozen"] = True
        view.filter = merge_filter_with_membership(dict(view.filter or {}), role=role, membership=mem)
    view.updated_at = _utcnow()
    db.commit()
    g = get_view_graph(db, view.id)
    if truncated:
        g.truncated = True
        g.truncate_reason = g.truncate_reason or "membership_cap"
    return ViewPopulateOut(
        view_id=view.id,
        dry_run=False,
        candidate_count=len(candidates),
        would_add=len(would_add),
        added=added,
        max_nodes=max_nodes,
        truncated=truncated,
        outside_peers=g.outside_peers,
        graph=g,
    )


def remove_view_nodes(
    db: Session,
    view_id: str,
    fabric_node_ids: list[str] | ViewNodesRemove | None = None,
    *,
    body: ViewNodesRemove | None = None,
) -> ViewMutationOut | TopologyViewGraphOut:
    """Remove placements. Accept ViewNodesRemove, or a legacy list of fabric ids."""
    view = _get_view_or_404(db, view_id)
    mem = _membership_for_view(view)
    max_nodes = int(mem.get("max_nodes") or 300)

    if isinstance(fabric_node_ids, ViewNodesRemove):
        req = fabric_node_ids
    elif body is not None:
        req = body
    else:
        req = ViewNodesRemove(fabric_node_ids=list(fabric_node_ids or []), return_graph=True)

    filter_mode = _has_fabric_filter(
        keyword=req.keyword, role=req.role, vendor=req.vendor, link_status=req.link_status
    )
    explicit = [str(x).strip() for x in (req.fabric_node_ids or []) if str(x).strip()]
    if filter_mode or explicit:
        ids = select_view_fabric_ids(
            db,
            view.id,
            fabric_node_ids=explicit or None,
            keyword=req.keyword,
            role=req.role,
            vendor=req.vendor,
            link_status=req.link_status,
        )
    else:
        ids = []

    removed = 0
    if ids:
        removed = (
            db.query(TopoViewNode)
            .filter(TopoViewNode.view_id == view.id, TopoViewNode.fabric_node_id.in_(ids))
            .delete(synchronize_session=False)
        )
        view.updated_at = _utcnow()
        db.commit()
    return _mutation_result(
        db,
        view.id,
        max_nodes=max_nodes,
        return_graph=bool(req.return_graph),
        matched=len(ids),
        removed=int(removed or 0),
    )


_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_LINE_STYLES = {"", "solid", "dashed", "dotted"}


def patch_view_edge_style(
    db: Session, view_id: str, body: ViewEdgeStylePatch
) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    eid = str(body.fabric_edge_id or "").strip()
    if not eid or db.get(TopoFabricEdge, eid) is None:
        raise HTTPException(status_code=404, detail="fabric_edge_not_found")
    color = str(body.stroke_color or "").strip()
    if color and not _HEX_COLOR_RE.match(color):
        raise HTTPException(status_code=400, detail="invalid_stroke_color")
    width = int(body.stroke_width or 0)
    if width < 0 or width > 12:
        raise HTTPException(status_code=400, detail="invalid_stroke_width")
    style = str(body.line_style or "").strip().lower()
    if style not in _LINE_STYLES:
        raise HTTPException(status_code=400, detail="invalid_line_style")
    now = _utcnow()
    row = (
        db.query(TopoViewEdgeStyle)
        .filter(TopoViewEdgeStyle.view_id == view.id, TopoViewEdgeStyle.fabric_edge_id == eid)
        .one_or_none()
    )
    if row is None:
        row = TopoViewEdgeStyle(
            id=uuid4().hex,
            view_id=view.id,
            fabric_edge_id=eid,
            stroke_color=color,
            stroke_width=width,
            line_style=style,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.stroke_color = color
        row.stroke_width = width
        row.line_style = style
        row.updated_at = now
    view.updated_at = now
    db.commit()
    return get_view_graph(db, view.id)


