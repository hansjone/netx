"""Pydantic schemas for fabric topology + views (final model)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Fabric
# ---------------------------------------------------------------------------


class FabricNodeOut(BaseModel):
    id: str
    managed_ne_id: str = ""
    ume_ne_id: str = ""
    name: str = ""
    ip: str = ""
    vendor: str = ""
    device_type: str = ""
    role: str = ""
    region_folder_id: str | None = None
    role_source: str = ""
    region_source: str = ""
    attrs: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None
    # Inventory link diagnostics (list/enrich); empty when not enriched.
    link_status: str = ""  # managed | ume | both | orphaned
    managed_alive: bool = False
    ume_alive: bool = False
    managed_source: str = ""  # manual | ume_sync | lldp | webcrt | …
    deletable: bool = False


class FabricEdgeOut(BaseModel):
    id: str
    layer: str = "physical"
    a_node_id: str
    b_node_id: str
    a_port: str = ""
    b_port: str = ""
    a_name: str = ""
    b_name: str = ""
    a_ip: str = ""
    b_ip: str = ""
    source: str = "lldp"
    status: str = "active"
    attrs: dict[str, Any] = Field(default_factory=dict)
    discovered_at: datetime | None = None
    last_seen_at: datetime | None = None
    updated_at: datetime | None = None


class FabricSummaryOut(BaseModel):
    node_count: int = 0
    edge_count: int = 0
    edge_active: int = 0
    edge_stale: int = 0  # legacy alias of edge_missing
    edge_missing: int = 0
    last_discover_at: datetime | None = None
    updated_at: datetime | None = None


class FabricNeighborhoodOut(BaseModel):
    center_node_id: str
    depth: int = 1
    nodes: list[FabricNodeOut] = Field(default_factory=list)
    edges: list[FabricEdgeOut] = Field(default_factory=list)


class FabricDiscoverRequest(BaseModel):
    """Start LLDP discovery into fabric (no CDP)."""

    scope: str = Field(default="ne_ids", description="all_inventory (managed+UME) | ne_ids")
    ne_ids: list[str] = Field(
        default_factory=list,
        description="Legacy mixed ids (managed first, then ume). Prefer managed_ne_ids/ume_ne_ids.",
    )
    managed_ne_ids: list[str] = Field(default_factory=list)
    ume_ne_ids: list[str] = Field(default_factory=list)
    auto_add_unmatched: bool = Field(
        default=True,
        description="Create SSH placeholder ManagedNEs for LLDP neighbors not in inventory",
    )
    concurrency: int = Field(default=4, ge=1, le=32)
    trigger_mode: str = Field(default="manual", description="manual | schedule | topology")


class FabricDiscoverUnmatched(BaseModel):
    remote_name: str = ""
    remote_ip: str = ""
    local_port: str = ""
    remote_port: str = ""


class FabricDiscoverJobItemOut(BaseModel):
    id: str
    job_id: str
    ne_id: str = ""
    ume_ne_id: str = ""
    fabric_node_id: str = ""
    ne_name: str = ""
    ne_ip: str = ""
    ok: bool = False
    command: str = ""
    neighbors: int = 0
    edges_added: int = 0
    edges_updated: int = 0
    unmatched_count: int = 0
    unmatched: list[FabricDiscoverUnmatched] = Field(default_factory=list)
    parser_key: str = ""
    parser_stub: bool = False
    error: str = ""
    raw_preview: str = ""


class FabricDiscoverJobOut(BaseModel):
    id: str
    scope: str
    trigger_mode: str = "manual"
    status: str
    total: int = 0
    done: int = 0
    edges_added: int = 0
    edges_updated: int = 0
    edges_stale: int = 0  # legacy alias of edges_missing
    edges_missing: int = 0
    error: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    items: list[FabricDiscoverJobItemOut] = Field(default_factory=list)
    items_total: int = 0
    items_page: int = 1
    items_page_size: int = 0


# ---------------------------------------------------------------------------
# Folders (tree grouping) + Views (leaf canvases)
# ---------------------------------------------------------------------------


class TopologyFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    kind: str = Field(default="region", description="region only from API")
    parent_id: str | None = None
    sort_order: int = 0


class TopologyFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    parent_id: str | None = None
    sort_order: int | None = None


class TopologyFolderOut(BaseModel):
    id: str
    parent_id: str = ""
    kind: str
    name: str
    sort_order: int = 0
    is_system: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TopologyViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    remark: str = Field(default="", max_length=1024)
    filter: dict[str, Any] = Field(default_factory=dict)
    folder_id: str = Field(..., min_length=1, description="Site/region folder id (required)")
    kind: str = Field(default="custom", description="physical | custom")
    role: str = Field(
        default="core",
        description="Optional filter preset label (legacy); not a tree level",
    )
    sort_order: int = 0


class TopologyViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    remark: str | None = Field(default=None, max_length=1024)
    filter: dict[str, Any] | None = None
    viewport: dict[str, Any] | None = None
    folder_id: str | None = None
    kind: str | None = None
    role: str | None = None
    sort_order: int | None = None


class TopologyViewOut(BaseModel):
    id: str
    name: str
    remark: str = ""
    folder_id: str = ""
    kind: str = "custom"
    role: str = "core"
    sort_order: int = 0
    filter: dict[str, Any] = Field(default_factory=dict)
    viewport: dict[str, Any] = Field(default_factory=dict)
    node_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TopologyTreeViewOut(BaseModel):
    id: str
    name: str
    kind: str = "custom"
    role: str = "core"
    sort_order: int = 0
    node_count: int = 0
    updated_at: datetime | None = None


class TopologyTreeFolderOut(BaseModel):
    id: str
    parent_id: str = ""
    kind: str
    name: str
    sort_order: int = 0
    is_system: bool = False
    views: list[TopologyTreeViewOut] = Field(default_factory=list)
    children: list["TopologyTreeFolderOut"] = Field(default_factory=list)


class TopologyTreeOut(BaseModel):
    root: TopologyTreeFolderOut | None = None


class ViewPopulateRequest(BaseModel):
    """Fill a leaf view from membership rules (optional dry-run)."""

    dry_run: bool = False
    membership: dict[str, Any] | None = None
    freeze_after: bool = True


class ViewNodeIn(BaseModel):
    fabric_node_id: str = Field(min_length=1, max_length=64)
    x: float = 0.0
    y: float = 0.0
    label: str = ""
    locked: bool = False


class ViewNodeOut(BaseModel):
    fabric_node_id: str
    managed_ne_id: str = ""
    ume_ne_id: str = ""
    label: str = ""
    x: float = 0.0
    y: float = 0.0
    locked: bool = False
    name: str = ""
    ip: str = ""
    vendor: str = ""
    device_type: str = ""
    connect_status: str = ""
    managed_source: str = ""  # manual | ume_sync | lldp | topology | webcrt | …


class ViewEdgeOut(BaseModel):
    id: str
    a_node_id: str
    b_node_id: str
    a_port: str = ""
    b_port: str = ""
    source: str = "lldp"
    status: str = "active"
    layer: str = "physical"
    stroke_color: str = ""
    stroke_width: int = 0
    line_style: str = ""
    discovered_at: datetime | None = None


class TopologyViewGraphOut(BaseModel):
    view: TopologyViewOut
    nodes: list[ViewNodeOut]
    edges: list[ViewEdgeOut]
    truncated: bool = False
    truncate_reason: str = ""
    outside_peers: list[dict[str, str]] = Field(default_factory=list)


class ViewPopulateOut(BaseModel):
    view_id: str
    dry_run: bool = False
    candidate_count: int = 0
    would_add: int = 0
    added: int = 0
    max_nodes: int = 0
    truncated: bool = False
    outside_peers: list[dict[str, str]] = Field(default_factory=list)
    graph: TopologyViewGraphOut | None = None


class ViewMutationOut(BaseModel):
    """Summary for bulk view mutations (add / move / remove)."""

    ok: bool = True
    view_id: str = ""
    matched: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped_existing: int = 0
    skipped_missing: int = 0
    skipped_locked: int = 0
    view_node_count: int = 0
    max_nodes: int = 0
    truncated: bool = False
    next_offset: int | None = None
    graph: TopologyViewGraphOut | None = None


class ViewPositionsPatch(BaseModel):
    """Move nodes: explicit positions and/or filter + layout (grid|offset|stack)."""

    positions: list[ViewNodeIn] = Field(default_factory=list)
    fabric_node_ids: list[str] = Field(default_factory=list)
    keyword: str = ""
    role: str = ""
    vendor: str = ""
    link_status: str = ""
    layout: str = Field(default="", description="grid | offset | stack | empty=use positions")
    origin_x: float = 40.0
    origin_y: float = 40.0
    gap_x: float = 180.0
    gap_y: float = 120.0
    cols: int = Field(default=0, ge=0, le=2000)
    dx: float = 0.0
    dy: float = 0.0
    # Default True keeps web UI / existing clients returning a full graph.
    return_graph: bool = True


class ViewNodesAdd(BaseModel):
    """Add NEs onto a view. Prefer fabric filters for bulk; managed/ume still allowed for UI."""

    managed_ne_ids: list[str] = Field(default_factory=list)
    ume_ne_ids: list[str] = Field(default_factory=list)
    fabric_node_ids: list[str] = Field(
        default_factory=list,
        description="Place existing fabric nodes onto the view",
    )
    keyword: str = ""
    role: str = ""
    vendor: str = ""
    link_status: str = ""
    limit: int = Field(default=500, ge=1, le=2000)
    offset: int = Field(default=0, ge=0)
    layout: str = Field(default="grid", description="grid | keep")
    return_graph: bool = True


class TopologyPlaceholderCreate(BaseModel):
    """Create a canvas placeholder ManagedNE (source=topology) and place it on the view."""

    name: str = Field(min_length=1, max_length=256)
    ip_address: str = ""
    x: float = 0.0
    y: float = 0.0


class ViewNodesRemove(BaseModel):
    """Remove placements from a view (does not delete fabric). Filter and/or id list."""

    fabric_node_ids: list[str] = Field(default_factory=list)
    keyword: str = ""
    role: str = ""
    vendor: str = ""
    link_status: str = ""
    return_graph: bool = True


class ViewEdgeStylePatch(BaseModel):
    fabric_edge_id: str
    stroke_color: str = ""
    stroke_width: int = Field(default=0, ge=0, le=12)
    line_style: str = Field(default="", max_length=16)


class FabricManualEdgeIn(BaseModel):
    a_node_id: str = Field(min_length=1, max_length=64)
    b_node_id: str = Field(min_length=1, max_length=64)
    a_port: str = ""
    b_port: str = ""


class FabricEdgesDeleteRequest(BaseModel):
    edge_ids: list[str] = Field(default_factory=list, min_length=1)


class FabricEdgesDeleteOut(BaseModel):
    deleted: int = 0


# ---------------------------------------------------------------------------
# Classify rules + slices + search
# ---------------------------------------------------------------------------


class ClassifyRuleOut(BaseModel):
    id: str
    scope: str = "role"
    name: str = ""
    pattern: str = ""
    match_field: str = "name"
    priority: int = 100
    enabled: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    remark: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ClassifyRuleCreate(BaseModel):
    scope: str = Field(default="role", description="role | region")
    name: str = ""
    pattern: str
    match_field: str = "name"
    priority: int = 100
    enabled: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    remark: str = ""


class ClassifyRuleUpdate(BaseModel):
    name: str | None = None
    pattern: str | None = None
    match_field: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    payload: dict[str, Any] | None = None
    remark: str | None = None


class ClassifyPreviewOut(BaseModel):
    total_nodes: int = 0
    role_matched: int = 0
    role_unmatched: int = 0
    role_conflicts: int = 0
    region_matched: int = 0
    region_unmatched: int = 0
    region_conflicts: int = 0
    role_samples: list[dict[str, Any]] = Field(default_factory=list)
    region_samples: list[dict[str, Any]] = Field(default_factory=list)
    unmatched_samples: list[dict[str, Any]] = Field(default_factory=list)


class ClassifyApplyOut(BaseModel):
    role_updated: int = 0
    region_updated: int = 0
    skipped_manual: int = 0
    total_nodes: int = 0


class FabricNodeTagPatch(BaseModel):
    role: str | None = None
    region_folder_id: str | None = None


class FabricNodesMatchRequest(BaseModel):
    """Ephemeral regex match over fabric inventory (not persisted as rules)."""

    pattern: str
    match_field: str = "name"
    sample_limit: int = Field(default=50, ge=1, le=200)


class FabricNodesMatchOut(BaseModel):
    pattern: str
    match_field: str = "name"
    total_matched: int = 0
    samples: list[dict[str, Any]] = Field(default_factory=list)
    fabric_node_ids: list[str] = Field(default_factory=list)


class FabricNodesBulkTagRequest(BaseModel):
    """Assign role/region to explicit ids or to an ephemeral regex match."""

    fabric_node_ids: list[str] = Field(default_factory=list)
    pattern: str = ""
    match_field: str = "name"
    role: str | None = None
    region_folder_id: str | None = None
    dry_run: bool = False


class FabricNodesBulkTagOut(BaseModel):
    dry_run: bool = False
    matched: int = 0
    updated: int = 0
    role: str | None = None
    region_folder_id: str | None = None
    samples: list[dict[str, Any]] = Field(default_factory=list)


class FabricNodesDeleteRequest(BaseModel):
    fabric_node_ids: list[str] = Field(default_factory=list, min_length=1)


class FabricNodesDeleteOut(BaseModel):
    deleted: int = 0
    edges_deleted: int = 0
    placements_deleted: int = 0


class SliceGenerateRequest(BaseModel):
    folder_id: str
    template: str = Field(description="core_only | core_agg | agg_access")
    dry_run: bool = True
    max_nodes: int = Field(default=300, ge=1, le=2000)
    seed_physical_cores: bool = False


class SliceMapPlan(BaseModel):
    name: str
    role: str = "core"
    seed_fabric_node_ids: list[str] = Field(default_factory=list)
    member_fabric_node_ids: list[str] = Field(default_factory=list)
    node_count: int = 0


class SliceGenerateOut(BaseModel):
    folder_id: str
    template: str
    dry_run: bool = True
    maps: list[SliceMapPlan] = Field(default_factory=list)
    map_count: int = 0
    overlap_node_count: int = 0
    created_view_ids: list[str] = Field(default_factory=list)
