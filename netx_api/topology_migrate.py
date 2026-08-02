"""Startup helpers for final topology schema (drop legacy map tables)."""

from __future__ import annotations

import logging

from sqlalchemy.engine import Connection

from .schema_patches import _run_sql

_log = logging.getLogger("netx.topology.migrate")

_LEGACY_TABLES = ("topology_edge", "topology_node", "topology_map")


def drop_legacy_topology_tables(conn: Connection) -> None:
    """Remove document-style topology_* tables after cutover to fabric/view."""
    dialect = str(getattr(conn.dialect, "name", "") or "").lower()
    for table in _LEGACY_TABLES:
        if dialect.startswith("postgres"):
            _run_sql(conn, f'DROP TABLE IF EXISTS "{table}" CASCADE')
        else:
            _run_sql(conn, f"DROP TABLE IF EXISTS {table}")


def ensure_topology_schema(conn: Connection) -> None:
    drop_legacy_topology_tables(conn)
    dialect = str(getattr(conn.dialect, "name", "") or "").lower()
    # Best-effort column add for existing DBs (create_all won't alter).
    alter_stmts: list[str] = []
    if dialect.startswith("postgres"):
        alter_stmts.extend(
            [
                "ALTER TABLE topo_discover_job ADD COLUMN IF NOT EXISTS trigger_mode VARCHAR(32) DEFAULT 'manual'",
                "ALTER TABLE lldp_collect_policy ADD COLUMN IF NOT EXISTS history_keep INTEGER DEFAULT 30",
                "ALTER TABLE lldp_collect_policy ADD COLUMN IF NOT EXISTS interval_hours INTEGER DEFAULT 24",
                "ALTER TABLE topo_view ADD COLUMN IF NOT EXISTS folder_id VARCHAR(64)",
                "ALTER TABLE topo_view ADD COLUMN IF NOT EXISTS parent_view_id VARCHAR(64)",
                "ALTER TABLE topo_view ADD COLUMN IF NOT EXISTS kind VARCHAR(32) DEFAULT 'custom'",
                "ALTER TABLE topo_view ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT 'core'",
                "ALTER TABLE topo_view ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0",
                "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS role VARCHAR(32) DEFAULT ''",
                "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS region_folder_id VARCHAR(64)",
                "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS role_source VARCHAR(16) DEFAULT ''",
                "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS region_source VARCHAR(16) DEFAULT ''",
                """
                CREATE TABLE IF NOT EXISTS topo_folder (
                    id VARCHAR(64) PRIMARY KEY,
                    parent_id VARCHAR(64),
                    kind VARCHAR(32) DEFAULT 'region',
                    name VARCHAR(256) DEFAULT '',
                    sort_order INTEGER DEFAULT 0,
                    is_system BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS topo_classify_rule (
                    id VARCHAR(64) PRIMARY KEY,
                    scope VARCHAR(16) DEFAULT 'role',
                    name VARCHAR(256) DEFAULT '',
                    pattern VARCHAR(512) DEFAULT '',
                    match_field VARCHAR(32) DEFAULT 'name',
                    priority INTEGER DEFAULT 100,
                    enabled BOOLEAN DEFAULT TRUE,
                    payload JSONB DEFAULT '{}',
                    remark VARCHAR(512) DEFAULT '',
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """,
            ]
        )
    elif dialect.startswith("sqlite"):
        # SQLite: ignore if column already exists.
        alter_stmts.extend(
            [
                "ALTER TABLE topo_discover_job ADD COLUMN trigger_mode VARCHAR(32) DEFAULT 'manual'",
                "ALTER TABLE lldp_collect_policy ADD COLUMN history_keep INTEGER DEFAULT 30",
                "ALTER TABLE lldp_collect_policy ADD COLUMN interval_hours INTEGER DEFAULT 24",
                "ALTER TABLE topo_view ADD COLUMN folder_id VARCHAR(64)",
                "ALTER TABLE topo_view ADD COLUMN parent_view_id VARCHAR(64)",
                "ALTER TABLE topo_view ADD COLUMN kind VARCHAR(32) DEFAULT 'custom'",
                "ALTER TABLE topo_view ADD COLUMN role VARCHAR(32) DEFAULT 'core'",
                "ALTER TABLE topo_view ADD COLUMN sort_order INTEGER DEFAULT 0",
                "ALTER TABLE topo_fabric_node ADD COLUMN role VARCHAR(32) DEFAULT ''",
                "ALTER TABLE topo_fabric_node ADD COLUMN region_folder_id VARCHAR(64)",
                "ALTER TABLE topo_fabric_node ADD COLUMN role_source VARCHAR(16) DEFAULT ''",
                "ALTER TABLE topo_fabric_node ADD COLUMN region_source VARCHAR(16) DEFAULT ''",
                """
                CREATE TABLE IF NOT EXISTS topo_folder (
                    id VARCHAR(64) PRIMARY KEY,
                    parent_id VARCHAR(64),
                    kind VARCHAR(32) DEFAULT 'region',
                    name VARCHAR(256) DEFAULT '',
                    sort_order INTEGER DEFAULT 0,
                    is_system BOOLEAN DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS topo_classify_rule (
                    id VARCHAR(64) PRIMARY KEY,
                    scope VARCHAR(16) DEFAULT 'role',
                    name VARCHAR(256) DEFAULT '',
                    pattern VARCHAR(512) DEFAULT '',
                    match_field VARCHAR(32) DEFAULT 'name',
                    priority INTEGER DEFAULT 100,
                    enabled INTEGER DEFAULT 1,
                    payload TEXT DEFAULT '{}',
                    remark VARCHAR(512) DEFAULT '',
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """,
            ]
        )
    for sql in alter_stmts:
        _run_sql(conn, sql)

    # Backfill hours from legacy days when hours unset/zero.
    _run_sql(
        conn,
        "UPDATE lldp_collect_policy SET interval_hours = "
        "CASE WHEN COALESCE(interval_hours, 0) <= 0 "
        "THEN GREATEST(1, COALESCE(interval_days, 1)) * 24 "
        "ELSE interval_hours END",
    )
    # SQLite has no GREATEST in older builds — use MAX (no-op if previous applied).
    _run_sql(
        conn,
        "UPDATE lldp_collect_policy SET interval_hours = "
        "CASE WHEN COALESCE(interval_hours, 0) <= 0 "
        "THEN MAX(1, COALESCE(interval_days, 1)) * 24 "
        "ELSE interval_hours END",
    )

    if not dialect.startswith("postgres"):
        return
    stmts = [
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_layer_a ON topo_fabric_edge (layer, a_node_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_layer_b ON topo_fabric_edge (layer, b_node_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_layer_seen ON topo_fabric_edge (layer, last_seen_at)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_edge_active ON topo_fabric_edge (layer) WHERE status = 'active'",
        "CREATE INDEX IF NOT EXISTS ix_topo_view_node_view ON topo_view_node (view_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_view_folder ON topo_view (folder_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_view_parent ON topo_view (parent_view_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_view_kind ON topo_view (kind)",
        "CREATE INDEX IF NOT EXISTS ix_topo_folder_parent ON topo_folder (parent_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_role ON topo_fabric_node (role)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_region ON topo_fabric_node (region_folder_id)",
        "CREATE INDEX IF NOT EXISTS ix_topo_classify_rule_scope ON topo_classify_rule (scope, enabled, priority)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_topo_fabric_node_managed_nn ON topo_fabric_node (managed_ne_id) WHERE managed_ne_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_topo_fabric_node_ume_nn ON topo_fabric_node (ume_ne_id) WHERE ume_ne_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_topo_discover_job_trigger ON topo_discover_job (trigger_mode)",
    ]
    for sql in stmts:
        _run_sql(conn, sql)
