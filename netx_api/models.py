from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UmeSyncJob(Base):
    __tablename__ = "ume_sync_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(32), index=True, default="inventory")
    status: Mapped[str] = mapped_column(String(32), index=True, default="running")
    trigger_mode: Mapped[str] = mapped_column(String(32), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    pulled_count: Mapped[int] = mapped_column(Integer, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(String(1024), default="")
    details_json: Mapped[str] = mapped_column(Text, default="{}")


class UmeAlarmBatch(Base):
    __tablename__ = "ume_alarm_batches"

    batch_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    kind: Mapped[str] = mapped_column(String(16), index=True, default="current")  # current/history
    status: Mapped[str] = mapped_column(String(32), default="done")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    success_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str] = mapped_column(String(1024), default="")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class UmeInventoryNE(Base):
    __tablename__ = "ume_inventory_ne"

    ne_id: Mapped[str] = mapped_column(String(128), primary_key=True, comment="网元uuid")
    ne_name: Mapped[str] = mapped_column(String(256), default="", index=True, comment="资源名称")
    user_label: Mapped[str] = mapped_column(String(256), default="", index=True, comment="用户标签")
    ip_address: Mapped[str] = mapped_column(String(128), default="", index=True, comment="网元IPv4地址")
    ipv6_address: Mapped[str] = mapped_column(String(128), default="", index=True, comment="IPv6地址")
    ne_type: Mapped[str] = mapped_column(String(128), default="", comment="网元类型")
    device_level: Mapped[str] = mapped_column(String(64), default="", comment="网元层次")
    host_name: Mapped[str] = mapped_column(String(256), default="", comment="主机名称")
    location: Mapped[str] = mapped_column(String(512), default="")
    hardware_version: Mapped[str] = mapped_column(String(128), default="", comment="硬件版本")
    loopback: Mapped[str] = mapped_column(String(128), default="", comment="业务环回IP(IPv4)")
    consistent_state: Mapped[str] = mapped_column(String(64), default="", comment="数据一致性状态")
    interface_version: Mapped[str] = mapped_column(String(128), default="", comment="网元接口版本号")
    mac: Mapped[str] = mapped_column(String(128), default="", comment="设备机架MAC地址")
    admin_status: Mapped[str] = mapped_column(String(64), default="", comment="管理状态")
    address_type: Mapped[str] = mapped_column(String(64), default="", comment="管理地址类型(1:IPv4,2:IPv6)")
    connection_status: Mapped[str] = mapped_column(String(64), default="", comment="连接状态")
    maintain_status: Mapped[str] = mapped_column(String(64), default="", comment="工程状态")
    net_mask: Mapped[str] = mapped_column(String(128), default="", comment="管理IPv4掩码")
    create_time: Mapped[str] = mapped_column(String(64), default="")
    creator: Mapped[str] = mapped_column(String(128), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="ZTE", comment="网元提供商")
    source_type: Mapped[str] = mapped_column(String(64), default="ume_restconf")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class UmeAlarmCurrent(Base):
    __tablename__ = "ume_alarms_current"

    alarm_key: Mapped[str] = mapped_column(Text, primary_key=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    host_name: Mapped[str] = mapped_column(String(256), default="", index=True, comment="网元主机名(同步时从inventory联表写入)")
    object_name: Mapped[str] = mapped_column(Text, default="", index=True)
    event_type: Mapped[str] = mapped_column(Text, default="")
    native_probable_cause: Mapped[str] = mapped_column(Text, default="")
    perceived_severity: Mapped[str] = mapped_column(Text, default="", index=True)
    is_cleared: Mapped[str] = mapped_column(Text, default="", index=True)
    time_created: Mapped[str] = mapped_column(Text, default="", index=True)
    root_cause_alarm_indication: Mapped[str] = mapped_column(Text, default="")
    notification_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class UmeAlarmHistory(Base):
    __tablename__ = "ume_alarms_history"

    alarm_key: Mapped[str] = mapped_column(Text, primary_key=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    host_name: Mapped[str] = mapped_column(String(256), default="", index=True, comment="网元主机名(同步时从inventory联表写入)")
    object_name: Mapped[str] = mapped_column(Text, default="", index=True)
    event_type: Mapped[str] = mapped_column(Text, default="")
    native_probable_cause: Mapped[str] = mapped_column(Text, default="")
    perceived_severity: Mapped[str] = mapped_column(Text, default="", index=True)
    is_cleared: Mapped[str] = mapped_column(Text, default="", index=True)
    time_created: Mapped[str] = mapped_column(Text, default="", index=True)
    root_cause_alarm_indication: Mapped[str] = mapped_column(Text, default="")
    notification_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class UmeKeyAlertRule(Base):
    """Key alert rule matched by UME notificationId."""

    __tablename__ = "ume_key_alert_rule"

    notification_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    forward_on_clear: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UmeKeyAlertForwardLog(Base):
    __tablename__ = "ume_key_alert_forward_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alarm_key: Mapped[str] = mapped_column(Text, index=True)
    action: Mapped[str] = mapped_column(String(32), default="", index=True)
    notification_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    forwarded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    oclaw_ok: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(512), default="")


class UmeAlarmSubscription(Base):
    """Persisted UME ALARM notification subscription (manual establish/cancel)."""

    __tablename__ = "ume_alarm_subscription"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    subscription_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    wss_uri: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(64), default="ALARM")
    established_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UmeTokenCache(Base):
    __tablename__ = "ume_token_cache"

    cache_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    token: Mapped[str] = mapped_column(Text, default="")
    expires_at_epoch_s: Mapped[int] = mapped_column(Integer, default=0)
    lock_owner: Mapped[str] = mapped_column(String(128), default="", index=True)
    lock_expires_at_epoch_s: Mapped[int] = mapped_column(Integer, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ManagedNE(Base):
    """Locally managed network element (SSH/Telnet), independent of UME inventory."""

    __tablename__ = "managed_ne"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="Other", index=True)
    device_type: Mapped[str] = mapped_column(String(128), default="")
    ip_address: Mapped[str] = mapped_column(String(128), unique=True, index=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class NeCollectionJob(Base):
    """Batch CLI collection job over managed NEs."""

    __tablename__ = "ne_collection_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    title: Mapped[str] = mapped_column(String(256), default="")
    commands: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    ne_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class NeCollectionRun(Base):
    """Per-NE execution within a collection job."""

    __tablename__ = "ne_collection_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    ne_id: Mapped[str] = mapped_column(String(64), index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    message: Mapped[str] = mapped_column(String(1024), default="")
    output_rel_path: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
