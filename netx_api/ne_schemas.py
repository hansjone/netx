from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .device_types import SUPPORTED_VENDORS

ConnectStatus = Literal["unknown", "testing", "pass", "fail"]


class ManagedNeCreate(BaseModel):
    name: str = ""
    vendor: str
    device_type: str
    ip_address: str
    port: int = 22
    protocol: str = "ssh"
    username: str
    password: str = ""
    tags: str = ""
    remark: str = ""
    hop_enabled: bool = False
    hop_vendor: str = "zte"
    hop_host: str = ""
    hop_port: int = 22
    hop_protocol: str = "ssh"
    hop_username: str = ""
    hop_password: str = ""
    hop_command_template: str = ""
    hop_vrf: str = ""
    hop_target_auth_mode: str = "bastion_managed"

    @field_validator("vendor")
    @classmethod
    def normalize_vendor(cls, v: str) -> str:
        raw = str(v or "").strip()
        if not raw:
            raise ValueError("vendor_required")
        for item in SUPPORTED_VENDORS:
            if item.lower() == raw.lower():
                return item
        return "Other"


class ManagedNeUpdate(BaseModel):
    name: str | None = None
    vendor: str | None = None
    device_type: str | None = None
    ip_address: str | None = None
    port: int | None = None
    protocol: str | None = None
    username: str | None = None
    password: str | None = None
    tags: str | None = None
    remark: str | None = None
    hop_enabled: bool | None = None
    hop_vendor: str | None = None
    hop_host: str | None = None
    hop_port: int | None = None
    hop_protocol: str | None = None
    hop_username: str | None = None
    hop_password: str | None = None
    hop_command_template: str | None = None
    hop_vrf: str | None = None
    hop_target_auth_mode: str | None = None

    @field_validator("vendor")
    @classmethod
    def normalize_vendor_update(cls, v: str | None) -> str | None:
        if v is None:
            return None
        raw = str(v).strip()
        if not raw:
            raise ValueError("vendor_required")
        for item in SUPPORTED_VENDORS:
            if item.lower() == raw.lower():
                return item
        return "Other"


class ManagedNeOut(BaseModel):
    id: str
    name: str
    vendor: str
    device_type: str
    ip_address: str
    port: int
    protocol: str
    username: str
    connect_status: ConnectStatus
    connect_message: str
    connect_detail: str = ""
    connect_tested_at: datetime | None
    tags: str
    remark: str
    hop_enabled: bool = False
    hop_vendor: str = "zte"
    hop_host: str = ""
    hop_port: int = 22
    hop_protocol: str = "ssh"
    hop_username: str = ""
    hop_command_template: str = ""
    hop_vrf: str = ""
    hop_target_auth_mode: str = "bastion_managed"
    created_at: datetime
    updated_at: datetime


class ConnectTestRequest(BaseModel):
    ids: list[str] = Field(min_length=1)


class ManagedNeExecRequest(BaseModel):
    """Run read-only show/display CLI on a managed NE or UME inventory NE (oclaw ops integration)."""

    ne_id: str | None = None
    ume_ne_id: str | None = None
    commands: list[str] = Field(min_length=1, max_length=5)
    read_timeout_sec: int | None = Field(default=None, ge=10, le=120)


class HopProxyConfig(BaseModel):
    """Shared jump-host (proxy) settings applied to one or many NEs."""

    hop_vendor: str = "zte"
    hop_host: str
    hop_port: int = 22
    hop_protocol: str = "ssh"
    hop_username: str
    hop_password: str = ""
    hop_command_template: str = ""
    hop_vrf: str = ""
    hop_target_auth_mode: str = "bastion_managed"


class BatchHopApplyRequest(BaseModel):
    ids: list[str] = Field(min_length=1)
    hop: HopProxyConfig


class BatchAccountConfig(BaseModel):
    username: str = ""
    password: str = ""


class BatchAccountApplyRequest(BaseModel):
    ids: list[str] = Field(min_length=1)
    account: BatchAccountConfig


class ImportFailure(BaseModel):
    row: int
    reason: str


class ImportResult(BaseModel):
    inserted: int
    updated: int
    failed: list[ImportFailure]


class UmeManagedSyncResult(BaseModel):
    inserted: int
    updated: int
    deleted: int
    total_inventory: int


class UmeManagedDeleteResult(BaseModel):
    deleted: int
