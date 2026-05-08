from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.models import UmeAlarmCurrent, UmeInventoryNE
from netx_api.ume_client import UMEClient
from netx_api.ume_sync_service import _derive_ne_id_from_alarm, sync_alarms_current, sync_inventory_full


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
            def get_network_elements(self):
                rows = [
                    {
                        "ne-id": "NE-1",
                        "name": "ne1",
                        "user-label": "网元1",
                        "ip-Address": "10.0.0.1",
                        "type": "A",
                        "device-level": "Access",
                        "host-name": "host-1",
                        "hardware-version": "V1",
                        "vendor-name": "ZTE",
                    },
                ]
                return rows, None

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

    def test_derive_ne_id_from_alarmkey_formats(self):
        alarm_hash = {"alarmkey": "00ceb960-1b62-478e-8303-0935ffea1d28#99010"}
        alarm_csv = {"alarmkey": "00ceb960-1b62-478e-8303-0935ffea1d28, 4237, 79"}

        self.assertEqual(
            _derive_ne_id_from_alarm(alarm_hash),
            "00ceb960-1b62-478e-8303-0935ffea1d28",
        )
        self.assertEqual(
            _derive_ne_id_from_alarm(alarm_csv),
            "00ceb960-1b62-478e-8303-0935ffea1d28",
        )


if __name__ == "__main__":
    unittest.main()
