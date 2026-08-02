"""Smoke tests for DB pool / CLI budget / metrics wiring."""

from __future__ import annotations

import unittest

from netx_api.cli_budget import acquire_cli_slot, clamp_cli_workers, cli_budget_status
from netx_api.config import settings
from netx_api.db import db_pool_status
from netx_api.metrics_router import _prom_lines, collect_runtime_metrics


class StabilityHardeningTests(unittest.TestCase):
    def test_settings_have_pool_and_budget_knobs(self) -> None:
        self.assertGreaterEqual(int(settings.db_pool_size), 1)
        self.assertGreaterEqual(int(settings.cli_max_concurrent), 1)
        self.assertGreaterEqual(int(settings.audit_queue_max), 100)

    def test_clamp_cli_workers(self) -> None:
        n = clamp_cli_workers(999, hard_cap=32)
        self.assertLessEqual(n, int(settings.cli_max_concurrent))
        self.assertLessEqual(n, 32)
        self.assertGreaterEqual(n, 1)

    def test_cli_budget_acquire_release(self) -> None:
        before = cli_budget_status()["in_use"]
        with acquire_cli_slot() as ok:
            self.assertTrue(ok)
            self.assertEqual(cli_budget_status()["in_use"], before + 1)
        self.assertEqual(cli_budget_status()["in_use"], before)

    def test_db_pool_status_shape(self) -> None:
        st = db_pool_status()
        self.assertIn("backend", st)
        self.assertIn("pool_size_cfg", st)

    def test_metrics_prometheus_text(self) -> None:
        body = _prom_lines(collect_runtime_metrics())
        self.assertIn("netx_uptime_seconds", body)
        self.assertIn("netx_thread_count", body)
        self.assertIn("netx_cli_budget_limit", body)


if __name__ == "__main__":
    unittest.main()
