"""biz_state ORM: tasks, items, bindings, batches, LLDP neighbor rows."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from ..timeutil import utcnow_naive
from ._types import JsonType as _JsonType


class BizStateTask(Base):
    """Per-NE business state monitoring config.

    Multiple tasks per ``(source, ne_id)`` are allowed (e.g. hourly full +
    cutover high-freq port-only).
    """

    __tablename__ = "biz_state_task"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    source: Mapped[str] = mapped_column(String(32), default="managed", index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    ne_ip: Mapped[str] = mapped_column(String(128), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="")
    device_type: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[str] = mapped_column(String(256), default="")
    # "" | portrait | cutover_hf — filter cutover HF in biz-state list
    purpose: Mapped[str] = mapped_column(String(32), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)  # draft|running|paused|stopped
    interval_sec: Mapped[int] = mapped_column(Integer, default=300)
    # Keep snapshots for N calendar days (protected baselines/refs never auto-deleted)
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    # Optional: keep only N batches per past calendar day (purge extras next day). Default off.
    daily_keep_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_keep_count: Mapped[int] = mapped_column(Integer, default=10)
    # Legacy column kept for brownfield reads; unused by new purge path
    retention_batches: Mapped[int] = mapped_column(Integer, default=30)
    collect_running: Mapped[bool] = mapped_column(Boolean, default=False)
    last_collect_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_collect_ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizStateTaskItem(Base):
    """Enabled collect row on a task (catalog profile or custom_raw)."""

    __tablename__ = "biz_state_task_item"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    source_profile_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    kind: Mapped[str] = mapped_column(String(32), default="catalog")  # catalog|custom_raw
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    command_override: Mapped[str] = mapped_column(String(512), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizStateTaskItemBinding(Base):
    """Placeholder binding for parameterized profiles (Phase3 UX; model ready)."""

    __tablename__ = "biz_state_task_item_binding"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    item_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    placeholder: Mapped[str] = mapped_column(String(64), default="")
    value: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizStateBatch(Base):
    """One collect round snapshot header."""

    __tablename__ = "biz_state_batch"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    source: Mapped[str] = mapped_column(String(32), default="managed", index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    ne_name: Mapped[str] = mapped_column(String(256), default="")
    vendor: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)  # running|success|partial|failed
    command_count: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Manual pin: excluded from auto-purge until revoked
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    baseline_marked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Optional display name for compare / cutover pickers
    alias: Mapped[str] = mapped_column(String(128), default="")


class BizStateBatchCommand(Base):
    """Per-command audit inside a batch."""

    __tablename__ = "biz_state_batch_command"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task_item_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    profile_id: Mapped[str] = mapped_column(String(128), default="")
    parser_id: Mapped[str] = mapped_column(String(128), default="")
    metric_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    raw_command: Mapped[str] = mapped_column(String(512), default="")
    params_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    parse_status: Mapped[str] = mapped_column(String(32), default="")  # ok|unmatched|failed|skipped_custom
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    message: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizStateLldpNeighbor(Base):
    """Structured LLDP neighbor rows for a batch (cutover-compare ready)."""

    __tablename__ = "biz_state_lldp_neighbor"
    __table_args__ = (
        UniqueConstraint("batch_id", "local_if", "remote_sys", "remote_if", name="uq_biz_lldp_row"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    batch_command_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    local_if: Mapped[str] = mapped_column(String(128), default="", index=True)
    remote_sys: Mapped[str] = mapped_column(String(256), default="", index=True)
    remote_if: Mapped[str] = mapped_column(String(128), default="")
    remote_ip: Mapped[str] = mapped_column(String(128), default="")
    protocol: Mapped[str] = mapped_column(String(32), default="lldp")
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class BizStateVrfRouteSummary(Base):
    """Per-VRF route source counts for a batch."""

    __tablename__ = "biz_state_vrf_route_summary"
    __table_args__ = (
        UniqueConstraint("batch_id", "vrf", "source", name="uq_biz_vrf_route_row"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    batch_command_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    vrf: Mapped[str] = mapped_column(String(128), default="", index=True)
    source: Mapped[str] = mapped_column(String(64), default="", index=True)
    networks: Mapped[int] = mapped_column(Integer, default=0)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class BizStateMetricRow(Base):
    """Generic structured rows for tabular status metrics (ISIS/ARP/BGP/…)."""

    __tablename__ = "biz_state_metric_row"
    __table_args__ = (
        Index("ix_biz_state_metric_row_batch_metric", "batch_id", "metric_id"),
        Index("ix_biz_state_metric_row_batch_metric_seq", "batch_id", "metric_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    batch_command_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    ne_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    metric_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    data_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class BizStateEvent(Base):
    __tablename__ = "biz_state_event"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    level: Mapped[str] = mapped_column(String(16), default="error", index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class BizStateCommandOverride(Base):
    """Hot-edit overlay for profile display / template / enabled."""

    __tablename__ = "biz_state_command_override"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    profile_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    command_template: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    sample_output: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizCompareTemplate(Base):
    """Compare template: one or more metric sheets with key/iface/compare roles.

    ``metrics_json`` is the source of truth (list of sheet defs). Each sheet has
    ``metric_id`` (collected source table) and optional ``sheet_id`` / ``title`` /
    ``row_filters`` so one source can be split into multiple compare items
    (e.g. bgp_peer → vpnv4/vpnv6). Legacy ``metric_id`` / ``key_fields`` / …
    mirror the first sheet for older rows.
    Empty ``compare_fields`` on a sheet = presence-only (entry set match).
    """

    __tablename__ = "biz_compare_template"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    metric_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    key_fields: Mapped[list] = mapped_column(_JsonType, default=list)
    iface_fields: Mapped[list] = mapped_column(_JsonType, default=list)
    compare_fields: Mapped[list] = mapped_column(_JsonType, default=list)
    ignore_fields: Mapped[list] = mapped_column(_JsonType, default=list)
    metrics_json: Mapped[list] = mapped_column(_JsonType, default=list)
    # Template-owned type aliases: [{"from":"GE","to":"gei"}, ...]
    iface_normalize_json: Mapped[list] = mapped_column(_JsonType, default=list)
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizMonitorTemplate(Base):
    """Cutover/monitor overlay on a BizCompareTemplate (HOW + WHEN rules).

    ``compare_template_id`` selects sheets / field rules.
    ``defaults_json`` / ``sheet_overrides_json`` carry dual-verdict & status semantics.
    ``collect_metric_ids_json`` optional HF collect subset.
    """

    __tablename__ = "biz_monitor_template"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    compare_template_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    collect_metric_ids_json: Mapped[list] = mapped_column(_JsonType, default=list)
    defaults_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    sheet_overrides_json: Mapped[list] = mapped_column(_JsonType, default=list)
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizPortMapping(Base):
    """Named port mapping set for cutover (before_if → after_if)."""

    __tablename__ = "biz_port_mapping"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizPortMappingRow(Base):
    __tablename__ = "biz_port_mapping_row"
    __table_args__ = (
        UniqueConstraint("mapping_id", "before_if", name="uq_biz_port_map_before"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    mapping_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    before_if: Mapped[str] = mapped_column(String(128), default="")
    after_if: Mapped[str] = mapped_column(String(128), default="")


class BizCompareJob(Base):
    """Cutover compare job linking template, port map, and before/after batches."""

    __tablename__ = "biz_compare_job"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    name: Mapped[str] = mapped_column(String(256), default="", index=True)
    template_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    mapping_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    before_task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    after_task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    before_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    after_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    # manual: fixed after_batch; auto: after_batch_id empty → use latest after task batch
    mode: Mapped[str] = mapped_column(String(16), default="manual", index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)  # draft|ready|auto
    # Empty = all template sheets; non-empty = only these sheet_id values
    enabled_sheet_ids: Mapped[list] = mapped_column(_JsonType, default=list)
    note: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class BizCompareRun(Base):
    """One execution of a compare job."""

    __tablename__ = "biz_compare_run"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    job_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    template_id: Mapped[str] = mapped_column(String(64), default="")
    mapping_id: Mapped[str] = mapped_column(String(64), default="")
    before_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    after_batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    metric_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    summary_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    diffs_json: Mapped[list] = mapped_column(_JsonType, default=list)
    mapping_stats_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    message: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, index=True)


class BizCompareDiff(Base):
    """Per-row compare result — supports million-scale sheets with paged queries."""

    __tablename__ = "biz_compare_diff"
    __table_args__ = (
        Index("ix_biz_compare_diff_run_metric_kind", "run_id", "metric_id", "kind"),
        Index("ix_biz_compare_diff_run_metric_seq", "run_id", "metric_id", "seq"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid4().hex)
    run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    metric_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="", index=True)
    key_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    before_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    after_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    mapped_before_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    changes_json: Mapped[dict] = mapped_column(_JsonType, default=dict)
    search_text: Mapped[str] = mapped_column(Text, default="")
