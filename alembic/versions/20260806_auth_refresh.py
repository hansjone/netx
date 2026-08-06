"""Add refresh token columns on auth_session.

Revision ID: 20260806_auth_refresh
Revises: 20260806_auth_session
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260806_auth_refresh"
down_revision: Union[str, Sequence[str], None] = "20260806_auth_session"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from netx_api.schema_patches import apply_auth_schema_patches

    apply_auth_schema_patches(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE auth_session DROP COLUMN IF EXISTS refresh_expires_at")
        op.execute("ALTER TABLE auth_session DROP COLUMN IF EXISTS refresh_token_hash")
    else:
        try:
            op.drop_column("auth_session", "refresh_expires_at")
        except Exception:
            pass
        try:
            op.drop_column("auth_session", "refresh_token_hash")
        except Exception:
            pass
