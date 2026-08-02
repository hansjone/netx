from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType

class UmeSyncJob(Base):
    __tablename__ = "ume_sync_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(32), index=True, default="inventory")
    status: Mapped[str] = mapped_column(String(32), index=True, default="running")
    trigger_mode: Mapped[str] = mapped_column(String(32), default="manual")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
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
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
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
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
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
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
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
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class UmeKeyAlertMonitorConfig(Base):
    """Global monitor options for key alert forwarding (singleton row id=1)."""

    __tablename__ = "ume_key_alert_monitor_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    forward_on_clear: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class UmeKeyAlertForwardLog(Base):
    __tablename__ = "ume_key_alert_forward_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alarm_key: Mapped[str] = mapped_column(Text, index=True)
    action: Mapped[str] = mapped_column(String(32), default="", index=True)
    rule_key: Mapped[str] = mapped_column(String(128), default="", index=True)
    notification_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    forwarded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    oclaw_ok: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(String(512), default="")


class UmeAlarmSubscription(Base):
    """Persisted UME ALARM notification subscription (manual establish/cancel)."""

    __tablename__ = "ume_alarm_subscription"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    subscription_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    wss_uri: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(64), default="ALARM")
    established_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class UmeTokenCache(Base):
    __tablename__ = "ume_token_cache"

    cache_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    token: Mapped[str] = mapped_column(Text, default="")
    expires_at_epoch_s: Mapped[int] = mapped_column(Integer, default=0)
    lock_owner: Mapped[str] = mapped_column(String(128), default="", index=True)
    lock_expires_at_epoch_s: Mapped[int] = mapped_column(Integer, default=0, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
