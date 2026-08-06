"""Create auth_session table for revocable JWT logins.

Revision ID: 20260806_auth_session
Revises: 20260802_legacy
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260806_auth_session"
down_revision: Union[str, Sequence[str], None] = "20260802_legacy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from netx_api.schema_patches import apply_auth_schema_patches

    apply_auth_schema_patches(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("DROP TABLE IF EXISTS auth_session")
    else:
        try:
            op.drop_table("auth_session")
        except Exception:
            pass
