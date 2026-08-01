"""Topology HTTP routes — fabric + views (final model)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .db import get_db
from .topology_schemas import (
    FabricDiscoverRequest,
    FabricManualEdgeIn,
    TopologyViewCreate,
    TopologyViewUpdate,
    ViewEdgeStylePatch,
    ViewNodesAdd,
    ViewPositionsPatch,
)
from .topology_service import (
    add_nodes_to_view,
    create_view,
    delete_view,
    get_discover_job,
    get_fabric_neighborhood,
    get_fabric_summary,
    get_view_graph,
    list_fabric_edges,
    list_fabric_nodes,
    list_views,
    merge_duplicate_fabric_nodes,
    patch_view_edge_style,
    patch_view_positions,
    project_fabric_neighbors_to_view,
    refresh_fabric_stats,
    remove_view_nodes,
    start_discover_job,
    update_view,
    upsert_fabric_edge,
)

router = APIRouter(prefix="/v1/topology", tags=["topology"])


# --- Fabric -----------------------------------------------------------------


@router.get("/fabric/summary")
def api_fabric_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_fabric_summary(db).model_dump()


@router.get("/fabric/nodes")
def api_fabric_nodes(
    keyword: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_fabric_nodes(db, keyword=keyword, page=page, page_size=page_size)


@router.get("/fabric/edges")
def api_fabric_edges(
    node_id: str = "",
    layer: str = "physical",
    status: str = "",
    source: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_fabric_edges(
        db,
        node_id=node_id,
        layer=layer,
        status=status,
        source=source,
        page=page,
        page_size=page_size,
    )


@router.get("/fabric/neighborhood")
def api_fabric_neighborhood(
    node_id: str,
    depth: int = Query(default=1, ge=1, le=3),
    layer: str = "physical",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return get_fabric_neighborhood(db, node_id, depth=depth, layer=layer).model_dump()


@router.post("/fabric/edges")
def api_fabric_manual_edge(
    body: FabricManualEdgeIn,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    edge, action = upsert_fabric_edge(
        db,
        a_node_id=body.a_node_id,
        b_node_id=body.b_node_id,
        a_port=body.a_port,
        b_port=body.b_port,
        source="manual",
    )
    db.commit()
    refresh_fabric_stats(db)
    return {"ok": True, "action": action, "edge": {
        "id": edge.id,
        "a_node_id": edge.a_node_id,
        "b_node_id": edge.b_node_id,
        "a_port": edge.a_port,
        "b_port": edge.b_port,
        "source": edge.source,
        "status": edge.status,
    }}


@router.post("/fabric/discover")
def api_fabric_discover(
    body: FabricDiscoverRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return start_discover_job(db, body or FabricDiscoverRequest()).model_dump()


@router.post("/fabric/cleanup-duplicates")
def api_fabric_cleanup_duplicates(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Merge duplicate fabric nodes (same managed/ume/name/ip) and retarget edges."""
    result = merge_duplicate_fabric_nodes(db)
    return {"ok": True, **result}


@router.get("/fabric/discover/{job_id}")
def api_fabric_discover_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_discover_job(db, job_id).model_dump()


# --- Views ------------------------------------------------------------------


@router.get("/views")
def api_list_views(db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_views(db)


@router.post("/views")
def api_create_view(body: TopologyViewCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return create_view(db, body).model_dump()


@router.get("/views/{view_id}")
def api_get_view(view_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_view_graph(db, view_id).model_dump()


@router.patch("/views/{view_id}")
def api_patch_view(
    view_id: str, body: TopologyViewUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return update_view(db, view_id, body).model_dump()


@router.delete("/views/{view_id}")
def api_delete_view(view_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return delete_view(db, view_id)


@router.patch("/views/{view_id}/positions")
def api_patch_positions(
    view_id: str, body: ViewPositionsPatch, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return patch_view_positions(db, view_id, body).model_dump()


@router.post("/views/{view_id}/nodes")
def api_add_nodes(
    view_id: str, body: ViewNodesAdd, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return add_nodes_to_view(db, view_id, body).model_dump()


@router.post("/views/{view_id}/project-neighbors")
def api_project_neighbors(view_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return project_fabric_neighbors_to_view(db, view_id).model_dump()


@router.post("/views/{view_id}/nodes/remove")
def api_remove_nodes(
    view_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ids = body.get("fabric_node_ids") if isinstance(body, dict) else None
    return remove_view_nodes(db, view_id, list(ids or [])).model_dump()


@router.patch("/views/{view_id}/edge-style")
def api_edge_style(
    view_id: str, body: ViewEdgeStylePatch, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return patch_view_edge_style(db, view_id, body).model_dump()
