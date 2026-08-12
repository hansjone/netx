from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CollectionTargetRef(BaseModel):
    source: str = "managed"  # managed | ume
    id: str


class CollectionPolicyOut(BaseModel):
    enabled: bool = False
    interval_days: int = 1
    interval_hours: int = 24
    scope_mode: str = "all"
    selected_targets: list[CollectionTargetRef] = Field(default_factory=list)
    title: str = ""
    commands: str = ""
    history_keep: int = 3
    updated_at: datetime | None = None


class CollectionPolicyUpdate(BaseModel):
    enabled: bool | None = None
    interval_days: int | None = Field(default=None, ge=1, le=365)
    interval_hours: int | None = Field(default=None, ge=1, le=8760)
    scope_mode: str | None = None
    selected_targets: list[CollectionTargetRef] | None = None
    title: str | None = None
    commands: str | None = None
    history_keep: int | None = Field(default=None, ge=0, le=200)


class CollectionJobCreate(BaseModel):
    title: str = ""
    commands: str = Field(min_length=1)
    ne_ids: list[str] = Field(default_factory=list)
    ume_ne_ids: list[str] = Field(default_factory=list)


class CollectionRunOut(BaseModel):
    id: str
    job_id: str
    ne_id: str
    ne_source: str = "managed"
    ne_name: str
    ne_ip: str
    status: str
    message: str
    output_rel_path: str
    has_output: bool
    started_at: datetime | None
    ended_at: datetime | None


class CollectionJobOut(BaseModel):
    id: str
    title: str
    commands: str
    trigger_mode: str = "manual"
    status: str
    ne_count: int
    success_count: int
    fail_count: int
    output_count: int = 0
    error_message: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    last_run_at: datetime | None = None


class CollectionJobSummary(BaseModel):
    id: str
    title: str
    status: str
    trigger_mode: str = "manual"
    ne_count: int
    success_count: int
    fail_count: int
    created_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_run_at: datetime | None = None


class CollectionDashboardOut(BaseModel):
    job_count: int = 0
    active_count: int = 0
    running_job: CollectionJobSummary | None = None
    last_job: CollectionJobSummary | None = None
    next_due_at: datetime | None = None
    policy: CollectionPolicyOut | None = None
