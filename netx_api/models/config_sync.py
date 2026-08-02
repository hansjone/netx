from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType

class ConfigSyncPolicy(Base):
    """Singleton policy for periodic config sync (id=1)."""

    __tablename__ = "config_sync_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=3)
    concurrency: Mapped[int] = mapped_column(Integer, default=5)
    scope_mode: Mapped[str] = mapped_column(String(32), default="all")  # all | selected
    selected_targets: Mapped[list] = mapped_column(_JsonType, default=list)
    history_keep: Mapped[int] = mapped_column(Integer, default=3)
    # Finished sync cycles to retain (newest kept); active cycles always kept.
    cycle_keep: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class ConfigSyncCycle(Base):
    """One config-sync cycle over many NEs."""

    __tablename__ = "config_sync_cycle"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    trigger_mode: Mapped[str] = mapped_column(String(32), default="schedule", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    concurrency: Mapped[int] = mapped_column(Integer, default=5)
    planned_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    skip_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class ConfigSyncTask(Base):
    """Per-NE work item inside a config sync cycle."""

    __tablename__ = "config_sync_task"
    __table_args__ = (UniqueConstraint("cycle_id", "source", "target_id", name="uq_config_sync_task_target"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    cycle_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), default="managed", index=True)  # managed | ume
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    message: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NeConfigSnapshot(Base):
    """Latest successful config snapshot per NE (zlib-compressed)."""

    __tablename__ = "ne_config_snapshot"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)  # managed | ume
    target_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    vendor: Mapped[str] = mapped_column(String(64), default="")
    device_type: Mapped[str] = mapped_column(String(64), default="")
    ne_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    ne_ip: Mapped[str] = mapped_column(String(128), default="", index=True)
    config_zlib: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    config_alt_zlib: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    config_sha256: Mapped[str] = mapped_column(String(64), default="")
    config_alt_sha256: Mapped[str] = mapped_column(String(64), default="")
    plain_size: Mapped[int] = mapped_column(Integer, default=0)
    plain_alt_size: Mapped[int] = mapped_column(Integer, default=0)
    zlib_size: Mapped[int] = mapped_column(Integer, default=0)
    zlib_alt_size: Mapped[int] = mapped_column(Integer, default=0)
    commands_json: Mapped[list] = mapped_column(_JsonType, default=list)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    last_cycle_id: Mapped[str] = mapped_column(String(64), default="")
    last_task_id: Mapped[str] = mapped_column(String(64), default="")


class NeConfigHistory(Base):
    """Historical config versions when content changes."""

    __tablename__ = "ne_config_history"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    source: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[str] = mapped_column(String(128), index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="")
    device_type: Mapped[str] = mapped_column(String(64), default="")
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    config_zlib: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    config_alt_zlib: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    config_sha256: Mapped[str] = mapped_column(String(64), default="")
    config_alt_sha256: Mapped[str] = mapped_column(String(64), default="")
    plain_size: Mapped[int] = mapped_column(Integer, default=0)
    plain_alt_size: Mapped[int] = mapped_column(Integer, default=0)
    zlib_size: Mapped[int] = mapped_column(Integer, default=0)
    zlib_alt_size: Mapped[int] = mapped_column(Integer, default=0)
    commands_json: Mapped[list] = mapped_column(_JsonType, default=list)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), default="")
    task_id: Mapped[str] = mapped_column(String(64), default="")

