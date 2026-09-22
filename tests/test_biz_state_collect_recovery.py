"""Startup recovery for interrupted biz-state collects."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_state.collect_recovery import recover_interrupted_collects_on_startup
from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateBatchCommand, BizStateTask


class BizStateCollectRecoveryTests(unittest.TestCase):
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
            ne_name="PE1",
            status="running",
            collect_running=True,
            interval_sec=300,
        )
        self.db.add(self.task)
        self.batch = BizStateBatch(
            id="b1",
            task_id="t1",
            status="running",
            command_count=1,
            row_count=2,
            message="",
        )
        self.db.add(self.batch)
        self.db.add(
            BizStateBatchCommand(
                id="c1",
                batch_id="b1",
                profile_id="zte.lldp_neighbors",
                parser_id="lldp_neighbors",
                metric_id="lldp_neighbor",
                raw_command="show lldp neighbor brief",
                parse_status="running",
                row_count=0,
            )
        )
        self.db.add(
            BizStateBatch(
                id="b_ok",
                task_id="t1",
                status="success",
                command_count=1,
                row_count=1,
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_running_batch_becomes_partial_no_resume(self) -> None:
        out = recover_interrupted_collects_on_startup(self.db)
        self.assertEqual(out["batches"], 1)
        self.assertEqual(out["tasks"], 1)
        self.assertEqual(out["commands"], 1)

        self.db.refresh(self.batch)
        self.db.refresh(self.task)
        self.assertEqual(self.batch.status, "partial")
        self.assertIsNotNone(self.batch.ended_at)
        self.assertIn("interrupted_by_restart", self.batch.message)
        self.assertFalse(self.task.collect_running)
        self.assertIn("interrupted_by_restart", self.task.last_error or "")

        cmd = self.db.get(BizStateBatchCommand, "c1")
        assert cmd is not None
        self.assertEqual(cmd.parse_status, "failed")
        self.assertIn("interrupted_by_restart", cmd.message or "")

        ok = self.db.get(BizStateBatch, "b_ok")
        assert ok is not None
        self.assertEqual(ok.status, "success")


if __name__ == "__main__":
    unittest.main()
