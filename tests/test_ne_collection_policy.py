from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from netx_api.collection_policy import (
    DEFAULT_HISTORY_KEEP,
    _normalize_interval_hours,
    history_keep_value,
    next_due_at,
    policy_to_out,
)
from netx_api.collection_schemas import CollectionPolicyUpdate
from netx_api.models import NeCollectionJob, NeCollectionPolicy


class NeCollectionPolicyDefaultsTests(unittest.TestCase):
    def test_default_history_keep_is_three(self) -> None:
        self.assertEqual(DEFAULT_HISTORY_KEEP, 3)
        row = NeCollectionPolicy(id=1, enabled=False, history_keep=3)
        self.assertEqual(history_keep_value(row), 3)
        out = policy_to_out(row)
        self.assertFalse(out.enabled)
        self.assertEqual(out.history_keep, 3)

    def test_normalize_interval_hours_from_days(self) -> None:
        row = NeCollectionPolicy(id=1, interval_hours=0, interval_days=2)
        self.assertEqual(_normalize_interval_hours(row), 48)

    def test_policy_update_schema_bounds(self) -> None:
        body = CollectionPolicyUpdate(history_keep=3, interval_hours=24, enabled=False)
        data = body.model_dump(exclude_unset=True)
        self.assertEqual(data["history_keep"], 3)
        self.assertFalse(data["enabled"])


class NeCollectionNextDueTests(unittest.TestCase):
    def test_next_due_none_when_disabled(self) -> None:
        db = MagicMock()
        policy = NeCollectionPolicy(id=1, enabled=False, interval_hours=24)
        self.assertIsNone(next_due_at(db, policy))

    def test_next_due_now_when_no_prior_schedule(self) -> None:
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        policy = NeCollectionPolicy(id=1, enabled=True, interval_hours=24)
        due = next_due_at(db, policy)
        self.assertIsNotNone(due)
        assert due is not None
        self.assertLessEqual(abs((due - datetime.now()).total_seconds()), 5)

    def test_next_due_from_last_scheduled_done(self) -> None:
        db = MagicMock()
        ended = datetime.now() - timedelta(hours=2)
        last = NeCollectionJob(
            id="abc",
            status="done",
            trigger_mode="schedule",
            ended_at=ended,
        )
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = last
        policy = NeCollectionPolicy(id=1, enabled=True, interval_hours=24)
        due = next_due_at(db, policy)
        self.assertIsNotNone(due)
        assert due is not None
        self.assertEqual(due, ended + timedelta(hours=24))


if __name__ == "__main__":
    unittest.main()
