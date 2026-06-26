from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .device_types import SUPPORTED_VENDORS


class CliConnectProfileCreate(BaseModel):
    name: str
    username: str
    password: str = ""
    port: int = 22
    protocol: str = "ssh"
    device_type_default: str = "zte_zxros"
    vendor_default: str = "ZTE"
    ne_type_rules: str = ""
    is_default: bool = False
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

    @field_validator("vendor_default")
    @classmethod
    def normalize_vendor(cls, v: str) -> str:
        raw = str(v or "").strip()
        if not raw:
            return "ZTE"
        for item in SUPPORTED_VENDORS:
            if item.lower() == raw.lower():
                return item
        return "Other"


class CliConnectProfileUpdate(BaseModel):
    name: str | None = None
    username: str | None = None
    password: str | None = None
    port: int | None = None
    protocol: str | None = None
    device_type_default: str | None = None
    vendor_default: str | None = None
    ne_type_rules: str | None = None
    is_default: bool | None = None
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


class CliConnectProfileOut(BaseModel):
    id: str
    name: str
    is_default: bool
    username: str
    port: int
    protocol: str
    device_type_default: str
    vendor_default: str
    ne_type_rules: str
    hop_enabled: bool
    hop_vendor: str
    hop_host: str
    hop_port: int
    hop_protocol: str
    hop_username: str
    hop_command_template: str
    hop_vrf: str
    hop_target_auth_mode: str
    created_at: datetime
    updated_at: datetime


class UmeCliOverrideUpdate(BaseModel):
    profile_id: str | None = None
    username_override: str | None = None
    device_type_override: str | None = None
    vendor_override: str | None = None


class UmeCliOverrideOut(BaseModel):
    ume_ne_id: str
    profile_id: str | None
    username_override: str
    device_type_override: str
    vendor_override: str
    connect_status: str
    connect_message: str
    connect_detail: str
    connect_tested_at: datetime | None
    updated_at: datetime


class UmeConnectTestRequest(BaseModel):
    ume_ne_ids: list[str] = Field(min_length=1)


class CliTargetOut(BaseModel):
    source: str
    id: str
    ume_ne_id: str | None = None
    name: str
    ip_address: str
    ne_type: str = ""
    vendor: str = ""
    device_type: str = ""
    connect_status: str = "unknown"
    cli_profile_ready: bool = False
