from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType

class ManagedNE(Base):
    """Locally managed network element (SSH/Telnet), independent of UME inventory."""

    __tablename__ = "managed_ne"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="Other", index=True)
    device_type: Mapped[str] = mapped_column(String(128), default="")
    # Not unique: WebCRT sessions may share a host IP with distinct session names.
    # Inventory create/update still enforces uniqueness in ne_service.
    ip_address: Mapped[str] = mapped_column(String(128), index=True)
    port: Mapped[int] = mapped_column(Integer, default=22)
    protocol: Mapped[str] = mapped_column(String(16), default="ssh")
    username: Mapped[str] = mapped_column(String(128), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    enable_secret_enc: Mapped[str] = mapped_column(Text, default="")
    connect_status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    connect_message: Mapped[str] = mapped_column(String(512), default="")
    connect_detail: Mapped[str] = mapped_column(Text, default="")
    connect_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    site: Mapped[str] = mapped_column(String(256), default="")
    tags: Mapped[str] = mapped_column(String(512), default="")
    remark: Mapped[str] = mapped_column(String(1024), default="")
    source: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_ref: Mapped[str] = mapped_column(String(128), default="", index=True)
    hop_enabled: Mapped[bool] = mapped_column(default=False)
    hop_vendor: Mapped[str] = mapped_column(String(32), default="zte")
    hop_host: Mapped[str] = mapped_column(String(128), default="")
    hop_port: Mapped[int] = mapped_column(Integer, default=22)
    hop_protocol: Mapped[str] = mapped_column(String(16), default="ssh")
    hop_username: Mapped[str] = mapped_column(String(128), default="")
    hop_password_enc: Mapped[str] = mapped_column(Text, default="")
    hop_command_template: Mapped[str] = mapped_column(Text, default="")
    hop_vrf: Mapped[str] = mapped_column(String(128), default="")
    hop_target_auth_mode: Mapped[str] = mapped_column(String(32), default="bastion_managed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class CliConnectProfile(Base):
    """Reusable SSH/Telnet + hop credentials for UME lazy CLI exec."""

    __tablename__ = "cli_connect_profile"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    is_default: Mapped[bool] = mapped_column(default=False, index=True)
    username: Mapped[str] = mapped_column(String(128), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    port: Mapped[int] = mapped_column(Integer, default=22)
    protocol: Mapped[str] = mapped_column(String(16), default="ssh")
    device_type_default: Mapped[str] = mapped_column(String(128), default="zte_zxros")
    vendor_default: Mapped[str] = mapped_column(String(64), default="ZTE")
    ne_type_rules: Mapped[str] = mapped_column(Text, default="")
    hop_enabled: Mapped[bool] = mapped_column(default=False)
    hop_vendor: Mapped[str] = mapped_column(String(32), default="zte")
    hop_host: Mapped[str] = mapped_column(String(128), default="")
    hop_port: Mapped[int] = mapped_column(Integer, default=22)
    hop_protocol: Mapped[str] = mapped_column(String(16), default="ssh")
    hop_username: Mapped[str] = mapped_column(String(128), default="")
    hop_password_enc: Mapped[str] = mapped_column(Text, default="")
    hop_command_template: Mapped[str] = mapped_column(Text, default="")
    hop_vrf: Mapped[str] = mapped_column(String(128), default="")
    hop_target_auth_mode: Mapped[str] = mapped_column(String(32), default="bastion_managed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class UmeCliOverride(Base):
    """Per-UME NE CLI overrides and connect-test cache."""

    __tablename__ = "ume_cli_override"

    ume_ne_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    profile_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("cli_connect_profile.id", ondelete="SET NULL"), nullable=True
    )
    username_override: Mapped[str] = mapped_column(String(128), default="")
    device_type_override: Mapped[str] = mapped_column(String(128), default="")
    vendor_override: Mapped[str] = mapped_column(String(64), default="")
    connect_status: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    connect_message: Mapped[str] = mapped_column(String(512), default="")
    connect_detail: Mapped[str] = mapped_column(Text, default="")
    connect_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class NeCollectionPolicy(Base):
    """Singleton policy for periodic batch CLI collect (id=1).

    Default ``enabled=False``: one-shot / manual only until the operator turns on schedule.
    ``history_keep`` defaults to 3 finished jobs.
    """

    __tablename__ = "ne_collection_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)  # legacy mirror of hours
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    scope_mode: Mapped[str] = mapped_column(String(32), default="all")  # all | selected
    selected_targets: Mapped[list] = mapped_column(_JsonType, default=list)
    title: Mapped[str] = mapped_column(String(256), default="")
    commands: Mapped[str] = mapped_column(Text, default="")
    # Finished jobs to retain (newest kept); active jobs always kept.
    history_keep: Mapped[int] = mapped_column(Integer, default=3)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class NeCollectionJob(Base):
    """Batch CLI collection job over managed NEs."""

    __tablename__ = "ne_collection_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    title: Mapped[str] = mapped_column(String(256), default="")
    commands: Mapped[str] = mapped_column(Text, default="")
    # manual | schedule
    trigger_mode: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    ne_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class NeCollectionRun(Base):
    """Per-NE execution within a collection job."""

    __tablename__ = "ne_collection_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    # managed_ne.id or ume_inventory_ne.ne_id
    ne_id: Mapped[str] = mapped_column(String(128), index=True)
    # managed | ume
    ne_source: Mapped[str] = mapped_column(String(16), default="managed", index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    message: Mapped[str] = mapped_column(String(1024), default="")
    output_rel_path: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

