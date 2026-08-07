"""Topology HTTP routes — fabric + folder tree + leaf views."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .db import get_db
from .topology_classify import (
    apply_classify,
    bulk_tag_fabric_nodes,
    create_rule,
    delete_rule,
    generate_slices,
    list_rules,
    list_unmatched,
    match_fabric_nodes,
    patch_fabric_node_tags,
    preview_classify,
    search_fabric_nodes_with_views,
    update_rule,
)
from .topology_schemas import (
    ClassifyRuleCreate,
    ClassifyRuleUpdate,
    FabricDiscoverRequest,
    FabricEdgesDeleteRequest,
    FabricManualEdgeIn,
    FabricNodesBulkTagRequest,
    FabricNodesDeleteRequest,
    FabricNodesMatchRequest,
    FabricNodeTagPatch,
    SliceGenerateRequest,
    TopologyFolderCreate,
    TopologyFolderUpdate,
    TopologyPlaceholderCreate,
    TopologyViewCreate,
    TopologyViewUpdate,
    ViewEdgeStylePatch,
    ViewNodesAdd,
    ViewNodesRemove,
    ViewPopulateRequest,
    ViewPositionsPatch,
    ViewProjectNeighborsRequest,
)
from .topology_discover import get_discover_job, start_discover_job
from .topology_fabric import (
    delete_fabric_edge,
    delete_fabric_edges,
    get_fabric_neighborhood,
    get_fabric_summary,
    list_fabric_edges,
    list_fabric_nodes,
    merge_duplicate_fabric_nodes,
    refresh_fabric_stats,
    upsert_fabric_edge,
)
from .topology_views import (
    add_nodes_to_view,
    bootstrap_topology_tree,
    create_folder,
    create_topology_placeholder_on_view,
    create_view,
    delete_folder,
    delete_view,
    get_topology_tree,
    get_view_graph,
    list_views,
    patch_view_edge_style,
    patch_view_positions,
    populate_view,
    project_fabric_neighbors_to_view,
    remove_view_nodes,
    update_folder,
    update_view,
)

router = APIRouter(prefix="/v1/topology", tags=["topology"])


# --- Fabric -----------------------------------------------------------------


@router.get("/fabric/summary")
def api_fabric_summary(db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_fabric_summary(db).model_dump()


@router.get("/fabric/nodes")
def api_fabric_nodes(
    keyword: str = "",
    role: str = "",
    region_folder_id: str = "",
    unmatched: str = Query(default="", description="any | role | region"),
    link_status: str = Query(
        default="",
        description="linked | orphaned | managed | ume | both",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_fabric_nodes(
        db,
        keyword=keyword,
        role=role,
        region_folder_id=region_folder_id,
        unmatched=unmatched,
        link_status=link_status,
        page=page,
        page_size=page_size,
    )


@router.get("/fabric/edges")
def api_fabric_edges(
    node_id: str = "",
    layer: str = "physical",
    status: str = "",
    source: str = "",
    keyword: str = "",
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
        keyword=keyword,
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
    if edge is None or action == "skipped_self_loop":
        raise HTTPException(status_code=400, detail="edge_self_loop")
    db.commit()
    refresh_fabric_stats(db)
    return {
        "ok": True,
        "action": action,
        "edge": {
            "id": edge.id,
            "a_node_id": edge.a_node_id,
            "b_node_id": edge.b_node_id,
            "a_port": edge.a_port,
            "b_port": edge.b_port,
            "source": edge.source,
            "status": edge.status,
        },
    }


@router.delete("/fabric/edges/{edge_id}")
def api_fabric_delete_edge(edge_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = delete_fabric_edge(db, edge_id)
    return {"ok": True, **out}


@router.post("/fabric/edges/delete")
def api_fabric_delete_edges(
    body: FabricEdgesDeleteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    out = delete_fabric_edges(db, body.edge_ids)
    return {"ok": True, **out}


@router.post("/fabric/discover")
def api_fabric_discover(
    body: FabricDiscoverRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return start_discover_job(db, body or FabricDiscoverRequest()).model_dump()


@router.post("/fabric/cleanup-duplicates")
def api_fabric_cleanup_duplicates(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Merge duplicate fabric nodes (same managed/ume/name/ip) and retarget edges."""
    from .topology_inventory_lifecycle import (
        purge_fully_orphaned_fabric_nodes,
        reconcile_dangling_fabric_links,
    )

    # Detach dangling first but defer full orphan GC until after absorb/merge.
    link_stats = reconcile_dangling_fabric_links(db, sweep_orphans=False)
    result = merge_duplicate_fabric_nodes(db)
    gc = purge_fully_orphaned_fabric_nodes(db, None)
    db.commit()
    return {
        "ok": True,
        **result,
        "reconcile_links": link_stats,
        "purged_orphans": gc,
    }


@router.post("/fabric/reconcile-links")
def api_fabric_reconcile_links(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Detach fabric refs whose managed/UME inventory rows no longer exist; GC full orphans."""
    from .topology_inventory_lifecycle import reconcile_dangling_fabric_links

    stats = reconcile_dangling_fabric_links(db, sweep_orphans=True)
    db.commit()
    return {"ok": True, **stats}


@router.get("/fabric/discover/{job_id}")
def api_fabric_discover_job(
    job_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return get_discover_job(db, job_id, page=page, page_size=page_size).model_dump()


# --- Tree / folders ---------------------------------------------------------


@router.get("/tree")
def api_topology_tree(db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return get_topology_tree(db).model_dump(mode="json")
    except Exception as exc:
        import logging

        logging.getLogger("netx.topology").exception("GET /v1/topology/tree failed")
        raise HTTPException(
            status_code=500,
            detail=f"topology_tree_failed:{type(exc).__name__}:{exc}",
        ) from exc


@router.post("/folders")
def api_create_folder(body: TopologyFolderCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return create_folder(db, body).model_dump(mode="json")
    except HTTPException:
        raise
    except Exception as exc:
        import logging

        logging.getLogger("netx.topology").exception("POST /v1/topology/folders failed")
        raise HTTPException(
            status_code=500,
            detail=f"topology_folder_create_failed:{type(exc).__name__}:{exc}",
        ) from exc


@router.patch("/folders/{folder_id}")
def api_patch_folder(
    folder_id: str, body: TopologyFolderUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return update_folder(db, folder_id, body).model_dump()


@router.delete("/folders/{folder_id}")
def api_delete_folder(
    folder_id: str,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return delete_folder(db, folder_id, force=force)


@router.post("/tree/bootstrap")
def api_bootstrap_tree(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"ok": True, **bootstrap_topology_tree(db)}


# --- Views (leaf canvases) --------------------------------------------------


@router.get("/views")
def api_list_views(db: Session = Depends(get_db)) -> dict[str, Any]:
    return list_views(db)


@router.post("/views")
def api_create_view(body: TopologyViewCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    return create_view(db, body).model_dump()


@router.get("/views/{view_id}")
def api_get_view(
    view_id: str,
    min_x: float | None = Query(default=None),
    max_x: float | None = Query(default=None),
    min_y: float | None = Query(default=None),
    max_y: float | None = Query(default=None),
    sbn_id: str = Query(default=""),
    folder_id: str = Query(default=""),
    lod: str = Query(default="auto", description="auto|sbn|me"),
    status: str = Query(default="active"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from .topology_views_tree import _get_view_or_404
    from .ume_topology_world import is_ume_canvas_view
    from .ume_topology_world_graph import get_ume_canvas_graph

    row = _get_view_or_404(db, view_id)
    if is_ume_canvas_view(row):
        return get_ume_canvas_graph(
            db,
            view_id,
            min_x=min_x,
            max_x=max_x,
            min_y=min_y,
            max_y=max_y,
            sbn_id=sbn_id,
            folder_id=folder_id,
            lod=lod,
            status=status,
        ).model_dump()
    return get_view_graph(db, view_id).model_dump()


@router.get("/folders/{folder_id}/bbox")
def api_folder_bbox(folder_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    from .ume_topology_world import folder_bbox

    box = folder_bbox(db, folder_id)
    if box is None:
        raise HTTPException(status_code=404, detail="bbox_unavailable")
    return {"folder_id": folder_id, **box}


@router.get("/world")
def api_get_world(db: Session = Depends(get_db)) -> dict[str, Any]:
    from .ume_topology_world import ensure_ume_world_and_sbn_folders, get_world_view

    ensure_ume_world_and_sbn_folders(db)
    view = get_world_view(db)
    if view is None:
        raise HTTPException(status_code=404, detail="world_view_missing")
    return {"view_id": view.id, "folder_id": view.folder_id or "", "name": view.name}


@router.post("/world/apply-ume")
def api_apply_ume_world(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Manual: dock tables → fabric coords/edges + SBN folders (does not pull UME)."""
    from .ume_topology_apply import apply_ume_topology_to_fabric
    from .ume_topology_world import ensure_ume_world_and_sbn_folders

    apply_stats = apply_ume_topology_to_fabric(db)
    world_stats = ensure_ume_world_and_sbn_folders(db)
    return {"ok": True, "fabric_apply": apply_stats, "world": world_stats}


@router.patch("/views/{view_id}")
def api_patch_view(
    view_id: str, body: TopologyViewUpdate, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return update_view(db, view_id, body).model_dump()


@router.delete("/views/{view_id}")
def api_delete_view(
    view_id: str,
    force: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return delete_view(db, view_id, force=force)


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


@router.post("/views/{view_id}/nodes/create-placeholder")
def api_create_topology_placeholder(
    view_id: str,
    body: TopologyPlaceholderCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return create_topology_placeholder_on_view(db, view_id, body).model_dump()


@router.post("/views/{view_id}/project-neighbors")
def api_project_neighbors(
    view_id: str,
    body: ViewProjectNeighborsRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return project_fabric_neighbors_to_view(
        db, view_id, body or ViewProjectNeighborsRequest()
    ).model_dump()


@router.post("/views/{view_id}/populate")
def api_populate_view(
    view_id: str,
    body: ViewPopulateRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return populate_view(db, view_id, body or ViewPopulateRequest()).model_dump()


@router.post("/views/{view_id}/nodes/remove")
def api_remove_nodes(
    view_id: str,
    body: ViewNodesRemove,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return remove_view_nodes(db, view_id, body=body).model_dump()


@router.patch("/views/{view_id}/edge-style")
def api_edge_style(
    view_id: str, body: ViewEdgeStylePatch, db: Session = Depends(get_db)
) -> dict[str, Any]:
    return patch_view_edge_style(db, view_id, body).model_dump()


# --- Classify rules / slices / search ---------------------------------------


@router.get("/classify/rules")
def api_list_classify_rules(
    scope: str = "",
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items = list_rules(db, scope=scope)
    return {"items": [x.model_dump() for x in items]}


@router.post("/classify/rules")
def api_create_classify_rule(
    body: ClassifyRuleCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return create_rule(db, body).model_dump()


@router.patch("/classify/rules/{rule_id}")
def api_patch_classify_rule(
    rule_id: str,
    body: ClassifyRuleUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return update_rule(db, rule_id, body).model_dump()


@router.delete("/classify/rules/{rule_id}")
def api_delete_classify_rule(rule_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return delete_rule(db, rule_id)


@router.post("/classify/preview")
def api_classify_preview(db: Session = Depends(get_db)) -> dict[str, Any]:
    return preview_classify(db).model_dump()


@router.post("/classify/apply")
def api_classify_apply(
    skip_manual: bool = Query(default=True),
    fill_empty_only: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return apply_classify(
        db, skip_manual=skip_manual, fill_empty_only=fill_empty_only
    ).model_dump()


@router.get("/classify/unmatched")
def api_classify_unmatched(
    kind: str = Query(default="any", description="any | role | region"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_unmatched(db, kind=kind, page=page, page_size=page_size)


@router.patch("/fabric/nodes/{fabric_node_id}/tags")
def api_patch_fabric_node_tags(
    fabric_node_id: str,
    body: FabricNodeTagPatch,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return patch_fabric_node_tags(db, fabric_node_id, body).model_dump()


@router.post("/fabric/nodes/match")
def api_fabric_nodes_match(
    body: FabricNodesMatchRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Regex match over inventory (ephemeral; not stored as rules)."""
    return match_fabric_nodes(db, body).model_dump()


@router.post("/fabric/nodes/tags/bulk")
def api_fabric_nodes_bulk_tag(
    body: FabricNodesBulkTagRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return bulk_tag_fabric_nodes(db, body).model_dump()


@router.delete("/fabric/nodes/{fabric_node_id}")
def api_delete_fabric_node(
    fabric_node_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Delete a fabric node (orphaned / non-managed only). Does not touch inventory tables."""
    from .topology_inventory_lifecycle import delete_fabric_nodes

    return delete_fabric_nodes(db, [fabric_node_id])


@router.post("/fabric/nodes/delete")
def api_delete_fabric_nodes(
    body: FabricNodesDeleteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    from .topology_inventory_lifecycle import delete_fabric_nodes

    return delete_fabric_nodes(db, body.fabric_node_ids)


@router.post("/fabric/nodes/purge-placeholders")
def api_purge_placeholder_fabric_nodes(
    body: FabricNodesDeleteRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Hard-delete topology/LLDP placeholder ManagedNE + fabric node + incident edges."""
    from .topology_inventory_lifecycle import purge_placeholder_fabric_nodes

    return purge_placeholder_fabric_nodes(db, body.fabric_node_ids)


@router.post("/slices/generate")
def api_generate_slices(
    body: SliceGenerateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return generate_slices(db, body).model_dump()


@router.get("/fabric/nodes/search")
def api_fabric_nodes_search(
    q: str = "",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return search_fabric_nodes_with_views(db, keyword=q, page=page, page_size=page_size)
