"""Startup recovery for interrupted biz-state compare runs."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_state.compare_service import (
    cancel_compare_run,
    recover_interrupted_compares_on_startup,
)
from netx_api.db import Base
from netx_api.models import BizCompareJob, BizCompareRun


class BizStateCompareRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.db = TestingSession()
        self.job = BizCompareJob(
            id="j1",
            name="cutover",
            template_id="tpl1",
            status="active",
        )
        self.db.add(self.job)
        self.running = BizCompareRun(
            id="r_run",
            job_id="j1",
            status="running",
            message="loading 1/2 · BGP",
            summary_json={
                "progress": {"phase": "loading", "sheet_index": 1, "sheet_total": 2},
                "sheets": [
                    {"sheet_id": "bgp", "status": "running"},
                    {"sheet_id": "isis", "status": "pending"},
                ],
            },
        )
        self.queued = BizCompareRun(
            id="r_q",
            job_id="j1",
            status="queued",
            message="queued",
            summary_json={"sheets": [{"sheet_id": "bgp", "status": "pending"}]},
        )
        self.ok = BizCompareRun(
            id="r_ok",
            job_id="j1",
            status="success",
            message="done",
            summary_json={"added": 0, "removed": 0, "changed": 0},
        )
        self.db.add_all([self.running, self.queued, self.ok])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_startup_cancels_running_and_queued(self) -> None:
        out = recover_interrupted_compares_on_startup(self.db)
        self.assertEqual(out["runs"], 2)

        self.db.refresh(self.running)
        self.db.refresh(self.queued)
        self.db.refresh(self.ok)
        self.assertEqual(self.running.status, "cancelled")
        self.assertIn("interrupted_by_restart", self.running.message or "")
        self.assertEqual(
            (self.running.summary_json or {}).get("progress", {}).get("phase"),
            "cancelled",
        )
        sheets = list((self.running.summary_json or {}).get("sheets") or [])
        self.assertEqual(sheets[0].get("status"), "cancelled")
        self.assertEqual(sheets[1].get("status"), "cancelled")
        self.assertEqual(self.queued.status, "cancelled")
        self.assertEqual(self.ok.status, "success")

    def test_cancel_compare_run_user(self) -> None:
        out = cancel_compare_run(self.db, "r_run")
        self.assertEqual(out["status"], "cancelled")
        self.db.refresh(self.running)
        self.assertEqual(self.running.status, "cancelled")
        self.assertIn("cancelled_by_user", self.running.message or "")

    def test_cancel_idempotent_on_success(self) -> None:
        out = cancel_compare_run(self.db, "r_ok")
        self.assertEqual(out["status"], "success")


if __name__ == "__main__":
    unittest.main()
