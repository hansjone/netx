"""Pydantic schemas for topology maps / nodes / edges."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TopologyMapCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    remark: str = Field(default="", max_length=1024)


class TopologyMapUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    remark: str | None = Field(default=None, max_length=1024)


class TopologyMapOut(BaseModel):
    id: str
    name: str
    remark: str
    node_count: int = 0
    edge_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TopologyNodeIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    managed_ne_id: str = ""
    ume_ne_id: str = ""
    label: str = ""
    x: float = 0.0
    y: float = 0.0


class TopologyEdgeIn(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    source_node_id: str = Field(min_length=1, max_length=64)
    target_node_id: str = Field(min_length=1, max_length=64)
    source_port: str = ""
    target_port: str = ""
    source: str = "manual"


class TopologyNodeOut(BaseModel):
    id: str
    map_id: str
    managed_ne_id: str = ""
    ume_ne_id: str = ""
    label: str = ""
    x: float = 0.0
    y: float = 0.0
    ne_name: str = ""
    ne_ip: str = ""
    vendor: str = ""
    protocol: str = ""
    connect_status: str = ""


class TopologyEdgeOut(BaseModel):
    id: str
    map_id: str
    source_node_id: str
    target_node_id: str
    source_port: str = ""
    target_port: str = ""
    source: str = "manual"
    discovered_at: datetime | None = None


class TopologyGraphOut(BaseModel):
    map: TopologyMapOut
    nodes: list[TopologyNodeOut]
    edges: list[TopologyEdgeOut]


class TopologyGraphPut(BaseModel):
    nodes: list[TopologyNodeIn] = Field(default_factory=list)
    edges: list[TopologyEdgeIn] = Field(default_factory=list)


class TopologyDiscoverRequest(BaseModel):
    """Run LLDP/CDP discovery for managed NEs currently on the map."""

    protocol: str = Field(default="auto", description="auto | lldp | cdp")
    ne_ids: list[str] | None = None


class TopologyDiscoverNeResult(BaseModel):
    ne_id: str
    ne_name: str = ""
    ne_ip: str = ""
    ok: bool = False
    command: str = ""
    neighbors: int = 0
    edges_added: int = 0
    edges_updated: int = 0
    error: str = ""
    raw_preview: str = ""


class TopologyDiscoverOut(BaseModel):
    map_id: str
    protocol: str
    scanned: int = 0
    edges_added: int = 0
    edges_updated: int = 0
    edges_stale: int = 0
    results: list[TopologyDiscoverNeResult] = Field(default_factory=list)
    graph: TopologyGraphOut | None = None
