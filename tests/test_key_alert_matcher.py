from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.key_alert_matcher import invalidate_key_alert_rule_cache, match_key_alert_rule
from netx_api.models import UmeKeyAlertRule
from netx_api.ume_sync_service import apply_alarm_to_current, notification_id_from_norm


class KeyAlertMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()
        invalidate_key_alert_rule_cache()

    def tearDown(self) -> None:
        self.db.close()

    def test_notification_id_from_norm(self) -> None:
        norm = {"notificationId": "NID-1001", "is-cleared": False}
        self.assertEqual(notification_id_from_norm(norm), "NID-1001")
        norm2 = {"notification-id": "NID-1002"}
        self.assertEqual(notification_id_from_norm(norm2), "NID-1002")

    def test_apply_alarm_persists_notification_id(self) -> None:
        alarm = {
            "alarmkey": "AK-NID-1",
            "notificationId": "NID-9001",
            "is-cleared": False,
            "perceivedSeverity": "critical",
        }
        action, changed = apply_alarm_to_current(self.db, alarm, touch_ts=datetime.utcnow())
        self.db.commit()
        self.assertEqual(action, "inserted")
        self.assertTrue(changed)
        from netx_api.models import UmeAlarmCurrent

        row = self.db.get(UmeAlarmCurrent, "AK-NID-1")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.notification_id, "NID-9001")

    def test_match_insert_and_clear(self) -> None:
        self.db.add(
            UmeKeyAlertRule(
                notification_id="NID-42",
                enabled=1,
                forward_on_clear=0,
                label="test",
            )
        )
        self.db.commit()
        invalidate_key_alert_rule_cache()
        norm = {"notificationId": "NID-42", "is-cleared": False}
        rule = match_key_alert_rule(self.db, norm=norm, action="inserted")
        self.assertIsNotNone(rule)
        cleared = {"notificationId": "NID-42", "is-cleared": True}
        self.assertIsNone(match_key_alert_rule(self.db, norm=cleared, action="deleted"))
        row = self.db.get(UmeKeyAlertRule, "NID-42")
        assert row is not None
        row.forward_on_clear = 1
        self.db.commit()
        invalidate_key_alert_rule_cache()
        self.assertIsNotNone(match_key_alert_rule(self.db, norm=cleared, action="deleted"))


if __name__ == "__main__":
    unittest.main()
