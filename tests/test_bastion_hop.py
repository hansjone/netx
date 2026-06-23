from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.ne_session_factory import (
    default_bastion_username_template,
    open_netmiko_connection,
    render_hop_command,
)


class BastionTemplateTests(unittest.TestCase):
    def test_default_bastion_username_template(self) -> None:
        self.assertEqual(
            default_bastion_username_template(),
            "{hop_user}@{target_user}@{target_ip}@{hop_host}",
        )

    def test_render_bastion_composite_username(self) -> None:
        creds = {
            "hop_vendor": "bastion",
            "hop_username": "bastion-user",
            "hop_host": "1.1.1.1",
            "username": "target-user",
            "ip_address": "2.2.2.2",
            "hop_protocol": "ssh",
            "hop_vrf": "",
        }
        out = render_hop_command("", creds)
        self.assertEqual(out, "bastion-user@target-user@2.2.2.2@1.1.1.1")

    def test_render_custom_bastion_template(self) -> None:
        creds = {
            "hop_vendor": "bastion",
            "hop_username": "admin",
            "hop_host": "1.2.3.4",
            "username": "root",
            "ip_address": "5.6.7.8",
            "hop_command_template": "{hop_user}#{target_user}@{target_ip}",
            "hop_protocol": "ssh",
            "hop_vrf": "",
        }
        out = render_hop_command(creds["hop_command_template"], creds)
        self.assertEqual(out, "admin#root@5.6.7.8")


class BastionConnectRoutingTests(unittest.TestCase):
    @patch("netx_api.ne_session_factory._connect_via_bastion")
    @patch("netx_api.ne_session_factory._connect_direct")
    def test_open_routes_to_bastion_when_enabled(self, direct, bastion) -> None:
        bastion.return_value = MagicMock()
        creds = {"hop_enabled": True, "hop_vendor": "bastion"}
        open_netmiko_connection(creds)
        bastion.assert_called_once()
        direct.assert_not_called()

    @patch("netx_api.ne_session_factory._connect_via_bastion")
    @patch("netx_api.ne_session_factory._connect_via_linux_hop")
    def test_open_routes_linux_not_bastion(self, linux, bastion) -> None:
        linux.return_value = MagicMock()
        creds = {"hop_enabled": True, "hop_vendor": "linux"}
        open_netmiko_connection(creds)
        linux.assert_called_once()
        bastion.assert_not_called()


class BastionConnectImplTests(unittest.TestCase):
    @patch("netx_api.ne_session_factory._netmiko_over_ssh_client")
    @patch("netx_api.ne_session_factory._bastion_ssh_connect")
    def test_bastion_managed_skips_secondary_auth(self, bastion_ssh, netmiko_wrap) -> None:
        from netx_api.ne_session_factory import _connect_via_bastion

        ssh_client = MagicMock()
        bastion_ssh.return_value = ssh_client
        conn = MagicMock()
        netmiko_wrap.return_value = conn
        creds = {
            "hop_host": "1.1.1.1",
            "hop_username": "bastion-user",
            "hop_password": "vault-pass",
            "hop_port": 22,
            "device_type": "zte_zxros",
            "protocol": "ssh",
            "username": "target-user",
            "ip_address": "2.2.2.2",
            "password": "",
            "hop_target_auth_mode": "bastion_managed",
            "hop_vendor": "bastion",
            "hop_protocol": "ssh",
            "hop_vrf": "",
        }
        _connect_via_bastion(creds)
        bastion_ssh.assert_called_once_with(
            host="1.1.1.1",
            port=22,
            username="bastion-user@target-user@2.2.2.2@1.1.1.1",
            password="vault-pass",
            timeout=unittest.mock.ANY,
        )
        netmiko_wrap.assert_called_once()
        wrap_kwargs = netmiko_wrap.call_args.kwargs
        self.assertEqual(wrap_kwargs["host"], "1.1.1.1")
        self.assertEqual(wrap_kwargs["username"], "bastion-user@target-user@2.2.2.2@1.1.1.1")
        self.assertEqual(wrap_kwargs["password"], "vault-pass")
        conn.disconnect.assert_not_called()

    @patch("netx_api.ne_session_factory._interactive_target_auth")
    @patch("netx_api.ne_session_factory._read_channel")
    @patch("netx_api.ne_session_factory._netmiko_over_ssh_client")
    @patch("netx_api.ne_session_factory._bastion_ssh_connect")
    def test_bastion_manual_invokes_secondary_auth(
        self, bastion_ssh, netmiko_wrap, _read, interact
    ) -> None:
        from netx_api.ne_session_factory import _connect_via_bastion

        bastion_ssh.return_value = MagicMock()
        conn = MagicMock()
        netmiko_wrap.return_value = conn
        creds = {
            "hop_host": "10.0.0.1",
            "hop_username": "bastion-user",
            "hop_password": "bastion-pass",
            "hop_port": 2222,
            "device_type": "cisco_ios",
            "protocol": "ssh",
            "username": "target-user",
            "ip_address": "10.0.0.2",
            "password": "target-pass",
            "hop_target_auth_mode": "manual",
            "hop_vendor": "bastion",
            "hop_protocol": "ssh",
            "hop_vrf": "",
        }
        _connect_via_bastion(creds)
        interact.assert_called_once_with(conn, "target-user", "target-pass")


class BastionInteractiveHandlerTests(unittest.TestCase):
    def test_replies_to_vault_password_prompt(self) -> None:
        from netx_api.ne_session_factory import _bastion_interactive_handler

        handler = _bastion_interactive_handler("vault-secret")
        out = handler(
            "Login",
            "",
            [("(ZTE-TSM@user@1.1.1.1@2.2.2.2) Vault Password:", False)],
        )
        self.assertEqual(out, ["vault-secret"])

    def test_replies_to_all_fields_when_prompt_unknown(self) -> None:
        from netx_api.ne_session_factory import _bastion_interactive_handler

        handler = _bastion_interactive_handler("vault-secret")
        out = handler("Login", "", [("Enter code:", False), ("Confirm:", False)])
        self.assertEqual(out, ["vault-secret", "vault-secret"])

    def test_empty_prompt_list_returns_empty(self) -> None:
        from netx_api.ne_session_factory import _bastion_interactive_handler

        handler = _bastion_interactive_handler("vault-secret")
        self.assertEqual(handler("Login", "", []), [])


if __name__ == "__main__":
    unittest.main()
