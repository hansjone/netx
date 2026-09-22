"""Regression: dual-lane absorb must not inflate row_count from cumulative DB."""

from __future__ import annotations

import unittest

from netx_api.biz_state.collect_runner import _absorb_lane_result


class DualLaneAbsorbTests(unittest.TestCase):
    def test_absorb_sums_lane_deltas_not_cumulative(self) -> None:
        # Each lane should report only its own delta (0 when persist owns rows).
        lane_errors: list[str] = []
        total_rows, cmd_count, any_fail, any_ok = _absorb_lane_result(
            (0, 10, False, True),
            total_rows=0,
            cmd_count=0,
            any_fail=False,
            any_ok=False,
            lane_errors=lane_errors,
        )
        total_rows, cmd_count, any_fail, any_ok = _absorb_lane_result(
            (0, 5, False, True),
            total_rows=total_rows,
            cmd_count=cmd_count,
            any_fail=any_fail,
            any_ok=any_ok,
            lane_errors=lane_errors,
        )
        self.assertEqual(total_rows, 0)
        self.assertEqual(cmd_count, 15)
        self.assertTrue(any_ok)
        self.assertFalse(any_fail)

    def test_legacy_bug_pattern_would_inflate(self) -> None:
        # Document what NOT to do: feeding cumulative DB totals into absorb.
        lane_errors: list[str] = []
        # If light returns cumulative 100 and heavy returns cumulative 150...
        bad_rows, _, _, _ = _absorb_lane_result(
            (150, 5, False, True),
            total_rows=100,
            cmd_count=10,
            any_fail=False,
            any_ok=True,
            lane_errors=lane_errors,
        )
        self.assertEqual(bad_rows, 250)  # inflated — lanes must return deltas only


if __name__ == "__main__":
    unittest.main()
