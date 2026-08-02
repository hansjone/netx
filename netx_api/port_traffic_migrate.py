"""Startup migration / backfill for port traffic device-centric model."""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import PortTrafficDevice, PortTrafficSample, PortTrafficSeries, PortTrafficTarget
from .schema_patches import _run_sql

_log = logging.getLogger("netx.port_traffic.migrate")


def ensure_port_traffic_series_schema(conn) -> None:
    """DDL for device + series + device_id columns (Postgres IF NOT EXISTS)."""
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS port_traffic_device (
            id VARCHAR(64) PRIMARY KEY,
            source VARCHAR(32) DEFAULT 'managed',
            ne_id VARCHAR(128) DEFAULT '',
            ne_name VARCHAR(256) DEFAULT '',
            ne_ip VARCHAR(128) DEFAULT '',
            vendor VARCHAR(64) DEFAULT '',
            note VARCHAR(256) DEFAULT '',
            status VARCHAR(32) DEFAULT 'draft',
            interval_sec INTEGER DEFAULT 60,
            retention_days INTEGER DEFAULT 7,
            concurrency INTEGER DEFAULT 1,
            collect_running BOOLEAN DEFAULT FALSE,
            last_collect_started_at TIMESTAMP,
            last_collect_ended_at TIMESTAMP,
            last_error VARCHAR(1024) DEFAULT '',
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_port_traffic_device_ne ON port_traffic_device (source, ne_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_device_status ON port_traffic_device (status)"
    )

    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS port_traffic_series (
            id VARCHAR(64) PRIMARY KEY,
            device_id VARCHAR(64) DEFAULT '',
            title VARCHAR(256) DEFAULT '',
            status VARCHAR(32) DEFAULT 'active',
            created_at TIMESTAMP
        )
        """
    )
    # Legacy column may still exist as task_id
    conn.exec_driver_sql(
        "ALTER TABLE port_traffic_series ADD COLUMN IF NOT EXISTS device_id VARCHAR(64) DEFAULT ''"
    )
    conn.exec_driver_sql(
        "ALTER TABLE port_traffic_series ADD COLUMN IF NOT EXISTS task_id VARCHAR(64) DEFAULT ''"
    )
    # Older installs created task_id as NOT NULL before the device-centric rename.
    # Keep both columns populated and relax NOT NULL so ORM inserts that only set
    # device_id (or only task_id) do not 500. Each statement uses SAVEPOINT.
    for sql in (
        """
        UPDATE port_traffic_series
        SET device_id = task_id
        WHERE COALESCE(device_id, '') = '' AND COALESCE(task_id, '') <> ''
        """,
        """
        UPDATE port_traffic_series
        SET task_id = device_id
        WHERE COALESCE(task_id, '') = '' AND COALESCE(device_id, '') <> ''
        """,
        "ALTER TABLE port_traffic_series ALTER COLUMN task_id DROP NOT NULL",
        "ALTER TABLE port_traffic_series ALTER COLUMN task_id SET DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_series_device_id ON port_traffic_series (device_id)",
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_series_status ON port_traffic_series (status)",
        "ALTER TABLE port_traffic_target ADD COLUMN IF NOT EXISTS series_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE port_traffic_target ADD COLUMN IF NOT EXISTS device_id VARCHAR(64) DEFAULT ''",
        "ALTER TABLE port_traffic_target ADD COLUMN IF NOT EXISTS task_id VARCHAR(64) DEFAULT ''",
        """
        UPDATE port_traffic_target
        SET device_id = task_id
        WHERE COALESCE(device_id, '') = '' AND COALESCE(task_id, '') <> ''
        """,
        """
        UPDATE port_traffic_target
        SET task_id = device_id
        WHERE COALESCE(task_id, '') = '' AND COALESCE(device_id, '') <> ''
        """,
        "ALTER TABLE port_traffic_target ALTER COLUMN task_id DROP NOT NULL",
        "ALTER TABLE port_traffic_target ALTER COLUMN task_id SET DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_target_series_id ON port_traffic_target (series_id)",
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_target_device_id ON port_traffic_target (device_id)",
        "ALTER TABLE port_traffic_sample ADD COLUMN IF NOT EXISTS series_id VARCHAR(64) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_sample_series_id ON port_traffic_sample (series_id)",
        # One active interface globally per physical port (may fail if duplicates exist)
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_port_traffic_target_active_if
        ON port_traffic_target (source, target_id, ifname)
        WHERE status = 'active'
        """,
    ):
        _run_sql(conn, sql)

    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS port_traffic_event (
            id VARCHAR(64) PRIMARY KEY,
            device_id VARCHAR(64) DEFAULT '',
            target_row_id VARCHAR(64) DEFAULT '',
            ifname VARCHAR(128) DEFAULT '',
            level VARCHAR(16) DEFAULT 'error',
            message TEXT DEFAULT '',
            created_at TIMESTAMP
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_event_device_id ON port_traffic_event (device_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_event_created_at ON port_traffic_event (created_at)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_event_level ON port_traffic_event (level)"
    )

    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS port_traffic_board (
            id VARCHAR(64) PRIMARY KEY,
            name VARCHAR(256) DEFAULT '',
            remark VARCHAR(1024) DEFAULT '',
            cols INTEGER DEFAULT 2,
            created_by VARCHAR(64) DEFAULT '',
            updated_by VARCHAR(64) DEFAULT '',
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_board_name ON port_traffic_board (name)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_board_updated_at ON port_traffic_board (updated_at)"
    )

    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS port_traffic_panel (
            id VARCHAR(64) PRIMARY KEY,
            board_id VARCHAR(64) DEFAULT '',
            title VARCHAR(256) DEFAULT '',
            target_id VARCHAR(64) DEFAULT '',
            range_hours INTEGER DEFAULT 24,
            baseline VARCHAR(16) DEFAULT 'off',
            offset_hours INTEGER DEFAULT 0,
            ahead_hours INTEGER DEFAULT 1,
            baseline_target_id VARCHAR(64) DEFAULT '',
            y_mode VARCHAR(16) DEFAULT 'auto',
            ord INTEGER DEFAULT 0,
            col_span INTEGER DEFAULT 1,
            row_span INTEGER DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
        """
    )
    conn.exec_driver_sql(
        "ALTER TABLE port_traffic_panel ADD COLUMN IF NOT EXISTS ahead_hours INTEGER DEFAULT 1"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_panel_board_id ON port_traffic_panel (board_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_panel_target_id ON port_traffic_panel (target_id)"
    )


def migrate_tasks_to_devices(db: Session) -> int:
    """Collapse legacy port_traffic_task rows into per-NE devices; reassign targets/series."""
    # Copy task_id → device_id where missing
    try:
        db.execute(
            text(
                """
                UPDATE port_traffic_target
                SET device_id = task_id
                WHERE COALESCE(device_id, '') = '' AND COALESCE(task_id, '') <> ''
                """
            )
        )
        db.execute(
            text(
                """
                UPDATE port_traffic_series
                SET device_id = task_id
                WHERE COALESCE(device_id, '') = '' AND COALESCE(task_id, '') <> ''
                """
            )
        )
        db.commit()
    except Exception:
        db.rollback()

    # If legacy task table missing, nothing else to do
    try:
        db.execute(text("SELECT 1 FROM port_traffic_task LIMIT 1"))
    except Exception:
        db.rollback()
        return 0

    targets = db.query(PortTrafficTarget).all()
    if not targets:
        # Still create devices from empty? skip
        return 0

    # Group active+any targets by (source, ne_id)
    groups: dict[tuple[str, str], list[PortTrafficTarget]] = {}
    for t in targets:
        src = str(t.source or "managed").strip().lower() or "managed"
        ne = str(t.target_id or "").strip()
        if not ne:
            continue
        groups.setdefault((src, ne), []).append(t)

    created = 0
    # Load legacy tasks for interval/status
    legacy: dict[str, dict] = {}
    try:
        rows = db.execute(
            text(
                """
                SELECT id, title, status, interval_sec, retention_days, concurrency,
                       collect_running, last_collect_started_at, last_collect_ended_at,
                       last_error, created_at, updated_at
                FROM port_traffic_task
                """
            )
        ).mappings().all()
        for r in rows:
            legacy[str(r["id"])] = dict(r)
    except Exception:
        db.rollback()
        legacy = {}

    for (src, ne), members in groups.items():
        existing = (
            db.query(PortTrafficDevice)
            .filter(PortTrafficDevice.source == src, PortTrafficDevice.ne_id == ne)
            .first()
        )
        if existing:
            device = existing
        else:
            # Pick policy from the richest legacy task among members
            intervals: list[int] = []
            retentions: list[int] = []
            statuses: list[str] = []
            note = ""
            created_at = None
            for m in members:
                tid = str(getattr(m, "device_id", None) or getattr(m, "task_id", None) or "")
                meta = legacy.get(tid) or {}
                if meta.get("interval_sec"):
                    intervals.append(int(meta["interval_sec"]))
                if meta.get("retention_days"):
                    retentions.append(int(meta["retention_days"]))
                if meta.get("status"):
                    statuses.append(str(meta["status"]))
                if not note and meta.get("title"):
                    note = str(meta["title"])[:256]
                if created_at is None and meta.get("created_at"):
                    created_at = meta["created_at"]
            status = "running" if "running" in statuses else (statuses[0] if statuses else "stopped")
            sample = members[0]
            device = PortTrafficDevice(
                id=uuid4().hex,
                source=src,
                ne_id=ne,
                ne_name=str(sample.ne_name or ""),
                ne_ip=str(sample.ne_ip or ""),
                vendor=str(sample.vendor or ""),
                note=note,
                status=status if status in ("running", "paused", "stopped", "draft") else "stopped",
                interval_sec=min(intervals) if intervals else 60,
                retention_days=max(retentions) if retentions else 7,
                concurrency=1,
                collect_running=False,
                created_at=created_at,
            )
            db.add(device)
            db.flush()
            created += 1

        # Deduplicate active ifnames: keep earliest active, retire the rest
        seen_active: set[str] = set()
        for m in sorted(members, key=lambda x: str(x.created_at or "")):
            m.device_id = str(device.id)
            if str(m.status) == "active":
                key = str(m.ifname or "").strip()
                if key in seen_active:
                    m.status = "retired"
                else:
                    seen_active.add(key)
            # Keep ne metadata in sync
            if not device.ne_name and m.ne_name:
                device.ne_name = str(m.ne_name)
            if not device.ne_ip and m.ne_ip:
                device.ne_ip = str(m.ne_ip)
            if not device.vendor and m.vendor:
                device.vendor = str(m.vendor)

        # Re-point series
        series_ids = {str(m.series_id) for m in members if m.series_id}
        if series_ids:
            db.query(PortTrafficSeries).filter(PortTrafficSeries.id.in_(list(series_ids))).update(
                {PortTrafficSeries.device_id: str(device.id)},
                synchronize_session=False,
            )

    db.commit()
    if created:
        _log.info("port_traffic migrated devices created=%s groups=%s", created, len(groups))
    return created


def backfill_port_traffic_series(db: Session) -> int:
    """Create one series per target missing series_id; stamp samples."""
    migrate_tasks_to_devices(db)

    targets = (
        db.query(PortTrafficTarget)
        .filter((PortTrafficTarget.series_id == None) | (PortTrafficTarget.series_id == ""))  # noqa: E711
        .all()
    )
    created = 0
    for t in targets:
        device_id = str(t.device_id or "")
        title = _default_series_title(t.ne_name or "", t.ifname or "")
        title = _unique_series_title(db, device_id, title)
        sid = uuid4().hex
        db.add(
            PortTrafficSeries(
                id=sid,
                device_id=device_id,
                title=title,
                status="active",
            )
        )
        t.series_id = sid
        db.query(PortTrafficSample).filter(
            PortTrafficSample.target_row_id == str(t.id),
            (PortTrafficSample.series_id == None) | (PortTrafficSample.series_id == ""),  # noqa: E711
        ).update({"series_id": sid}, synchronize_session=False)
        created += 1
    if created:
        db.commit()
        _log.info("port_traffic series backfill targets=%s", created)
    try:
        orphans = db.execute(
            text(
                """
                UPDATE port_traffic_sample AS s
                SET series_id = t.series_id
                FROM port_traffic_target AS t
                WHERE s.target_row_id = t.id
                  AND COALESCE(s.series_id, '') = ''
                  AND COALESCE(t.series_id, '') <> ''
                """
            )
        )
        if orphans.rowcount:
            db.commit()
            _log.info("port_traffic series sample stamp rows=%s", orphans.rowcount)
    except Exception:
        db.rollback()
    return created


def default_series_title(ne_name: str, ifname: str) -> str:
    return _default_series_title(ne_name, ifname)


def unique_series_title(db: Session, device_id: str, base: str) -> str:
    return _unique_series_title(db, device_id, base)


def _default_series_title(ne_name: str, ifname: str) -> str:
    ne = (ne_name or "").strip() or "NE"
    iface = (ifname or "").strip() or "if"
    return f"{ne}:{iface}"[:256]


def _unique_series_title(db: Session, device_id: str, base: str) -> str:
    title = base[:256]
    exists = (
        db.query(PortTrafficSeries.id)
        .filter(PortTrafficSeries.device_id == device_id, PortTrafficSeries.title == title)
        .first()
    )
    if not exists:
        return title
    for i in range(2, 1000):
        candidate = f"{base[:240]}#{i}"
        exists = (
            db.query(PortTrafficSeries.id)
            .filter(PortTrafficSeries.device_id == device_id, PortTrafficSeries.title == candidate)
            .first()
        )
        if not exists:
            return candidate
    return f"{base[:200]}#{uuid4().hex[:8]}"
