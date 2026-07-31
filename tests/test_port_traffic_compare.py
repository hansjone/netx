"""Unit tests for port traffic compare: interface + optional mapped baseline."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.db import Base
from netx_api.models import PortTrafficSample, PortTrafficSeries, PortTrafficTarget, PortTrafficTask
from netx_api.port_traffic_service import baseline_offset_hours, compare_targets


class BaselineOffsetTests(unittest.TestCase):
    def test_presets(self):
        self.assertIsNone(baseline_offset_hours("off", 24, None))
        self.assertEqual(baseline_offset_hours("shift", 24, None), 24.0)
        self.assertEqual(baseline_offset_hours("day", 6, None), 24.0)
        self.assertEqual(baseline_offset_hours("week", 24, None), 168.0)
        self.assertEqual(baseline_offset_hours("custom", 24, 36), 36.0)


class CompareTargetsTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.task_id = uuid4().hex
        self.series_id = uuid4().hex
        self.target_id = uuid4().hex
        self.mapped_id = uuid4().hex
        now = datetime(2026, 7, 31, 12, 0, 0)
        self.now = now
        self.db.add(
            PortTrafficTask(
                id=self.task_id,
                title="t1",
                status="running",
                created_at=now,
                updated_at=now,
            )
        )
        self.db.add(
            PortTrafficSeries(
                id=self.series_id,
                task_id=self.task_id,
                title="uplink1",
                status="active",
                created_at=now,
            )
        )
        self.db.add(
            PortTrafficTarget(
                id=self.target_id,
                task_id=self.task_id,
                series_id=self.series_id,
                source="managed",
                target_id="ne1",
                ne_name="R1",
                vendor="Huawei",
                ifname="Ethernet1/0/0",
                status="active",
                created_at=now,
            )
        )
        self.db.add(
            PortTrafficTarget(
                id=self.mapped_id,
                task_id=self.task_id,
                series_id="",
                source="managed",
                target_id="ne2",
                ne_name="R2",
                vendor="ZTE",
                ifname="gei-0/0/1",
                status="active",
                created_at=now,
            )
        )
        # current window points (last 2h) on primary interface
        for i in range(3):
            self.db.add(
                PortTrafficSample(
                    id=uuid4().hex,
                    target_row_id=self.target_id,
                    series_id=self.series_id,
                    ts=now - timedelta(hours=1, minutes=30) + timedelta(minutes=30 * i),
                    in_bps=1000 + i,
                    out_bps=2000 + i,
                    raw_ok=True,
                )
            )
        # baseline day-1 points on same port
        for i in range(3):
            self.db.add(
                PortTrafficSample(
                    id=uuid4().hex,
                    target_row_id=self.target_id,
                    series_id=self.series_id,
                    ts=now - timedelta(days=1, hours=1, minutes=30) + timedelta(minutes=30 * i),
                    in_bps=500 + i,
                    out_bps=800 + i,
                    raw_ok=True,
                )
            )
        # mapped port same-window points
        for i in range(2):
            self.db.add(
                PortTrafficSample(
                    id=uuid4().hex,
                    target_row_id=self.mapped_id,
                    series_id="",
                    ts=now - timedelta(hours=1) + timedelta(minutes=30 * i),
                    in_bps=90 + i,
                    out_bps=110 + i,
                    raw_ok=True,
                )
            )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_off_returns_current_only(self):
        out = compare_targets(
            self.db,
            target_row_id=self.target_id,
            range_hours=2,
            baseline="off",
            to_ts=self.now,
        )
        self.assertEqual(len(out.current), 3)
        self.assertEqual(len(out.baseline), 0)
        self.assertEqual(out.meta.target_id, self.target_id)

    def test_day_baseline_same_port(self):
        out = compare_targets(
            self.db,
            target_row_id=self.target_id,
            range_hours=2,
            baseline="day",
            to_ts=self.now,
        )
        self.assertEqual(len(out.current), 3)
        self.assertEqual(len(out.baseline), 3)
        self.assertEqual(out.meta.offset_hours, 24.0)
        for p in out.baseline:
            self.assertIsNotNone(p.ts_raw)
            self.assertGreaterEqual(p.ts, self.now - timedelta(hours=2))
            self.assertLessEqual(p.ts, self.now + timedelta(seconds=5))
        self.assertEqual(out.baseline[0].in_bps, 500.0)

    def test_mapped_port_same_window(self):
        out = compare_targets(
            self.db,
            target_row_id=self.target_id,
            range_hours=2,
            baseline="off",
            baseline_target_id=self.mapped_id,
            to_ts=self.now,
        )
        self.assertEqual(len(out.current), 3)
        self.assertEqual(len(out.baseline), 2)
        self.assertEqual(out.meta.baseline_target_id, self.mapped_id)
        self.assertEqual(out.baseline[0].in_bps, 90.0)
        self.assertIsNone(out.baseline[0].ts_raw)

    def test_mapped_port_with_day_offset(self):
        # day-ago samples on mapped port
        for i in range(2):
            self.db.add(
                PortTrafficSample(
                    id=uuid4().hex,
                    target_row_id=self.mapped_id,
                    ts=self.now - timedelta(days=1, hours=1) + timedelta(minutes=30 * i),
                    in_bps=40 + i,
                    out_bps=50 + i,
                    raw_ok=True,
                )
            )
        self.db.commit()
        out = compare_targets(
            self.db,
            target_row_id=self.target_id,
            range_hours=2,
            baseline="day",
            baseline_target_id=self.mapped_id,
            to_ts=self.now,
        )
        self.assertEqual(len(out.baseline), 2)
        self.assertEqual(out.baseline[0].in_bps, 40.0)
        self.assertIsNotNone(out.baseline[0].ts_raw)

    def test_shift_baseline(self):
        for i in range(2):
            self.db.add(
                PortTrafficSample(
                    id=uuid4().hex,
                    target_row_id=self.target_id,
                    series_id=self.series_id,
                    ts=self.now - timedelta(hours=3, minutes=30) + timedelta(minutes=30 * i),
                    in_bps=50 + i,
                    out_bps=60 + i,
                    raw_ok=True,
                )
            )
        self.db.commit()
        out = compare_targets(
            self.db,
            target_row_id=self.target_id,
            range_hours=2,
            baseline="shift",
            to_ts=self.now,
        )
        self.assertGreaterEqual(len(out.baseline), 2)
        self.assertEqual(out.meta.offset_hours, 2.0)


if __name__ == "__main__":
    unittest.main()
