from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from netx_api.ne_exec import _validate_command, execute_managed_ne_commands


class NeExecValidationTests(unittest.TestCase):
    def test_allows_show(self) -> None:
        _validate_command("show ip interface brief")

    def test_allows_display(self) -> None:
        _validate_command("display interface brief")

    def test_blocks_ping_and_other_non_show_display(self) -> None:
        for cmd in (
            "ping 192.168.0.1",
            "ping6 2001::1",
            "get system info",
            "traceroute 192.168.0.1",
            "tracert 192.168.0.1",
            "terminal length 0",
            "?",
        ):
            with self.subTest(cmd=cmd):
                with self.assertRaises(HTTPException) as ctx:
                    _validate_command(cmd)
                self.assertEqual(ctx.exception.detail, "command_not_allowed_prefix")

    def test_blocks_pipe(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("show run | include hostname")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.detail, "command_chars_not_allowed")

    def test_blocks_configure(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_command("configure terminal")

    def test_blocks_non_show_prefix(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("interface GigabitEthernet0/0")
        self.assertEqual(ctx.exception.detail, "command_not_allowed_prefix")

    def test_blocks_newline_chained_show_and_configure(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("show interface\nconfigure terminal")
        self.assertEqual(ctx.exception.detail, "command_chars_not_allowed")

    def test_blocks_unicode_line_separator_chained_commands(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("show interface\u2028system-view")
        self.assertEqual(ctx.exception.detail, "command_chars_not_allowed")

    def test_blocks_system_view_on_one_line(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("system-view")
        self.assertEqual(ctx.exception.detail, "command_blocked")

    def test_blocks_ip_address_config(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("ip address 1.1.1.1 255.255.255.0")
        self.assertEqual(ctx.exception.detail, "command_blocked")

    def test_agent_batch_rejects_configure_before_connect(self) -> None:
        cmds = [
            "show interface",
            "configure terminal",
            "interface ge1/1",
            "ip address 1.1.1.1 255.255.255.0",
        ]
        for c in cmds[1:]:
            with self.assertRaises(HTTPException):
                _validate_command(c)


class NeExecRunTests(unittest.TestCase):
    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="ok-output")
    @patch("netx_api.ne_exec.get_device_credentials", return_value={"ip_address": "1.1.1.1"})
    def test_execute_success(self, _creds, _collect, _configured) -> None:
        row = MagicMock()
        row.id = "ne-1"
        db = MagicMock()
        db.get.return_value = row
        with patch("netx_api.ne_exec.row_to_out") as row_out:
            row_out.return_value.model_dump.return_value = {
                "id": "ne-1",
                "name": "R2",
                "vendor": "Cisco",
                "device_type": "cisco_ios",
                "ip_address": "192.168.0.128",
                "port": 22,
                "protocol": "ssh",
                "connect_status": "pass",
                "hop_enabled": False,
                "hop_vendor": "zte",
            }
            out = execute_managed_ne_commands(db, "ne-1", ["show version"])
        self.assertTrue(out["ok"])
        self.assertEqual(out["output"], "ok-output")
        self.assertEqual(out["commands"], ["show version"])

    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="ok-output")
    @patch("netx_api.ne_exec.get_device_credentials", return_value={"ip_address": "1.1.1.1"})
    def test_execute_skips_device_when_any_command_invalid(self, _creds, collect, _configured) -> None:
        db = MagicMock()
        with self.assertRaises(HTTPException) as ctx:
            execute_managed_ne_commands(
                db,
                "ne-1",
                ["show interface", "configure terminal"],
            )
        self.assertEqual(ctx.exception.detail, "command_blocked")
        collect.assert_not_called()
        db.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
