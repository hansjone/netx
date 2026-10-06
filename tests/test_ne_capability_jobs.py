"""Tests for NE capability descriptors and background exec jobs."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from netx_api.ne_capability import build_ne_capability
from netx_api.ne_exec_jobs import (
    get_ne_exec_job,
    should_run_exec_async,
    start_ne_exec_job,
)


class NeCapabilityTests(unittest.TestCase):
    def test_linux_shell_recommends_script(self) -> None:
        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=True):
            cap = build_ne_capability(
                device_type="linux",
                exec_policy="linux_shell",
                vendor="Other",
            )
        self.assertEqual(cap["device_family"], "linux")
        self.assertEqual(cap["exec_policy_effective"], "linux_shell")
        self.assertTrue(cap["allows_shell_scripts"])
        self.assertTrue(cap["supports_async_job"])
        self.assertEqual(cap["recommended_mode"], "script_on_device")

    def test_mikrotik_shell_recommends_short_cli(self) -> None:
        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=True):
            cap = build_ne_capability(
                device_type="mikrotik_routeros",
                exec_policy="linux_shell",
            )
        self.assertEqual(cap["device_family"], "mikrotik")
        self.assertEqual(cap["recommended_mode"], "short_cli")
        self.assertTrue(cap["allows_multiline"])

    def test_readonly_network_cli(self) -> None:
        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=True):
            cap = build_ne_capability(device_type="zte_zxros", exec_policy="readonly")
        self.assertEqual(cap["device_family"], "network_cli")
        self.assertEqual(cap["recommended_mode"], "show_only")
        self.assertFalse(cap["allows_shell_scripts"])

    def test_feature_off_forces_readonly_effective(self) -> None:
        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=False):
            cap = build_ne_capability(device_type="linux", exec_policy="linux_shell")
        self.assertEqual(cap["exec_policy_stored"], "linux_shell")
        self.assertEqual(cap["exec_policy_effective"], "readonly")
        self.assertFalse(cap["allows_shell_scripts"])


class NeExecJobTests(unittest.TestCase):
    def test_should_run_async_flags(self) -> None:
        self.assertTrue(should_run_exec_async({"async": True, "ne_id": "a"}))
        self.assertFalse(should_run_exec_async({"async": False, "ne_ids": ["a", "b", "c", "d", "e"]}))
        with patch("netx_api.ne_exec_jobs.async_min_ne_count", return_value=4):
            self.assertTrue(
                should_run_exec_async({"ne_ids": ["1", "2", "3", "4"]})
            )
            self.assertFalse(
                should_run_exec_async({"ne_ids": ["1", "2"]})
            )

    def test_job_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"NETX_NE_EXEC_JOB_DIR": tmp}):
                ack = start_ne_exec_job(
                    kind="exec",
                    arguments={"ne_id": "abc", "commands": ["show version"]},
                    runner=lambda: {"ok": True, "output": "hi"},
                )
                self.assertTrue(ack.get("ok"))
                jid = str(ack.get("job_id") or "")
                self.assertTrue(jid)
                done = None
                for _ in range(50):
                    polled = get_ne_exec_job(jid)
                    if polled.get("terminal"):
                        done = polled
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(done)
                assert done is not None
                self.assertEqual(done.get("status"), "succeeded")
                self.assertEqual((done.get("result") or {}).get("output"), "hi")
                self.assertTrue(Path(tmp, f"{jid}.json").is_file())


if __name__ == "__main__":
    unittest.main()
