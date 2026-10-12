"""Atomic view canvas commit (single transaction)."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    TopoFabricEdge,
    TopoFabricNode,
    TopoViewEdgeStyle,
    TopoViewNode,
)
from .topology_common import VIEW_GRAPH_NODE_HARD_CAP, _utcnow
from .topology_fabric_links import upsert_fabric_edge
from .topology_fabric_nodes import _normalize_endpoints, refresh_fabric_stats
from .topology_region_canvas import add_missing_region_placement, is_region_canvas_node_id
from .topology_schemas import (
    TopologyViewGraphOut,
    ViewCommitIn,
    ViewCommitOut,
    ViewEdgeStyleItem,
    ViewNodeIn,
)
from .topology_views_graph import (
    _get_view_or_404,
    _membership_for_view,
    get_view_graph,
)
from .ume_topology_world import is_world_flat_view

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_LINE_STYLES = {"", "solid", "dashed", "dotted"}


def _fail(step: str, detail: str, *, status: int = 400) -> None:
    raise HTTPException(
        status_code=status,
        detail={"failed_step": step, "detail": detail},
    )


def _apply_edge_creates(
    db: Session, creates: list[Any], edge_id_map: dict[str, str]
) -> None:
    for item in creates:
        a = str(getattr(item, "a_node_id", "") or "").strip()
        b = str(getattr(item, "b_node_id", "") or "").strip()
        if not a or not b:
            _fail("edge_creates", "edge_endpoints_required")
        edge, action = upsert_fabric_edge(
            db,
            a_node_id=a,
            b_node_id=b,
            a_port=str(getattr(item, "a_port", "") or ""),
            b_port=str(getattr(item, "b_port", "") or ""),
            source="manual",
        )
        if edge is None or action == "skipped_self_loop":
            _fail("edge_creates", "edge_self_loop")
        client_id = str(getattr(item, "client_id", "") or "").strip()
        if client_id:
            edge_id_map[client_id] = str(edge.id)


def _apply_edge_deletes(db: Session, edge_ids: list[str]) -> set[str]:
    """Delete edges; return the ids that were requested (for skipping later edits)."""
    ids = [str(x).strip() for x in (edge_ids or []) if str(x).strip()]
    if not ids:
        return set()
    seen: set[str] = set()
    ordered: list[str] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        ordered.append(i)
    rows = db.query(TopoFabricEdge).filter(TopoFabricEdge.id.in_(ordered)).all()
    found_ids = [str(r.id) for r in rows]
    if not found_ids:
        return seen
    db.query(TopoViewEdgeStyle).filter(
        TopoViewEdgeStyle.fabric_edge_id.in_(found_ids)
    ).delete(synchronize_session=False)
    db.query(TopoFabricEdge).filter(TopoFabricEdge.id.in_(found_ids)).delete(
        synchronize_session=False
    )
    return seen


def patch_manual_edge_ports(
    db: Session,
    edge_id: str,
    *,
    a_port: str,
    b_port: str,
    commit: bool = True,
) -> TopoFabricEdge:
    """Update ports on a manual fabric edge. Raises 400/404/409."""
    eid = str(edge_id or "").strip()
    row = db.get(TopoFabricEdge, eid) if eid else None
    if row is None:
        raise HTTPException(status_code=404, detail="fabric_edge_not_found")
    if str(row.source or "").strip().lower() != "manual":
        raise HTTPException(status_code=400, detail="edge_not_manual")
    _a, _b, ap, bp = _normalize_endpoints(
        row.a_node_id,
        row.b_node_id,
        str(a_port or ""),
        str(b_port or ""),
    )
    conflict = (
        db.query(TopoFabricEdge)
        .filter(
            TopoFabricEdge.id != row.id,
            TopoFabricEdge.layer == row.layer,
            TopoFabricEdge.a_node_id == row.a_node_id,
            TopoFabricEdge.b_node_id == row.b_node_id,
            TopoFabricEdge.a_port == ap,
            TopoFabricEdge.b_port == bp,
        )
        .one_or_none()
    )
    if conflict is not None:
        raise HTTPException(status_code=409, detail="edge_port_conflict")
    row.a_port = ap
    row.b_port = bp
    row.updated_at = _utcnow()
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="edge_port_conflict") from exc
    if commit:
        db.commit()
        db.refresh(row)
    return row


def _apply_edge_port_edits(db: Session, edits: list[Any], *, deleted: set[str]) -> None:
    for item in edits:
        eid = str(getattr(item, "id", "") or "").strip()
        if not eid:
            _fail("edge_port_edits", "edge_id_required")
        if eid in deleted:
            continue
        try:
            patch_manual_edge_ports(
                db,
                eid,
                a_port=str(getattr(item, "a_port", "") or ""),
                b_port=str(getattr(item, "b_port", "") or ""),
                commit=False,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "edge_port_edit_failed"
            _fail("edge_port_edits", str(detail), status=int(exc.status_code or 400))


def _upsert_edge_style(
    db: Session,
    view_id: str,
    item: ViewEdgeStyleItem,
    *,
    now,
) -> None:
    eid = str(item.fabric_edge_id or "").strip()
    if not eid or db.get(TopoFabricEdge, eid) is None:
        _fail("edge_styles", "fabric_edge_not_found", status=404)
    color = str(item.stroke_color or "").strip()
    if color and not _HEX_COLOR_RE.match(color):
        _fail("edge_styles", "invalid_stroke_color")
    width = int(item.stroke_width or 0)
    if width < 0 or width > 12:
        _fail("edge_styles", "invalid_stroke_width")
    style = str(item.line_style or "").strip().lower()
    if style not in _LINE_STYLES:
        _fail("edge_styles", "invalid_line_style")
    row = (
        db.query(TopoViewEdgeStyle)
        .filter(
            TopoViewEdgeStyle.view_id == view_id,
            TopoViewEdgeStyle.fabric_edge_id == eid,
        )
        .one_or_none()
    )
    if row is None:
        db.add(
            TopoViewEdgeStyle(
                id=uuid4().hex,
                view_id=view_id,
                fabric_edge_id=eid,
                stroke_color=color,
                stroke_width=width,
                line_style=style,
                created_at=now,
                updated_at=now,
            )
        )
    else:
        row.stroke_color = color
        row.stroke_width = width
        row.line_style = style
        row.updated_at = now


def _apply_edge_styles(
    db: Session,
    view_id: str,
    styles: list[ViewEdgeStyleItem],
    *,
    edge_id_map: dict[str, str],
    deleted: set[str],
) -> None:
    now = _utcnow()
    for item in styles:
        eid = str(item.fabric_edge_id or "").strip()
        if eid in deleted:
            continue
        # Styles on a just-created local edge are keyed by its client id.
        if eid in edge_id_map:
            item = item.model_copy(update={"fabric_edge_id": edge_id_map[eid]})
        _upsert_edge_style(db, view_id, item, now=now)


def _apply_node_adds(
    db: Session,
    view,
    fabric_ids: list[str],
    *,
    max_nodes: int,
    existing: dict[str, TopoViewNode],
) -> None:
    if is_world_flat_view(view):
        _fail("node_adds", "world_map_no_direct_nes")
    ids = [str(x).strip() for x in (fabric_ids or []) if str(x).strip()]
    if not ids:
        return
    if len(existing) >= max_nodes:
        _fail("node_adds", "membership_max_nodes")
    now = _utcnow()
    room = max(0, max_nodes - len(existing))
    added = 0
    for fid in ids:
        if fid in existing:
            continue
        if is_region_canvas_node_id(fid):
            continue
        if db.get(TopoFabricNode, fid) is None:
            _fail("node_adds", f"fabric_node_not_found:{fid}")
        if added >= room:
            _fail("node_adds", "membership_max_nodes")
        row = TopoViewNode(
            id=uuid4().hex,
            view_id=view.id,
            fabric_node_id=fid,
            x=40.0,
            y=40.0,
            label="",
            locked=False,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        existing[fid] = row
        added += 1
    if added:
        view.updated_at = now


def _apply_node_removes(
    db: Session,
    view,
    fabric_ids: list[str],
    *,
    existing: dict[str, TopoViewNode],
) -> None:
    ids = [str(x).strip() for x in (fabric_ids or []) if str(x).strip()]
    ids = [x for x in ids if x and not is_region_canvas_node_id(x)]
    if not ids:
        return
    db.query(TopoViewNode).filter(
        TopoViewNode.view_id == view.id,
        TopoViewNode.fabric_node_id.in_(ids),
    ).delete(synchronize_session=False)
    for fid in ids:
        existing.pop(fid, None)
    view.updated_at = _utcnow()


def _apply_positions(
    db: Session,
    view,
    positions: list[ViewNodeIn],
    *,
    existing: dict[str, TopoViewNode],
) -> int:
    """Apply positions in-session. Returns skipped_locked count."""
    if len(positions) > VIEW_GRAPH_NODE_HARD_CAP:
        _fail("positions", "too_many_positions")
    now = _utcnow()
    skipped_locked = 0
    updated = 0
    fabric_ids = [
        str(p.fabric_node_id or "").strip()
        for p in positions
        if str(p.fabric_node_id or "").strip()
        and not is_region_canvas_node_id(str(p.fabric_node_id or "").strip())
    ]
    alive = (
        {str(r[0]) for r in db.query(TopoFabricNode.id).filter(TopoFabricNode.id.in_(fabric_ids))}
        if fabric_ids
        else set()
    )
    for p in positions:
        fid = str(p.fabric_node_id or "").strip()
        if not fid:
            continue
        if not is_region_canvas_node_id(fid) and fid not in alive and fid not in existing:
            # Missing fabric: skip rather than fail mid-commit when node was just removed.
            continue
        row = existing.get(fid)
        if row is None:
            # Fabric NEs are placed via node_adds; only synthesized child-region icons are created here.
            row = add_missing_region_placement(db, view, fid, x=p.x, y=p.y)
            if row is None:
                continue
            existing[fid] = row
        if row.locked and p.locked is not False:
            skipped_locked += 1
            continue
        row.x = float(p.x or 0)
        row.y = float(p.y or 0)
        if p.label is not None:
            row.label = str(p.label or "")[:256]
        if p.locked is not None:
            row.locked = bool(p.locked)
        row.updated_at = now
        updated += 1
    if updated or skipped_locked:
        view.updated_at = now
    return skipped_locked


def commit_view_changes(db: Session, view_id: str, body: ViewCommitIn) -> ViewCommitOut:
    """Apply canvas mutations atomically; return one graph + client→server edge map."""
    view = _get_view_or_404(db, view_id)
    mem = _membership_for_view(view)
    max_nodes = int(mem.get("max_nodes") or 300)
    existing = {
        vn.fabric_node_id: vn
        for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
    }
    edge_id_map: dict[str, str] = {}
    skipped_locked = 0

    try:
        _apply_edge_creates(db, list(body.edge_creates or []), edge_id_map)
        deleted = _apply_edge_deletes(db, list(body.edge_deletes or []))
        _apply_edge_port_edits(db, list(body.edge_port_edits or []), deleted=deleted)
        _apply_edge_styles(
            db,
            view.id,
            list(body.edge_styles or []),
            edge_id_map=edge_id_map,
            deleted=deleted,
        )
        _apply_node_adds(
            db,
            view,
            list(body.node_adds or []),
            max_nodes=max_nodes,
            existing=existing,
        )
        _apply_node_removes(
            db,
            view,
            list(body.node_removes or []),
            existing=existing,
        )
        # Re-load placements after add/remove so positions see current rows.
        # Flush first: sessions run with autoflush=False, so new placements would be invisible.
        db.flush()
        existing = {
            vn.fabric_node_id: vn
            for vn in db.query(TopoViewNode).filter(TopoViewNode.view_id == view.id).all()
        }
        skipped_locked = _apply_positions(
            db,
            view,
            list(body.positions or []),
            existing=existing,
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _fail("commit", str(exc)[:500], status=500)

    try:
        refresh_fabric_stats(db)
    except Exception:  # noqa: BLE001
        pass

    graph: TopologyViewGraphOut = get_view_graph(db, view.id)
    return ViewCommitOut(
        ok=True,
        view_id=view.id,
        edge_id_map=edge_id_map,
        skipped_locked=skipped_locked,
        graph=graph,
    )


def normalize_edge_style_items(body: Any) -> list[ViewEdgeStyleItem]:
    """Accept legacy single-edge body or batch ``items``."""
    items = list(getattr(body, "items", None) or [])
    if items:
        return [
            ViewEdgeStyleItem(
                fabric_edge_id=str(i.fabric_edge_id or ""),
                stroke_color=str(i.stroke_color or ""),
                stroke_width=int(i.stroke_width or 0),
                line_style=str(i.line_style or ""),
            )
            for i in items
        ]
    fid = str(getattr(body, "fabric_edge_id", "") or "").strip()
    if not fid:
        return []
    return [
        ViewEdgeStyleItem(
            fabric_edge_id=fid,
            stroke_color=str(getattr(body, "stroke_color", "") or ""),
            stroke_width=int(getattr(body, "stroke_width", 0) or 0),
            line_style=str(getattr(body, "line_style", "") or ""),
        )
    ]
