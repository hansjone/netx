from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from netx_api.ne_exec import _validate_command, execute_managed_ne_commands


def _ready_creds(**overrides) -> dict:
    base = {
        "ip_address": "1.1.1.1",
        "protocol": "ssh",
        "username": "admin",
        "password": "secret",
        "hop_enabled": False,
    }
    base.update(overrides)
    return base


class NeExecValidationTests(unittest.TestCase):
    def test_allows_show(self) -> None:
        _validate_command("show ip interface brief")

    def test_allows_display(self) -> None:
        _validate_command("display interface brief")

    def test_allows_ping(self) -> None:
        _validate_command("ping 192.168.0.1")
        _validate_command("ping6 2001::1")
        _validate_command("PING 10.0.0.1 vrf MGMT")

    def test_allows_traceroute(self) -> None:
        _validate_command("traceroute 192.168.0.1")
        _validate_command("tracert 192.168.0.1")
        _validate_command("trace 10.0.0.1")
        _validate_command("trace6 2001::1")
        _validate_command("TRACEROUTE 10.0.0.1 vpn-instance MGMT")

    def test_blocks_non_allowed_prefix(self) -> None:
        for cmd in (
            "get system info",
            "terminal length 0",
            "?",
        ):
            with self.subTest(cmd=cmd):
                with self.assertRaises(HTTPException) as ctx:
                    _validate_command(cmd)
                self.assertEqual(ctx.exception.detail, "command_not_allowed_prefix")

    def test_allows_pipe_filter_subcommands(self) -> None:
        for cmd in (
            "show run | include hostname",
            "show configuration | include hostname",
            "display current-configuration | include sysname",
            "show run | exclude ^!",
            "show run | begin interface",
            "show run | section ^router",
            "show ip route | count",
            "show run | match hostname",
            "show run | grep hostname",
            "show run | one-line",
            "display current-configuration | no-more",
            "show run | include x | include y",
        ):
            with self.subTest(cmd=cmd):
                _validate_command(cmd)

    def test_blocks_pipe_redirect_and_unknown(self) -> None:
        for cmd in (
            "show run | redirect tftp://1.1.1.1/config",
            "show run | append flash:cfg.txt",
            "show run | tee flash:cfg.txt",
            "show run | send log",
            "show run | unknown-filter x",
            "show run |",
            "show run | | include x",
        ):
            with self.subTest(cmd=cmd):
                with self.assertRaises(HTTPException) as ctx:
                    _validate_command(cmd)
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(ctx.exception.detail, "command_pipe_not_allowed")

    def test_blocks_configure(self) -> None:
        with self.assertRaises(HTTPException):
            _validate_command("configure terminal")

    def test_blocks_configure_after_pipe(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("show run | configure terminal")
        self.assertEqual(ctx.exception.detail, "command_blocked")

    def test_blocks_non_show_prefix(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("interface GigabitEthernet0/0")
        self.assertEqual(ctx.exception.detail, "command_not_allowed_prefix")

    def test_linux_shell_allows_shell_commands(self) -> None:
        for cmd in (
            "ls -la /var/log",
            "ip addr | grep eth0",
            "systemctl status sshd",
            "cat /etc/os-release && uname -a",
            "df -h; free -m",
        ):
            with self.subTest(cmd=cmd):
                _validate_command(cmd, policy="linux_shell")
                _validate_command(cmd, policy="unrestricted")

    def test_effective_policy_forces_readonly_when_feature_off(self) -> None:
        from netx_api.ne_exec_guard import effective_exec_policy

        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=False):
            self.assertEqual(effective_exec_policy("linux_shell", device_type="linux"), "readonly")
            self.assertEqual(effective_exec_policy("unrestricted", device_type="linux"), "readonly")

    def test_effective_policy_forces_readonly_for_non_linux(self) -> None:
        from netx_api.ne_exec_guard import effective_exec_policy

        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=True):
            self.assertEqual(effective_exec_policy("linux_shell", device_type="zte_zxros"), "readonly")
            self.assertEqual(effective_exec_policy("linux_shell", device_type="linux"), "linux_shell")
            self.assertEqual(effective_exec_policy("unrestricted", device_type="linux_ssh"), "unrestricted")

    def test_require_writable_rejects_when_feature_off(self) -> None:
        from netx_api.ne_exec_guard import require_exec_policy_writable

        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=False):
            self.assertEqual(require_exec_policy_writable("readonly"), "readonly")
            with self.assertRaises(HTTPException) as ctx:
                require_exec_policy_writable("linux_shell", device_type="linux")
            self.assertEqual(ctx.exception.detail, "exec_policy_feature_disabled")

    def test_require_writable_rejects_non_linux(self) -> None:
        from netx_api.ne_exec_guard import require_exec_policy_writable

        with patch("netx_api.ne_exec_guard.exec_policy_feature_enabled", return_value=True):
            self.assertEqual(
                require_exec_policy_writable("linux_shell", device_type="linux"),
                "linux_shell",
            )
            with self.assertRaises(HTTPException) as ctx:
                require_exec_policy_writable("linux_shell", device_type="cisco_ios")
            self.assertEqual(ctx.exception.detail, "exec_policy_requires_linux_device_type")

    def test_linux_shell_blocks_newline(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("ls\nrm -rf /", policy="linux_shell")
        self.assertEqual(ctx.exception.detail, "command_chars_not_allowed")

    def test_readonly_still_blocks_linux_cmds(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            _validate_command("ls -la", policy="readonly")
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

    def test_blocks_vendor_destructive(self) -> None:
        for cmd in ("save", "request system reboot", "clear configuration", "file delete flash:/x"):
            with self.assertRaises(HTTPException) as ctx:
                _validate_command(cmd)
            self.assertEqual(ctx.exception.detail, "command_blocked", cmd)

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
    @patch("netx_api.ne_exec.resolve_cli_target")
    def test_execute_success(self, resolve, _collect, _configured) -> None:
        resolve.return_value = (
            _ready_creds(),
            {
                "source": "managed",
                "id": "ne-1",
                "ume_ne_id": None,
                "name": "R2",
                "vendor": "Cisco",
                "device_type": "cisco_ios",
                "ip_address": "192.168.0.128",
                "port": 22,
                "protocol": "ssh",
                "connect_status": "pass",
                "hop_enabled": False,
                "hop_vendor": "zte",
            },
        )
        db = MagicMock()
        out = execute_managed_ne_commands(db, ["show version"], ne_id="ne-1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["output"], "ok-output")
        self.assertEqual(out["commands"], ["show version"])

    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="ok-output")
    @patch("netx_api.ne_exec.resolve_cli_target")
    def test_execute_skips_when_no_password(self, resolve, collect, _configured) -> None:
        resolve.return_value = (
            _ready_creds(password=""),
            {"source": "managed", "id": "ne-1", "name": "R2", "ip_address": "192.168.0.128"},
        )
        db = MagicMock()
        out = execute_managed_ne_commands(db, ["show version"], ne_id="ne-1")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "no_password")
        collect.assert_not_called()

    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="ok-output")
    @patch("netx_api.ne_exec.resolve_cli_target")
    def test_execute_skips_device_when_any_command_invalid(self, resolve, collect, _configured) -> None:
        resolve.return_value = (
            _ready_creds(),
            {
                "source": "managed",
                "id": "ne-1",
                "exec_policy": "readonly",
                "name": "R2",
                "ip_address": "192.168.0.128",
            },
        )
        db = MagicMock()
        with self.assertRaises(HTTPException) as ctx:
            execute_managed_ne_commands(
                db,
                ["show interface", "configure terminal"],
                ne_id="ne-1",
            )
        self.assertEqual(ctx.exception.detail, "command_blocked")
        collect.assert_not_called()
        resolve.assert_called_once()

    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="shell-ok")
    @patch("netx_api.ne_exec.resolve_cli_target")
    def test_execute_linux_shell_policy_allows_shell(self, resolve, collect, _configured) -> None:
        resolve.return_value = (
            _ready_creds(),
            {
                "source": "managed",
                "id": "linux-1",
                "exec_policy": "linux_shell",
                "name": "lab",
                "device_type": "linux",
                "ip_address": "10.0.0.9",
            },
        )
        db = MagicMock()
        with patch("netx_api.config.settings") as mock_settings:
            mock_settings.ne_exec_policy_enabled = True
            out = execute_managed_ne_commands(db, ["ls -la /tmp"], ne_id="linux-1")
        self.assertTrue(out["ok"])
        self.assertEqual(out["output"], "shell-ok")
        self.assertEqual(out["device"]["exec_policy"], "linux_shell")
        collect.assert_called_once()

    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="ok-output")
    @patch("netx_api.ne_exec.resolve_cli_target")
    def test_execute_ignores_db_policy_when_feature_off(self, resolve, collect, _configured) -> None:
        resolve.return_value = (
            _ready_creds(),
            {
                "source": "managed",
                "id": "linux-1",
                "exec_policy": "linux_shell",
                "name": "lab",
                "device_type": "linux",
                "ip_address": "10.0.0.9",
            },
        )
        db = MagicMock()
        with patch("netx_api.config.settings") as mock_settings:
            mock_settings.ne_exec_policy_enabled = False
            with self.assertRaises(HTTPException) as ctx:
                execute_managed_ne_commands(db, ["ls -la /tmp"], ne_id="linux-1")
            self.assertEqual(ctx.exception.detail, "command_not_allowed_prefix")
        collect.assert_not_called()

    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="ok-output")
    @patch("netx_api.ne_exec.resolve_cli_target")
    def test_execute_respects_max_commands_setting(self, resolve, collect, _configured) -> None:
        db = MagicMock()
        cmds = [f"show version {i}" for i in range(6)]
        with patch("netx_api.ne_exec.settings") as mock_settings:
            mock_settings.ne_exec_max_commands = 5
            with self.assertRaises(HTTPException) as ctx:
                execute_managed_ne_commands(db, cmds, ne_id="ne-1")
            self.assertIn("too_many_commands", str(ctx.exception.detail))
            collect.assert_not_called()

        resolve.return_value = (
            _ready_creds(),
            {
                "source": "managed",
                "id": "ne-1",
                "ume_ne_id": None,
                "name": "R2",
                "vendor": "Cisco",
                "device_type": "cisco_ios",
                "ip_address": "192.168.0.128",
                "port": 22,
                "protocol": "ssh",
                "connect_status": "pass",
                "hop_enabled": False,
                "hop_vendor": "zte",
            },
        )
        with patch("netx_api.ne_exec.settings") as mock_settings:
            mock_settings.ne_exec_max_commands = 10
            mock_settings.ne_collect_read_timeout_sec = 120
            out = execute_managed_ne_commands(db, cmds, ne_id="ne-1")
        self.assertTrue(out["ok"])
        collect.assert_called_once()


class NeExecBatchTests(unittest.TestCase):
    @patch("netx_api.ne_exec.credentials_configured", return_value=True)
    @patch("netx_api.ne_exec._collect_on_device", return_value="ok-output")
    @patch("netx_api.ne_exec.resolve_cli_target")
    @patch("netx_api.ne_exec.SessionLocal")
    def test_batch_ne_ids_runs_concurrently(
        self,
        session_local: MagicMock,
        resolve: MagicMock,
        collect: MagicMock,
        _creds: MagicMock,
    ) -> None:
        from netx_api.ne_exec import execute_managed_ne_commands_batch

        session_local.return_value = MagicMock()
        resolve.return_value = (
            _ready_creds(),
            {
                "source": "managed",
                "id": "ne-1",
                "name": "R1",
                "ip_address": "1.1.1.1",
            },
        )
        with patch("netx_api.ne_exec.settings") as mock_settings:
            mock_settings.ne_exec_max_commands = 5
            out = execute_managed_ne_commands_batch(
                ne_ids=["a", "b", "c"],
                commands=["show version"],
                concurrency=3,
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["summary"]["total"], 3)
        self.assertEqual(out["summary"]["ok"], 3)
        self.assertEqual(out["summary"]["failed"], 0)
        self.assertEqual(collect.call_count, 3)
        self.assertEqual(len(out["results"]), 3)

    def test_batch_requires_targets(self) -> None:
        from netx_api.ne_exec import execute_managed_ne_commands_batch

        with self.assertRaises(HTTPException) as ctx:
            execute_managed_ne_commands_batch(commands=["show version"])
        self.assertEqual(ctx.exception.detail, "targets_required")


if __name__ == "__main__":
    unittest.main()
