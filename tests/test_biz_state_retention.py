"""Retention purge: age days + daily keep + baseline protection."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateTask
from netx_api.biz_state.retention import purge_task_batches, protected_batch_ids


class RetentionPurgeTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.db = TestingSession()
        self.task = BizStateTask(
            id="t1",
            source="managed",
            ne_id="ne1",
            status="running",
            interval_sec=60,
            retention_days=7,
            daily_keep_enabled=False,
            daily_keep_count=10,
        )
        self.db.add(self.task)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _add_batch(self, bid: str, *, days_ago: float, baseline: bool = False) -> BizStateBatch:
        started = datetime.utcnow() - timedelta(days=days_ago)
        b = BizStateBatch(
            id=bid,
            task_id=self.task.id,
            status="success",
            started_at=started,
            ended_at=started,
            is_baseline=baseline,
            baseline_marked_at=started if baseline else None,
        )
        self.db.add(b)
        self.db.commit()
        return b

    def test_age_purge_keeps_baseline(self) -> None:
        self._add_batch("old", days_ago=10)
        self._add_batch("pin", days_ago=20, baseline=True)
        self._add_batch("fresh", days_ago=1)
        info = purge_task_batches(self.db, self.task)
        self.assertEqual(info["dropped"], 1)
        ids = {b.id for b in self.db.query(BizStateBatch).all()}
        self.assertEqual(ids, {"pin", "fresh"})
        self.assertIn("pin", protected_batch_ids(self.db, task_id=self.task.id))

    def test_daily_keep_skips_today(self) -> None:
        self.task.daily_keep_enabled = True
        self.task.daily_keep_count = 1
        self.db.commit()
        # same past calendar day: 3 batches → keep 1 newest
        yesterday_noon = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(
            days=1
        )
        for i, bid in enumerate(("y1", "y2", "y3")):
            b = BizStateBatch(
                id=bid,
                task_id=self.task.id,
                status="success",
                started_at=yesterday_noon - timedelta(hours=i),
                ended_at=yesterday_noon - timedelta(hours=i),
            )
            self.db.add(b)
        # today: keep all even if > N
        self._add_batch("tod1", days_ago=0.01)
        self._add_batch("tod2", days_ago=0.02)
        self.db.commit()
        info = purge_task_batches(self.db, self.task)
        self.assertEqual(info["dropped"], 2)
        ids = {b.id for b in self.db.query(BizStateBatch).all()}
        self.assertIn("y1", ids)  # newest yesterday
        self.assertIn("tod1", ids)
        self.assertIn("tod2", ids)
        self.assertNotIn("y2", ids)
        self.assertNotIn("y3", ids)


if __name__ == "__main__":
    unittest.main()
