"""Startup migration / backfill for port traffic logical series."""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import PortTrafficSample, PortTrafficSeries, PortTrafficTarget

_log = logging.getLogger("netx.port_traffic.migrate")


def ensure_port_traffic_series_schema(conn) -> None:
    """DDL for series + series_id columns (Postgres IF NOT EXISTS)."""
    conn.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS port_traffic_series (
            id VARCHAR(64) PRIMARY KEY,
            task_id VARCHAR(64),
            title VARCHAR(256) DEFAULT '',
            status VARCHAR(32) DEFAULT 'active',
            created_at TIMESTAMP
        )
        """
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_series_task_id ON port_traffic_series (task_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_series_status ON port_traffic_series (status)"
    )
    conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_port_traffic_series_title ON port_traffic_series (task_id, title)"
    )
    conn.exec_driver_sql(
        "ALTER TABLE port_traffic_target ADD COLUMN IF NOT EXISTS series_id VARCHAR(64) DEFAULT ''"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_target_series_id ON port_traffic_target (series_id)"
    )
    conn.exec_driver_sql(
        "ALTER TABLE port_traffic_sample ADD COLUMN IF NOT EXISTS series_id VARCHAR(64) DEFAULT ''"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_port_traffic_sample_series_id ON port_traffic_sample (series_id)"
    )


def backfill_port_traffic_series(db: Session) -> int:
    """Create one series per target missing series_id; stamp samples."""
    targets = (
        db.query(PortTrafficTarget)
        .filter((PortTrafficTarget.series_id == None) | (PortTrafficTarget.series_id == ""))  # noqa: E711
        .all()
    )
    created = 0
    for t in targets:
        title = _default_series_title(t.ne_name or "", t.ifname or "")
        title = _unique_series_title(db, str(t.task_id), title)
        sid = uuid4().hex
        db.add(
            PortTrafficSeries(
                id=sid,
                task_id=str(t.task_id),
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


def unique_series_title(db: Session, task_id: str, base: str) -> str:
    return _unique_series_title(db, task_id, base)


def _default_series_title(ne_name: str, ifname: str) -> str:
    ne = (ne_name or "").strip() or "NE"
    iface = (ifname or "").strip() or "if"
    return f"{ne}:{iface}"[:256]


def _unique_series_title(db: Session, task_id: str, base: str) -> str:
    title = base[:256]
    exists = (
        db.query(PortTrafficSeries.id)
        .filter(PortTrafficSeries.task_id == task_id, PortTrafficSeries.title == title)
        .first()
    )
    if not exists:
        return title
    for i in range(2, 1000):
        candidate = f"{base[:240]}#{i}"
        exists = (
            db.query(PortTrafficSeries.id)
            .filter(PortTrafficSeries.task_id == task_id, PortTrafficSeries.title == candidate)
            .first()
        )
        if not exists:
            return candidate
    return f"{base[:200]}#{uuid4().hex[:8]}"
