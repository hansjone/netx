"""Tests for Netmiko show helpers (IOSv leftover-prompt drain; no re-send)."""

from __future__ import annotations

import unittest
import re
from unittest.mock import MagicMock, patch

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
    def test_mikrotik_does_not_probe_prompt_after_drain(self, drain: MagicMock) -> None:
        conn = MagicMock()
        conn.device_type = "mikrotik_routeros_ssh"

        def delayed_routeros_output(**kwargs: object) -> str:
            # A prompt probe queues an old prompt before the actual output.
            if kwargs.get("auto_find_prompt", True):
                return ""
            return "name: MikroTik"

        conn.send_command.side_effect = delayed_routeros_output
        self.assertEqual(send_show_command(conn, "/system identity print"), "name: MikroTik")
        conn.send_command.assert_called_once()
        conn.find_prompt.assert_not_called()
        conn.send_command_timing.assert_not_called()
        drain.assert_called_once_with(conn)
        pattern = conn.send_command.call_args.kwargs["expect_string"]
        for prompt in ("[admin@MikroTik] >", "[ops@edge-router] /ip/firewall>"):
            self.assertIsNotNone(re.search(pattern, prompt))
        for echo in ("[admin@MikroTik]", "[admin@MikroTik] > /system resource print"):
            self.assertIsNone(re.search(pattern, echo))

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_mikrotik_wrapped_script_does_not_require_exact_echo(self, drain: MagicMock) -> None:
        conn = MagicMock()
        conn.device_type = "mikrotik_routeros"
        conn.send_command.return_value = "finished"
        command = ':put "' + "x" * 600 + '"'
        self.assertEqual(send_show_command(conn, command, read_timeout=45), "finished")
        conn.send_command.assert_called_once()
        self.assertFalse(conn.send_command.call_args.kwargs["cmd_verify"])
        self.assertEqual(conn.send_command.call_args.kwargs["read_timeout"], 45)

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_mikrotik_timeout_never_resends_write(self, drain: MagicMock) -> None:
        conn = MagicMock()
        conn.device_type = "mikrotik_routeros"
        conn.send_command.side_effect = TimeoutError("late prompt")
        with self.assertRaises(TimeoutError):
            send_show_command(conn, '/ip address add address=192.0.2.1/32 interface=lo')
        conn.send_command.assert_called_once()
        conn.send_command_timing.assert_not_called()

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_drains_then_sends_once(self, drain: MagicMock) -> None:
        conn = MagicMock()
        conn.send_command.return_value = "*12:00:00 UTC"
        out = send_show_command(conn, "show clock", read_timeout=30)
        self.assertEqual(out, "*12:00:00 UTC")
        conn.send_command.assert_called_once()
        conn.send_command_timing.assert_not_called()
        drain.assert_called_once_with(conn)

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_empty_output_does_not_resend(self, drain: MagicMock) -> None:
        conn = MagicMock()
        conn.send_command.return_value = ""
        out = send_show_command(conn, "show version", read_timeout=30)
        self.assertEqual(out, "")
        conn.send_command.assert_called_once()
        conn.send_command_timing.assert_not_called()
        drain.assert_called_once_with(conn)


if __name__ == "__main__":
    unittest.main()
