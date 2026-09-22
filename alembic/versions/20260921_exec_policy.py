"""Add managed_ne.exec_policy for per-NE CLI exec gates.

Revision ID: 20260921_exec_policy
Revises: 20260812_ne_collect_trigger
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260921_exec_policy"
down_revision: Union[str, Sequence[str], None] = "20260812_ne_collect_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from netx_api.schema_patches import apply_hop_schema_safety_net

    apply_hop_schema_safety_net(op.get_bind())


def downgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute("ALTER TABLE managed_ne DROP COLUMN IF EXISTS exec_policy")
    else:
        try:
            op.drop_column("managed_ne", "exec_policy")
        except Exception:
            pass
