from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType

class TopoFabricNode(Base):
    """Global topology fabric node (inventory-aligned). Target scale ~50k."""

    __tablename__ = "topo_fabric_node"
    __table_args__ = (
        UniqueConstraint("managed_ne_id", name="uq_topo_fabric_node_managed_ne_id"),
        UniqueConstraint("ume_ne_id", name="uq_topo_fabric_node_ume_ne_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    managed_ne_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ume_ne_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    ip: Mapped[str] = mapped_column(String(128), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="")
    device_type: Mapped[str] = mapped_column(String(64), default="")
    # Classify tags (regex rules / manual). role: core|aggregation|access|unknown|""
    role: Mapped[str] = mapped_column(String(32), default="", index=True)
    region_folder_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # rule | manual | ""
    role_source: Mapped[str] = mapped_column(String(16), default="")
    region_source: Mapped[str] = mapped_column(String(16), default="")
    # Composed flat-world coordinates (packed per-SBN local layouts). Not raw UME xPos/yPos.
    world_x: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    world_y: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    attrs: Mapped[dict] = mapped_column(_JsonType, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class TopoClassifyRule(Base):
    """Regex rules to assign fabric role or region from NE name/IP."""

    __tablename__ = "topo_classify_rule"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    # role | region
    scope: Mapped[str] = mapped_column(String(16), default="role", index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    pattern: Mapped[str] = mapped_column(String(512), default="")
    # name | ip | name_ip
    match_field: Mapped[str] = mapped_column(String(32), default="name")
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # role: {role}; region: {folder_id} or {region_name_from_group}
    payload: Mapped[dict] = mapped_column(_JsonType, default=dict)
    remark: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class TopoFabricEdge(Base):
    """Global fabric link. Target scale ~1M; layer reserved for future BGP/tunnel/l2vpn."""

    __tablename__ = "topo_fabric_edge"
    __table_args__ = (
        UniqueConstraint(
            "layer",
            "a_node_id",
            "b_node_id",
            "a_port",
            "b_port",
            name="uq_topo_fabric_edge_endpoints",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    # physical | bgp | tunnel | l2vpn (P1 writes physical only)
    layer: Mapped[str] = mapped_column(String(32), default="physical", index=True)
    a_node_id: Mapped[str] = mapped_column(String(64), index=True)
    b_node_id: Mapped[str] = mapped_column(String(64), index=True)
    a_port: Mapped[str] = mapped_column(String(128), default="")
    b_port: Mapped[str] = mapped_column(String(128), default="")
    # lldp | manual | stale
    source: Mapped[str] = mapped_column(String(32), default="lldp", index=True)
    # active | stale
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    attrs: Mapped[dict] = mapped_column(_JsonType, default=dict)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TopoFolder(Base):
    """Grouping node for topology tree (root / region). Not a canvas."""

    __tablename__ = "topo_folder"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # root | region
    kind: Mapped[str] = mapped_column(String(32), default="region", index=True)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    # UME SBN uuid when folder is synced from TopoNodes; empty for manual regions.
    external_ref: Mapped[str] = mapped_column(String(128), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class TopoView(Base):
    """Topology canvas under a site/region (physical or custom; flat siblings)."""

    __tablename__ = "topo_view"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    folder_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Legacy nesting column; unused (always null after vendor-model migration).
    parent_view_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # physical | custom
    kind: Mapped[str] = mapped_column(String(32), default="custom", index=True)
    # Optional filter preset label (legacy core|aggregation|access); not a tree level.
    role: Mapped[str] = mapped_column(String(32), default="core", index=True)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    remark: Mapped[str] = mapped_column(String(1024), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # { layer?, status?, membership?: {...} }
    filter: Mapped[dict] = mapped_column(_JsonType, default=dict)
    viewport: Mapped[dict] = mapped_column(_JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class TopoViewNode(Base):
    """Node placement on a view; no link facts."""

    __tablename__ = "topo_view_node"
    __table_args__ = (UniqueConstraint("view_id", "fabric_node_id", name="uq_topo_view_node"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    view_id: Mapped[str] = mapped_column(String(64), index=True)
    fabric_node_id: Mapped[str] = mapped_column(String(64), index=True)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String(256), default="")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TopoViewEdgeStyle(Base):
    """Optional per-view edge style override."""

    __tablename__ = "topo_view_edge_style"
    __table_args__ = (UniqueConstraint("view_id", "fabric_edge_id", name="uq_topo_view_edge_style"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    view_id: Mapped[str] = mapped_column(String(64), index=True)
    fabric_edge_id: Mapped[str] = mapped_column(String(64), index=True)
    stroke_color: Mapped[str] = mapped_column(String(32), default="")
    stroke_width: Mapped[int] = mapped_column(Integer, default=0)
    line_style: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class LldpCollectPolicy(Base):
    """Singleton policy for periodic fabric LLDP collect (id=1)."""

    __tablename__ = "lldp_collect_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)  # legacy; prefer interval_hours
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    concurrency: Mapped[int] = mapped_column(Integer, default=4)
    scope_mode: Mapped[str] = mapped_column(String(32), default="all")  # all | selected
    selected_targets: Mapped[list] = mapped_column(_JsonType, default=list)
    auto_add_unmatched: Mapped[bool] = mapped_column(Boolean, default=True)
    # Keep N finished discover jobs (items + raw_preview); 0 = keep none finished.
    history_keep: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TopoDiscoverJob(Base):
    """Async LLDP discovery job over inventory / NE ids."""

    __tablename__ = "topo_discover_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    # all_inventory | ne_ids
    scope: Mapped[str] = mapped_column(String(32), default="ne_ids", index=True)
    # manual | schedule | topology (ad-hoc from canvas)
    trigger_mode: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    ne_ids_json: Mapped[list] = mapped_column(_JsonType, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    edges_added: Mapped[int] = mapped_column(Integer, default=0)
    edges_updated: Mapped[int] = mapped_column(Integer, default=0)
    edges_stale: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class TopoDiscoverJobItem(Base):
    """Per-NE result row for a discover job."""

    __tablename__ = "topo_discover_job_item"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    ume_ne_id: Mapped[str] = mapped_column(String(128), default="")
    fabric_node_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    command: Mapped[str] = mapped_column(String(256), default="")
    neighbors: Mapped[int] = mapped_column(Integer, default=0)
    edges_added: Mapped[int] = mapped_column(Integer, default=0)
    edges_updated: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_json: Mapped[list] = mapped_column(_JsonType, default=list)
    parser_key: Mapped[str] = mapped_column(String(64), default="")
    parser_stub: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(String(1024), default="")
    raw_preview: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class TopoFabricStats(Base):
    """Cached fabric counters for summary (avoid COUNT on 1M edges)."""

    __tablename__ = "topo_fabric_stats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_active: Mapped[int] = mapped_column(Integer, default=0)
    edge_stale: Mapped[int] = mapped_column(Integer, default=0)
    last_discover_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
