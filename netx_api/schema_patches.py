"""Idempotent brownfield schema patches (shared by API startup and Alembic).

Historically these lived inline in ``main.on_startup``. Prefer:

1. ``alembic upgrade head`` (applies this module via revision)
2. ``NETX_SKIP_LEGACY_STARTUP_DDL=true`` so API startup does not re-run the big ALTER block

``create_all`` still creates missing tables from ORM metadata; these patches add/alter
columns and indexes that ``create_all`` will not evolve on existing DBs.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

_log = logging.getLogger("netx.schema_patches")


def _dialect_name(conn: Connection) -> str:
    return str(getattr(conn.dialect, "name", "") or "").lower()


def _normalize_ddl_for_dialect(conn: Connection, sql: str) -> str:
    """SQLite lacks ``ADD COLUMN IF NOT EXISTS`` (PG 9.5+/11+). Strip for quiet retry."""
    stmt = str(sql or "").strip()
    if not stmt:
        return stmt
    if _dialect_name(conn) == "sqlite":
        # Keep CREATE/INDEX IF NOT EXISTS; only ALTER TABLE … ADD COLUMN needs help.
        upper = stmt.upper()
        if "ADD COLUMN IF NOT EXISTS" in upper:
            stmt = re.sub(
                r"(?i)\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b",
                "ADD COLUMN",
                stmt,
                count=1,
            )
    return stmt


def _run_sql(conn: Connection, sql: str, *, quiet: bool = True) -> None:
    """Run one DDL/DML statement.

    On PostgreSQL a failed statement aborts the whole transaction. Optional
    patches must use a SAVEPOINT (``begin_nested``) so Alembic can still stamp
    ``alembic_version`` after a best-effort ALTER fails.
    """
    stmt = _normalize_ddl_for_dialect(conn, sql)
    if not stmt:
        return

    def _exec() -> None:
        if hasattr(conn, "exec_driver_sql"):
            conn.exec_driver_sql(stmt)
        else:
            conn.execute(text(stmt))

    if not quiet:
        _exec()
        return

    nested = None
    try:
        nested = conn.begin_nested()
    except Exception:
        nested = None
    try:
        _exec()
        if nested is not None:
            nested.commit()
    except Exception:
        if nested is not None:
            try:
                nested.rollback()
            except Exception:
                pass
        _log.debug("schema patch skipped/failed: %s", stmt[:120], exc_info=True)


def _run_optional_block(conn: Connection, label: str, fn) -> None:
    """Run a block of optional DDL inside a SAVEPOINT."""
    nested = None
    try:
        nested = conn.begin_nested()
    except Exception:
        nested = None
    try:
        fn(conn)
        if nested is not None:
            nested.commit()
    except Exception:
        if nested is not None:
            try:
                nested.rollback()
            except Exception:
                pass
        _log.debug("schema block skipped/failed: %s", label, exc_info=True)


def apply_auth_schema_patches(conn: Connection) -> None:
    """Columns required before auth bootstrap (safe to run even in Alembic mode)."""
    _run_sql(
        conn,
        "ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE",
    )
    _run_sql(conn, "ALTER TABLE app_user ADD COLUMN IF NOT EXISTS scopes JSON DEFAULT '[]'")
    _run_sql(conn, "ALTER TABLE api_token ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP")
    _run_sql(conn, "ALTER TABLE api_token ADD COLUMN IF NOT EXISTS scopes JSON DEFAULT '[]'")
    _run_sql(
        conn,
        """
        CREATE TABLE IF NOT EXISTS auth_session (
            id VARCHAR(64) PRIMARY KEY,
            user_id VARCHAR(64) NOT NULL,
            created_at TIMESTAMP,
            expires_at TIMESTAMP,
            revoked_at TIMESTAMP,
            client_ip VARCHAR(128) DEFAULT '',
            user_agent VARCHAR(512) DEFAULT '',
            last_seen_at TIMESTAMP
        )
        """,
    )
    _run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_auth_session_user_id ON auth_session (user_id)")
    _run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_auth_session_expires_at ON auth_session (expires_at)")
    _run_sql(conn, "CREATE INDEX IF NOT EXISTS ix_auth_session_revoked_at ON auth_session (revoked_at)")
    _run_sql(
        conn,
        "ALTER TABLE auth_session ADD COLUMN IF NOT EXISTS refresh_token_hash VARCHAR(128) DEFAULT ''",
    )
    _run_sql(
        conn,
        "ALTER TABLE auth_session ADD COLUMN IF NOT EXISTS refresh_expires_at TIMESTAMP",
    )
    _run_sql(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_auth_session_refresh_token_hash ON auth_session (refresh_token_hash)",
    )


def apply_topology_schema_safety_net(conn: Connection) -> None:
    """Always-on topology columns (Alembic stamp-without-upgrade must not skip these)."""
    _run_sql(
        conn,
        "ALTER TABLE topo_folder ADD COLUMN IF NOT EXISTS external_ref VARCHAR(128) DEFAULT ''",
    )
    _run_sql(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_topo_folder_external_ref ON topo_folder (external_ref)",
    )
    _run_sql(conn, "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS world_x DOUBLE PRECISION")
    _run_sql(conn, "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS world_y DOUBLE PRECISION")
    _run_sql(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_world_x ON topo_fabric_node (world_x)",
    )
    _run_sql(
        conn,
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_world_y ON topo_fabric_node (world_y)",
    )
    # UME link ifnames — required by topology sync/apply; missing when DB predates the columns
    # or Alembic was stamped head without running domain patches.
    _run_sql(
        conn,
        "ALTER TABLE ume_topo_link ADD COLUMN IF NOT EXISTS a_ifname VARCHAR(128) DEFAULT ''",
    )
    _run_sql(
        conn,
        "ALTER TABLE ume_topo_link ADD COLUMN IF NOT EXISTS z_ifname VARCHAR(128) DEFAULT ''",
    )


def apply_key_alert_schema_patches(
    engine: Engine | None = None,
    *,
    conn: Connection | None = None,
) -> None:
    """Evolve ume_key_alert_rule (isolated tx when using engine; in-place when given conn)."""
    from .key_alert_config import invalidate_key_alert_config_cache

    steps = [
        (
            "add match_type",
            "ALTER TABLE ume_key_alert_rule ADD COLUMN IF NOT EXISTS match_type VARCHAR(32) DEFAULT 'notification_id'",
        ),
        (
            "add match_value",
            "ALTER TABLE ume_key_alert_rule ADD COLUMN IF NOT EXISTS match_value VARCHAR(256) DEFAULT ''",
        ),
        (
            "create monitor_config",
            """
            CREATE TABLE IF NOT EXISTS ume_key_alert_monitor_config (
                id INTEGER PRIMARY KEY,
                forward_on_clear INTEGER DEFAULT 0,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
            )
            """,
        ),
        (
            "seed monitor_config",
            "INSERT INTO ume_key_alert_monitor_config (id, forward_on_clear, updated_at) "
            "VALUES (1, 0, NOW()) ON CONFLICT (id) DO NOTHING",
        ),
        (
            "backfill match_value from notification_id",
            "UPDATE ume_key_alert_rule SET match_value = notification_id "
            "WHERE (match_value IS NULL OR match_value = '') "
            "AND NOT starts_with(notification_id, 'kw:')",
        ),
        (
            "backfill keyword rules",
            "UPDATE ume_key_alert_rule SET match_type = 'keyword', match_value = SUBSTRING(notification_id FROM 4) "
            "WHERE starts_with(notification_id, 'kw:') "
            "AND (match_type IS NULL OR match_type = '' OR match_type = 'notification_id')",
        ),
        (
            "migrate forward_on_clear to global config",
            "UPDATE ume_key_alert_monitor_config SET forward_on_clear = 1, updated_at = NOW() "
            "WHERE id = 1 AND EXISTS (SELECT 1 FROM ume_key_alert_rule WHERE forward_on_clear = 1)",
        ),
        (
            "add forward_log rule_key",
            "ALTER TABLE ume_key_alert_forward_log ADD COLUMN IF NOT EXISTS rule_key VARCHAR(128) DEFAULT ''",
        ),
        (
            "add rule ne_types",
            "ALTER TABLE ume_key_alert_rule ADD COLUMN IF NOT EXISTS ne_types TEXT DEFAULT '[]'",
        ),
    ]
    if conn is not None:
        for _label, sql in steps:
            _run_sql(conn, sql)
    else:
        if engine is None:
            raise ValueError("engine or conn required")
        for label, sql in steps:
            try:
                with engine.begin() as c:
                    _run_sql(c, sql, quiet=False)
            except Exception:
                _log.exception("ume_key_alert_rule schema migration failed at %s", label)
    invalidate_key_alert_config_cache()


def apply_domain_schema_patches(conn: Connection) -> None:
    """Port traffic, topology, collection, config_sync, and historical column evolves."""
    from .port_traffic_migrate import ensure_port_traffic_series_schema
    from .topology_migrate import ensure_topology_schema

    # Never let optional ensure_* poison the Alembic transaction.
    _run_optional_block(conn, "port_traffic", ensure_port_traffic_series_schema)
    _run_optional_block(conn, "topology", ensure_topology_schema)

    _run_sql(
        conn,
        "ALTER TABLE ne_collection_run ADD COLUMN IF NOT EXISTS ne_source VARCHAR(16) DEFAULT 'managed'",
    )
    _run_sql(conn, "ALTER TABLE ne_collection_run ALTER COLUMN ne_id TYPE VARCHAR(128)")
    _run_sql(
        conn,
        "ALTER TABLE config_sync_policy ADD COLUMN IF NOT EXISTS cycle_keep INTEGER DEFAULT 30",
    )

    pg = _dialect_name(conn).startswith("postgres")

    _run_sql(conn, "DROP TABLE IF EXISTS ume_inventory_equipment_holder")

    for sql in (
        "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS relevancy VARCHAR(128) DEFAULT ''",
        "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS l3vpn_peer_ne VARCHAR(256) DEFAULT ''",
        "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS service VARCHAR(256) DEFAULT ''",
        "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS affected_client_service_number INTEGER DEFAULT 0",
        "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS intermittence_count INTEGER DEFAULT 0",
        "ALTER TABLE alarms_norm ADD COLUMN IF NOT EXISTS me_level VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_token_cache ADD COLUMN IF NOT EXISTS lock_owner VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_token_cache ADD COLUMN IF NOT EXISTS lock_expires_at_epoch_s INTEGER DEFAULT 0",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS device_level VARCHAR(64) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS host_name VARCHAR(256) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS location VARCHAR(512) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS ipv6_address VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS hardware_version VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS loopback VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS consistent_state VARCHAR(64) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS interface_version VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS mac VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS admin_status VARCHAR(64) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS address_type VARCHAR(64) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS connection_status VARCHAR(64) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS maintain_status VARCHAR(64) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS net_mask VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS create_time VARCHAR(64) DEFAULT ''",
        "ALTER TABLE ume_inventory_ne ADD COLUMN IF NOT EXISTS creator VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_alarms_current ADD COLUMN IF NOT EXISTS host_name VARCHAR(256) DEFAULT ''",
        "ALTER TABLE ume_alarms_history ADD COLUMN IF NOT EXISTS host_name VARCHAR(256) DEFAULT ''",
        "ALTER TABLE ume_alarms_current ADD COLUMN IF NOT EXISTS notification_id VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_alarms_history ADD COLUMN IF NOT EXISTS notification_id VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_topo_link ADD COLUMN IF NOT EXISTS a_ifname VARCHAR(128) DEFAULT ''",
        "ALTER TABLE ume_topo_link ADD COLUMN IF NOT EXISTS z_ifname VARCHAR(128) DEFAULT ''",
        "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS world_x DOUBLE PRECISION",
        "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS world_y DOUBLE PRECISION",
        "ALTER TABLE topo_folder ADD COLUMN IF NOT EXISTS external_ref VARCHAR(128) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_topo_folder_external_ref ON topo_folder (external_ref)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_world_x ON topo_fabric_node (world_x)",
        "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_world_y ON topo_fabric_node (world_y)",
        "CREATE INDEX IF NOT EXISTS ix_ume_alarms_current_notification_id ON ume_alarms_current (notification_id)",
        "CREATE INDEX IF NOT EXISTS ix_ume_alarms_history_notification_id ON ume_alarms_history (notification_id)",
        "CREATE INDEX IF NOT EXISTS ix_ume_alarms_current_host_name ON ume_alarms_current (host_name)",
        "CREATE INDEX IF NOT EXISTS ix_ume_alarms_history_host_name ON ume_alarms_history (host_name)",
        "ALTER TABLE api_token ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
        "ALTER TABLE app_user ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN DEFAULT FALSE",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_enabled BOOLEAN DEFAULT FALSE",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_vendor VARCHAR(32) DEFAULT 'zte'",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_host VARCHAR(128) DEFAULT ''",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_port INTEGER DEFAULT 22",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_protocol VARCHAR(16) DEFAULT 'ssh'",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_username VARCHAR(128) DEFAULT ''",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_password_enc TEXT DEFAULT ''",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_command_template TEXT DEFAULT ''",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_vrf VARCHAR(128) DEFAULT ''",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS hop_target_auth_mode VARCHAR(32) DEFAULT 'bastion_managed'",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS source VARCHAR(64) DEFAULT ''",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS source_ref VARCHAR(128) DEFAULT ''",
        "ALTER TABLE managed_ne ADD COLUMN IF NOT EXISTS connect_detail TEXT DEFAULT ''",
        "ALTER TABLE ne_collection_job ADD COLUMN IF NOT EXISTS last_run_at TIMESTAMP",
        "UPDATE ne_collection_job SET last_run_at = COALESCE(ended_at, started_at, created_at) "
        "WHERE last_run_at IS NULL",
        "ALTER TABLE topology_edge ADD COLUMN IF NOT EXISTS stroke_color VARCHAR(32) DEFAULT ''",
        "ALTER TABLE topology_edge ADD COLUMN IF NOT EXISTS stroke_width INTEGER DEFAULT 0",
        "ALTER TABLE topology_edge ADD COLUMN IF NOT EXISTS line_style VARCHAR(16) DEFAULT ''",
    ):
        _run_sql(conn, sql)

    if pg:
        for sql in (
            "ALTER TABLE ume_alarms_current ALTER COLUMN alarm_key TYPE TEXT",
            "ALTER TABLE ume_alarms_current ALTER COLUMN object_name TYPE TEXT",
            "ALTER TABLE ume_alarms_current ALTER COLUMN event_type TYPE TEXT",
            "ALTER TABLE ume_alarms_current ALTER COLUMN native_probable_cause TYPE TEXT",
            "ALTER TABLE ume_alarms_current ALTER COLUMN perceived_severity TYPE TEXT",
            "ALTER TABLE ume_alarms_current ALTER COLUMN is_cleared TYPE TEXT",
            "ALTER TABLE ume_alarms_current ALTER COLUMN time_created TYPE TEXT",
            "ALTER TABLE ume_alarms_current ALTER COLUMN root_cause_alarm_indication TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN alarm_key TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN object_name TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN event_type TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN native_probable_cause TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN perceived_severity TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN is_cleared TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN time_created TYPE TEXT",
            "ALTER TABLE ume_alarms_history ALTER COLUMN root_cause_alarm_indication TYPE TEXT",
            "ALTER TABLE ume_alarms_current DROP COLUMN IF EXISTS ne_name",
            "ALTER TABLE ume_alarms_current DROP COLUMN IF EXISTS user_label",
            "ALTER TABLE ume_alarms_history DROP COLUMN IF EXISTS ne_name",
            "ALTER TABLE ume_alarms_history DROP COLUMN IF EXISTS user_label",
            "ALTER TABLE managed_ne DROP CONSTRAINT IF EXISTS managed_ne_ip_address_key",
            "DROP INDEX IF EXISTS managed_ne_ip_address_key",
            "DROP INDEX IF EXISTS ix_managed_ne_ip_address",
            "CREATE INDEX IF NOT EXISTS ix_managed_ne_ip_address ON managed_ne (ip_address)",
            """
            CREATE TABLE IF NOT EXISTS cli_connect_profile (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(256) DEFAULT '',
                is_default BOOLEAN DEFAULT FALSE,
                username VARCHAR(128) DEFAULT '',
                password_enc TEXT DEFAULT '',
                port INTEGER DEFAULT 22,
                protocol VARCHAR(16) DEFAULT 'ssh',
                device_type_default VARCHAR(128) DEFAULT 'zte_zxros',
                vendor_default VARCHAR(64) DEFAULT 'ZTE',
                ne_type_rules TEXT DEFAULT '',
                hop_enabled BOOLEAN DEFAULT FALSE,
                hop_vendor VARCHAR(32) DEFAULT 'zte',
                hop_host VARCHAR(128) DEFAULT '',
                hop_port INTEGER DEFAULT 22,
                hop_protocol VARCHAR(16) DEFAULT 'ssh',
                hop_username VARCHAR(128) DEFAULT '',
                hop_password_enc TEXT DEFAULT '',
                hop_command_template TEXT DEFAULT '',
                hop_vrf VARCHAR(128) DEFAULT '',
                hop_target_auth_mode VARCHAR(32) DEFAULT 'bastion_managed',
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_cli_connect_profile_is_default ON cli_connect_profile (is_default)",
            """
            CREATE TABLE IF NOT EXISTS ume_cli_override (
                ume_ne_id VARCHAR(128) PRIMARY KEY,
                profile_id VARCHAR(64),
                username_override VARCHAR(128) DEFAULT '',
                device_type_override VARCHAR(128) DEFAULT '',
                vendor_override VARCHAR(64) DEFAULT '',
                connect_status VARCHAR(32) DEFAULT 'unknown',
                connect_message VARCHAR(512) DEFAULT '',
                connect_detail TEXT DEFAULT '',
                connect_tested_at TIMESTAMP,
                updated_at TIMESTAMP
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_ume_cli_override_connect_status ON ume_cli_override (connect_status)",
            """
            CREATE TABLE IF NOT EXISTS ume_topo_node (
                node_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(512) DEFAULT '',
                node_type VARCHAR(64) DEFAULT '',
                user_label VARCHAR(512) DEFAULT '',
                owner VARCHAR(64) DEFAULT '',
                parent_node VARCHAR(512) DEFAULT '',
                x_pos INTEGER,
                y_pos INTEGER,
                ume_ne_id VARCHAR(128) DEFAULT '',
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                raw_json TEXT DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_ume_topo_node_node_type ON ume_topo_node (node_type)",
            "CREATE INDEX IF NOT EXISTS ix_ume_topo_node_ume_ne_id ON ume_topo_node (ume_ne_id)",
            "CREATE INDEX IF NOT EXISTS ix_ume_topo_node_last_seen_at ON ume_topo_node (last_seen_at)",
            """
            CREATE TABLE IF NOT EXISTS ume_topo_link (
                link_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(1024) DEFAULT '',
                user_label TEXT DEFAULT '',
                owner VARCHAR(64) DEFAULT '',
                direction VARCHAR(32) DEFAULT '',
                layer_rate INTEGER,
                connection_status VARCHAR(64) DEFAULT '',
                a_end_tp_ref TEXT DEFAULT '',
                z_end_tp_ref TEXT DEFAULT '',
                a_ume_ne_id VARCHAR(128) DEFAULT '',
                z_ume_ne_id VARCHAR(128) DEFAULT '',
                a_ptp VARCHAR(256) DEFAULT '',
                z_ptp VARCHAR(256) DEFAULT '',
                a_ifname VARCHAR(128) DEFAULT '',
                z_ifname VARCHAR(128) DEFAULT '',
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                raw_json TEXT DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_ume_topo_link_a_ume_ne_id ON ume_topo_link (a_ume_ne_id)",
            "CREATE INDEX IF NOT EXISTS ix_ume_topo_link_z_ume_ne_id ON ume_topo_link (z_ume_ne_id)",
            "CREATE INDEX IF NOT EXISTS ix_ume_topo_link_last_seen_at ON ume_topo_link (last_seen_at)",
            "ALTER TABLE ume_topo_link ADD COLUMN IF NOT EXISTS a_ifname VARCHAR(128) DEFAULT ''",
            "ALTER TABLE ume_topo_link ADD COLUMN IF NOT EXISTS z_ifname VARCHAR(128) DEFAULT ''",
            "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS world_x DOUBLE PRECISION",
            "ALTER TABLE topo_fabric_node ADD COLUMN IF NOT EXISTS world_y DOUBLE PRECISION",
            "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_world_x ON topo_fabric_node (world_x)",
            "CREATE INDEX IF NOT EXISTS ix_topo_fabric_node_world_y ON topo_fabric_node (world_y)",
            "COMMENT ON TABLE ume_inventory_ne IS '网元对象详细信息'",
            "COMMENT ON COLUMN ume_inventory_ne.ne_id IS '网元uuid'",
            "COMMENT ON COLUMN ume_inventory_ne.ne_name IS '资源名称'",
            "COMMENT ON COLUMN ume_inventory_ne.ne_type IS '网元类型'",
            "COMMENT ON COLUMN ume_inventory_ne.user_label IS '用户标签'",
            "COMMENT ON COLUMN ume_inventory_ne.address_type IS '管理地址类型(1:IPv4,2:IPv6)'",
            "COMMENT ON COLUMN ume_inventory_ne.ip_address IS '网元IPv4地址'",
            "COMMENT ON COLUMN ume_inventory_ne.net_mask IS '管理IPv4掩码(点分十进制)'",
            "COMMENT ON COLUMN ume_inventory_ne.ipv6_address IS 'IPv6地址'",
            "COMMENT ON COLUMN ume_inventory_ne.admin_status IS '管理状态(0-离线,1-在线)'",
            "COMMENT ON COLUMN ume_inventory_ne.connection_status IS '连接状态(0-断链,1-正常)'",
            "COMMENT ON COLUMN ume_inventory_ne.consistent_state IS '数据一致性状态(1一致,2不一致,3冲突)'",
            "COMMENT ON COLUMN ume_inventory_ne.maintain_status IS '工程状态(0普通,1调测,2新建)'",
            "COMMENT ON COLUMN ume_inventory_ne.vendor IS '网元提供商'",
            "COMMENT ON COLUMN ume_inventory_ne.interface_version IS '网元接口版本号'",
            "COMMENT ON COLUMN ume_inventory_ne.hardware_version IS '硬件版本'",
            "COMMENT ON COLUMN ume_inventory_ne.mac IS '设备机架MAC地址'",
            "COMMENT ON COLUMN ume_inventory_ne.loopback IS '业务环回IP(IPv4)'",
            "COMMENT ON COLUMN ume_inventory_ne.device_level IS '网元层次'",
            "COMMENT ON COLUMN ume_inventory_ne.host_name IS '主机名称'",
        ):
            _run_sql(conn, sql)
    else:
        for sql in (
            "DROP INDEX IF EXISTS managed_ne_ip_address_key",
            "DROP INDEX IF EXISTS ix_managed_ne_ip_address",
            "DROP INDEX IF EXISTS sqlite_autoindex_managed_ne_1",
            "CREATE INDEX IF NOT EXISTS ix_managed_ne_ip_address ON managed_ne (ip_address)",
            "ALTER TABLE ume_alarms_current DROP COLUMN IF EXISTS ne_name",
            "ALTER TABLE ume_alarms_current DROP COLUMN IF EXISTS user_label",
            "ALTER TABLE ume_alarms_history DROP COLUMN IF EXISTS ne_name",
            "ALTER TABLE ume_alarms_history DROP COLUMN IF EXISTS user_label",
        ):
            _run_sql(conn, sql)


def apply_all_legacy_startup_ddl(engine: Engine) -> None:
    """Full brownfield patch set previously inlined in ``main.on_startup``."""
    apply_key_alert_schema_patches(engine)
    with engine.begin() as conn:
        apply_auth_schema_patches(conn)
        apply_domain_schema_patches(conn)


def run_alembic_upgrade_to_head() -> None:
    """Programmatic ``alembic upgrade head`` (optional on API start)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    # env.py reads settings.database_url; keep ini placeholder overwritten there.
    command.upgrade(cfg, "head")
    _log.info("alembic upgrade head completed")
