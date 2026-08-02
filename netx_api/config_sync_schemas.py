"""Pydantic schemas for config sync API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ConfigSyncTargetRef(BaseModel):
    source: Literal["managed", "ume"]
    id: str


class ConfigSyncPolicyOut(BaseModel):
    enabled: bool
    interval_days: int
    concurrency: int
    scope_mode: str
    selected_targets: list[ConfigSyncTargetRef] = Field(default_factory=list)
    history_keep: int
    cycle_keep: int = 30
    updated_at: datetime | None = None


class ConfigSyncPolicyUpdate(BaseModel):
    enabled: bool | None = None
    interval_days: int | None = Field(default=None, ge=1, le=365)
    concurrency: int | None = Field(default=None, ge=1, le=32)
    scope_mode: Literal["all", "selected"] | None = None
    selected_targets: list[ConfigSyncTargetRef] | None = None
    history_keep: int | None = Field(default=None, ge=0, le=30)
    cycle_keep: int | None = Field(default=None, ge=0, le=200)


class ConfigSyncCycleCreate(BaseModel):
    mode: Literal["full", "retry_failed"] = "full"
    cycle_id: str | None = None


class ConfigSyncCycleOut(BaseModel):
    id: str
    trigger_mode: str
    status: str
    concurrency: int
    planned_count: int
    success_count: int
    fail_count: int
    skip_count: int
    error_message: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime | None = None


class ConfigSyncTaskOut(BaseModel):
    id: str
    cycle_id: str
    source: str
    target_id: str
    ne_name: str
    ne_ip: str
    vendor: str
    status: str
    message: str = ""
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ConfigSyncDashboardOut(BaseModel):
    policy: ConfigSyncPolicyOut
    snapshot_count: int
    last_cycle: ConfigSyncCycleOut | None = None
    running_cycle: ConfigSyncCycleOut | None = None
    next_due_at: datetime | None = None
    fail_by_vendor: dict[str, int] = Field(default_factory=dict)


class NeConfigSnapshotMetaOut(BaseModel):
    source: str
    target_id: str
    vendor: str
    device_type: str
    ne_name: str
    ne_ip: str
    config_sha256: str
    config_alt_sha256: str
    plain_size: int
    plain_alt_size: int
    zlib_size: int
    zlib_alt_size: int
    has_alt: bool
    commands: list[str] = Field(default_factory=list)
    collected_at: datetime | None = None
    last_cycle_id: str = ""


class NeConfigSnapshotDetailOut(NeConfigSnapshotMetaOut):
    config_text: str = ""
    config_alt_text: str = ""


class NeConfigHistoryOut(NeConfigSnapshotMetaOut):
    id: str
    cycle_id: str = ""
