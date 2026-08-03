from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType


def _mirror_device_task_id(target: object) -> None:
    """Keep legacy task_id column aligned with device_id (brownfield NOT NULL)."""
    did = str(getattr(target, "device_id", None) or getattr(target, "task_id", None) or "")
    target.device_id = did  # type: ignore[attr-defined]
    target.task_id = did  # type: ignore[attr-defined]

class PortTrafficDevice(Base):
    """Per-NE port traffic monitoring config (device-centric)."""

    __tablename__ = "port_traffic_device"
    __table_args__ = (
        UniqueConstraint("source", "ne_id", name="uq_port_traffic_device_ne"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    source: Mapped[str] = mapped_column(String(32), default="managed", index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(256), default="")  # optional remark
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)  # draft|running|paused|stopped
    interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    collect_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_collect_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_collect_ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


# Back-compat alias while callers migrate.
PortTrafficTask = PortTrafficDevice


class PortTrafficSeries(Base):
    """Logical port (business link) that survives physical NE/if replacement."""

    __tablename__ = "port_traffic_series"
    __table_args__ = (UniqueConstraint("device_id", "title", name="uq_port_traffic_series_title"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    device_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Legacy column retained for brownfield DBs that still enforce NOT NULL task_id.
    task_id: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)  # active|disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class PortTrafficTarget(Base):
    """Monitored interface under a device monitoring config."""

    __tablename__ = "port_traffic_target"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    device_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # Legacy column retained for brownfield DBs that still enforce NOT NULL task_id.
    task_id: Mapped[str] = mapped_column(String(64), default="")
    series_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    source: Mapped[str] = mapped_column(String(32), default="managed", index=True)
    target_id: Mapped[str] = mapped_column(String(128), index=True)  # NE id
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="")
    ifname: Mapped[str] = mapped_column(String(128), default="")
    if_description: Mapped[str] = mapped_column(String(512), default="")
    bw_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)  # active|disabled|retired
    last_error: Mapped[str] = mapped_column(String(1024), default="")
    last_sample_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


@event.listens_for(PortTrafficSeries, "before_insert")
@event.listens_for(PortTrafficSeries, "before_update")
def _sync_series_task_id(mapper, connection, target) -> None:  # noqa: ANN001
    _mirror_device_task_id(target)


@event.listens_for(PortTrafficTarget, "before_insert")
@event.listens_for(PortTrafficTarget, "before_update")
def _sync_target_task_id(mapper, connection, target) -> None:  # noqa: ANN001
    _mirror_device_task_id(target)


class PortTrafficSample(Base):
    """Time-series sample for a monitored interface."""

    __tablename__ = "port_traffic_sample"
    __table_args__ = (UniqueConstraint("target_row_id", "ts", name="uq_port_traffic_sample_ts"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    target_row_id: Mapped[str] = mapped_column(String(64), index=True)
    series_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    in_bps: Mapped[float] = mapped_column(Float, default=0.0)
    out_bps: Mapped[float] = mapped_column(Float, default=0.0)
    in_util_pct: Mapped[float] = mapped_column(Float, default=0.0)
    out_util_pct: Mapped[float] = mapped_column(Float, default=0.0)
    bw_bps: Mapped[int] = mapped_column(BigInteger, default=0)
    rate_period_sec: Mapped[int] = mapped_column(Integer, default=0)
    raw_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    message: Mapped[str] = mapped_column(String(512), default="")


class PortTrafficEvent(Base):
    """Collect / ops log line for a monitored device (and optional interface)."""

    __tablename__ = "port_traffic_event"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    device_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    target_row_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ifname: Mapped[str] = mapped_column(String(128), default="")
    level: Mapped[str] = mapped_column(String(16), default="error", index=True)  # info|warn|error
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class PortTrafficBoard(Base):
    """Named multi-panel traffic ops wall (cutover / duty view)."""

    __tablename__ = "port_traffic_board"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    remark: Mapped[str] = mapped_column(String(1024), default="")
    cols: Mapped[int] = mapped_column(Integer, default=2)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class PortTrafficPanel(Base):
    """One chart cell on a traffic board."""

    __tablename__ = "port_traffic_panel"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    board_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    range_hours: Mapped[int] = mapped_column(Integer, default=24)
    baseline: Mapped[str] = mapped_column(String(16), default="off")  # off|day|week|shift|custom
    offset_hours: Mapped[int] = mapped_column(Integer, default=0)
    ahead_hours: Mapped[int] = mapped_column(Integer, default=1)  # extend chart past "now" for baseline peek
    baseline_target_id: Mapped[str] = mapped_column(String(64), default="")
    y_mode: Mapped[str] = mapped_column(String(16), default="auto")  # auto|current|util
    ord: Mapped[int] = mapped_column(Integer, default=0)
    col_span: Mapped[int] = mapped_column(Integer, default=1)
    row_span: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
