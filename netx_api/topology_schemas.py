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
    attrs: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None


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

    scope: str = Field(default="ne_ids", description="all_inventory | ne_ids")
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


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


class TopologyViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    remark: str = Field(default="", max_length=1024)
    filter: dict[str, Any] = Field(default_factory=dict)


class TopologyViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    remark: str | None = Field(default=None, max_length=1024)
    filter: dict[str, Any] | None = None
    viewport: dict[str, Any] | None = None


class TopologyViewOut(BaseModel):
    id: str
    name: str
    remark: str = ""
    filter: dict[str, Any] = Field(default_factory=dict)
    viewport: dict[str, Any] = Field(default_factory=dict)
    node_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


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


class ViewPositionsPatch(BaseModel):
    positions: list[ViewNodeIn] = Field(default_factory=list)


class ViewNodesAdd(BaseModel):
    """Add inventory NEs onto a view (creates fabric nodes as needed)."""

    managed_ne_ids: list[str] = Field(default_factory=list)
    ume_ne_ids: list[str] = Field(default_factory=list)
    fabric_node_ids: list[str] = Field(
        default_factory=list,
        description="Place existing fabric nodes onto the view",
    )
    # Optional initial positions keyed by managed/ume id
    layout: str = Field(default="grid", description="grid | keep")


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
