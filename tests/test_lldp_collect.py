"""LLDP collect policy / dashboard (network topology management)."""

from __future__ import annotations

import unittest
from uuid import uuid4

from netx_api.db import Base, SessionLocal, engine
from netx_api.lldp_collect_schemas import LldpCollectPolicyUpdate
from netx_api.lldp_collect_service import (
    ensure_policy,
    get_dashboard,
    next_due_at,
    update_policy,
)
from netx_api.models import LldpCollectPolicy


class LldpCollectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        Base.metadata.create_all(bind=engine)

    def setUp(self) -> None:
        self.db = SessionLocal()
        self.db.query(LldpCollectPolicy).delete()
        self.db.commit()

    def tearDown(self) -> None:
        # Do not leave scheduled collect enabled in shared DB.
        row = self.db.get(LldpCollectPolicy, 1)
        if row is not None:
            row.enabled = False
            self.db.commit()
        self.db.close()

    def test_policy_defaults_disabled(self) -> None:
        row = ensure_policy(self.db)
        self.assertEqual(row.id, 1)
        self.assertFalse(row.enabled)
        dash = get_dashboard(self.db)
        self.assertFalse(dash.policy.enabled)
        self.assertIsNone(dash.next_due_at)

    def test_policy_enable_updates(self) -> None:
        ensure_policy(self.db)
        out = update_policy(
            self.db,
            LldpCollectPolicyUpdate(
                enabled=True,
                interval_days=2,
                concurrency=6,
                scope_mode="all",
                auto_add_unmatched=True,
            ),
        )
        self.assertTrue(out.enabled)
        self.assertEqual(out.interval_days, 2)
        self.assertEqual(out.concurrency, 6)
        due = next_due_at(self.db, ensure_policy(self.db))
        self.assertIsNotNone(due)


if __name__ == "__main__":
    unittest.main()
