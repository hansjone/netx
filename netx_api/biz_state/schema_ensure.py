"""Idempotent DDL safety-net for biz_state tables (create_all + ADD COLUMN)."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Connection

from ..schema_patches import _run_sql

_log = logging.getLogger("netx.biz_state.schema")


def apply_biz_state_schema(conn: Connection) -> None:
    """Ensure core tables exist; create_all usually handles this — safety net for brownfield."""
    # Tables are defined on ORM Base; create_all covers new installs.
    # Keep lightweight indexes that older DBs might miss.
    for sql in (
        "CREATE INDEX IF NOT EXISTS ix_biz_state_task_status ON biz_state_task (status)",
        "CREATE INDEX IF NOT EXISTS ix_biz_state_batch_task_id ON biz_state_batch (task_id)",
        "CREATE INDEX IF NOT EXISTS ix_biz_state_batch_command_batch_id ON biz_state_batch_command (batch_id)",
        "CREATE INDEX IF NOT EXISTS ix_biz_state_lldp_neighbor_batch_id ON biz_state_lldp_neighbor (batch_id)",
        "CREATE INDEX IF NOT EXISTS ix_biz_compare_job_status ON biz_compare_job (status)",
        "CREATE INDEX IF NOT EXISTS ix_biz_compare_run_job_id ON biz_compare_run (job_id)",
    ):
        try:
            _run_sql(conn, sql)
        except Exception:
            _log.debug("biz_state schema patch skipped: %s", sql[:80], exc_info=True)
