from __future__ import annotations

import unittest

from netx_api.cli_creds import (
    REASON_HOP_INCOMPLETE,
    REASON_NO_PASSWORD,
    REASON_USERNAME_REQUIRED,
    cli_creds_ready,
    cli_creds_skip_reason,
)


class CliCredsTests(unittest.TestCase):
    def test_direct_ssh_requires_password(self) -> None:
        creds = {
            "ip_address": "10.0.0.1",
            "protocol": "ssh",
            "username": "admin",
            "password": "",
            "hop_enabled": False,
        }
        ready, reason = cli_creds_ready(creds, interactive=False)
        self.assertFalse(ready)
        self.assertEqual(reason, REASON_NO_PASSWORD)
        self.assertEqual(cli_creds_skip_reason(creds), REASON_NO_PASSWORD)

    def test_direct_ssh_ready(self) -> None:
        creds = {
            "ip_address": "10.0.0.1",
            "protocol": "ssh",
            "username": "admin",
            "password": "secret",
            "hop_enabled": False,
        }
        ready, reason = cli_creds_ready(creds, interactive=False)
        self.assertTrue(ready)
        self.assertEqual(reason, "")
        self.assertIsNone(cli_creds_skip_reason(creds))

    def test_bastion_managed_allows_empty_target_password(self) -> None:
        creds = {
            "ip_address": "10.0.0.2",
            "protocol": "ssh",
            "username": "ca-oper",
            "password": "",
            "hop_enabled": True,
            "hop_vendor": "bastion",
            "hop_target_auth_mode": "bastion_managed",
            "hop_host": "10.34.145.27",
            "hop_username": "jump",
            "hop_password": "hop-secret",
        }
        ready, reason = cli_creds_ready(creds, interactive=False)
        self.assertTrue(ready)
        self.assertEqual(reason, "")

    def test_cli_hop_requires_target_password(self) -> None:
        creds = {
            "ip_address": "10.0.0.3",
            "protocol": "ssh",
            "username": "admin",
            "password": "",
            "hop_enabled": True,
            "hop_vendor": "zte",
            "hop_target_auth_mode": "manual",
            "hop_host": "10.1.1.1",
            "hop_username": "hop",
            "hop_password": "hop-secret",
        }
        ready, reason = cli_creds_ready(creds, interactive=False)
        self.assertFalse(ready)
        self.assertEqual(reason, REASON_NO_PASSWORD)

    def test_hop_incomplete(self) -> None:
        creds = {
            "ip_address": "10.0.0.4",
            "protocol": "ssh",
            "username": "admin",
            "password": "secret",
            "hop_enabled": True,
            "hop_vendor": "zte",
            "hop_host": "",
            "hop_username": "hop",
            "hop_password": "",
        }
        ready, reason = cli_creds_ready(creds, interactive=False)
        self.assertFalse(ready)
        self.assertEqual(reason, REASON_HOP_INCOMPLETE)

    def test_interactive_telnet_allows_empty_password(self) -> None:
        creds = {
            "ip_address": "10.0.0.5",
            "protocol": "telnet",
            "username": "",
            "password": "",
            "hop_enabled": False,
        }
        ready, reason = cli_creds_ready(creds, interactive=True)
        self.assertTrue(ready)
        ready_exec, reason_exec = cli_creds_ready(creds, interactive=False)
        self.assertFalse(ready_exec)
        self.assertEqual(reason_exec, REASON_USERNAME_REQUIRED)

    def test_non_interactive_telnet_requires_password(self) -> None:
        creds = {
            "ip_address": "10.0.0.6",
            "protocol": "telnet",
            "username": "admin",
            "password": "",
            "hop_enabled": False,
        }
        ready, reason = cli_creds_ready(creds, interactive=False)
        self.assertFalse(ready)
        self.assertEqual(reason, REASON_NO_PASSWORD)


if __name__ == "__main__":
    unittest.main()
