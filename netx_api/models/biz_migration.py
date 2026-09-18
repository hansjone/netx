"""Cutover / migration monitor ORM (separate from biz_state CompareJob)."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType


class BizMigrationProject(Base):
    """Cutover project linking old/new collect tasks + baselines."""

    __tablename__ = "biz_migration_project"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    old_task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    new_task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    old_baseline_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    new_baseline_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Reuse biz_port_mapping (before_if=old, after_if=new)
    mapping_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # BizMonitorTemplate — HOW (via compare template) + dual/status rules
    monitor_template_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)  # draft|active|done
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizMigrationBatch(Base):
    """One cutover night / wave with an expect set."""

    __tablename__ = "biz_migration_batch"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    project_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    batch_label: Mapped[str] = mapped_column(String(128), default="")
    # pending | active | review | done
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    # {"ports": ["gei-..."], "items": [{"metric_id":"bgp_peer","key":"..."}]}
    expect_set_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Acceptance (set by finish_batch): none | passed | failed
    accept_status: Mapped[str] = mapped_column(String(32), default="none", index=True)
    accept_run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    accept_summary_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizMigrationRun(Base):
    """One user-triggered evaluation against baselines + current batches."""

    __tablename__ = "biz_migration_run"
    __table_args__ = (Index("ix_biz_migration_run_batch_created", "batch_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    project_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    old_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    new_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # manual | acceptance
    purpose: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    summary_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    message: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class BizMigrationDiff(Base):
    """Per-row / dual verdict row for a migration run (paged board detail)."""

    __tablename__ = "biz_migration_diff"
    __table_args__ = (
        Index("ix_biz_migration_diff_run_metric", "run_id", "metric_id"),
        Index("ix_biz_migration_diff_run_verdict", "run_id", "verdict"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    metric_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    # ok | expected | anomaly | migrating | migrated | lost | not_involved | unfinished
    verdict: Mapped[str] = mapped_column(String(32), default="", index=True)
    # green | yellow | red | gray
    color: Mapped[str] = mapped_column(String(16), default="")
    key_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    old_kind: Mapped[str] = mapped_column(String(16), default="")
    new_kind: Mapped[str] = mapped_column(String(16), default="")
    old_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    new_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    in_expect: Mapped[bool] = mapped_column(Boolean, default=False)
    search_text: Mapped[str] = mapped_column(Text, default="")


class BizMigrationRedTicket(Base):
    """Persisted anomaly / unfinished expect items — survive across batches (带红继续)."""

    __tablename__ = "biz_migration_red_ticket"
    __table_args__ = (
        Index("ix_biz_migration_red_project_status", "project_id", "status"),
        Index("ix_biz_migration_red_batch", "batch_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    project_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    metric_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    key_str: Mapped[str] = mapped_column(String(256), default="")
    new_key_str: Mapped[str] = mapped_column(String(256), default="")
    verdict: Mapped[str] = mapped_column(String(32), default="")
    color: Mapped[str] = mapped_column(String(16), default="red")
    old_status: Mapped[str] = mapped_column(String(64), default="")
    new_status: Mapped[str] = mapped_column(String(64), default="")
    detail_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    # open | carried | resolved
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    carried_to_batch_id: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
