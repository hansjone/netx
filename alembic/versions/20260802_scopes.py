"""Add app_user.scopes and api_token.scopes for capability RBAC.

Revision ID: 20260802_scopes
Revises:
Create Date: 2026-08-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_scopes"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    # Idempotent adds for brownfield DBs that already ran startup ALTER TABLE.
    if dialect == "postgresql":
        op.execute("ALTER TABLE app_user ADD COLUMN IF NOT EXISTS scopes JSON DEFAULT '[]'")
        op.execute("ALTER TABLE api_token ADD COLUMN IF NOT EXISTS scopes JSON DEFAULT '[]'")
    else:
        insp = sa.inspect(bind)
        if "app_user" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("app_user")}
            if "scopes" not in cols:
                op.add_column("app_user", sa.Column("scopes", sa.JSON(), nullable=True))
        if "api_token" in insp.get_table_names():
            cols = {c["name"] for c in insp.get_columns("api_token")}
            if "scopes" not in cols:
                op.add_column("api_token", sa.Column("scopes", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE api_token DROP COLUMN IF EXISTS scopes")
        op.execute("ALTER TABLE app_user DROP COLUMN IF EXISTS scopes")
    else:
        try:
            op.drop_column("api_token", "scopes")
        except Exception:
            pass
        try:
            op.drop_column("app_user", "scopes")
        except Exception:
            pass
