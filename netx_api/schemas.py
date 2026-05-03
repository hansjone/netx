from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BatchSummary(BaseModel):
    batch_id: str
    total_rows: int
    success_rows: int
    failed_rows: int
    status: str
    created_at: datetime


class AlarmItem(BaseModel):
    id: int
    batch_id: str
    row_no: int
    alarm_time: datetime
    severity_norm: str
    severity_raw: str
    ne_name: str
    alarm_code: str
    description: str
    ack_state: str


class AlarmQueryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AlarmItem]


class AlarmAggregateBucket(BaseModel):
    key: str
    count: int


class AlarmAggregateResponse(BaseModel):
    group_by: str = Field(pattern="^(severity_norm|alarm_code|ne_name)$")
    buckets: list[AlarmAggregateBucket]


class ImportJobItem(BaseModel):
    id: int
    kind: str
    file_name: str
    batch_id: str | None = None
    ok: bool
    summary: str
    created_at: datetime


class ImportJobListResponse(BaseModel):
    items: list[ImportJobItem]


class AiAnalyzeHistoryItem(BaseModel):
    id: int
    analysis_request_id: str = ""
    batch_id: str = ""
    question: str = ""
    filters: dict = Field(default_factory=dict)
    ok: bool
    answer: str = ""
    error: str = ""
    created_at: datetime


class AiAnalyzeHistoryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AiAnalyzeHistoryItem]
