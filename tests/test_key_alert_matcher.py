from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.key_alert_config import invalidate_key_alert_config_cache, set_key_alert_monitor_config
from netx_api.key_alert_matcher import (
    invalidate_key_alert_rule_cache,
    match_key_alert_rule,
    parse_rule_items,
    rule_storage_key,
)
from netx_api.models import UmeKeyAlertRule
from netx_api.ume_sync_service import apply_alarm_to_current, notification_id_from_norm


class KeyAlertMatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()
        invalidate_key_alert_rule_cache()
        invalidate_key_alert_config_cache()

    def tearDown(self) -> None:
        self.db.close()
        invalidate_key_alert_config_cache()

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
        set_key_alert_monitor_config(self.db, forward_on_clear=True)
        invalidate_key_alert_rule_cache()
        self.assertIsNotNone(match_key_alert_rule(self.db, norm=cleared, action="deleted"))

    def test_parse_rule_items(self) -> None:
        self.assertEqual(parse_rule_items("a, b；c\n d"), ["a", "b", "c", "d"])
        self.assertEqual(parse_rule_items("A, a"), ["A"])
        self.assertEqual(parse_rule_items(""), [])

    def test_rule_storage_key_keyword(self) -> None:
        self.assertEqual(rule_storage_key(match_type="keyword", value="BGP Down"), "kw:bgp down")

    def test_keyword_match_insert(self) -> None:
        pk = rule_storage_key(match_type="keyword", value="链路中断")
        self.db.add(
            UmeKeyAlertRule(
                notification_id=pk,
                match_type="keyword",
                match_value="链路中断",
                enabled=1,
                label="链路",
            )
        )
        self.db.commit()
        invalidate_key_alert_rule_cache()
        norm = {
            "notificationId": "NID-999",
            "nativeProbableCause": "核心链路中断告警",
            "is-cleared": False,
        }
        rule = match_key_alert_rule(self.db, norm=norm, action="inserted")
        self.assertIsNotNone(rule)
        assert rule is not None
        self.assertEqual(rule.match_value, "链路中断")

        norm_miss = {"notificationId": "NID-1", "nativeProbableCause": "CPU high", "is-cleared": False}
        self.assertIsNone(match_key_alert_rule(self.db, norm=norm_miss, action="inserted"))

    def test_keyword_match_case_insensitive(self) -> None:
        pk = rule_storage_key(match_type="keyword", value="bgp down")
        self.db.add(
            UmeKeyAlertRule(
                notification_id=pk,
                match_type="keyword",
                match_value="BGP DOWN",
                enabled=1,
                label="BGP",
            )
        )
        self.db.commit()
        invalidate_key_alert_rule_cache()
        for cause in ("BGP DOWN alarm", "bgp down on port", "Link BGP DOWN detected"):
            norm = {"notificationId": "NID-x", "nativeProbableCause": cause, "is-cleared": False}
            self.assertIsNotNone(
                match_key_alert_rule(self.db, norm=norm, action="inserted"),
                msg=f"expected match for cause={cause!r}",
            )


if __name__ == "__main__":
    unittest.main()
