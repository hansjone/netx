"""UME hierarchical level graph + flat world viewport graph."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import TopoFabricEdge, TopoFabricNode, TopoFolder, TopoView, TopoViewNode, UmeTopoNode
from .topology_common import (
    VIEW_GRAPH_EDGE_HARD_CAP,
    _EDGE_STATUS_MISSING,
    _EDGE_STATUS_MISSING_COMPAT,
    _normalize_edge_status,
)
from .topology_schemas import (
    TopologyViewGraphOut,
    ViewEdgeOut,
    ViewNodeOut,
)
from .topology_views_tree import _get_view_or_404, _view_out
from .ume_topology_world import (
    folder_bbox,
    is_ume_canvas_view,
    is_ume_level_view,
    is_world_flat_view,
    is_world_view,
)

WORLD_NODE_SOFT_CAP = 8000


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
            str(n.node_id)
            for n in db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_SBN").all()
            if str(n.node_id or "").strip()
        }
        child_sbns = [
            n
            for n in db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_SBN").all()
            if str(n.parent_node or "").strip() not in sbn_ids
        ]
        child_mes = [
            n
            for n in db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_ME").all()
            if str(n.parent_node or "").strip() not in sbn_ids
        ]
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

    # Folder / view lookup for region drill targets
    folders_by_ref: dict[str, TopoFolder] = {
        str(f.external_ref): f
        for f in db.query(TopoFolder)
        .filter(TopoFolder.external_ref.isnot(None), TopoFolder.external_ref != "")
        .all()
        if str(f.external_ref or "").strip() and not str(f.external_ref).startswith("ume:")
    }
    level_view_by_sbn: dict[str, TopoView] = {}
    for v in db.query(TopoView).all():
        vf = dict(v.filter or {})
        if vf.get("ume_level") and str(vf.get("sbn_id") or "").strip():
            level_view_by_sbn[str(vf["sbn_id"])] = v

    # Descendants of each direct child SBN (for logical edge lift + counts)
    all_sbns = {
        str(n.node_id): n
        for n in db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_SBN").all()
        if str(n.node_id or "").strip()
    }
    sbn_children: dict[str, list[str]] = defaultdict(list)
    for sid, node in all_sbns.items():
        p = str(node.parent_node or "").strip()
        if p:
            sbn_children[p].append(sid)

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

    child_sbn_ids = [str(s.node_id) for s in child_sbns if str(s.node_id or "").strip()]
    region_desc: dict[str, set[str]] = {sid: _descendants(sid) for sid in child_sbn_ids}

    # ME → which direct child region (if under a child SBN subtree)
    me_to_region: dict[str, str] = {}
    me_uid_to_topo: dict[str, UmeTopoNode] = {}
    for tn in db.query(UmeTopoNode).filter(UmeTopoNode.node_type == "TOPO_NODE_ME").all():
        uid = str(tn.ume_ne_id or tn.node_id or "").strip()
        if not uid:
            continue
        me_uid_to_topo[uid] = tn
        parent = str(tn.parent_node or "").strip()
        for rid, desc in region_desc.items():
            if parent in desc:
                me_to_region[uid] = rid
                break

    fabric_by_ume: dict[str, TopoFabricNode] = {
        str(n.ume_ne_id): n
        for n in db.query(TopoFabricNode).filter(TopoFabricNode.ume_ne_id.isnot(None)).all()
        if str(n.ume_ne_id or "").strip()
    }

    nodes_out: list[ViewNodeOut] = []
    # Region nodes
    region_node_ids: dict[str, str] = {}  # sbn_id -> fabric_node_id used in graph
    for sbn in child_sbns:
        sid = str(sbn.node_id or "").strip()
        if not sid:
            continue
        folder = folders_by_ref.get(sid)
        child_view = level_view_by_sbn.get(sid)
        # Count MEs under this region tree
        n_me = sum(1 for uid, rid in me_to_region.items() if rid == sid)
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
                view_id=child_view.id if child_view else "",
                node_count=n_me,
            )
        )

    # Direct ME nodes (local coords)
    direct_me_fids: set[str] = set()
    me_fid_by_uid: dict[str, str] = {}
    for tn in child_mes:
        uid = str(tn.ume_ne_id or tn.node_id or "").strip()
        if not uid:
            continue
        fn = fabric_by_ume.get(uid)
        if fn is None:
            continue
        direct_me_fids.add(fn.id)
        me_fid_by_uid[uid] = fn.id
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

    # Edges: physical among direct MEs; logical lifts across regions
    st = str(status or "active").strip().lower() or "active"
    st_norm = _normalize_edge_status(st)
    eq = db.query(TopoFabricEdge).filter(TopoFabricEdge.layer == "physical")
    if st_norm == _EDGE_STATUS_MISSING:
        eq = eq.filter(TopoFabricEdge.status.in_(list(_EDGE_STATUS_MISSING_COMPAT)))
    else:
        eq = eq.filter(TopoFabricEdge.status == st_norm)

    # Map fabric id → ume ne id
    fid_to_uid = {fn.id: uid for uid, fn in fabric_by_ume.items()}

    def _endpoint_on_canvas(fid: str) -> str | None:
        """Return canvas node id for a fabric endpoint, or None if outside this level."""
        if fid in direct_me_fids:
            return fid
        uid = fid_to_uid.get(fid, "")
        rid = me_to_region.get(uid, "")
        if rid and rid in region_node_ids:
            return region_node_ids[rid]
        return None

    physical: list[ViewEdgeOut] = []
    logical_count: dict[tuple[str, str], int] = defaultdict(int)
    for e in eq.limit(VIEW_GRAPH_EDGE_HARD_CAP * 20).all():
        a = _endpoint_on_canvas(e.a_node_id)
        b = _endpoint_on_canvas(e.b_node_id)
        if not a or not b or a == b:
            continue
        # Both direct MEs → physical
        if a in direct_me_fids and b in direct_me_fids:
            physical.append(
                ViewEdgeOut(
                    id=e.id,
                    a_node_id=a,
                    b_node_id=b,
                    a_port=e.a_port or "",
                    b_port=e.b_port or "",
                    source=e.source or "lldp",
                    status=e.status or "active",
                    layer=e.layer or "physical",
                    discovered_at=e.discovered_at,
                )
            )
        else:
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

    # Manual nested regions (新建子区域) on this UME canvas.
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
    status: str = "active",
) -> TopologyViewGraphOut:
    """All NEs using composed world_x/y — no region nodes."""
    region_folder_ids: set[str] | None = None
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
        if bbox and min_x is None:
            pad_x = max(50.0, (bbox["max_x"] - bbox["min_x"]) * 0.05)
            pad_y = max(50.0, (bbox["max_y"] - bbox["min_y"]) * 0.05)
            min_x = float(bbox["min_x"]) - pad_x
            max_x = float(bbox["max_x"]) + pad_x
            min_y = float(bbox["min_y"]) - pad_y
            max_y = float(bbox["max_y"]) + pad_y

    extent = (
        db.query(TopoFabricNode)
        .filter(TopoFabricNode.world_x.isnot(None), TopoFabricNode.world_y.isnot(None))
        .all()
    )
    if not extent:
        return TopologyViewGraphOut(view=_view_out(view), nodes=[], edges=[])

    full_min_x = min(float(n.world_x) for n in extent)
    full_max_x = max(float(n.world_x) for n in extent)
    full_min_y = min(float(n.world_y) for n in extent)
    full_max_y = max(float(n.world_y) for n in extent)

    if min_x is None or max_x is None or min_y is None or max_y is None:
        min_x, max_x, min_y, max_y = full_min_x, full_max_x, full_min_y, full_max_y

    q = db.query(TopoFabricNode).filter(
        TopoFabricNode.world_x.isnot(None),
        TopoFabricNode.world_y.isnot(None),
        TopoFabricNode.world_x >= min_x,
        TopoFabricNode.world_x <= max_x,
        TopoFabricNode.world_y >= min_y,
        TopoFabricNode.world_y <= max_y,
    )
    if region_folder_ids is not None:
        q = q.filter(TopoFabricNode.region_folder_id.in_(list(region_folder_ids)))
    if sbn_id.strip():
        sid = sbn_id.strip()
        nodes = [
            n
            for n in q.all()
            if str((n.attrs or {}).get("ume_sbn_id") or "") == sid
            or str(n.region_folder_id or "") == sid
        ]
    else:
        nodes = q.all()

    truncated = False
    reason = ""
    if len(nodes) > WORLD_NODE_SOFT_CAP:
        nodes = nodes[:WORLD_NODE_SOFT_CAP]
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
            x=float(n.world_x or 0),
            y=float(n.world_y or 0),
            locked=False,
            name=n.name or "",
            ip=n.ip or "",
            vendor=n.vendor or "",
            device_type=n.device_type or "",
            kind="ne",
        )
        for n in nodes
    ]
    nodes_out = apply_persisted_view_positions(db, view.id, nodes_out)

    edges_out: list[ViewEdgeOut] = []
    if fids:
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
    )
