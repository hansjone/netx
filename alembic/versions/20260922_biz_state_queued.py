"""Add biz_state_task.collect_queued_at for collect queue claim.

Revision ID: 20260922_biz_state_queued
Revises: 20260921_exec_policy
Create Date: 2026-09-22
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260922_biz_state_queued"
down_revision: Union[str, Sequence[str], None] = "20260921_exec_policy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute(
            "ALTER TABLE biz_state_task ADD COLUMN IF NOT EXISTS collect_queued_at TIMESTAMP"
        )
    else:
        # SQLite / others: best-effort
        try:
            op.add_column(
                "biz_state_task",
                __import__("sqlalchemy", fromlist=["Column"]).Column(
                    "collect_queued_at",
                    __import__("sqlalchemy", fromlist=["DateTime"]).DateTime(),
                    nullable=True,
                ),
            )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute("ALTER TABLE biz_state_task DROP COLUMN IF EXISTS collect_queued_at")
    else:
        try:
            op.drop_column("biz_state_task", "collect_queued_at")
        except Exception:
            pass
