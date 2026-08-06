from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.main import _extract_ume_raw_group_field, _serialize_ume_alarm_raw_row, sql_ume_query, ume_alarms_fields
from netx_api.models import UmeAlarmCurrent, UmeInventoryNE, UmeTopoLink, UmeTopoNode
from netx_api.ume_client import UMEClient, _parse_json_response
from netx_api import ume_alarm_ws
from netx_api.models import UmeAlarmSubscription
from netx_api.ume_alarm_subscription_store import clear_subscription, load_subscription, save_subscription
from netx_api.ume_alarm_ws import (
    append_ws_log,
    cancel_alarm_subscription_manual,
    clear_local_alarm_subscription_manual,
    establish_alarm_subscription_manual,
    get_subscription_status,
    get_ws_connection_status,
    get_ws_logs,
    is_ume_subscription_missing_error,
    load_persisted_subscription,
    mark_ume_subscription_lost,
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
    sync_topology_full,
)
from netx_api.ume_sync_topology import extract_me_uuid, extract_ptp
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

        sync_alarms_current(self.db, _COne(), trigger_mode="manual", wss_active=False)
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

        sync_alarms_current(self.db, _CPartial(), trigger_mode="manual", wss_active=False)
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

        job1, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual", wss_active=False)
        self.assertEqual(job1.status, "done")
        self.assertEqual(job1.inserted_count, 1)

        job2, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual", wss_active=False)
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
            job, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual", wss_active=False)
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
            job, _ = sync_alarms_current(self.db, _C(), trigger_mode="manual", wss_active=False)
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
        job, _ = sync_alarms_current(self.db, c, trigger_mode="manual", wss_active=False)
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
        job, _ = sync_alarms_current(self.db, c, trigger_mode="manual", wss_active=False)
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

    def test_is_ume_subscription_missing_error(self):
        err = (
            "ConnectFailed: Server responded with a non-101 status: 503 "
            "status-reason: Subscription not found or overtime! Please establish again"
        )
        self.assertTrue(is_ume_subscription_missing_error(err))
        self.assertTrue(
            is_ume_subscription_missing_error(
                'ume_request_failed:DELETE ... 400:{"errorInfo":"subscription not exist!"}'
            )
        )
        self.assertTrue(
            is_ume_subscription_missing_error(
                "Handshake status 500 Server Error -+-+- {'content-type': 'text/html'}"
            )
        )

    def test_cancel_returns_needs_cleanup_when_ume_missing(self):
        from time import time as _time

        ume_alarm_ws._clear_active_subscription()
        ume_alarm_ws.clear_ume_subscription_lost_flag()
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        db = TestingSessionLocal()

        client = UMEClient(base_url="https://ume.local:18014", username="u", password="p", verify_tls=False)
        client._token_value = "token-1"
        client._token_expires_at = _time() + 3600
        save_subscription(db, subscription_id="sub-gone", wss_uri="wss://ume.local/stream/sub-gone", topic="ALARM")
        db.commit()
        ume_alarm_ws._set_active_subscription("sub-gone", "wss://ume.local/stream/sub-gone")

        def _fail_delete(_id: str) -> None:
            raise RuntimeError('ume_request_failed:DELETE ... 400:{"errorInfo":"subscription not exist!"}')

        client.delete_alarm_subscription = _fail_delete  # type: ignore[method-assign]
        client.refresh_if_needed = lambda: "token-1"  # type: ignore[method-assign]

        st = cancel_alarm_subscription_manual(client, db)
        self.assertFalse(st.get("ok", True))
        self.assertTrue(st.get("needs_local_cleanup"))
        self.assertTrue(st.get("active"))
        self.assertTrue(ume_alarm_ws.is_ume_subscription_lost())

        st2 = cancel_alarm_subscription_manual(client, db, force_clear_local=True)
        self.assertTrue(st2.get("ok"))
        self.assertFalse(st2.get("active"))
        self.assertFalse(ume_alarm_ws.is_ume_subscription_lost())
        db.close()

    def test_ws_connection_status_not_alarm_action(self):
        from netx_api import ume_alarm_ws as ws_mod

        with ws_mod._ws_connection_lock:
            ws_mod._ws_connection_state = "init"
            ws_mod._ws_connection_detail = ""
        ws_mod._set_ws_connection_state("connected", detail="ws connected")
        st = get_ws_connection_status()
        self.assertEqual(st["state"], "connected")
        self.assertEqual(st["label"], "已连接")
        self.assertEqual(st["detail"], "ws connected")

    def test_ws_log_ring_buffer(self):
        from netx_api import ume_alarm_ws as ws_mod

        with ws_mod._WS_LOG_LOCK:
            ws_mod._WS_LOG_ENTRIES.clear()
        try:
            for i in range(5):
                append_ws_log(f"line-{i}", dedup=False)
            logs = get_ws_logs(limit=3)
            self.assertEqual(len(logs), 3)
            self.assertEqual(logs[-1]["message"], "line-4")
            self.assertIn("ts", logs[0])
        finally:
            with ws_mod._WS_LOG_LOCK:
                ws_mod._WS_LOG_ENTRIES.clear()

    def test_ws_log_dedup_suppresses_repeat(self):
        from netx_api import ume_alarm_ws as ws_mod

        with ws_mod._WS_LOG_LOCK:
            ws_mod._WS_LOG_ENTRIES.clear()
        with ws_mod._LOG_DEDUP_LOCK:
            ws_mod._LOG_DEDUP_CACHE.clear()
        try:
            append_ws_log("same-line", dedup_cooldown_s=60.0)
            append_ws_log("same-line", dedup_cooldown_s=60.0)
            logs = get_ws_logs()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["message"], "same-line")
        finally:
            with ws_mod._WS_LOG_LOCK:
                ws_mod._WS_LOG_ENTRIES.clear()
            with ws_mod._LOG_DEDUP_LOCK:
                ws_mod._LOG_DEDUP_CACHE.clear()

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

    def test_sync_current_alarms_wss_active_skips_stale_delete(self):
        from datetime import datetime, timedelta

        class _COne:
            def get_alarms(self, *, is_uncleared: bool, limit=None, marker=None):
                class _D:
                    marker = ""
                    is_end_of_reply = True

                return (
                    [
                        {
                            "alarmKey": "AK-REST",
                            "ne-id": "NE-1",
                            "perceivedSeverity": "major",
                            "isCleared": "false",
                        },
                    ],
                    _D(),
                )

        ws_touch = datetime.utcnow()
        self.db.add(
            UmeAlarmCurrent(
                alarm_key="AK-WS-ONLY",
                ne_id="NE-2",
                perceived_severity="minor",
                is_cleared="false",
                last_seen_at=ws_touch,
            )
        )
        self.db.add(
            UmeAlarmCurrent(
                alarm_key="AK-STALE",
                ne_id="NE-X",
                perceived_severity="minor",
                is_cleared="false",
                last_seen_at=ws_touch - timedelta(hours=1),
            )
        )
        self.db.commit()

        job, batch = sync_alarms_current(self.db, _COne(), trigger_mode="manual", wss_active=True)
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-REST"))
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-WS-ONLY"))
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-STALE"))
        details = json.loads(batch.raw_json or "{}")
        self.assertEqual(int(details.get("deleted_stale_current_alarms") or 0), 0)
        self.assertEqual(str(details.get("reconcile_mode") or ""), "upsert_only")
        self.assertEqual(job.status, "done")

    def test_apply_alarm_rest_skips_tombstone_after_wss_clear(self):
        from datetime import datetime

        touch = datetime.utcnow()
        self.db.add(
            UmeAlarmCurrent(
                alarm_key="AK-CLEARED-2",
                ne_id="NE-1",
                is_cleared="false",
            )
        )
        self.db.commit()
        action, changed = apply_alarm_to_current(
            self.db,
            {"alarmKey": "AK-CLEARED-2", "isCleared": True},
            touch_ts=touch,
        )
        self.db.commit()
        self.assertEqual(action, "deleted")
        self.assertTrue(changed)
        action2, changed2 = apply_alarm_to_current(
            self.db,
            {"alarmKey": "AK-CLEARED-2", "isCleared": False, "perceivedSeverity": "major"},
            touch_ts=touch,
            source="rest",
        )
        self.assertEqual(action2, "skipped")
        self.assertFalse(changed2)
        self.assertIsNone(self.db.get(UmeAlarmCurrent, "AK-CLEARED-2"))

    def test_apply_alarm_concurrent_upsert_no_integrity_error(self):
        from datetime import datetime

        touch = datetime.utcnow()
        alarm = {
            "alarmKey": "AK-DUP",
            "ne-id": "NE-1",
            "perceivedSeverity": "major",
            "isCleared": "false",
        }
        a1, _ = apply_alarm_to_current(self.db, alarm, touch_ts=touch)
        self.db.commit()
        a2, _ = apply_alarm_to_current(self.db, alarm, touch_ts=touch, source="rest")
        self.db.commit()
        self.assertEqual(a1, "inserted")
        self.assertIn(a2, ("updated", "inserted"))
        self.assertIsNotNone(self.db.get(UmeAlarmCurrent, "AK-DUP"))

    def test_is_wss_active_for_current_alarms(self):
        from netx_api.ume_alarm_ws import (
            _clear_active_subscription,
            _set_active_subscription,
            clear_ume_subscription_lost_flag,
            is_wss_active_for_current_alarms,
        )

        _clear_active_subscription()
        clear_ume_subscription_lost_flag()
        self.assertFalse(is_wss_active_for_current_alarms())
        _set_active_subscription("sub-x", "wss://ume.local/stream/sub-x")
        self.assertTrue(is_wss_active_for_current_alarms())

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


class UmeTopologySyncTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()

    def tearDown(self):
        self.db.close()

    def test_extract_me_and_ptp(self):
        tp = "ME{4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2},EQ={/r=0/sh=1/sl=1},PTP={/p=1_16}"
        self.assertEqual(extract_me_uuid(tp), "4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2")
        self.assertEqual(extract_ptp(tp), "/p=1_16")
        self.assertEqual(
            extract_me_uuid("TOPO_NODE_ME7e8ac1c7-9d34-42d3-adfb-b1031e7c145a"),
            "7e8ac1c7-9d34-42d3-adfb-b1031e7c145a",
        )

    def test_sync_topology_upsert_and_reconcile(self):
        class _Diag:
            latency_ms = 1

        class _CWide:
            def get_topo_nodes(self):
                return (
                    [
                        {
                            "nodeId": "66e807c0-94b6-4c50-a02e-fcb56d08bdea",
                            "name": "SBN{66e807c0-94b6-4c50-a02e-fcb56d08bdea}",
                            "nodeType": "TOPO_NODE_SBN",
                            "owner": "ZTE",
                            "userLabel": "SC IPRAN Network",
                            "yPos": 126,
                            "xPos": 460,
                            "parentNode": "topLevel",
                        },
                        {
                            "nodeId": "me-node-1",
                            "name": "TOPO_NODE_ME4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2",
                            "nodeType": "TOPO_NODE_ME",
                            "userLabel": "RMP01",
                            "xPos": 10,
                            "yPos": 20,
                            "parentNode": "66e807c0-94b6-4c50-a02e-fcb56d08bdea",
                        },
                    ],
                    _Diag(),
                )

            def get_topological_links(self):
                return (
                    [
                        {
                            "linkId": "415a0bbb-9387-4ff3-8de9-7e36846f4b6a",
                            "name": "TL{/ME{4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2},EQ={/r=0/sh=1/sl=1},PTP={/p=1_16}_/ME{7508cb6c-59f6-45aa-9e62-4fda61d80553},EQ={/r=0/sh=0/sl=1/ssl=0},PTP={/p=1_20}}",
                            "connection-status": "Connected",
                            "owner": "ZTE",
                            "aEndTpRefList": [
                                "ME{4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2},EQ={/r=0/sh=1/sl=1},PTP={/p=1_16}"
                            ],
                            "userLabel": "sample-label",
                            "direction": "BI",
                            "layerRate": 113,
                            "zEndTpRefList": [
                                "ME{7508cb6c-59f6-45aa-9e62-4fda61d80553},EQ={/r=0/sh=0/sl=1/ssl=0},PTP={/p=1_20}"
                            ],
                        },
                        {
                            "linkId": "link-to-drop",
                            "name": "TL-drop",
                            "aEndTpRefList": ["ME{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa},EQ={},PTP={/p=1}"],
                            "zEndTpRefList": ["ME{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb},EQ={},PTP={/p=2}"],
                            "direction": "BI",
                            "layerRate": 1,
                        },
                    ],
                    _Diag(),
                )

        class _CNarrow:
            def get_topo_nodes(self):
                return (
                    [
                        {
                            "nodeId": "me-node-1",
                            "name": "TOPO_NODE_ME4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2",
                            "nodeType": "TOPO_NODE_ME",
                            "userLabel": "RMP01-upd",
                            "xPos": 11,
                            "yPos": 21,
                            "parentNode": "topLevel",
                        }
                    ],
                    _Diag(),
                )

            def get_topological_links(self):
                return (
                    [
                        {
                            "linkId": "415a0bbb-9387-4ff3-8de9-7e36846f4b6a",
                            "name": "TL-keep",
                            "connection-status": "Connected",
                            "aEndTpRefList": [
                                "ME{4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2},EQ={/r=0/sh=1/sl=1},PTP={/p=1_16}"
                            ],
                            "zEndTpRefList": [
                                "ME{7508cb6c-59f6-45aa-9e62-4fda61d80553},EQ={/r=0/sh=0/sl=1/ssl=0},PTP={/p=1_20}"
                            ],
                            "direction": "BI",
                            "layerRate": 113,
                        }
                    ],
                    _Diag(),
                )

        job1 = sync_topology_full(self.db, _CWide(), trigger_mode="manual")
        self.assertEqual(job1.status, "done")
        self.assertEqual(job1.pulled_count, 4)
        self.assertEqual(job1.inserted_count, 4)

        sbn = self.db.get(UmeTopoNode, "66e807c0-94b6-4c50-a02e-fcb56d08bdea")
        self.assertIsNotNone(sbn)
        self.assertEqual(sbn.node_type, "TOPO_NODE_SBN")
        self.assertEqual(sbn.x_pos, 460)
        self.assertEqual(sbn.ume_ne_id, "")

        me = self.db.get(UmeTopoNode, "me-node-1")
        self.assertIsNotNone(me)
        self.assertEqual(me.ume_ne_id, "4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2")

        link = self.db.get(UmeTopoLink, "415a0bbb-9387-4ff3-8de9-7e36846f4b6a")
        self.assertIsNotNone(link)
        self.assertEqual(link.a_ume_ne_id, "4e598e5d-fe42-4c79-9f62-7d3e5d4eb5b2")
        self.assertEqual(link.z_ume_ne_id, "7508cb6c-59f6-45aa-9e62-4fda61d80553")
        self.assertEqual(link.a_ptp, "/p=1_16")
        self.assertEqual(link.z_ptp, "/p=1_20")
        self.assertEqual(link.connection_status, "Connected")
        self.assertIsNotNone(self.db.get(UmeTopoLink, "link-to-drop"))

        job2 = sync_topology_full(self.db, _CNarrow(), trigger_mode="manual")
        self.assertEqual(job2.status, "done")
        self.db.expire_all()
        self.assertIsNone(self.db.get(UmeTopoNode, "66e807c0-94b6-4c50-a02e-fcb56d08bdea"))
        self.assertIsNotNone(self.db.get(UmeTopoNode, "me-node-1"))
        self.assertEqual(self.db.get(UmeTopoNode, "me-node-1").user_label, "RMP01-upd")
        self.assertIsNone(self.db.get(UmeTopoLink, "link-to-drop"))
        self.assertIsNotNone(self.db.get(UmeTopoLink, "415a0bbb-9387-4ff3-8de9-7e36846f4b6a"))
        details = json.loads(job2.details_json or "{}")
        self.assertEqual(details.get("deleted_topo_nodes"), 1)
        self.assertEqual(details.get("deleted_topo_links"), 1)

    def test_sync_topology_duplicate_ids_in_payload(self):
        class _Diag:
            latency_ms = 1

        class _C:
            def get_topo_nodes(self):
                row = {
                    "nodeId": "dup-node",
                    "name": "TOPO_NODE_MEaaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "nodeType": "TOPO_NODE_ME",
                    "userLabel": "v1",
                    "xPos": 1,
                    "yPos": 2,
                }
                row2 = dict(row)
                row2["userLabel"] = "v2"
                row2["xPos"] = 9
                return ([row, row2], _Diag())

            def get_topological_links(self):
                link = {
                    "linkId": "dup-link",
                    "name": "TL-1",
                    "aEndTpRefList": ["ME{aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa},PTP={/p=1}"],
                    "zEndTpRefList": ["ME{bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb},PTP={/p=2}"],
                    "direction": "BI",
                    "layerRate": 1,
                }
                link2 = dict(link)
                link2["layerRate"] = 2
                return ([link, link2], _Diag())

        job = sync_topology_full(self.db, _C(), trigger_mode="manual")
        self.assertEqual(job.status, "done", job.error_message)
        self.db.expire_all()
        node = self.db.get(UmeTopoNode, "dup-node")
        self.assertIsNotNone(node)
        self.assertEqual(node.user_label, "v2")
        self.assertEqual(node.x_pos, 9)
        link = self.db.get(UmeTopoLink, "dup-link")
        self.assertIsNotNone(link)
        self.assertEqual(link.layer_rate, 2)


if __name__ == "__main__":
    unittest.main()
