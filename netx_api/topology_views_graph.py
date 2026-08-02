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


def get_view_graph(db: Session, view_id: str) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    truncated = False
    reason = ""
    if len(vnodes) > VIEW_GRAPH_NODE_HARD_CAP:
        vnodes = vnodes[:VIEW_GRAPH_NODE_HARD_CAP]
        truncated = True
        reason = "too_many_view_nodes"
    fids = [vn.fabric_node_id for vn in vnodes]
    fabric_nodes = {
        n.id: n for n in db.query(TopoFabricNode).filter(TopoFabricNode.id.in_(fids)).all()
    } if fids else {}
    filt = dict(view.filter or {})
    layer = str(filt.get("layer") or "physical").strip() or "physical"
    status = str(filt.get("status") or "").strip().lower()
    nodes_out: list[ViewNodeOut] = []
    for vn in vnodes:
        fn = fabric_nodes.get(vn.fabric_node_id)
        label = (vn.label or "").strip()
        if not label and fn is not None:
            label = (fn.name or fn.ip or vn.fabric_node_id)[:256]
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
                connect_status=_connect_status_for_node(db, fn) if fn else "",
            )
        )
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
) -> int:
    now = _utcnow()
    added = 0
    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    max_x = max((float(vn.x or 0) for vn in vnodes), default=40.0)
    base_x = max_x + 200.0
    cols = max(1, int(len(fabric_ids) ** 0.5) or 1)
    for i, fid in enumerate(fabric_ids):
        if fid in existing or db.get(TopoFabricNode, fid) is None:
            continue
        x = base_x + (i % cols) * 180.0
        y = 40.0 + (i // cols) * 120.0
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


def patch_view_positions(
    db: Session, view_id: str, body: ViewPositionsPatch
) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    now = _utcnow()
    positions = list(body.positions or [])
    if len(positions) > VIEW_GRAPH_NODE_HARD_CAP:
        raise HTTPException(status_code=400, detail="too_many_positions")
    existing = {
        vn.fabric_node_id: vn
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    for p in positions:
        fid = str(p.fabric_node_id or "").strip()
        if not fid:
            continue
        if db.get(TopoFabricNode, fid) is None:
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
        else:
            if row.locked and not p.locked:
                # allow unlock + move when explicitly unlocked in patch
                pass
            if row.locked and bool(p.locked):
                continue
            row.x = float(p.x or 0)
            row.y = float(p.y or 0)
            if p.label is not None:
                row.label = str(p.label or "")[:256]
            row.locked = bool(p.locked)
            row.updated_at = now
    view.updated_at = now
    db.commit()
    return get_view_graph(db, view.id)


def add_nodes_to_view(db: Session, view_id: str, body: ViewNodesAdd) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    mem = _membership_for_view(view)
    max_nodes = int(mem.get("max_nodes") or 300)
    now = _utcnow()
    existing = {
        vn.fabric_node_id
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    if len(existing) >= max_nodes:
        raise HTTPException(status_code=400, detail="membership_max_nodes")
    added_ids: list[str] = []
    for mid in body.managed_ne_ids or []:
        mid_s = str(mid or "").strip()
        if not mid_s:
            continue
        ne = db.get(ManagedNE, mid_s)
        if ne is None:
            continue
        fn = ensure_fabric_node_for_managed(db, ne)
        if fn.id not in existing:
            added_ids.append(fn.id)
            existing.add(fn.id)
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
        if fn.id not in existing:
            added_ids.append(fn.id)
            existing.add(fn.id)
    for fid in body.fabric_node_ids or []:
        fid_s = str(fid or "").strip()
        if not fid_s or fid_s in existing:
            continue
        if db.get(TopoFabricNode, fid_s) is None:
            continue
        added_ids.append(fid_s)
        existing.add(fid_s)
    # `existing` already includes ids in added_ids; cap new placements.
    original_count = len(existing) - len(added_ids)
    room = max(0, max_nodes - original_count)
    if len(added_ids) > room:
        added_ids = added_ids[:room]
    cols = max(1, int(len(added_ids) ** 0.5) or 1)
    for i, fid in enumerate(added_ids):
        x = (i % cols) * 180.0 + 40.0
        y = (i // cols) * 120.0 + 40.0
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


def project_fabric_neighbors_to_view(db: Session, view_id: str) -> TopologyViewGraphOut:
    """Add in-scope fabric neighbors onto the leaf view (bounded by membership)."""
    merge_duplicate_fabric_nodes(db)
    view = _get_view_or_404(db, view_id)
    mem = _membership_for_view(view)
    if bool(mem.get("frozen")):
        return get_view_graph(db, view.id)

    max_nodes = int(mem.get("max_nodes") or 300)
    hops = int(mem.get("expand_hops") or 1)
    filt = dict(view.filter or {})
    layer = str(filt.get("layer") or "physical").strip() or "physical"

    vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    # Drop placements pointing at missing fabric rows only (keep LLDP placeholders).
    orphan_vns = [vn for vn in vnodes if db.get(TopoFabricNode, vn.fabric_node_id) is None]
    if orphan_vns:
        for vn in orphan_vns:
            db.delete(vn)
        view.updated_at = _utcnow()
        db.commit()
        vnodes = db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()

    existing = {vn.fabric_node_id for vn in vnodes}
    if not existing:
        return get_view_graph(db, view.id)
    if len(existing) >= max_nodes:
        g = get_view_graph(db, view.id)
        g.truncated = True
        g.truncate_reason = g.truncate_reason or "membership_cap"
        return g

    peer_ids = _neighbor_ids(db, seed_ids=existing, layer=layer, hops=hops)
    to_add: list[str] = []
    for peer in sorted(peer_ids):
        if peer in existing:
            continue
        fn = db.get(TopoFabricNode, peer)
        if fn is None or not _is_inventory_node(fn):
            continue
        if _fabric_match_score(db, fn) < 2:
            continue
        if not _fabric_in_hard_scope(db, fn, mem):
            continue
        to_add.append(peer)
        if len(existing) + len(to_add) >= max_nodes:
            break

    truncated = len(peer_ids) > len(to_add)
    if to_add:
        _place_fabric_ids_on_view(db, view, to_add, existing=existing)
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


def remove_view_nodes(db: Session, view_id: str, fabric_node_ids: list[str]) -> TopologyViewGraphOut:
    view = _get_view_or_404(db, view_id)
    ids = [str(x).strip() for x in (fabric_node_ids or []) if str(x).strip()]
    if ids:
        db.query(TopoViewNode).filter(
            TopoViewNode.view_id == view.id, TopoViewNode.fabric_node_id.in_(ids)
        ).delete(synchronize_session=False)
        view.updated_at = _utcnow()
        db.commit()
    return get_view_graph(db, view.id)


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


