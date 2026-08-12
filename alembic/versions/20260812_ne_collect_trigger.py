"""Add ne_collection_job.trigger_mode for scheduled batch collect.

Revision ID: 20260812_ne_collect_trigger
Revises: 20260811_fabric_level
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260812_ne_collect_trigger"
down_revision: Union[str, Sequence[str], None] = "20260811_fabric_level"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from netx_api.schema_patches import apply_collection_schema_safety_net

    apply_collection_schema_safety_net(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute("DROP INDEX IF EXISTS ix_ne_collection_job_trigger_mode")
        op.execute("ALTER TABLE ne_collection_job DROP COLUMN IF EXISTS trigger_mode")
    else:
        try:
            op.drop_column("ne_collection_job", "trigger_mode")
        except Exception:
            pass
