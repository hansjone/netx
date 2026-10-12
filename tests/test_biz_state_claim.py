"""biz_state enqueue / claim (NE mutex) tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.biz_state import claim as claim_mod
from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateTask, BizStateTaskItem


class BizStateClaimTests(unittest.TestCase):
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
        self._session_patch = patch.object(claim_mod, "SessionLocal", TestingSession)
        self._session_patch.start()

        self.task_a = BizStateTask(
            id="ta",
            source="managed",
            ne_id="ne-a",
            ne_name="A",
            status="running",
            collect_running=False,
            interval_sec=300,
        )
        self.task_b = BizStateTask(
            id="tb",
            source="managed",
            ne_id="ne-a",  # same NE as A
            ne_name="A2",
            status="running",
            collect_running=False,
            interval_sec=300,
        )
        self.task_c = BizStateTask(
            id="tc",
            source="managed",
            ne_id="ne-c",
            ne_name="C",
            status="running",
            collect_running=False,
            interval_sec=300,
        )
        self.db.add_all([self.task_a, self.task_b, self.task_c])
        for tid in ("ta", "tb", "tc"):
            self.db.add(
                BizStateTaskItem(
                    id=uuid4().hex,
                    task_id=tid,
                    source_profile_id="zte.arp",
                    kind="catalog",
                    enabled=True,
                )
            )
        self.db.commit()

    def tearDown(self) -> None:
        self._session_patch.stop()
        self.db.close()

    def test_enqueue_sets_queued_batch(self) -> None:
        r = claim_mod.enqueue_collect("ta", manual=True)
        self.assertTrue(r.get("queued"), r)
        self.db.expire_all()
        task = self.db.get(BizStateTask, "ta")
        assert task is not None
        self.assertTrue(task.collect_running)
        batch = self.db.get(BizStateBatch, r["batch_id"])
        assert batch is not None
        self.assertEqual(batch.status, "queued")

    def test_enqueue_idempotent_while_running(self) -> None:
        r1 = claim_mod.enqueue_collect("ta", manual=True)
        self.assertTrue(r1.get("queued"), r1)
        r2 = claim_mod.enqueue_collect("ta", manual=True)
        self.assertFalse(r2.get("queued"))
        self.assertEqual(r2.get("reason"), "already_collecting")

    def test_claim_ne_mutex_skips_same_ne(self) -> None:
        # Session cap 1 reproduces the classic "one collection per NE" mutex.
        with patch.object(claim_mod, "max_concurrent_tasks", return_value=10),                 patch.object(claim_mod, "max_sessions_per_ne", return_value=1):
            ra = claim_mod.enqueue_collect("ta", manual=True)
            rb = claim_mod.enqueue_collect("tb", manual=True)
            rc = claim_mod.enqueue_collect("tc", manual=True)
            self.assertTrue(ra["queued"] and rb["queued"] and rc["queued"])
            claimed = claim_mod.claim_queued_batches(10)
        ids = {c["batch_id"] for c in claimed}
        # ta and tc can run; tb same NE as ta must wait
        self.assertIn(ra["batch_id"], ids)
        self.assertIn(rc["batch_id"], ids)
        self.assertNotIn(rb["batch_id"], ids)
        self.db.expire_all()
        self.assertEqual(self.db.get(BizStateBatch, ra["batch_id"]).status, "running")
        self.assertEqual(self.db.get(BizStateBatch, rb["batch_id"]).status, "queued")
        self.assertEqual(self.db.get(BizStateBatch, rc["batch_id"]).status, "running")

    def test_a_ne_runs_up_to_its_session_cap_and_no_more(self) -> None:
        self.db.add_all([BizStateTask(id=f"x{i}", source="managed", ne_id="ne-a", status="running") for i in range(3)])
        for i in range(3):
            self.db.add(BizStateTaskItem(id=uuid4().hex, task_id=f"x{i}", source_profile_id="zte.arp",
                                         kind="catalog", enabled=True))
        self.db.commit()
        with patch.object(claim_mod, "max_concurrent_tasks", return_value=10),                 patch.object(claim_mod, "max_sessions_per_ne", return_value=2):
            queued = [claim_mod.enqueue_collect(f"x{i}", manual=True)["batch_id"] for i in range(3)]
            first = claim_mod.claim_queued_batches(10)
            again = claim_mod.claim_queued_batches(10)
        self.assertEqual(len(first), 2)  # three batches queued for ne-a, only two sessions allowed
        self.assertEqual(again, [])
        self.db.expire_all()
        states = sorted(self.db.get(BizStateBatch, b).status for b in queued)
        self.assertEqual(states, ["queued", "running", "running"])

    def test_default_session_cap_is_two(self) -> None:
        self.assertEqual(claim_mod.max_sessions_per_ne(), 2)

    def test_single_task_group_is_a_valid_round_for_a_same_device_cutover(self) -> None:
        task = self.db.get(BizStateTask, "ta")
        task.collect_group_id = "solo"
        self.db.commit()
        with patch.object(claim_mod, "max_concurrent_tasks", return_value=10):
            r = claim_mod.enqueue_collect("ta", manual=True)
            self.assertTrue(r["queued"], r)
            claimed = claim_mod.claim_queued_batches(10)
        self.assertEqual([c["task_id"] for c in claimed], ["ta"])

    def test_a_pair_needs_both_sides_before_it_is_claimed(self) -> None:
        for tid, group in (("ta", "pair"), ("tc", "pair")):
            self.db.get(BizStateTask, tid).collect_group_id = group
        self.db.commit()
        with patch.object(claim_mod, "max_concurrent_tasks", return_value=10):
            r = claim_mod.enqueue_collect("ta", manual=True)
            self.assertTrue(r["queued"], r)
            self.db.expire_all()
            self.db.query(BizStateBatch).filter(BizStateBatch.task_id == "tc").delete()
            self.db.commit()
            self.assertEqual(claim_mod.claim_queued_batches(10), [])

    def test_claim_respects_slot_ceiling(self) -> None:
        with patch.object(claim_mod, "max_concurrent_tasks", return_value=1):
            claim_mod.enqueue_collect("ta", manual=True)
            claim_mod.enqueue_collect("tc", manual=True)
            first = claim_mod.claim_queued_batches(10)
            self.assertEqual(len(first), 1)
            second = claim_mod.claim_queued_batches(10)
            self.assertEqual(len(second), 0)


if __name__ == "__main__":
    unittest.main()
