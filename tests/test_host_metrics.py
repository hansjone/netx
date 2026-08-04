"""Host CPU/memory metrics (Windows + Linux)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import netx_api.host_metrics as host_metrics
from netx_api.host_metrics import collect_host_metrics


class HostMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        host_metrics._CACHED_HOST = None
        host_metrics._CACHED_AT = 0.0

    def test_collect_returns_cpu_mem_fields(self) -> None:
        out = collect_host_metrics()
        src = str(out.get("source") or "")
        self.assertTrue(src.startswith("psutil") or src in {"stdlib", "error"}, src)
        self.assertIn("cpu_percent", out)
        self.assertIn("mem_percent", out)
        self.assertGreaterEqual(float(out["cpu_percent"]), 0.0)
        self.assertLessEqual(float(out["cpu_percent"]), 100.0)
        self.assertGreaterEqual(float(out["mem_percent"]), 0.0)
        self.assertLessEqual(float(out["mem_percent"]), 100.0)
        self.assertGreaterEqual(int(out.get("mem_total_bytes") or 0), 0)

    def test_stdlib_fallback_when_psutil_missing(self) -> None:
        with patch("netx_api.host_metrics._host_via_psutil", return_value=None):
            out = collect_host_metrics()
        self.assertEqual(out.get("source"), "stdlib")
        self.assertIn("cpu_percent", out)
        self.assertIn("mem_percent", out)


if __name__ == "__main__":
    unittest.main()
