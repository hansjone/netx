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
    password: str
    tags: str = ""
    remark: str = ""

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
    connect_tested_at: datetime | None
    tags: str
    remark: str
    created_at: datetime
    updated_at: datetime


class ConnectTestRequest(BaseModel):
    ids: list[str] = Field(min_length=1)


class ImportFailure(BaseModel):
    row: int
    reason: str


class ImportResult(BaseModel):
    inserted: int
    updated: int
    failed: list[ImportFailure]
