"""Apply shared brownfield schema patches (legacy startup DDL).

Revision ID: 20260802_legacy
Revises: 20260802_scopes
Create Date: 2026-08-02

Idempotent: safe on DBs that already received startup ALTER TABLE.
Fresh installs: prefer ``create_all`` then ``alembic upgrade head``.
Brownfield already patched: ``alembic stamp head`` then set
``NETX_SKIP_LEGACY_STARTUP_DDL=true``.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260802_legacy"
down_revision: Union[str, Sequence[str], None] = "20260802_scopes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from netx_api.schema_patches import (
        apply_auth_schema_patches,
        apply_domain_schema_patches,
        apply_key_alert_schema_patches,
    )

    bind = op.get_bind()
    apply_auth_schema_patches(bind)
    apply_key_alert_schema_patches(conn=bind)
    apply_domain_schema_patches(bind)


def downgrade() -> None:
    # Brownfield additive patches are not safely reversible.
    pass
