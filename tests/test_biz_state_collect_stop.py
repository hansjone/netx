"""biz_state collect stop: cancel queued / signal running."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.biz_state import collect_stop as stop_mod
from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateTask


class BizStateCollectStopTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestingSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.Session = TestingSession
        self.db = TestingSession()
        self._session_patch = patch.object(stop_mod, "SessionLocal", TestingSession)
        self._session_patch.start()
        stop_mod._stop_batches.clear()
        stop_mod._holders.clear()
        stop_mod._db_cache.clear()

        self.task = BizStateTask(
            id="t-stop",
            source="managed",
            ne_id="ne-1",
            ne_name="NE1",
            status="running",
            collect_running=True,
        )
        self.db.add(self.task)
        self.db.commit()

    def tearDown(self) -> None:
        self._session_patch.stop()
        self.db.close()

    def test_stop_cancels_queued_batch(self) -> None:
        batch = BizStateBatch(
            id="b-q",
            task_id=self.task.id,
            status="queued",
            message="queued_manual",
        )
        self.db.add(batch)
        self.db.commit()

        out = stop_mod.request_stop_collect(self.task.id)
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("stopped"))
        self.assertEqual(out.get("cancelled_batches"), ["b-q"])
        self.assertEqual(out.get("signaled_batches"), [])

        self.db.expire_all()
        b = self.db.get(BizStateBatch, "b-q")
        t = self.db.get(BizStateTask, self.task.id)
        assert b is not None and t is not None
        self.assertEqual(b.status, "cancelled")
        self.assertEqual(b.message, stop_mod.STOP_USER_MESSAGE)
        self.assertFalse(t.collect_running)

    def test_stop_signals_running_batch(self) -> None:
        batch = BizStateBatch(
            id="b-r",
            task_id=self.task.id,
            status="running",
            message="",
        )
        self.db.add(batch)
        self.db.commit()

        out = stop_mod.request_stop_collect(self.task.id)
        self.assertTrue(out.get("ok"))
        self.assertTrue(out.get("stopped"))
        self.assertEqual(out.get("signaled_batches"), ["b-r"])
        self.assertTrue(stop_mod.is_stop_requested("b-r"))

        self.db.expire_all()
        b = self.db.get(BizStateBatch, "b-r")
        t = self.db.get(BizStateTask, self.task.id)
        assert b is not None and t is not None
        self.assertEqual(b.status, "running")
        self.assertEqual(b.message, stop_mod.STOP_REQUEST_TOKEN)
        # Still running until worker finishes.
        self.assertTrue(t.collect_running)

    def test_stop_idle(self) -> None:
        self.task.collect_running = False
        self.db.commit()
        out = stop_mod.request_stop_collect(self.task.id)
        self.assertTrue(out.get("ok"))
        self.assertFalse(out.get("stopped"))
        self.assertEqual(out.get("reason"), "not_collecting")


if __name__ == "__main__":
    unittest.main()
