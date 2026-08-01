"""Schemas for network-management LLDP link collect (policy + dashboard)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LldpCollectTargetRef(BaseModel):
    source: str = "managed"  # managed | ume
    id: str


class LldpCollectPolicyOut(BaseModel):
    enabled: bool = False
    interval_days: int = 1
    concurrency: int = 4
    scope_mode: str = "all"
    selected_targets: list[LldpCollectTargetRef] = Field(default_factory=list)
    auto_add_unmatched: bool = True
    updated_at: datetime | None = None


class LldpCollectPolicyUpdate(BaseModel):
    enabled: bool | None = None
    interval_days: int | None = Field(default=None, ge=1, le=365)
    concurrency: int | None = Field(default=None, ge=1, le=32)
    scope_mode: str | None = None
    selected_targets: list[LldpCollectTargetRef] | None = None
    auto_add_unmatched: bool | None = None


class LldpCollectJobSummary(BaseModel):
    id: str
    scope: str = ""
    trigger_mode: str = "manual"
    status: str = ""
    total: int = 0
    done: int = 0
    edges_added: int = 0
    edges_updated: int = 0
    edges_stale: int = 0
    error: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime | None = None


class LldpCollectDashboardOut(BaseModel):
    policy: LldpCollectPolicyOut
    fabric_node_count: int = 0
    fabric_edge_count: int = 0
    fabric_edge_active: int = 0
    fabric_edge_stale: int = 0
    last_discover_at: datetime | None = None
    running_job: LldpCollectJobSummary | None = None
    last_job: LldpCollectJobSummary | None = None
    next_due_at: datetime | None = None


class LldpCollectStartOut(BaseModel):
    ok: bool = True
    job: dict[str, Any] = Field(default_factory=dict)
