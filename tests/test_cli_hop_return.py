"""Unit tests for vendor CLI hop return-to-proxy detection."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.ne_session_factory import (
    cli_hop_nested_session_ended,
    cli_hop_returned_to_proxy,
    extract_cli_prompt_marker,
    get_cli_hop_guard,
    should_close_cli_hop_session,
)


class CliHopReturnDetectionTests(unittest.TestCase):
    def test_extract_huawei_and_cisco_prompts(self) -> None:
        self.assertEqual(extract_cli_prompt_marker("banner\n<BJ-CORE>\n"), "<BJ-CORE>")
        self.assertEqual(extract_cli_prompt_marker("[BJ-CORE]\n"), "[BJ-CORE]")
        self.assertEqual(extract_cli_prompt_marker("R1#"), "R1#")
        self.assertEqual(extract_cli_prompt_marker("R1>"), "R1>")
        self.assertEqual(extract_cli_prompt_marker(""), "")

    def test_extract_strips_ansi(self) -> None:
        self.assertEqual(
            extract_cli_prompt_marker("\x1b[32m<HOP>\x1b[0m"),
            "<HOP>",
        )

    def test_nested_session_end_messages(self) -> None:
        self.assertTrue(cli_hop_nested_session_ended("quit\nConnection closed by foreign host\n"))
        self.assertTrue(cli_hop_nested_session_ended("Connection closed\n"))
        self.assertTrue(cli_hop_nested_session_ended("% Connection closed by remote host\n"))
        self.assertFalse(cli_hop_nested_session_ended("<TARGET>\n"))

    def test_returned_to_proxy_requires_exact_last_prompt(self) -> None:
        self.assertTrue(cli_hop_returned_to_proxy("x\n<HOP>\n", "<HOP>"))
        self.assertFalse(cli_hop_returned_to_proxy("x\n<TARGET>\n", "<HOP>"))
        self.assertFalse(cli_hop_returned_to_proxy("mentions <HOP> in text\n<TARGET>\n", "<HOP>"))

    def test_should_close_on_nested_end_in_tail(self) -> None:
        old = ("old Connection closed by foreign host\n" * 40) + "<TARGET>\n"
        self.assertFalse(should_close_cli_hop_session(old, "<HOP>", seen_other_prompt=True))
        fresh = old + "Connection closed by foreign host\n<HOP>\n"
        self.assertTrue(should_close_cli_hop_session(fresh, "<HOP>", seen_other_prompt=True))

    def test_prompt_only_needs_seen_other_prompt(self) -> None:
        text = "work\n<HOP>\n"
        self.assertFalse(should_close_cli_hop_session(text, "<HOP>", seen_other_prompt=False))
        self.assertTrue(should_close_cli_hop_session(text, "<HOP>", seen_other_prompt=True))

    @patch("netx_api.ne_session_factory._interactive_target_auth")
    @patch("netx_api.ne_session_factory._read_channel")
    @patch("netx_api.ne_session_factory.ConnectHandler")
    def test_connect_attaches_cli_hop_guard(
        self,
        mock_ch: MagicMock,
        mock_read: MagicMock,
        mock_auth: MagicMock,
    ) -> None:
        from netx_api.ne_session_factory import _connect_via_cli_hop

        conn = MagicMock()
        conn.remote_conn = MagicMock()
        mock_ch.return_value = conn
        mock_read.side_effect = ["<HOP>\n", ""]
        mock_auth.return_value = None
        creds = {
            "hop_host": "10.0.0.1",
            "hop_username": "admin",
            "hop_password": "hop-pass",
            "hop_protocol": "ssh",
            "hop_vendor": "huawei",
            "hop_port": 22,
            "hop_vrf": "",
            "hop_command_template": "",
            "username": "target",
            "password": "target-pass",
            "ip_address": "10.0.0.2",
            "port": 22,
        }
        out = _connect_via_cli_hop(creds, cols=120, rows=40)
        self.assertIs(out, conn)
        # Nested stelnet must see WebCRT geometry, not Netmiko's default 511x1000.
        conn.remote_conn.resize_pty.assert_called_with(width=120, height=40)
        guard = get_cli_hop_guard(conn)
        self.assertIsNotNone(guard)
        assert guard is not None
        self.assertTrue(guard["enabled"])
        self.assertEqual(guard["hop_prompt"], "<HOP>")
        self.assertEqual(guard["hop_vendor"], "huawei")


if __name__ == "__main__":
    unittest.main()
