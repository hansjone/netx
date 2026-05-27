from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from netx_api.collection_job_state import finalize_collection_job


class CollectionJobFinalizeTests(unittest.TestCase):
    def test_paused_job_stays_paused_when_all_runs_terminal(self):
        job = MagicMock()
        job.status = "paused"
        job.ended_at = None
        job.success_count = 0
        job.fail_count = 0

        run_ok = MagicMock(status="success")
        run_cancel = MagicMock(status="cancelled")

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [run_ok, run_cancel]
        db.get.return_value = job

        finalize_collection_job(db, "job-1")

        self.assertEqual(job.status, "paused")
        self.assertEqual(job.success_count, 1)
        self.assertEqual(job.fail_count, 1)
        self.assertIsNotNone(job.ended_at)
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
