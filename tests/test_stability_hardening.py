"""Smoke tests for DB pool / CLI budget / metrics / production defaults."""

from __future__ import annotations

import unittest

from netx_api.cli_budget import acquire_cli_slot, clamp_cli_workers, cli_budget_status, feature_hard_cap
from netx_api.config import Settings, settings
from netx_api.db import db_pool_status
from netx_api.metrics_router import _prom_lines, collect_runtime_metrics
from netx_api.runtime_budget import log_runtime_budget


class StabilityHardeningTests(unittest.TestCase):
    def test_production_defaults(self) -> None:
        import os
        from unittest.mock import patch

        # Ignore process env (one-click start may set NETX_RUN_INLINE_SCHEDULERS=false).
        clean = {k: v for k, v in os.environ.items() if not k.startswith("NETX_")}
        with patch.dict(os.environ, clean, clear=True):
            s = Settings(_env_file=None)
        self.assertEqual(s.db_pool_size, 40)
        self.assertEqual(s.db_max_overflow, 40)
        self.assertEqual(s.cli_max_concurrent, 24)
        self.assertEqual(s.cli_feature_hard_cap, 32)
        self.assertEqual(s.cli_timeout_pool_workers, 24)
        self.assertEqual(s.port_traffic_dispatch_workers, 6)
        self.assertEqual(s.ne_connect_max_workers, 8)
        self.assertEqual(s.ne_collect_max_workers, 8)
        self.assertEqual(s.webcrt_max_sessions, 40)
        self.assertEqual(s.webcrt_keepalive_sec, 30)
        self.assertEqual(s.audit_sample_n, 5)
        self.assertEqual(s.audit_queue_max, 5000)
        self.assertEqual(s.oclaw_forward_max_retries, 3)
        self.assertEqual(s.oclaw_forward_queue_max, 5000)
        self.assertEqual(s.ne_collect_max_output_bytes, 8 * 1024 * 1024)
        self.assertEqual(s.webcrt_session_log_max_bytes, 4 * 1024 * 1024)
        self.assertEqual(s.ume_raw_json_max_bytes, 64 * 1024)
        self.assertEqual(s.ne_collection_keep_days, 14)
        self.assertTrue(s.run_inline_schedulers)
        self.assertEqual(s.scheduler_heartbeat_path, "data/runtime/scheduler_heartbeat.json")
        # Pool should cover CLI + multi-user HTTP/WS reserve under defaults.
        self.assertGreaterEqual(s.db_pool_size + s.db_max_overflow, s.cli_max_concurrent + 24)

    def test_settings_have_pool_and_budget_knobs(self) -> None:
        self.assertGreaterEqual(int(settings.db_pool_size), 1)
        self.assertGreaterEqual(int(settings.cli_max_concurrent), 1)
        self.assertGreaterEqual(int(settings.audit_queue_max), 100)

    def test_clamp_cli_workers(self) -> None:
        n = clamp_cli_workers(999)
        self.assertLessEqual(n, int(settings.cli_max_concurrent))
        self.assertLessEqual(n, feature_hard_cap())
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
        self.assertIn("netx_oclaw_forwarder_dropped", body)
        self.assertIn("netx_device_schedulers_stale", body)

    def test_log_runtime_budget_does_not_raise(self) -> None:
        log_runtime_budget(role="test")

    def test_scheduler_heartbeat_roundtrip(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from netx_api.scheduler_heartbeat import (
            publish_scheduler_heartbeat,
            read_scheduler_heartbeat,
            resolve_device_scheduler_metrics,
        )

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "hb.json"
            with patch("netx_api.scheduler_heartbeat.heartbeat_path", return_value=path):
                publish_scheduler_heartbeat(role="worker")
                hb = read_scheduler_heartbeat(max_age_sec=60)
                self.assertIsNotNone(hb)
                assert hb is not None
                self.assertFalse(hb["stale"])
                self.assertEqual(hb.get("role"), "worker")
                with patch.object(settings, "run_inline_schedulers", False):
                    resolved = resolve_device_scheduler_metrics()
                self.assertEqual(resolved["mode"], "external_worker")
                self.assertEqual(resolved["source"], "heartbeat")
                self.assertFalse(resolved["stale"])
                self.assertIn("port_traffic", resolved)


if __name__ == "__main__":
    unittest.main()
