from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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
