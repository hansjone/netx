"""Add topo_fabric_node.level for layout classification.

Revision ID: 20260811_fabric_level
Revises: 20260806_auth_refresh
Create Date: 2026-08-11
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20260811_fabric_level"
down_revision: Union[str, Sequence[str], None] = "20260806_auth_refresh"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute(
            "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS level DOUBLE PRECISION"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_level ON topo_fabric_node (level)"
        )
    else:
        # SQLite / others: best-effort
        try:
            op.execute("ALTER TABLE topo_fabric_node ADD COLUMN level REAL")
        except Exception:
            pass
        try:
            op.execute(
                "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_level ON topo_fabric_node (level)"
            )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = str(getattr(bind.dialect, "name", "") or "").lower()
    if dialect.startswith("postgres"):
        op.execute("DROP INDEX IF EXISTS ix_topo_fabric_node_level")
        op.execute("ALTER TABLE topo_fabric_node DROP COLUMN IF EXISTS level")
    else:
        try:
            op.drop_column("topo_fabric_node", "level")
        except Exception:
            pass
