from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.main import _extract_ume_raw_group_field, _serialize_ume_alarm_raw_row, sql_ume_query, ume_alarms_fields
from netx_api.models import UmeAlarmCurrent, UmeInventoryNE
from netx_api.ume_client import UMEClient
from netx_api.ume_sync_service import _derive_ne_id_from_alarm, sync_alarms_current, sync_inventory_full
from fastapi import HTTPException


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = int(status_code)
        self._payload = payload
        self.text = str(payload)
        self.is_success = 200 <= self.status_code < 300
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, headers=None):
        if not self._responses:
            raise RuntimeError("no_more_fake_responses")
        return self._responses.pop(0)

    def request(self, method, url, params=None, json=None, headers=None):
        if not self._responses:
            raise RuntimeError("no_more_fake_responses")
        return self._responses.pop(0)


class UMEClientTests(unittest.TestCase):
    def test_login_and_cached_token(self):
        responses = [
            _FakeResponse(200, {"output": {"accessToken": "token-1", "expires": 1800}}),
        ]
        with patch("netx_api.ume_client.httpx.Client", return_value=_FakeClient(responses)):
            client = UMEClient(
                base_url="https://ume.local:18014",
                username="u",
                password="p",
                verify_tls=False,
            )
            t1 = client.login()
            t2 = client.login()
            self.assertEqual(t1, "token-1")
            self.assertEqual(t2, "token-1")

    def test_request_retry_after_401(self):
        responses = [
            _FakeResponse(200, {"output": {"accessToken": "token-1", "expires": 1800}}),
            _FakeResponse(401, {"error": "expired"}),
            _FakeResponse(200, {"output": {"accessToken": "token-2", "expires": 1800}}),
            _FakeResponse(200, {"alarm-list": []}),
        ]
        with patch("netx_api.ume_client.httpx.Client", return_value=_FakeClient(responses)):
            client = UMEClient(
                base_url="https://ume.local:18014",
                username="u",
                password="p",
                verify_tls=False,
            )
            payload, diag = client.request_json("GET", "/restconf/data/zte-alarms:alarms/alarm-list")
            self.assertIsInstance(payload, dict)
            self.assertEqual(diag.retry_count, 1)
            self.assertEqual(client.refresh_if_needed(), "token-2")

    def test_extract_network_elements_from_wrapped_container(self):
        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)

        def _fake_request_json(method: str, path: str, **kwargs):
            return (
                {
                    "zte-resources-module:network-elements": {
                        "network-element": [
                            {"ne-id": "NE-1", "name": "ne1"},
                            {"ne-id": "NE-2", "name": "ne2"},
                        ]
                    }
                },
                None,
            )

        client.request_json = _fake_request_json  # type: ignore[method-assign]
        rows, _ = client.get_network_elements()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].get("ne-id"), "NE-1")

    def test_extract_alarm_rows_from_wrapped_alarm_list(self):
        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)

        def _fake_request_json(method: str, path: str, **kwargs):
            return (
                {
                    "zte-alarms:alarms": {
                        "alarm-list": {
                            "alarm": [
                                {"alarmKey": "AK-1", "ne-id": "NE-1"},
                                {"alarmKey": "AK-2", "ne-id": "NE-2"},
                            ]
                        }
                    }
                },
                None,
            )

        client.request_json = _fake_request_json  # type: ignore[method-assign]
        rows, _ = client.get_alarms(is_uncleared=False)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].get("alarmKey"), "AK-2")


class UmeSyncServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()

    def tearDown(self):
        self.db.close()

    def test_sync_inventory_upsert(self):
        class _C:
            def get_network_elements(self, *, limit=None, marker=None):
                class _D:
                    marker = ""
                    is_end_of_reply = True

                rows = [
                    {
                        "ne-id": "NE-1",
                        "name": "ne1",
                        "user-label": "网元1",
                        "ip-Address": "10.0.0.1",
                        "ipv6-address": "2001:db8::1",
                        "type": "A",
                        "device-level": "Access",
                        "host-name": "host-1",
                        "hardware-version": "V1",
                        "vendor-name": "ZTE",
                    },
                ]
                return rows, _D()

        job1 = sync_inventory_full(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job1.status, "done")
        self.assertEqual(job1.inserted_count, 1)

        job2 = sync_inventory_full(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job2.status, "done")
        self.assertEqual(job2.updated_count, 1)

        ne = self.db.get(UmeInventoryNE, "NE-1")
        self.assertIsNotNone(ne)
        self.assertEqual(ne.user_label, "网元1")
        self.assertEqual(ne.device_level, "Access")
        self.assertEqual(ne.host_name, "host-1")
        self.assertEqual(ne.hardware_version, "V1")
        self.assertEqual(ne.ipv6_address, "2001:db8::1")

    def test_sync_inventory_marker_pagination(self):
        class _C:
            def get_network_elements(self, *, limit=None, marker=None):
                class _D:
                    def __init__(self, mk: str, end: bool):
                        self.marker = mk
                        self.is_end_of_reply = end

                if not marker:
                    return (
                        [
                            {"ne-id": "NE-1", "name": "ne1"},
                        ],
                        _D("NM2", False),
                    )
                if marker == "NM2":
                    return (
                        [
                            {"ne-id": "NE-2", "name": "ne2"},
                        ],
                        _D("NM3", True),
                    )
                return ([], _D("", True))

        job = sync_inventory_full(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job.status, "done")
        self.assertEqual(job.pulled_count, 2)
        self.assertIsNotNone(self.db.get(UmeInventoryNE, "NE-1"))
        self.assertIsNotNone(self.db.get(UmeInventoryNE, "NE-2"))

    def test_sync_inventory_reconcile_removes_ne_not_in_snapshot(self):
        class _CWide:
            def get_network_elements(self, *, limit=None, marker=None):
                class _D:
                    marker = ""
                    is_end_of_reply = True

                return (
                    [
                        {"ne-id": "NE-1", "name": "ne1"},
                        {"ne-id": "NE-2", "name": "ne2"},
                    ],
                    _D(),
                )

        class _CNarrow:
            def get_network_elements(self, *, limit=None, marker=None):
                class _D:
                    marker = ""
                    is_end_of_reply = True

                return ([{"ne-id": "NE-1", "name": "ne1"}], _D())

        sync_inventory_full(self.db, _CWide(), trigger_mode="manual")
        self.assertIsNotNone(self.db.get(UmeInventoryNE, "NE-2"))

        sync_inventory_full(self.db, _CNarrow(), trigger_mode="manual")
        self.assertIsNotNone(self.db.get(UmeInventoryNE, "NE-1"))
        self.assertIsNone(self.db.get(UmeInventoryNE, "NE-2"))

    def test_sync_inventory_partial_pull_does_not_delete(self):
        class _CPartial:
            def get_network_elements(self, *, limit=None, marker=None):
                class _D:
                    marker = ""
                    is_end_of_reply = False

                return ([{"ne-id": "NE-9", "name": "ne9"}], _D())

        self.db.add(UmeInventoryNE(ne_id="NE-OLD", ne_name="old"))
        self.db.commit()

        sync_inventory_full(self.db, _CPartial(), trigger_mode="manual")
        self.assertIsNotNone(self.db.get(UmeInventoryNE, "NE-9"))
        self.assertIsNotNone(self.db.get(UmeInventoryNE, "NE-OLD"))

    def test_sync_current_alarms_reconcile_drops_missing_keys(self):
        class _COne:
            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                class _D:
                    marker = ""
                    is_end_of_reply = True

                return (
                    [
                        {
                            "alarmKey": "AK-KEEP",
                            "ne-id": "NE-1",
                            "perceivedSeverity": "major",
                            "isCleared": "false",
                        },
                    ],
                    _D(),
                )

        self.db.add(
            UmeAlarmCurrent(
                alarm_key="AK-GONE",
                ne_id="NE-X",
                perceived_severity="minor",
                is_cleared="false",
            )
        )
        self.db.commit()

        sync_alarms_current(self.db, _COne(), trigger_mode="manual")
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-KEEP"))
        self.assertIsNone(self.db.get(UmeAlarmCurrent, "AK-GONE"))

    def test_sync_current_alarms_partial_pull_keeps_stale_rows(self):
        class _CPartial:
            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                class _D:
                    marker = ""
                    is_end_of_reply = False

                return (
                    [
                        {
                            "alarmKey": "AK-NEW",
                            "ne-id": "NE-1",
                            "perceivedSeverity": "major",
                            "isCleared": "false",
                        },
                    ],
                    _D(),
                )

        self.db.add(
            UmeAlarmCurrent(
                alarm_key="AK-STALE",
                ne_id="NE-X",
                perceived_severity="minor",
                is_cleared="false",
            )
        )
        self.db.commit()

        sync_alarms_current(self.db, _CPartial(), trigger_mode="manual")
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-NEW"))
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-STALE"))

    def test_sync_current_alarms_upsert(self):
        class _C:
            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                rows = [
                    {
                        "alarmKey": "AK-1",
                        "ne-id": "NE-1",
                        "ne-name": "ne1",
                        "user-label": "网元1",
                        "objectName": "port-1",
                        "eventType": "COMMUNICATION",
                        "nativeProbableCause": "LOS",
                        "perceivedSeverity": "critical",
                        "isCleared": "false",
                        "timeCreated": "2026-01-01T00:00:00Z",
                    }
                ]
                class _D:
                    marker = ""
                    is_end_of_reply = True

                return rows, _D()

        job1, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job1.status, "done")
        self.assertEqual(job1.inserted_count, 1)

        job2, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job2.status, "done")
        self.assertEqual(job2.updated_count, 1)

        row = self.db.get(UmeAlarmCurrent, "AK-1")
        self.assertIsNotNone(row)
        self.assertEqual(row.perceived_severity, "critical")

    def test_sync_current_alarms_marker_pagination(self):
        class _C:
            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                class _D:
                    def __init__(self, mk: str, end: bool):
                        self.marker = mk
                        self.is_end_of_reply = end

                if not marker:
                    return (
                        [
                            {"alarmKey": "AK-1", "ne-id": "NE-1", "perceivedSeverity": "major", "isCleared": "false"},
                            {"alarmKey": "AK-2", "ne-id": "NE-2", "perceivedSeverity": "major", "isCleared": "false"},
                        ],
                        _D("M2", False),
                    )
                if marker == "M2":
                    return (
                        [
                            {"alarmKey": "AK-3", "ne-id": "NE-3", "perceivedSeverity": "minor", "isCleared": "false"},
                        ],
                        _D("M3", True),
                    )
                return ([], _D("", True))

        from netx_api import ume_sync_service as svc
        old_page_size = svc.settings.ume_page_size
        old_max_pages = svc.settings.ume_max_pages
        svc.settings.ume_page_size = 2
        svc.settings.ume_max_pages = 10
        try:
            job, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual")
        finally:
            svc.settings.ume_page_size = old_page_size
            svc.settings.ume_max_pages = old_max_pages
        self.assertEqual(job.status, "done")
        self.assertEqual(job.pulled_count, 3)
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-3"))

    def test_sync_current_alarms_iterator_500_as_end(self):
        class _C:
            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                class _D:
                    def __init__(self, mk: str, end: bool):
                        self.marker = mk
                        self.is_end_of_reply = end

                if not marker:
                    return (
                        [
                            {"alarmKey": "AK-1", "ne-id": "NE-1", "perceivedSeverity": "major", "isCleared": "false"},
                        ],
                        _D("M2", False),
                    )
                raise RuntimeError(
                    "ume_request_failed:500:{\"error\":{\"errorCode\":\"500\",\"errorInfo\":\"iterator is null\"}}"
                )

        from netx_api import ume_sync_service as svc

        old_page_size = svc.settings.ume_marker_page_limit
        old_max_pages = svc.settings.ume_marker_max_pages
        old_500_as_end = svc.settings.ume_iterator_500_as_end
        svc.settings.ume_marker_page_limit = 1000
        svc.settings.ume_marker_max_pages = 10
        svc.settings.ume_iterator_500_as_end = True
        try:
            job, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual")
        finally:
            svc.settings.ume_marker_page_limit = old_page_size
            svc.settings.ume_marker_max_pages = old_max_pages
            svc.settings.ume_iterator_500_as_end = old_500_as_end

        self.assertEqual(job.status, "done")
        self.assertEqual(job.pulled_count, 1)
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-1"))

    def test_sync_current_alarms_stop_when_marker_missing(self):
        class _C:
            def __init__(self):
                self.calls = 0

            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                self.calls += 1

                class _D:
                    marker = ""
                    is_end_of_reply = None

                return (
                    [
                        {"alarmKey": "AK-1", "ne-id": "NE-1", "perceivedSeverity": "major", "isCleared": "false"},
                    ],
                    _D(),
                )

        c = _C()
        job, _ = sync_alarms_current(self.db, c, trigger_mode="manual")
        self.assertEqual(job.status, "done")
        self.assertEqual(job.pulled_count, 1)
        self.assertEqual(c.calls, 1)
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-1"))

    def test_sync_current_alarms_stop_when_not_end_but_marker_missing(self):
        class _C:
            def __init__(self):
                self.calls = 0

            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                self.calls += 1

                class _D:
                    marker = ""
                    is_end_of_reply = False

                return (
                    [
                        {"alarmKey": "AK-9", "ne-id": "NE-9", "perceivedSeverity": "major", "isCleared": "false"},
                    ],
                    _D(),
                )

        c = _C()
        job, _ = sync_alarms_current(self.db, c, trigger_mode="manual")
        self.assertEqual(job.status, "done")
        self.assertEqual(job.pulled_count, 1)
        self.assertEqual(c.calls, 1)
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-9"))

    def test_derive_ne_id_from_alarmkey_formats(self):
        alarm_hash = {"alarmkey": "00ceb960-1b62-478e-8303-0935ffea1d28#99010"}
        alarm_csv = {"alarmkey": "00ceb960-1b62-478e-8303-0935ffea1d28, 4237, 79"}
        alarm_space = {"alarmkey": "00ceb960-1b62-478e-8303-0935ffea1d28 4205 3588"}
        alarm_object_name_me = {"alarmkey": "no-net-id-here", "objectName": "OLT/ME{00ceb960-1b62-478e-8303-0935ffea1d28}/PON-1"}

        self.assertEqual(
            _derive_ne_id_from_alarm(alarm_hash),
            "00ceb960-1b62-478e-8303-0935ffea1d28",
        )
        self.assertEqual(
            _derive_ne_id_from_alarm(alarm_csv),
            "00ceb960-1b62-478e-8303-0935ffea1d28",
        )
        self.assertEqual(
            _derive_ne_id_from_alarm(alarm_space),
            "00ceb960-1b62-478e-8303-0935ffea1d28",
        )
        self.assertEqual(
            _derive_ne_id_from_alarm(alarm_object_name_me),
            "00ceb960-1b62-478e-8303-0935ffea1d28",
        )

    def test_serialize_ume_alarm_raw_row_select_fields(self):
        alarm = UmeAlarmCurrent(
            alarm_key="AK-1",
            ne_id="NE-1",
            perceived_severity="critical",
            event_type="communications-alarm",
            is_cleared="false",
        )
        ne = UmeInventoryNE(ne_id="NE-1", ne_name="ne1", user_label="site-1", ip_address="10.0.0.1")
        selected = {"alarm_alarm_key", "alarm_perceived_severity", "ne_user_label", "ne_exists"}
        row = _serialize_ume_alarm_raw_row(alarm, ne, selected)
        self.assertEqual(set(row.keys()), selected)
        self.assertEqual(row["alarm_alarm_key"], "AK-1")
        self.assertEqual(row["alarm_perceived_severity"], "critical")
        self.assertEqual(row["ne_user_label"], "site-1")
        self.assertTrue(row["ne_exists"])

    def test_sql_ume_query_rejects_non_ume_tables(self):
        with self.assertRaises(HTTPException) as ctx:
            sql_ume_query(payload={"sql": "select * from alarms_norm", "limit": 10}, db=None)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("ume_table_not_allowed", str(ctx.exception.detail))

    def test_ume_alarms_fields_contains_selectable_fields(self):
        data = ume_alarms_fields()
        self.assertIn("alarm_fields", data)
        self.assertIn("ne_fields", data)
        self.assertIn("selectable_fields", data)
        self.assertIn("alarm_alarm_key", set(data["selectable_fields"]))
        self.assertIn("ne_user_label", set(data["selectable_fields"]))
        self.assertIn("ne_exists", set(data["selectable_fields"]))

    def test_extract_ume_raw_group_field(self):
        alarm = UmeAlarmCurrent(alarm_key="AK-X", perceived_severity="major")
        ne = UmeInventoryNE(ne_id="NE-X", user_label="site-x")
        self.assertEqual(_extract_ume_raw_group_field(alarm, ne, "alarm_perceived_severity"), "major")
        self.assertEqual(_extract_ume_raw_group_field(alarm, ne, "ne_user_label"), "site-x")
        self.assertEqual(_extract_ume_raw_group_field(alarm, None, "ne_exists"), "0")


if __name__ == "__main__":
    unittest.main()
