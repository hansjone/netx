"""Add app_user.prefs JSON for per-user UI preferences.

Revision ID: 20261013_user_prefs
Revises: 20260922_biz_state_queued
Create Date: 2026-10-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_user_prefs"
down_revision: Union[str, Sequence[str], None] = "20260922_biz_state_queued"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute(
            "ALTER TABLE app_user ADD COLUMN IF NOT EXISTS prefs JSON DEFAULT '{}'::json NOT NULL"
        )
        # Ensure NOT NULL + default for brownfield rows that already had a nullable column.
        op.execute("UPDATE app_user SET prefs = '{}'::json WHERE prefs IS NULL")
        op.execute("ALTER TABLE app_user ALTER COLUMN prefs SET DEFAULT '{}'::json")
        op.execute("ALTER TABLE app_user ALTER COLUMN prefs SET NOT NULL")
    else:
        insp = sa.inspect(bind)
        if "app_user" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("app_user")}
            if "prefs" not in cols:
                op.add_column(
                    "app_user",
                    sa.Column("prefs", sa.JSON(), nullable=False, server_default="{}"),
                )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS prefs")
    else:
        try:
            op.drop_column("app_user", "prefs")
        except Exception:
            pass
