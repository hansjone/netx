"""Startup helpers for final topology schema (drop legacy map tables)."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Connection

_log = logging.getLogger("netx.topology.migrate")

_LEGACY_TABLES = ("topology_edge", "topology_node", "topology_map")


def drop_legacy_topology_tables(conn: Connection) -> None:
    """Remove document-style topology_* tables after cutover to fabric/view."""
    dialect = str(getattr(conn.dialect, "name", "") or "").lower()
    for table in _LEGACY_TABLES:
        try:
            if dialect.startswith("postgres"):
                conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
            else:
                conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        except Exception:
            _log.exception("drop legacy topology table failed: %s", table)


def ensure_topology_schema(conn: Connection) -> None:
    drop_legacy_topology_tables(conn)
    dialect = str(getattr(conn.dialect, "name", "") or "").lower()
    # Best-effort column add for existing DBs (create_all won't alter).
    alter_stmts: list[str] = []
    if dialect.startswith("postgres"):
        alter_stmts.append(
            "ALTER TABLE topo_discover_job ADD COLUMN IF NOT EXISTS trigger_mode VARCHAR(32) DEFAULT 'manual'"
        )
    elif dialect.startswith("sqlite"):
        # SQLite: ignore if column already exists.
        alter_stmts.append(
            "ALTER TABLE topo_discover_job ADD COLUMN trigger_mode VARCHAR(32) DEFAULT 'manual'"
        )
    for sql in alter_stmts:
        try:
            conn.execute(text(sql))
        except Exception:
            _log.debug("topology alter skipped/failed: %s", sql[:80], exc_info=True)

    if not dialect.startswith("postgres"):
        return
    stmts = [
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_layer_a ON topo_fabric_edge (layer, a_node_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_layer_b ON topo_fabric_edge (layer, b_node_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_layer_seen ON topo_fabric_edge (layer, last_seen_at)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_active ON topo_fabric_edge (layer) WHERE status = 'active'",
        "CREATE INDEX IF NOT EXISTS ix_topo_view_node_view ON topo_view_node (view_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_topo_fabric_node_managed_nn ON topo_fabric_node (managed_ne_id) WHERE managed_ne_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_topo_fabric_node_ume_nn ON topo_fabric_node (ume_ne_id) WHERE ume_ne_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_topo_discover_job_trigger ON topo_discover_job (trigger_mode)",
    ]
    for sql in stmts:
        try:
            conn.execute(text(sql))
        except Exception:
            _log.exception("ensure topology index failed: %s", sql[:80])
