"""Collect work-list dedupe / if_intf remap / manual collect."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.biz_state.collect_runner import (
    _resolve_collect_profile,
    dispatch_collect,
    trigger_collect_now,
)
from netx_api.biz_state.profiles import get_profile, reload_profiles


class CollectProfileResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_profiles()

    def test_if_intf_remaps_to_config_interface(self) -> None:
        p = _resolve_collect_profile("zte.if_intf")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.profile_id, "zte.config_interface")
        self.assertEqual(p.metric_id, "config_interface")

    def test_enabled_profile_passthrough(self) -> None:
        p = _resolve_collect_profile("zte.arp")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.profile_id, "zte.arp")

    def test_if_intf_profile_disabled(self) -> None:
        raw = get_profile("zte.if_intf")
        self.assertIsNotNone(raw)
        assert raw is not None
        self.assertFalse(raw.enabled)


class ManualCollectTests(unittest.TestCase):
    def test_trigger_collect_now_passes_manual(self) -> None:
        task = MagicMock()
        task.collect_running = False
        db = MagicMock()
        db.get.return_value = task
        with (
            patch("netx_api.biz_state.collect_runner.SessionLocal", return_value=db),
            patch("netx_api.biz_state.collect_runner.dispatch_collect") as dc,
        ):
            out = trigger_collect_now("t1")
        self.assertTrue(out["ok"])
        dc.assert_called_once_with("t1", manual=True)

    def test_dispatch_manual_allows_paused(self) -> None:
        with patch(
            "netx_api.biz_state.claim.enqueue_collect",
            return_value={
                "ok": False,
                "queued": False,
                "reason": "no_enabled_items",
                "task_id": "t1",
            },
        ) as enq:
            dispatch_collect("t1", manual=True)
        enq.assert_called_once_with("t1", manual=True)

    def test_dispatch_inline_skips_if_already_claimed(self) -> None:
        with (
            patch(
                "netx_api.biz_state.claim.enqueue_collect",
                return_value={
                    "ok": True,
                    "queued": True,
                    "batch_id": "b1",
                    "task_id": "t1",
                },
            ),
            patch(
                "netx_api.biz_state.collect_runner._should_execute_inline",
                return_value=True,
            ),
            patch(
                "netx_api.biz_state.collect_runner._try_claim_batch_for_execute",
                return_value=False,
            ) as claim,
            patch(
                "netx_api.biz_state.collect_runner.execute_claimed_batch"
            ) as exe,
        ):
            dispatch_collect("t1", manual=True)
        claim.assert_called_once_with("b1")
        exe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
