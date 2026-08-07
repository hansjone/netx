"""UME hierarchical level graph + flat world viewport graph."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .models import TopoFabricEdge, TopoFabricNode, TopoFolder, TopoView, TopoViewNode, UmeTopoNode
from .topology_common import (
    VIEW_GRAPH_EDGE_HARD_CAP,
    VIEW_GRAPH_NODE_HARD_CAP,
    _EDGE_STATUS_MISSING,
    _EDGE_STATUS_MISSING_COMPAT,
    _normalize_edge_status,
)
from .topology_schemas import (
    TopologyViewGraphOut,
    ViewEdgeOut,
    ViewNodeOut,
    WorldScatterPointOut,
    WorldTransformOut,
)
from .topology_views_tree import _get_view_or_404, _view_out
from .ume_topology_world import (
    folder_bbox,
    is_ume_canvas_view,
    is_ume_level_view,
    is_world_flat_view,
    is_world_view,
)

# Browser-safe caps. Flat world used to ship 2000 RF nodes (~1MB JSON) and freeze
# the UI (fitView never settles; workbench feels stuck). Keep membership graphs at
# the shared hard cap; flat overview uses a lightweight scatter starfield (all
# coords) while detail uses viewport RF nodes.
WORLD_NODE_SOFT_CAP = VIEW_GRAPH_NODE_HARD_CAP
WORLD_FLAT_NODE_SOFT_CAP = 400  # legacy alias → detail
WORLD_FLAT_OVERVIEW_CAP = 1500  # legacy RF sample; overview now prefers scatter
WORLD_FLAT_SCATTER_CAP = 40000
# Close-up must be able to show a whole large SBN like the region canvas (≤ hard cap).
WORLD_FLAT_DETAIL_CAP = VIEW_GRAPH_NODE_HARD_CAP
# Viewport span (display/world units) at/under which we prefer a full region layout
# instead of stride-thinning — stops “zoomed in still a peppered blob”.
WORLD_FLAT_CLOSE_SPAN = 28000.0
# Legacy crush span (pre-LOD). Flat graph is now 1:1 with fabric world_*;
# this constant only detects stale TopoViewNode overrides from that era.
WORLD_FLAT_DISPLAY_SPAN = 6000.0


def apply_persisted_view_positions(
    db: Session, view_id: str, nodes: list[ViewNodeOut]
) -> list[ViewNodeOut]:
    """Overlay drag/save positions from TopoViewNode onto a synthetic UME graph.

    UME level canvases are built from dock coords; user moves persist on the view
    membership table and must win on reload (and on save return_graph).
    """
    vid = str(view_id or "").strip()
    if not vid or not nodes:
        return nodes
    rows = db.query(TopoViewNode).filter(TopoViewNode.view_id == vid).all()
    if not rows:
        return nodes
    overrides = {str(vn.fabric_node_id): vn for vn in rows}
    out: list[ViewNodeOut] = []
    for n in nodes:
        ov = overrides.get(str(n.fabric_node_id))
        if ov is None:
            out.append(n)
            continue
        # Keep UME-built labels (e.g. region NE counts); only persist geometry.
        out.append(
            n.model_copy(
                update={
                    "x": float(ov.x or 0),
                    "y": float(ov.y or 0),
                    "locked": bool(ov.locked),
                }
            )
        )
    return out


def get_ume_canvas_graph(
    db: Session,
    view_id: str,
    *,
    min_x: float | None = None,
    max_x: float | None = None,
    min_y: float | None = None,
    max_y: float | None = None,
    sbn_id: str = "",
    folder_id: str = "",
    lod: str = "auto",
    status: str = "active",
) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    if not is_ume_canvas_view(view):
        raise HTTPException(status_code=400, detail="not_a_ume_canvas_view")
    if is_world_flat_view(view) or (
        bool((view.filter or {}).get("world")) and not is_ume_level_view(view)
    ):
        return get_flat_view_graph(
            db,
            view,
            min_x=min_x,
            max_x=max_x,
            min_y=min_y,
            max_y=max_y,
            sbn_id=sbn_id,
            folder_id=folder_id,
            lod=lod,
            status=status,
        )
    return get_level_view_graph(db, view, status=status)


# Back-compat name used by router
def get_world_view_graph(
    db: Session,
    view_id: str,
    **kwargs: Any,
) -> TopologyViewGraphOut:
    return get_ume_canvas_graph(db, view_id, **kwargs)


def get_level_view_graph(
    db: Session,
    view: TopoView,
    *,
    status: str = "active",
) -> TopologyViewGraphOut:
    """One UME canvas level: direct child SBNs (regions) + direct child MEs."""
    filt = dict(view.filter or {})
    parent_key = str(filt.get("parent") or "").strip()
    sbn_id = str(filt.get("sbn_id") or "").strip()

    if parent_key == "md" or is_world_view(view):
        # Root: SBN whose parent is not another SBN (+ rare direct MEs under MD).
        sbn_ids = {
            str(row[0])
            for row in db.query(UmeTopoNode.node_id).filter(UmeTopoNode.node_type == "TOPO_NODE_SBN").all()
            if str(row[0] or "").strip()
        }
        child_sbns = [
            n
            for n in db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_SBN").all()
            if str(n.parent_node or "").strip() not in sbn_ids
        ]
        # Almost all MEs hang under an SBN; push the exclusion into SQL.
        if sbn_ids:
            child_mes = (
                db.query(UmeTopoNode)
                .filter(
                    UmeTopoNode.node_type == "TOPO_NODE_ME",
                    or_(
                        UmeTopoNode.parent_node.is_(None),
                        UmeTopoNode.parent_node == "",
                        ~UmeTopoNode.parent_node.in_(list(sbn_ids)),
                    ),
                )
                .all()
            )
        else:
            child_mes = (
                db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_ME").all()
            )
        level_parent = ""  # MD virtual
    else:
        if not sbn_id and view.folder_id:
            folder = db.get(TopoFolder, view.folder_id)
            sbn_id = str(getattr(folder, "external_ref", None) or "").strip()
            if sbn_id == "ume:world":
                sbn_id = ""
        if not sbn_id:
            return TopologyViewGraphOut(view=_view_out(view), nodes=[], edges=[])
        level_parent = sbn_id
        child_sbns = (
            db.query(UmeTopoNode)
            .filter(
                UmeTopoNode.node_type == "TOPO_NODE_SBN",
                UmeTopoNode.parent_node == level_parent,
            )
            .all()
        )
        child_mes = (
            db.query(UmeTopoNode)
            .filter(
                UmeTopoNode.node_type == "TOPO_NODE_ME",
                UmeTopoNode.parent_node == level_parent,
            )
            .all()
        )

    # --- Level-by-level: only THIS level's direct child SBNs + direct child MEs.
    # Region badge counts roll up the whole SBN subtree (cheap: 114 SBNs + GROUP BY).
    # Deep edge lifts still belong to deeper drills or the flat world map.
    child_sbn_ids = [str(s.node_id) for s in child_sbns if str(s.node_id or "").strip()]
    child_sbn_id_set = set(child_sbn_ids)

    folders_by_ref: dict[str, TopoFolder] = {}
    if child_sbn_id_set:
        for f in (
            db.query(TopoFolder)
            .filter(TopoFolder.external_ref.in_(list(child_sbn_id_set)))
            .all()
        ):
            ref = str(f.external_ref or "").strip()
            if ref:
                folders_by_ref[ref] = f

    level_view_id_by_sbn: dict[str, str] = {}
    if child_sbn_id_set:
        for vid, filt in db.query(TopoView.id, TopoView.filter).all():
            vf = dict(filt or {})
            sid = str(vf.get("sbn_id") or "").strip()
            if vf.get("ume_level") and sid in child_sbn_id_set:
                level_view_id_by_sbn[sid] = str(vid)

    # Subtree ME counts for each direct child region (badge), without hydrating all MEs.
    region_me_counts: dict[str, int] = {sid: 0 for sid in child_sbn_ids}
    if child_sbn_id_set:
        sbn_parent: dict[str, str] = {
            str(nid): str(parent or "").strip()
            for nid, parent in db.query(UmeTopoNode.node_id, UmeTopoNode.parent_node)
            .filter(UmeTopoNode.node_type == "TOPO_NODE_SBN")
            .all()
            if str(nid or "").strip()
        }
        sbn_children: dict[str, list[str]] = defaultdict(list)
        for sid, parent in sbn_parent.items():
            if parent:
                sbn_children[parent].append(sid)

        def _descendants(root_sid: str) -> set[str]:
            out = {root_sid}
            stack = [root_sid]
            while stack:
                cur = stack.pop()
                for cid in sbn_children.get(cur, []):
                    if cid not in out:
                        out.add(cid)
                        stack.append(cid)
            return out

        region_desc: dict[str, set[str]] = {sid: _descendants(sid) for sid in child_sbn_ids}
        me_by_parent: dict[str, int] = {
            str(parent): int(cnt or 0)
            for parent, cnt in (
                db.query(UmeTopoNode.parent_node, func.count())
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
        for rid, desc in region_desc.items():
            region_me_counts[rid] = sum(me_by_parent.get(p, 0) for p in desc)

    direct_me_uids = [
        uid
        for uid in (str(tn.ume_ne_id or tn.node_id or "").strip() for tn in child_mes)
        if uid
    ]
    fabric_by_ume: dict[str, TopoFabricNode] = {}
    if direct_me_uids:
        for n in (
            db.query(TopoFabricNode)
            .filter(TopoFabricNode.ume_ne_id.in_(direct_me_uids))
            .all()
        ):
            uid = str(n.ume_ne_id or "").strip()
            if uid:
                fabric_by_ume[uid] = n

    nodes_out: list[ViewNodeOut] = []
    region_node_ids: dict[str, str] = {}
    for sbn in child_sbns:
        sid = str(sbn.node_id or "").strip()
        if not sid:
            continue
        folder = folders_by_ref.get(sid)
        child_view_id = level_view_id_by_sbn.get(sid, "")
        n_me = int(region_me_counts.get(sid, 0))
        nid = f"region:{sid}"
        region_node_ids[sid] = nid
        label = (sbn.user_label or (folder.name if folder else sid) or sid)[:256]
        if n_me:
            label = f"{label} ({n_me})"
        nodes_out.append(
            ViewNodeOut(
                fabric_node_id=nid,
                managed_ne_id="",
                ume_ne_id=sid,
                label=label,
                x=float(sbn.x_pos or 0),
                y=float(sbn.y_pos or 0),
                locked=False,
                name=(sbn.user_label or "")[:256],
                ip="",
                vendor="SBN",
                device_type="region",
                kind="region",
                folder_id=folder.id if folder else "",
                view_id=child_view_id,
                node_count=n_me,
            )
        )

    direct_me_fids: set[str] = set()
    me_uid_to_fid: dict[str, str] = {}
    for tn in child_mes:
        uid = str(tn.ume_ne_id or tn.node_id or "").strip()
        if not uid:
            continue
        fn = fabric_by_ume.get(uid)
        if fn is None:
            continue
        direct_me_fids.add(fn.id)
        me_uid_to_fid[uid] = fn.id
        lx = float(tn.x_pos) if tn.x_pos is not None else float(fn.attrs or {}).get("ume_local_x") or 0.0
        ly = float(tn.y_pos) if tn.y_pos is not None else float(fn.attrs or {}).get("ume_local_y") or 0.0
        nodes_out.append(
            ViewNodeOut(
                fabric_node_id=fn.id,
                managed_ne_id=str(fn.managed_ne_id or ""),
                ume_ne_id=uid,
                label=(fn.name or fn.ip or uid)[:256],
                x=lx,
                y=ly,
                locked=False,
                name=fn.name or "",
                ip=fn.ip or "",
                vendor=fn.vendor or "",
                device_type=fn.device_type or "",
                kind="ne",
            )
        )

    st = str(status or "active").strip().lower() or "active"
    st_norm = _normalize_edge_status(st)
    status_filter = (
        list(_EDGE_STATUS_MISSING_COMPAT)
        if st_norm == _EDGE_STATUS_MISSING
        else [st_norm]
    )

    physical: list[ViewEdgeOut] = []
    if direct_me_fids:
        direct_list = list(direct_me_fids)
        for e in (
            db.query(TopoFabricEdge)
            .filter(
                TopoFabricEdge.layer == "physical",
                TopoFabricEdge.status.in_(status_filter),
                TopoFabricEdge.a_node_id.in_(direct_list),
                TopoFabricEdge.b_node_id.in_(direct_list),
            )
            .limit(VIEW_GRAPH_EDGE_HARD_CAP + 1)
            .all()
        ):
            if e.a_node_id == e.b_node_id:
                continue
            physical.append(
                ViewEdgeOut(
                    id=e.id,
                    a_node_id=e.a_node_id,
                    b_node_id=e.b_node_id,
                    a_port=e.a_port or "",
                    b_port=e.b_port or "",
                    source=e.source or "lldp",
                    status=e.status or "active",
                    layer=e.layer or "physical",
                    discovered_at=e.discovered_at,
                )
            )

    # Logical lifts only among MEs that hang *directly* under this level's child regions
    # (one hop). Deeper nesting is loaded when the user drills into that region.
    logical_count: dict[tuple[str, str], int] = defaultdict(int)
    if region_node_ids and child_sbn_id_set:
        me_uid_to_region: dict[str, str] = {}
        for ume_ne_id, node_id, parent_node in (
            db.query(UmeTopoNode.ume_ne_id, UmeTopoNode.node_id, UmeTopoNode.parent_node)
            .filter(
                UmeTopoNode.node_type == "TOPO_NODE_ME",
                UmeTopoNode.parent_node.in_(list(child_sbn_id_set)),
            )
            .all()
        ):
            uid = str(ume_ne_id or node_id or "").strip()
            parent = str(parent_node or "").strip()
            if uid and parent in region_node_ids:
                me_uid_to_region[uid] = parent

        if me_uid_to_region:
            fid_to_canvas: dict[str, str] = {}
            for fid, uid in (
                db.query(TopoFabricNode.id, TopoFabricNode.ume_ne_id)
                .filter(TopoFabricNode.ume_ne_id.in_(list(me_uid_to_region.keys())))
                .all()
            ):
                rid = me_uid_to_region.get(str(uid or "").strip(), "")
                if rid:
                    fid_to_canvas[str(fid)] = region_node_ids[rid]
            for fid in direct_me_fids:
                fid_to_canvas[fid] = fid

            fids = list(fid_to_canvas.keys())
            if fids:
                for a_id, b_id in (
                    db.query(TopoFabricEdge.a_node_id, TopoFabricEdge.b_node_id)
                    .filter(
                        TopoFabricEdge.layer == "physical",
                        TopoFabricEdge.status.in_(status_filter),
                        TopoFabricEdge.a_node_id.in_(fids),
                        TopoFabricEdge.b_node_id.in_(fids),
                    )
                    .all()
                ):
                    a = fid_to_canvas.get(str(a_id))
                    b = fid_to_canvas.get(str(b_id))
                    if not a or not b or a == b:
                        continue
                    if a in direct_me_fids and b in direct_me_fids:
                        continue
                    key = (a, b) if a < b else (b, a)
                    logical_count[key] += 1

    edges_out = physical[:VIEW_GRAPH_EDGE_HARD_CAP]
    truncated = len(physical) > VIEW_GRAPH_EDGE_HARD_CAP
    for (a, b), cnt in logical_count.items():
        edges_out.append(
            ViewEdgeOut(
                id=f"logical:{a}:{b}",
                a_node_id=a,
                b_node_id=b,
                a_port=str(cnt),
                b_port="",
                source="ume",
                status="active",
                layer="logical",
            )
        )

    from .topology_region_canvas import child_region_nodes_for_view

    seen_folder = {str(n.folder_id or "") for n in nodes_out if n.kind == "region"}
    for rn in child_region_nodes_for_view(db, view):
        fid = str(rn.folder_id or "").strip()
        if fid and fid not in seen_folder:
            nodes_out.append(rn)
            seen_folder.add(fid)

    nodes_out = apply_persisted_view_positions(db, view.id, nodes_out)

    return TopologyViewGraphOut(
        view=_view_out(view),
        nodes=nodes_out,
        edges=edges_out,
        truncated=truncated,
        truncate_reason="too_many_edges" if truncated else "",
    )


def get_flat_view_graph(
    db: Session,
    view: TopoView,
    *,
    min_x: float | None = None,
    max_x: float | None = None,
    min_y: float | None = None,
    max_y: float | None = None,
    sbn_id: str = "",
    folder_id: str = "",
    lod: str = "auto",
    status: str = "active",
) -> TopologyViewGraphOut:
    """Flat world map with LOD: overview (scatter starfield) or detail (viewport RF).

    Client bbox is in **display** coordinates (same as returned node x/y). Folder
    auto-bbox (when only folder_id is set) stays in packed world coordinates.
    """
    region_folder_ids: set[str] | None = None
    folder_world_bbox: dict[str, float] | None = None
    if folder_id.strip():
        bbox = folder_bbox(db, folder_id.strip())
        folder = db.get(TopoFolder, folder_id.strip())
        if folder is not None:
            all_folders = db.query(TopoFolder).all()
            children: dict[str | None, list[str]] = {}
            for f in all_folders:
                children.setdefault(f.parent_id, []).append(f.id)
            region_folder_ids = {folder.id}
            stack = [folder.id]
            while stack:
                cur = stack.pop()
                for cid in children.get(cur, []):
                    if cid not in region_folder_ids:
                        region_folder_ids.add(cid)
                        stack.append(cid)
        if bbox is not None:
            folder_world_bbox = {
                "min_x": float(bbox["min_x"]),
                "max_x": float(bbox["max_x"]),
                "min_y": float(bbox["min_y"]),
                "max_y": float(bbox["max_y"]),
            }

    extent_row = (
        db.query(
            func.min(TopoFabricNode.world_x),
            func.max(TopoFabricNode.world_x),
            func.min(TopoFabricNode.world_y),
            func.max(TopoFabricNode.world_y),
            func.count(TopoFabricNode.id),
        )
        .filter(TopoFabricNode.world_x.isnot(None), TopoFabricNode.world_y.isnot(None))
        .one()
    )
    total_all = int(extent_row[4] or 0)
    dock_me_count = 0
    if not total_all:
        try:
            dock_me_count = int(
                db.query(func.count(UmeTopoNode.node_id))
                .filter(UmeTopoNode.node_type == "TOPO_NODE_ME")
                .scalar()
                or 0
            )
        except Exception:
            dock_me_count = 0
        return TopologyViewGraphOut(
            view=_view_out(view),
            nodes=[],
            edges=[],
            world_transform=WorldTransformOut(
                total=0,
                lod=str(lod or "overview").strip().lower() or "overview",
                dock_me_count=dock_me_count,
            ),
        )

    full_min_x = float(extent_row[0])
    full_max_x = float(extent_row[1])
    full_min_y = float(extent_row[2])
    full_max_y = float(extent_row[3])
    span_x = max(1.0, full_max_x - full_min_x)
    span_y = max(1.0, full_max_y - full_min_y)
    # 1:1 with fabric world_* (only origin-shift to ~0). Infinite zoom keeps
    # each packed region block identical to its UME local relative layout.
    scale = 1.0

    client_bbox = (
        min_x is not None
        and max_x is not None
        and min_y is not None
        and max_y is not None
    )
    lod_norm = str(lod or "auto").strip().lower() or "auto"
    if lod_norm not in ("overview", "detail", "auto"):
        lod_norm = "auto"
    if lod_norm == "auto":
        lod_norm = "detail" if client_bbox else "overview"

    def _world_transform(*, total: int | None = None) -> WorldTransformOut:
        return WorldTransformOut(
            origin_x=full_min_x,
            origin_y=full_min_y,
            scale=scale,
            full_min_x=full_min_x,
            full_max_x=full_max_x,
            full_min_y=full_min_y,
            full_max_y=full_max_y,
            total=int(total if total is not None else total_all),
            lod=lod_norm,
            dock_me_count=0,
        )

    # Overview: lightweight starfield only (no RF nodes / edges). Screen-space
    # canvas draws these so fitView zoom no longer shrinks dots to sub-pixels.
    if lod_norm == "overview" and not sbn_id.strip():
        scatter_q = db.query(TopoFabricNode.world_x, TopoFabricNode.world_y).filter(
            TopoFabricNode.world_x.isnot(None),
            TopoFabricNode.world_y.isnot(None),
        )
        if region_folder_ids is not None:
            scatter_q = scatter_q.filter(TopoFabricNode.region_folder_id.in_(list(region_folder_ids)))
        rows = (
            scatter_q.order_by(TopoFabricNode.world_x.asc(), TopoFabricNode.world_y.asc())
            .limit(WORLD_FLAT_SCATTER_CAP + 1)
            .all()
        )
        truncated = len(rows) > WORLD_FLAT_SCATTER_CAP
        if truncated:
            rows = rows[:WORLD_FLAT_SCATTER_CAP]
        scatter = [
            WorldScatterPointOut(
                x=(float(wx or 0) - full_min_x) * scale,
                y=(float(wy or 0) - full_min_y) * scale,
            )
            for wx, wy in rows
        ]
        return TopologyViewGraphOut(
            view=_view_out(view),
            nodes=[],
            edges=[],
            truncated=truncated,
            truncate_reason="too_many_scatter_points" if truncated else "",
            world_transform=_world_transform(total=total_all),
            scatter=scatter,
        )

    if client_bbox:
        # Display == world - origin; pad detail viewport so pan feels continuous.
        pad = 0.2 if lod_norm == "detail" else 0.0
        dx0, dx1 = float(min_x), float(max_x)
        dy0, dy1 = float(min_y), float(max_y)
        if dx1 < dx0:
            dx0, dx1 = dx1, dx0
        if dy1 < dy0:
            dy0, dy1 = dy1, dy0
        dw = max(1.0, dx1 - dx0)
        dh = max(1.0, dy1 - dy0)
        dx0 -= dw * pad
        dx1 += dw * pad
        dy0 -= dh * pad
        dy1 += dh * pad
        w_min_x = dx0 / scale + full_min_x
        w_max_x = dx1 / scale + full_min_x
        w_min_y = dy0 / scale + full_min_y
        w_max_y = dy1 / scale + full_min_y
    elif folder_world_bbox is not None:
        pad_x = max(50.0, (folder_world_bbox["max_x"] - folder_world_bbox["min_x"]) * 0.05)
        pad_y = max(50.0, (folder_world_bbox["max_y"] - folder_world_bbox["min_y"]) * 0.05)
        w_min_x = folder_world_bbox["min_x"] - pad_x
        w_max_x = folder_world_bbox["max_x"] + pad_x
        w_min_y = folder_world_bbox["min_y"] - pad_y
        w_max_y = folder_world_bbox["max_y"] + pad_y
    else:
        w_min_x, w_max_x, w_min_y, w_max_y = full_min_x, full_max_x, full_min_y, full_max_y

    q = db.query(TopoFabricNode).filter(
        TopoFabricNode.world_x.isnot(None),
        TopoFabricNode.world_y.isnot(None),
        TopoFabricNode.world_x >= w_min_x,
        TopoFabricNode.world_x <= w_max_x,
        TopoFabricNode.world_y >= w_min_y,
        TopoFabricNode.world_y <= w_max_y,
    )
    if region_folder_ids is not None:
        q = q.filter(TopoFabricNode.region_folder_id.in_(list(region_folder_ids)))

    flat_cap = WORLD_FLAT_OVERVIEW_CAP if lod_norm == "overview" else WORLD_FLAT_DETAIL_CAP
    truncated = False
    reason = ""
    include_edges = lod_norm == "detail"
    # Unpadded display span of the client viewport (for close-up full-region load).
    view_span = 0.0
    if client_bbox:
        view_span = max(abs(float(max_x) - float(min_x)), abs(float(max_y) - float(min_y)))

    if sbn_id.strip():
        sid = sbn_id.strip()
        candidates = q.order_by(TopoFabricNode.id.asc()).limit(flat_cap * 5).all()
        nodes = [
            n
            for n in candidates
            if str((n.attrs or {}).get("ume_sbn_id") or "") == sid
            or str(n.region_folder_id or "") == sid
        ]
        if len(nodes) > flat_cap:
            nodes = nodes[:flat_cap]
            truncated = True
            reason = "too_many_viewport_nodes"
    else:
        total = int(q.with_entities(func.count(TopoFabricNode.id)).scalar() or 0)
        close_up = (
            lod_norm == "detail"
            and client_bbox
            and view_span > 0
            and view_span <= WORLD_FLAT_CLOSE_SPAN
        )
        if total <= flat_cap:
            nodes = q.order_by(TopoFabricNode.world_x.asc(), TopoFabricNode.world_y.asc()).all()
        elif close_up:
            # Zoomed into ~one region: load the dominant SBN in full so layout
            # matches that region's canvas (no stride peppering).
            in_view = q.order_by(TopoFabricNode.world_x.asc(), TopoFabricNode.world_y.asc()).all()
            counts: dict[str, int] = defaultdict(int)
            for n in in_view:
                counts[str((n.attrs or {}).get("ume_sbn_id") or "").strip() or "_"] += 1
            dominant = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0] if counts else ""
            if dominant and dominant != "_" and counts[dominant] >= max(3, len(in_view) // 5):
                me_uids = [
                    str(r[0])
                    for r in db.query(UmeTopoNode.ume_ne_id)
                    .filter(
                        UmeTopoNode.node_type == "TOPO_NODE_ME",
                        UmeTopoNode.parent_node == dominant,
                        UmeTopoNode.ume_ne_id.isnot(None),
                    )
                    .all()
                    if str(r[0] or "").strip()
                ]
                nodes = (
                    db.query(TopoFabricNode)
                    .filter(
                        TopoFabricNode.ume_ne_id.in_(me_uids),
                        TopoFabricNode.world_x.isnot(None),
                        TopoFabricNode.world_y.isnot(None),
                    )
                    .all()
                    if me_uids
                    else []
                )
                nodes.sort(key=lambda n: (float(n.world_x or 0), float(n.world_y or 0), n.id))
                if len(nodes) > flat_cap:
                    nodes = nodes[:flat_cap]
                    truncated = True
                    reason = "too_many_viewport_nodes"
            else:
                nodes = in_view[:flat_cap]
                truncated = len(in_view) > flat_cap
                reason = "too_many_viewport_nodes" if truncated else ""
        else:
            id_rows = (
                q.with_entities(TopoFabricNode.id)
                .order_by(TopoFabricNode.world_x.asc(), TopoFabricNode.world_y.asc())
                .all()
            )
            stride = max(1, total // flat_cap)
            pick_ids = [str(r[0]) for r in id_rows[::stride][:flat_cap]]
            nodes = (
                db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(pick_ids)).all()
                if pick_ids
                else []
            )
            order = {nid: i for i, nid in enumerate(pick_ids)}
            nodes.sort(key=lambda n: order.get(str(n.id), 0))
            truncated = True
            reason = "too_many_viewport_nodes"

    fids = [n.id for n in nodes]
    fid_set = set(fids)
    nodes_out = [
        ViewNodeOut(
            fabric_node_id=n.id,
            managed_ne_id=str(n.managed_ne_id or ""),
            ume_ne_id=str(n.ume_ne_id or ""),
            label=(n.name or n.ip or n.id)[:256],
            x=(float(n.world_x or 0) - full_min_x) * scale,
            y=(float(n.world_y or 0) - full_min_y) * scale,
            locked=False,
            name=n.name or "",
            ip=n.ip or "",
            vendor=n.vendor or "",
            device_type=n.device_type or "",
            kind="ne",
        )
        for n in nodes
    ]
    # Drag overrides in display space (world - origin). Skip legacy rows saved
    # under the old ~6000 crush — they would yank nodes to the wrong place.
    override_rows = (
        db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).limit(8).all()
    )
    display_span = max(span_x, span_y)
    overrides_ok = False
    if override_rows and display_span > 0:
        oxs = [abs(float(r.x or 0)) for r in override_rows]
        oys = [abs(float(r.y or 0)) for r in override_rows]
        peak = max(oxs + oys) if (oxs or oys) else 0.0
        legacy_crush = display_span > WORLD_FLAT_DISPLAY_SPAN * 2 and peak <= WORLD_FLAT_DISPLAY_SPAN * 1.5
        overrides_ok = (not legacy_crush) and all(
            0.0 <= float(r.x or 0) <= display_span * 1.2
            and 0.0 <= float(r.y or 0) <= display_span * 1.2
            for r in override_rows
        )
    if overrides_ok:
        nodes_out = apply_persisted_view_positions(db, view.id, nodes_out)

    edges_out: list[ViewEdgeOut] = []
    if include_edges and fids:
        eq = db.query(TopoFabricEdge).filter(
            TopoFabricEdge.layer == "physical",
            TopoFabricEdge.a_node_id.in_(fids),
            TopoFabricEdge.b_node_id.in_(fids),
        )
        st = str(status or "").strip().lower()
        if st:
            st_norm = _normalize_edge_status(st)
            if st_norm == _EDGE_STATUS_MISSING:
                eq = eq.filter(TopoFabricEdge.status.in_(list(_EDGE_STATUS_MISSING_COMPAT)))
            else:
                eq = eq.filter(TopoFabricEdge.status == st_norm)
        edges = eq.limit(VIEW_GRAPH_EDGE_HARD_CAP + 1).all()
        if len(edges) > VIEW_GRAPH_EDGE_HARD_CAP:
            edges = edges[:VIEW_GRAPH_EDGE_HARD_CAP]
            truncated = True
            reason = reason or "too_many_edges"
        for e in edges:
            if e.a_node_id not in fid_set or e.b_node_id not in fid_set:
                continue
            edges_out.append(
                ViewEdgeOut(
                    id=e.id,
                    a_node_id=e.a_node_id,
                    b_node_id=e.b_node_id,
                    a_port=e.a_port or "",
                    b_port=e.b_port or "",
                    source=e.source or "lldp",
                    status=e.status or "active",
                    layer=e.layer or "physical",
                    discovered_at=e.discovered_at,
                )
            )

    return TopologyViewGraphOut(
        view=_view_out(view),
        nodes=nodes_out,
        edges=edges_out,
        truncated=truncated,
        truncate_reason=reason,
        world_transform=_world_transform(),
        scatter=[],
    )
