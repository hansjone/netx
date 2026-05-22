from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.main import _extract_ume_raw_group_field, _serialize_ume_alarm_raw_row, sql_ume_query, ume_alarms_fields
from netx_api.models import UmeAlarmCurrent, UmeInventoryNE
from netx_api.ume_client import UMEClient, _parse_json_response
from netx_api import ume_alarm_ws
from netx_api.models import UmeAlarmSubscription
from netx_api.ume_alarm_subscription_store import clear_subscription, load_subscription, save_subscription
from netx_api.ume_alarm_ws import (
    cancel_alarm_subscription_manual,
    establish_alarm_subscription_manual,
    get_subscription_status,
    load_persisted_subscription,
    parse_subscription_id_from_already_exists_error,
    process_alarm_notification,
)
from netx_api.ume_sync_service import (
    _derive_ne_id_from_alarm,
    apply_alarm_to_current,
    extract_alarm_from_notification,
    normalize_yang_alarm,
    sync_alarms_current,
    sync_inventory_full,
)
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

    def test_establish_alarm_subscription(self):
        from time import time as _time

        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)
        client._token_value = "token-1"
        client._token_expires_at = _time() + 3600
        seen: dict[str, Any] = {}

        def _fake_request(method: str, path: str, *, params=None, body=None):
            seen["method"] = method
            seen["path"] = path
            seen["body"] = body
            return (
                {
                    "output": {
                        "id": "3282ac78-b38a-4242-81d2-cc5b77c28ef8",
                        "uri": "wss://ume.local:18014/restconf/stream/3282ac78-b38a-4242-81d2-cc5b77c28ef8",
                    }
                },
                None,
            )

        client._request_json_with_current_token = _fake_request  # type: ignore[method-assign]
        sub_id, uri = client.establish_alarm_subscription()
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["body"], {"input": {"topic": "ALARM"}})
        self.assertEqual(sub_id, "3282ac78-b38a-4242-81d2-cc5b77c28ef8")
        self.assertIn("wss://", uri)

    def test_parse_json_response_accepts_empty_success_body(self):
        class _Resp:
            is_success = True
            status_code = 204
            text = ""

            def json(self):
                raise json.JSONDecodeError("Expecting value", "", 0)

        self.assertEqual(_parse_json_response(_Resp()), {})  # type: ignore[arg-type]

    def test_delete_alarm_subscription_empty_response(self):
        from time import time as _time

        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)
        client._token_value = "token-1"
        client._token_expires_at = _time() + 3600

        class _Resp:
            is_success = True
            status_code = 204
            text = ""
            headers: dict[str, str] = {}

            def json(self):
                raise json.JSONDecodeError("Expecting value", "", 0)

        def _fake_request(method: str, path: str, *, params=None, body=None):
            return _parse_json_response(_Resp()), None  # type: ignore[arg-type]

        client.request_json = _fake_request  # type: ignore[method-assign]
        client.delete_alarm_subscription("sub-to-delete")

    def test_delete_alarm_subscription(self):
        from time import time as _time

        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)
        client._token_value = "token-1"
        client._token_expires_at = _time() + 3600
        seen: dict[str, Any] = {}

        def _fake_request(method: str, path: str, *, params=None, body=None):
            seen["method"] = method
            seen["body"] = body
            return ({}, None)

        client.request_json = _fake_request  # type: ignore[method-assign]
        client.delete_alarm_subscription("sub-to-delete")
        self.assertEqual(seen["method"], "DELETE")
        self.assertEqual(seen["body"], {"input": {"id": "sub-to-delete"}})


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

        self.db.add(
            UmeInventoryNE(
                ne_id="NE-1",
                ne_name="ne1",
                user_label="网元1",
                host_name="host-ne-1",
            )
        )
        self.db.commit()

        job1, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job1.status, "done")
        self.assertEqual(job1.inserted_count, 1)

        job2, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job2.status, "done")
        self.assertEqual(job2.updated_count, 1)

        row = self.db.get(UmeAlarmCurrent, "AK-1")
        self.assertIsNotNone(row)
        self.assertEqual(row.perceived_severity, "critical")
        self.assertEqual(row.host_name, "host-ne-1")

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
        alarm_key_me_wrapped = {
            "alarmkey": "BN:ME{353319917}:35 1778634688",
            "objectName": "ME{33a3e8f4-a76e-40fd-a0ba-045371a5f234},PWR={/module=3}",
        }

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
        self.assertEqual(
            _derive_ne_id_from_alarm(alarm_key_me_wrapped),
            "33a3e8f4-a76e-40fd-a0ba-045371a5f234",
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
        alarm = UmeAlarmCurrent(alarm_key="AK-X", perceived_severity="major", host_name="host-x")
        ne = UmeInventoryNE(ne_id="NE-X", user_label="site-x", host_name="inv-host")
        self.assertEqual(_extract_ume_raw_group_field(alarm, ne, "alarm_perceived_severity"), "major")
        self.assertEqual(_extract_ume_raw_group_field(alarm, ne, "ne_user_label"), "site-x")
        self.assertEqual(_extract_ume_raw_group_field(alarm, ne, "ne_host_name"), "host-x")
        self.assertEqual(_extract_ume_raw_group_field(alarm, None, "ne_exists"), "0")

    def test_ume_alarms_fields_includes_alarm_host_name(self):
        data = ume_alarms_fields()
        self.assertIn("alarm_host_name", set(data["selectable_fields"]))


class UmeAlarmNotificationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()

    def tearDown(self):
        self.db.close()

    def test_normalize_yang_alarm_strips_prefix(self):
        raw = {
            "zte-alarms:alarmkey": "AK-WS-1",
            "zte-alarms:is-cleared": False,
            "zte-alarms:perceivedSeverity": "critical",
        }
        norm = normalize_yang_alarm(raw)
        self.assertEqual(norm.get("alarmkey"), "AK-WS-1")
        self.assertEqual(norm.get("is-cleared"), False)
        self.assertEqual(norm.get("perceivedSeverity"), "critical")

    def test_apply_alarm_to_current_insert_from_notification(self):
        payload = {
            "alarm-notification": {
                "zte-alarms:alarmkey": "AK-WS-2",
                "zte-alarms:is-cleared": False,
                "zte-alarms:perceivedSeverity": "major",
                "zte-alarms:objectName": "ME{00ceb960-1b62-478e-8303-0935ffea1d28}",
                "zte-alarms:time-created": "2025-01-03T07:55:00.823+08:00",
            }
        }
        alarm = extract_alarm_from_notification(payload)
        self.assertIsNotNone(alarm)
        from datetime import datetime

        action, changed = apply_alarm_to_current(self.db, alarm or {}, touch_ts=datetime.utcnow())
        self.db.commit()
        self.assertEqual(action, "inserted")
        self.assertTrue(changed)
        row = self.db.get(UmeAlarmCurrent, "AK-WS-2")
        self.assertIsNotNone(row)
        self.assertEqual(row.perceived_severity, "major")

    def test_apply_alarm_cleared_deletes_current_only(self):
        from datetime import datetime

        touch = datetime.utcnow()
        self.db.add(
            UmeAlarmCurrent(
                alarm_key="AK-CLEARED-1",
                first_seen_at=touch,
                last_seen_at=touch,
                is_cleared="false",
            )
        )
        self.db.commit()
        alarm = {
            "alarmkey": "AK-CLEARED-1",
            "is-cleared": True,
        }
        action, changed = apply_alarm_to_current(self.db, alarm, touch_ts=touch)
        self.db.commit()
        self.assertEqual(action, "deleted")
        self.assertTrue(changed)
        self.assertIsNone(self.db.get(UmeAlarmCurrent, "AK-CLEARED-1"))

    def test_parse_subscription_id_from_already_exists_error(self):
        msg = (
            'ume_request_failed:400:{"error":{"errorInfo":"topic subscription already exist, '
            'id:087f3544-0171-49c9-9aea-2ac535b3deaa, uri:wss://10.0.0.1:18014/restconf/stream/087f3544"}}'
        )
        self.assertEqual(
            parse_subscription_id_from_already_exists_error(msg),
            "087f3544-0171-49c9-9aea-2ac535b3deaa",
        )

    def test_cancel_subscription_fails_without_clearing_db(self):
        from time import time as _time

        ume_alarm_ws._clear_active_subscription()
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()

        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)
        client._token_value = "token-1"
        client._token_expires_at = _time() + 3600
        save_subscription(db, subscription_id="sub-keep", wss_uri="wss://ume.local/stream/sub-keep", topic="ALARM")
        db.commit()
        ume_alarm_ws._set_active_subscription("sub-keep", "wss://ume.local/stream/sub-keep")

        def _fail_delete(_id: str) -> None:
            raise RuntimeError("ume_request_failed:500:delete failed")

        client.delete_alarm_subscription = _fail_delete  # type: ignore[method-assign]
        client.refresh_if_needed = lambda: "token-1"  # type: ignore[method-assign]

        with self.assertRaises(RuntimeError):
            cancel_alarm_subscription_manual(client, db)
        loaded = load_subscription(db)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0], "sub-keep")
        self.assertTrue(get_subscription_status()["active"])
        db.close()

    def test_establish_recovers_when_ume_reports_already_exists(self):
        from time import time as _time

        ume_alarm_ws._clear_active_subscription()
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()

        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)
        client._token_value = "token-1"
        client._token_expires_at = _time() + 3600
        deleted: list[str] = []
        calls = {"n": 0}

        def _fake_establish(*, topic=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError(
                    "ume_request_failed:400:{\"error\":{\"errorInfo\":\"topic subscription already exist, "
                    "id:087f3544-0171-49c9-9aea-2ac535b3deaa\"}}"
                )
            return ("sub-new", "wss://ume.local/stream/sub-new")

        def _fake_delete(sub_id: str) -> None:
            deleted.append(sub_id)

        client.establish_alarm_subscription = _fake_establish  # type: ignore[method-assign]
        client.delete_alarm_subscription = _fake_delete  # type: ignore[method-assign]

        st = establish_alarm_subscription_manual(client, db)
        self.assertEqual(deleted, ["087f3544-0171-49c9-9aea-2ac535b3deaa"])
        self.assertEqual(st["subscription_id"], "sub-new")
        self.assertTrue(st["active"])
        db.close()

    def test_subscription_store_and_manual_establish(self):
        from time import time as _time

        ume_alarm_ws._clear_active_subscription()
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()

        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)
        client._token_value = "token-1"
        client._token_expires_at = _time() + 3600

        calls = {"n": 0}

        def _fake_establish(*, topic=None):
            calls["n"] += 1
            return ("sub-1", "wss://ume.local:18014/restconf/stream/sub-1")

        client.establish_alarm_subscription = _fake_establish  # type: ignore[method-assign]
        client.delete_alarm_subscription = lambda _id: None  # type: ignore[method-assign]

        st = establish_alarm_subscription_manual(client, db)
        self.assertTrue(st["active"])
        self.assertEqual(st["subscription_id"], "sub-1")
        self.assertFalse(st.get("already_exists"))
        self.assertEqual(calls["n"], 1)
        st2 = establish_alarm_subscription_manual(client, db)
        self.assertTrue(st2.get("already_exists"))
        self.assertEqual(st2["subscription_id"], "sub-1")
        self.assertEqual(calls["n"], 1)
        loaded = load_subscription(db)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0], "sub-1")
        self.assertTrue(get_subscription_status()["active"])

        cancel_alarm_subscription_manual(client, db)
        self.assertFalse(get_subscription_status()["active"])
        self.assertIsNone(load_subscription(db))
        db.close()

    def test_process_alarm_notification_via_ws_helper(self):
        from datetime import datetime

        payload = {
            "alarm-notification": {
                "zte-alarms:alarmkey": "AK-WS-3",
                "zte-alarms:is-cleared": False,
                "zte-alarms:perceivedSeverity": "warning",
            }
        }
        action, changed = process_alarm_notification(self.db, payload)
        self.db.commit()
        self.assertEqual(action, "inserted")
        self.assertTrue(changed)
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-WS-3"))


if __name__ == "__main__":
    unittest.main()
