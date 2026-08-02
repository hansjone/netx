from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType

class AlarmBatch(Base):
    __tablename__ = "alarm_batches"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    source_file: Mapped[str] = mapped_column(String(512))
    parser_version: Mapped[str] = mapped_column(String(64), default="zte_alarm_monitor_v1")
    dict_version: Mapped[str] = mapped_column(String(64), default="v1")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    success_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="done")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    alarms: Mapped[list["AlarmNorm"]] = relationship(back_populates="batch")
    errors: Mapped[list["ImportErrorRow"]] = relationship(back_populates="batch")


class AlarmNorm(Base):
    __tablename__ = "alarms_norm"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("alarm_batches.batch_id"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)

    alarm_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    clear_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    severity_raw: Mapped[str] = mapped_column(String(64), default="")
    severity_norm: Mapped[str] = mapped_column(String(32), index=True, default="unknown")
    ne_name: Mapped[str] = mapped_column(String(256), default="", index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    site_name: Mapped[str] = mapped_column(String(256), default="")
    alarm_code: Mapped[str] = mapped_column(String(256), default="", index=True)
    # NOTE: historically we stored `alarm_name` separately, but for ZTE Alarm Monitor exports
    # "Alarm Code Name" is a single column. We keep the DB column for backward compatibility,
    # but APIs/UI are unified on `alarm_code`.
    alarm_name: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    ack_state: Mapped[str] = mapped_column(String(64), default="")
    clear_state: Mapped[str] = mapped_column(String(64), default="")
    relevancy: Mapped[str] = mapped_column(String(128), default="")
    l3vpn_peer_ne: Mapped[str] = mapped_column(String(256), default="")
    service: Mapped[str] = mapped_column(String(256), default="")
    affected_client_service_number: Mapped[int] = mapped_column(Integer, default=0)
    intermittence_count: Mapped[int] = mapped_column(Integer, default=0)
    me_level: Mapped[str] = mapped_column(String(128), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="ZTE")
    source_type: Mapped[str] = mapped_column(String(64), default="gateway_export_excel")
    source_file: Mapped[str] = mapped_column(String(512), default="")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")

    batch: Mapped[AlarmBatch] = relationship(back_populates="alarms")


class ImportErrorRow(Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("alarm_batches.batch_id"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(512))
    raw_json: Mapped[str] = mapped_column(Text, default="{}")

    batch: Mapped[AlarmBatch] = relationship(back_populates="errors")


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # alarms/logs/config
    file_name: Mapped[str] = mapped_column(String(512), default="")
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ok: Mapped[int] = mapped_column(Integer, default=1)  # 1 ok, 0 error
    summary: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class AiAnalyzeHistory(Base):
    __tablename__ = "ai_analyze_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_request_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    question: Mapped[str] = mapped_column(Text, default="")
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    ok: Mapped[int] = mapped_column(Integer, default=1)  # 1 ok, 0 error
    answer: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)

