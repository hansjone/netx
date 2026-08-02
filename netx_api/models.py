from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .db import Base

# JSONB on Postgres; plain JSON elsewhere (unit tests / sqlite).
_JsonType = JSON().with_variant(JSONB(), "postgresql")


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


class UmeKeyAlertMonitorConfig(Base):
    """Global monitor options for key alert forwarding (singleton row id=1)."""

    __tablename__ = "ume_key_alert_monitor_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forward_on_clear: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UmeKeyAlertRule(Base):
    """Key alert rule matched by UME notificationId or alarm description keyword."""

    __tablename__ = "ume_key_alert_rule"

    notification_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    match_type: Mapped[str] = mapped_column(String(32), default="notification_id", index=True)
    match_value: Mapped[str] = mapped_column(String(256), default="", index=True)
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    forward_on_clear: Mapped[int] = mapped_column(Integer, default=0)
    label: Mapped[str] = mapped_column(String(256), default="")
    ne_types: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UmeKeyAlertForwardLog(Base):
    __tablename__ = "ume_key_alert_forward_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alarm_key: Mapped[str] = mapped_column(Text, index=True)
    action: Mapped[str] = mapped_column(String(32), default="", index=True)
    rule_key: Mapped[str] = mapped_column(String(128), default="", index=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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


class TopoFabricNode(Base):
    """Global topology fabric node (inventory-aligned). Target scale ~50k."""

    __tablename__ = "topo_fabric_node"
    __table_args__ = (
        UniqueConstraint("managed_ne_id", name="uq_topo_fabric_node_managed_ne_id"),
        UniqueConstraint("ume_ne_id", name="uq_topo_fabric_node_ume_ne_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    managed_ne_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ume_ne_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    ip: Mapped[str] = mapped_column(String(128), default="", index=True)
    vendor: Mapped[str] = mapped_column(String(64), default="")
    device_type: Mapped[str] = mapped_column(String(64), default="")
    # Classify tags (regex rules / manual). role: core|aggregation|access|unknown|""
    role: Mapped[str] = mapped_column(String(32), default="", index=True)
    region_folder_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # rule | manual | ""
    role_source: Mapped[str] = mapped_column(String(16), default="")
    region_source: Mapped[str] = mapped_column(String(16), default="")
    attrs: Mapped[dict] = mapped_column(_JsonType, default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TopoClassifyRule(Base):
    """Regex rules to assign fabric role or region from NE name/IP."""

    __tablename__ = "topo_classify_rule"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    # role | region
    scope: Mapped[str] = mapped_column(String(16), default="role", index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    pattern: Mapped[str] = mapped_column(String(512), default="")
    # name | ip | name_ip
    match_field: Mapped[str] = mapped_column(String(32), default="name")
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # role: {role}; region: {folder_id} or {region_name_from_group}
    payload: Mapped[dict] = mapped_column(_JsonType, default=dict)
    remark: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TopoFabricEdge(Base):
    """Global fabric link. Target scale ~1M; layer reserved for future BGP/tunnel/l2vpn."""

    __tablename__ = "topo_fabric_edge"
    __table_args__ = (
        UniqueConstraint(
            "layer",
            "a_node_id",
            "b_node_id",
            "a_port",
            "b_port",
            name="uq_topo_fabric_edge_endpoints",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    # physical | bgp | tunnel | l2vpn (P1 writes physical only)
    layer: Mapped[str] = mapped_column(String(32), default="physical", index=True)
    a_node_id: Mapped[str] = mapped_column(String(64), index=True)
    b_node_id: Mapped[str] = mapped_column(String(64), index=True)
    a_port: Mapped[str] = mapped_column(String(128), default="")
    b_port: Mapped[str] = mapped_column(String(128), default="")
    # lldp | manual | stale
    source: Mapped[str] = mapped_column(String(32), default="lldp", index=True)
    # active | stale
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    attrs: Mapped[dict] = mapped_column(_JsonType, default=dict)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TopoFolder(Base):
    """Grouping node for topology tree (root / region). Not a canvas."""

    __tablename__ = "topo_folder"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # root | region
    kind: Mapped[str] = mapped_column(String(32), default="region", index=True)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TopoView(Base):
    """Topology canvas under a site/region (physical or custom; flat siblings)."""

    __tablename__ = "topo_view"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    folder_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Legacy nesting column; unused (always null after vendor-model migration).
    parent_view_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # physical | custom
    kind: Mapped[str] = mapped_column(String(32), default="custom", index=True)
    # Optional filter preset label (legacy core|aggregation|access); not a tree level.
    role: Mapped[str] = mapped_column(String(32), default="core", index=True)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    remark: Mapped[str] = mapped_column(String(1024), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # { layer?, status?, membership?: {...} }
    filter: Mapped[dict] = mapped_column(_JsonType, default=dict)
    viewport: Mapped[dict] = mapped_column(_JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TopoViewNode(Base):
    """Node placement on a view; no link facts."""

    __tablename__ = "topo_view_node"
    __table_args__ = (UniqueConstraint("view_id", "fabric_node_id", name="uq_topo_view_node"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    view_id: Mapped[str] = mapped_column(String(64), index=True)
    fabric_node_id: Mapped[str] = mapped_column(String(64), index=True)
    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String(256), default="")
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TopoViewEdgeStyle(Base):
    """Optional per-view edge style override."""

    __tablename__ = "topo_view_edge_style"
    __table_args__ = (UniqueConstraint("view_id", "fabric_edge_id", name="uq_topo_view_edge_style"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    view_id: Mapped[str] = mapped_column(String(64), index=True)
    fabric_edge_id: Mapped[str] = mapped_column(String(64), index=True)
    stroke_color: Mapped[str] = mapped_column(String(32), default="")
    stroke_width: Mapped[int] = mapped_column(Integer, default=0)
    line_style: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LldpCollectPolicy(Base):
    """Singleton policy for periodic fabric LLDP collect (id=1)."""

    __tablename__ = "lldp_collect_policy"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=1)  # legacy; prefer interval_hours
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    concurrency: Mapped[int] = mapped_column(Integer, default=4)
    scope_mode: Mapped[str] = mapped_column(String(32), default="all")  # all | selected
    selected_targets: Mapped[list] = mapped_column(_JsonType, default=list)
    auto_add_unmatched: Mapped[bool] = mapped_column(Boolean, default=True)
    # Keep N finished discover jobs (items + raw_preview); 0 = keep none finished.
    history_keep: Mapped[int] = mapped_column(Integer, default=30)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TopoDiscoverJob(Base):
    """Async LLDP discovery job over inventory / NE ids."""

    __tablename__ = "topo_discover_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    # all_inventory | ne_ids
    scope: Mapped[str] = mapped_column(String(32), default="ne_ids", index=True)
    # manual | schedule | topology (ad-hoc from canvas)
    trigger_mode: Mapped[str] = mapped_column(String(32), default="manual", index=True)
    ne_ids_json: Mapped[list] = mapped_column(_JsonType, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    edges_added: Mapped[int] = mapped_column(Integer, default=0)
    edges_updated: Mapped[int] = mapped_column(Integer, default=0)
    edges_stale: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class TopoDiscoverJobItem(Base):
    """Per-NE result row for a discover job."""

    __tablename__ = "topo_discover_job_item"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    ume_ne_id: Mapped[str] = mapped_column(String(128), default="")
    fabric_node_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    command: Mapped[str] = mapped_column(String(256), default="")
    neighbors: Mapped[int] = mapped_column(Integer, default=0)
    edges_added: Mapped[int] = mapped_column(Integer, default=0)
    edges_updated: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_count: Mapped[int] = mapped_column(Integer, default=0)
    unmatched_json: Mapped[list] = mapped_column(_JsonType, default=list)
    parser_key: Mapped[str] = mapped_column(String(64), default="")
    parser_stub: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(String(1024), default="")
    raw_preview: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TopoFabricStats(Base):
    """Cached fabric counters for summary (avoid COUNT on 1M edges)."""

    __tablename__ = "topo_fabric_stats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default="global")
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_active: Mapped[int] = mapped_column(Integer, default=0)
    edge_stale: Mapped[int] = mapped_column(Integer, default=0)
    last_discover_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppUser(Base):
    """Local netx application user (login account)."""

    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(32), default="user", index=True)  # admin | user
    # Optional capability override; empty => role defaults (see auth_scopes).
    scopes: Mapped[list] = mapped_column(_JsonType, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """Application audit trail for authenticated (and auth) actions."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor_user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    actor_username: Mapped[str] = mapped_column(String(128), default="", index=True)
    action: Mapped[str] = mapped_column(String(128), default="", index=True)
    method: Mapped[str] = mapped_column(String(16), default="")
    path: Mapped[str] = mapped_column(String(512), default="", index=True)
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    client_ip: Mapped[str] = mapped_column(String(128), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    detail: Mapped[dict] = mapped_column(_JsonType, default=dict)


class ApiToken(Base):
    """Long-lived API token (MCP/scripts); hashed at rest."""

    __tablename__ = "api_token"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(128), default="")
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    # Capability subset; empty inherits owner user scopes (then intersected).
    scopes: Mapped[list] = mapped_column(_JsonType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
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
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    cycle_id: Mapped[str] = mapped_column(String(64), default="")
    task_id: Mapped[str] = mapped_column(String(64), default="")


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
    interval_sec: Mapped[int] = mapped_column(Integer, default=60)
    retention_days: Mapped[int] = mapped_column(Integer, default=7)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    collect_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_collect_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_collect_ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Back-compat alias while callers migrate.
PortTrafficTask = PortTrafficDevice


class PortTrafficSeries(Base):
    """Logical port (business link) that survives physical NE/if replacement."""

    __tablename__ = "port_traffic_series"
    __table_args__ = (UniqueConstraint("device_id", "title", name="uq_port_traffic_series_title"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    device_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)  # active|disabled
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def task_id(self) -> str:
        return str(self.device_id or "")

    @task_id.setter
    def task_id(self, value: str) -> None:
        self.device_id = str(value or "")


class PortTrafficTarget(Base):
    """Monitored interface under a device monitoring config."""

    __tablename__ = "port_traffic_target"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    device_id: Mapped[str] = mapped_column(String(64), default="", index=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def task_id(self) -> str:
        return str(self.device_id or "")

    @task_id.setter
    def task_id(self, value: str) -> None:
        self.device_id = str(value or "")


class PortTrafficSample(Base):
    """Time-series sample for a monitored interface."""

    __tablename__ = "port_traffic_sample"
    __table_args__ = (UniqueConstraint("target_row_id", "ts", name="uq_port_traffic_sample_ts"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    target_row_id: Mapped[str] = mapped_column(String(64), index=True)
    series_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class PortTrafficBoard(Base):
    """Named multi-panel traffic ops wall (cutover / duty view)."""

    __tablename__ = "port_traffic_board"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    remark: Mapped[str] = mapped_column(String(1024), default="")
    cols: Mapped[int] = mapped_column(Integer, default=2)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    updated_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
