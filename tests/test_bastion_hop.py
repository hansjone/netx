from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.ne_session_factory import (
    bastion_ssh_cli,
    default_bastion_username_template,
    open_netmiko_connection,
    render_hop_command,
    resolve_bastion_ssh_username,
)


class BastionTemplateTests(unittest.TestCase):
    def test_default_bastion_username_template(self) -> None:
        self.assertEqual(
            default_bastion_username_template(),
            "{hop_user}@{target_user}@{target_ip}",
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
        self.assertEqual(out, "bastion-user@target-user@2.2.2.2")

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

    def test_resolve_strips_legacy_hop_host_suffix(self) -> None:
        self.assertEqual(
            resolve_bastion_ssh_username("bastion-user@target-user@198.51.100.10@192.0.2.10", "192.0.2.10"),
            "bastion-user@target-user@198.51.100.10",
        )

    def test_resolve_strips_domain_bastion_suffix(self) -> None:
        self.assertEqual(
            resolve_bastion_ssh_username(
                "bastion-user@target-user@198.51.100.20@ssh-bastion.example.com",
                "ssh-bastion.example.com",
            ),
            "bastion-user@target-user@198.51.100.20",
        )

    def test_resolve_keeps_username_without_hop_host_suffix(self) -> None:
        self.assertEqual(
            resolve_bastion_ssh_username("bastion-user@target-user@198.51.100.10", "192.0.2.10"),
            "bastion-user@target-user@198.51.100.10",
        )

    def test_bastion_ssh_cli(self) -> None:
        self.assertEqual(
            bastion_ssh_cli("bastion-user@target-user@198.51.100.10", "192.0.2.10"),
            "ssh bastion-user@target-user@198.51.100.10@192.0.2.10",
        )

    def test_bastion_ssh_cli_domain(self) -> None:
        self.assertEqual(
            bastion_ssh_cli("bastion-user@target-user@198.51.100.20", "ssh-bastion.example.com"),
            "ssh bastion-user@target-user@198.51.100.20@ssh-bastion.example.com",
        )

    def test_parse_domain_bastion_destination(self) -> None:
        from netx_api.ne_hop_templates import expand_bastion_hop_fields, parse_bastion_ssh_destination

        parsed = parse_bastion_ssh_destination(
            "ssh bastion-user@target-user@198.51.100.20@ssh-bastion.example.com"
        )
        self.assertEqual(parsed["hop_host"], "ssh-bastion.example.com")
        self.assertEqual(parsed["hop_username"], "bastion-user")
        self.assertEqual(parsed["target_user"], "target-user")
        self.assertEqual(parsed["target_ip"], "198.51.100.20")
        self.assertEqual(parsed["ssh_username"], "bastion-user@target-user@198.51.100.20")
        host, user = expand_bastion_hop_fields(
            hop_host="bastion-user@target-user@198.51.100.20@ssh-bastion.example.com",
            hop_username="",
        )
        self.assertEqual(host, "ssh-bastion.example.com")
        self.assertEqual(user, "bastion-user")

    def test_render_bastion_with_domain_hop_host(self) -> None:
        creds = {
            "hop_vendor": "bastion",
            "hop_username": "bastion-user",
            "hop_host": "ssh-bastion.example.com",
            "username": "target-user",
            "ip_address": "198.51.100.20",
            "hop_protocol": "ssh",
            "hop_vrf": "",
        }
        out = render_hop_command("", creds)
        self.assertEqual(out, "bastion-user@target-user@198.51.100.20")
        self.assertEqual(
            bastion_ssh_cli(out, creds["hop_host"]),
            "ssh bastion-user@target-user@198.51.100.20@ssh-bastion.example.com",
        )

    def test_netmiko_driver_class_resolves_zte(self) -> None:
        from netmiko.zte.zte_zxros import ZteZxrosSSH

        from netx_api.ne_session_factory import _netmiko_driver_class

        self.assertIs(_netmiko_driver_class("zte_zxros_ssh"), ZteZxrosSSH)


class BastionConnectRoutingTests(unittest.TestCase):
    @patch("netx_api.ne_session_connect._connect_via_bastion")
    @patch("netx_api.ne_session_connect._connect_direct")
    def test_open_routes_to_bastion_when_enabled(self, direct, bastion) -> None:
        bastion.return_value = MagicMock()
        creds = {"hop_enabled": True, "hop_vendor": "bastion"}
        open_netmiko_connection(creds)
        bastion.assert_called_once()
        direct.assert_not_called()

    @patch("netx_api.ne_session_connect._connect_via_bastion")
    @patch("netx_api.ne_session_connect._connect_via_linux_hop")
    def test_open_routes_linux_not_bastion(self, linux, bastion) -> None:
        linux.return_value = MagicMock()
        creds = {"hop_enabled": True, "hop_vendor": "linux"}
        open_netmiko_connection(creds)
        linux.assert_called_once()
        bastion.assert_not_called()


class BastionConnectImplTests(unittest.TestCase):
    @patch("netx_api.ne_session_connect._netmiko_over_ssh_client")
    @patch("netx_api.ne_session_connect._bastion_ssh_connect")
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
            username="bastion-user@target-user@2.2.2.2",
            password="vault-pass",
            timeout=unittest.mock.ANY,
        )
        netmiko_wrap.assert_called_once()
        wrap_kwargs = netmiko_wrap.call_args.kwargs
        self.assertEqual(wrap_kwargs["host"], "1.1.1.1")
        self.assertEqual(wrap_kwargs["username"], "bastion-user@target-user@2.2.2.2")
        self.assertEqual(wrap_kwargs["password"], "vault-pass")
        conn.disconnect.assert_not_called()

    @patch("netx_api.ne_session_connect._netmiko_over_ssh_client")
    @patch("netx_api.ne_session_connect._bastion_ssh_connect")
    def test_bastion_domain_host_connect(self, bastion_ssh, netmiko_wrap) -> None:
        from netx_api.ne_session_factory import _connect_via_bastion

        bastion_ssh.return_value = MagicMock()
        netmiko_wrap.return_value = MagicMock()
        creds = {
            "hop_host": "ssh-bastion.example.com",
            "hop_username": "bastion-user",
            "hop_password": "vault-pass",
            "hop_port": 22,
            "device_type": "zte_zxros",
            "protocol": "ssh",
            "username": "target-user",
            "ip_address": "198.51.100.20",
            "password": "",
            "hop_target_auth_mode": "bastion_managed",
            "hop_vendor": "bastion",
            "hop_protocol": "ssh",
            "hop_vrf": "",
        }
        _connect_via_bastion(creds)
        bastion_ssh.assert_called_once_with(
            host="ssh-bastion.example.com",
            port=22,
            username="bastion-user@target-user@198.51.100.20",
            password="vault-pass",
            timeout=unittest.mock.ANY,
        )

    @patch("netx_api.ne_session_connect._netmiko_over_ssh_client")
    @patch("netx_api.ne_session_connect._bastion_ssh_connect")
    def test_bastion_pasted_destination_expands_on_connect(self, bastion_ssh, netmiko_wrap) -> None:
        from netx_api.ne_session_factory import _connect_via_bastion

        bastion_ssh.return_value = MagicMock()
        netmiko_wrap.return_value = MagicMock()
        creds = {
            "hop_host": "bastion-user@target-user@198.51.100.20@ssh-bastion.example.com",
            "hop_username": "",
            "hop_password": "vault-pass",
            "hop_port": 22,
            "device_type": "zte_zxros",
            "protocol": "ssh",
            "username": "target-user",
            "ip_address": "198.51.100.20",
            "password": "",
            "hop_target_auth_mode": "bastion_managed",
            "hop_vendor": "bastion",
            "hop_protocol": "ssh",
            "hop_vrf": "",
        }
        _connect_via_bastion(creds)
        bastion_ssh.assert_called_once_with(
            host="ssh-bastion.example.com",
            port=22,
            username="bastion-user@target-user@198.51.100.20",
            password="vault-pass",
            timeout=unittest.mock.ANY,
        )

    @patch("netx_api.ne_session_connect._interactive_target_auth")
    @patch("netx_api.ne_session_connect._read_channel")
    @patch("netx_api.ne_session_connect._netmiko_over_ssh_client")
    @patch("netx_api.ne_session_connect._bastion_ssh_connect")
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
            "hop_command_template": "{hop_user}@{target_user}@{target_ip}@{hop_host}",
        }
        _connect_via_bastion(creds)
        bastion_ssh.assert_called_once_with(
            host="10.0.0.1",
            port=2222,
            username="bastion-user@target-user@10.0.0.2",
            password="bastion-pass",
            timeout=unittest.mock.ANY,
        )
        interact.assert_called_once_with(
            conn, "target-user", "target-pass", progress_cb=None, emit_raw=True
        )


class BastionInteractiveHandlerTests(unittest.TestCase):
    def test_replies_to_vault_password_prompt(self) -> None:
        from netx_api.ne_session_factory import _bastion_interactive_handler

        handler, _prompts = _bastion_interactive_handler("vault-secret")
        out = handler(
            "Login",
            "",
            [("(ZTE-TSM@user@1.1.1.1@2.2.2.2) Vault Password:", False)],
        )
        self.assertEqual(out, ["vault-secret"])

    def test_replies_to_all_fields(self) -> None:
        from netx_api.ne_session_factory import _bastion_interactive_handler

        handler, _prompts = _bastion_interactive_handler("vault-secret")
        out = handler("Login", "", [("Enter code:", False), ("Confirm:", False)])
        self.assertEqual(out, ["vault-secret", "vault-secret"])

    def test_empty_prompt_list_returns_empty(self) -> None:
        from netx_api.ne_session_factory import _bastion_interactive_handler

        handler, _prompts = _bastion_interactive_handler("vault-secret")
        self.assertEqual(handler("Login", "", []), [])

    def test_records_prompts_for_diagnostics(self) -> None:
        from netx_api.ne_session_factory import _bastion_interactive_handler

        handler, prompts = _bastion_interactive_handler("vault-secret")
        handler("Login", "", [("Vault Password:", False)])
        self.assertEqual(prompts, ["Vault Password:"])


if __name__ == "__main__":
    unittest.main()
