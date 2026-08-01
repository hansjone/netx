"""Pydantic schemas for port traffic monitoring API (device-centric)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PortTrafficIfaceIn(BaseModel):
    ifname: str
    if_description: str = ""
    bw_bps: int = 0


class PortTrafficTargetIn(BaseModel):
    """Legacy-shaped input; create/device APIs prefer PortTrafficIfaceIn under one NE."""

    source: Literal["managed", "ume"] = "managed"
    target_id: str = ""
    ne_name: str = ""
    ne_ip: str = ""
    vendor: str = ""
    ifname: str
    if_description: str = ""
    bw_bps: int = 0


class PortTrafficDeviceCreate(BaseModel):
    source: Literal["managed", "ume"]
    ne_id: str = Field(min_length=1, max_length=128)
    ne_name: str = ""
    ne_ip: str = ""
    vendor: str = ""
    note: str = Field(default="", max_length=256)
    interval_sec: int = Field(default=60, ge=15, le=3600)
    retention_days: int = Field(default=7, ge=1, le=90)
    concurrency: int = Field(default=1, ge=1, le=5)
    interfaces: list[PortTrafficIfaceIn] = Field(default_factory=list)
    start_now: bool = False


class PortTrafficDeviceUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=256)
    interval_sec: int | None = Field(default=None, ge=15, le=3600)
    retention_days: int | None = Field(default=None, ge=1, le=90)
    concurrency: int | None = Field(default=None, ge=1, le=5)
    ne_name: str | None = None
    ne_ip: str | None = None
    vendor: str | None = None


class PortTrafficDeviceOut(BaseModel):
    id: str
    source: str
    ne_id: str
    ne_name: str
    ne_ip: str
    vendor: str
    note: str = ""
    status: str
    interval_sec: int
    retention_days: int
    concurrency: int
    collect_running: bool = False
    target_count: int = 0
    active_target_count: int = 0
    last_collect_started_at: datetime | None = None
    last_collect_ended_at: datetime | None = None
    last_error: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PortTrafficTargetOut(BaseModel):
    id: str
    device_id: str
    task_id: str = ""  # alias of device_id for older clients
    series_id: str = ""
    source: str
    target_id: str
    ne_name: str
    ne_ip: str
    vendor: str
    ifname: str
    if_description: str
    bw_bps: int
    status: str
    last_error: str = ""
    last_sample_at: datetime | None = None
    created_at: datetime | None = None


class PortTrafficSeriesOut(BaseModel):
    id: str
    device_id: str
    task_id: str = ""
    title: str
    status: str
    active_target: PortTrafficTargetOut | None = None
    retired_target_count: int = 0
    created_at: datetime | None = None


class PortTrafficInterfacesPut(BaseModel):
    """Replace active interface set for a device (keeps samples for retired/removed)."""

    interfaces: list[PortTrafficIfaceIn]


class PortTrafficReplacePortRequest(BaseModel):
    ifname: str
    if_description: str = ""
    bw_bps: int = 0
    series_title: str | None = Field(default=None, max_length=256)


class DiscoverPortsRequest(BaseModel):
    source: Literal["managed", "ume"]
    id: str


class DiscoverPortItem(BaseModel):
    ifname: str
    attribute: str = ""
    mode: str = ""
    bw_raw: str = ""
    bw_bps: int = 0
    admin: str = ""
    phy: str = ""
    prot: str = ""
    description: str = ""


class DiscoverPortsResponse(BaseModel):
    source: str
    id: str
    ne_name: str = ""
    ne_ip: str = ""
    vendor: str = ""
    vendor_key: str = ""
    ports: list[DiscoverPortItem] = Field(default_factory=list)


class PortTrafficSamplePoint(BaseModel):
    ts: datetime
    in_bps: float
    out_bps: float
    in_util_pct: float
    out_util_pct: float
    bw_bps: int
    rate_period_sec: int = 0
    ts_raw: datetime | None = None


class PortTrafficSamplesOut(BaseModel):
    target: PortTrafficTargetOut
    points: list[PortTrafficSamplePoint] = Field(default_factory=list)


class PortTrafficCompareMeta(BaseModel):
    target_id: str
    baseline: str = "off"
    offset_hours: float = 0
    range_hours: float = 24
    current_target: PortTrafficTargetOut | None = None
    baseline_target: PortTrafficTargetOut | None = None
    baseline_target_id: str = ""


class PortTrafficCompareOut(BaseModel):
    meta: PortTrafficCompareMeta
    current: list[PortTrafficSamplePoint] = Field(default_factory=list)
    baseline: list[PortTrafficSamplePoint] = Field(default_factory=list)


class PortTrafficDashboardOut(BaseModel):
    device_count: int = 0
    running_device_count: int = 0
    active_target_count: int = 0
    sample_count_24h: int = 0
    last_sample_at: datetime | None = None
    # Back-compat aliases for older UI
    task_count: int = 0
    running_task_count: int = 0


class PortTrafficEventOut(BaseModel):
    id: str
    device_id: str
    target_row_id: str = ""
    ifname: str = ""
    level: str = "error"
    message: str = ""
    created_at: datetime | None = None


class PortTrafficEventsOut(BaseModel):
    items: list[PortTrafficEventOut] = Field(default_factory=list)
    total: int = 0
