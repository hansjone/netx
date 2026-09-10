"""Unit tests for DSH alarm hub subscriber bookkeeping."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from netx_api import dsh_alarm_hub as hub


class DshAlarmHubStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        with hub._LOCK:
            hub._CLIENTS.clear()
            hub._STATS.update(
                {
                    "published": 0,
                    "deliver_ok": 0,
                    "deliver_fail": 0,
                    "subscribers": 0,
                }
            )

    def tearDown(self) -> None:
        with hub._LOCK:
            hub._CLIENTS.clear()

    def test_hub_status_lists_connections(self) -> None:
        ws_a = MagicMock(name="ws_a")
        ws_b = MagicMock(name="ws_b")
        info_a = hub.SubscriberInfo(
            id="aaa111",
            user="alice",
            remote="10.0.0.1:40001",
            client="netxops@host-a",
            connected_at="2026-09-10T08:00:00+00:00",
            last_seen_at="2026-09-10T08:01:00+00:00",
        )
        info_b = hub.SubscriberInfo(
            id="bbb222",
            user="bob",
            remote="10.0.0.2:40002",
            client="netxops@host-b",
            connected_at="2026-09-10T08:00:30+00:00",
            last_seen_at="2026-09-10T08:01:30+00:00",
        )
        with hub._LOCK:
            hub._CLIENTS[ws_a] = info_a
            hub._CLIENTS[ws_b] = info_b
            hub._STATS["subscribers"] = 2
            hub._STATS["published"] = 5
            hub._STATS["deliver_ok"] = 8

        status = hub.hub_status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["subscribers"], 2)
        self.assertEqual(status["published"], 5)
        self.assertEqual(status["deliver_ok"], 8)
        self.assertEqual(status["path"], "/v1/integrations/dsh-alarm/ws")
        ids = [row["id"] for row in status["connections"]]
        self.assertEqual(ids, ["aaa111", "bbb222"])
        self.assertEqual(status["connections"][0]["user"], "alice")
        self.assertEqual(status["connections"][1]["client"], "netxops@host-b")

    def test_client_remote_formats_host_port(self) -> None:
        ws = SimpleNamespace(client=SimpleNamespace(host="192.168.1.9", port=54321))
        self.assertEqual(hub._client_remote(ws), "192.168.1.9:54321")


if __name__ == "__main__":
    unittest.main()
