"""Tests for Netmiko show helpers (IOSv leftover-prompt drain / retry)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch

from netx_api.ne_netmiko import drain_read_channel, send_show_command


class DrainReadChannelTests(unittest.TestCase):
    def test_drains_until_idle(self) -> None:
        conn = MagicMock()
        conn.read_channel.side_effect = ["R2#\n", "", "", "", ""]
        out = drain_read_channel(conn, idle_reads=3, pause_sec=0.0)
        self.assertEqual(out, "R2#\n")
        self.assertGreaterEqual(conn.read_channel.call_count, 4)

    def test_clear_buffer_when_no_read_channel(self) -> None:
        conn = MagicMock(spec=["clear_buffer"])
        self.assertEqual(drain_read_channel(conn), "")
        conn.clear_buffer.assert_called_once()


class SendShowCommandTests(unittest.TestCase):
    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_returns_first_nonempty_send_command(self, drain: MagicMock) -> None:
        conn = MagicMock()
        conn.send_command.return_value = "*12:00:00 UTC"
        out = send_show_command(conn, "show clock", read_timeout=30)
        self.assertEqual(out, "*12:00:00 UTC")
        conn.send_command.assert_called_once()
        conn.send_command_timing.assert_not_called()
        drain.assert_called()

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_retries_then_falls_back_to_timing_when_empty(self, drain: MagicMock) -> None:
        conn = MagicMock()
        conn.send_command.return_value = ""
        conn.send_command_timing.return_value = "Cisco IOS Software"
        out = send_show_command(conn, "show version", read_timeout=30)
        self.assertEqual(out, "Cisco IOS Software")
        self.assertEqual(conn.send_command.call_count, 2)
        conn.send_command_timing.assert_called_once_with("show version", read_timeout=30)
        self.assertGreaterEqual(drain.call_count, 2)


if __name__ == "__main__":
    unittest.main()
