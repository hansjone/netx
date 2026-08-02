from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.ne_session_factory import (
    _build_netmiko_connection,
    _interactive_driver_class,
    _netmiko_driver_class,
    open_netmiko_connection,
)


class InteractiveNetmikoTests(unittest.TestCase):
    def test_interactive_driver_skips_paging_commands(self) -> None:
        base = _netmiko_driver_class("cisco_ios")
        cls = _interactive_driver_class(base)
        self.assertEqual(cls.disable_paging(None), "")
        self.assertEqual(cls.set_terminal_width(None), "")

    def test_build_non_interactive_uses_connect_handler(self) -> None:
        fake = MagicMock(name="conn")
        with patch("netx_api.ne_session_connect.ConnectHandler", return_value=fake) as ch:
            out = _build_netmiko_connection(
                {
                    "device_type": "linux",
                    "host": "1.1.1.1",
                    "username": "u",
                    "password": "p",
                },
                interactive=False,
            )
        self.assertIs(out, fake)
        ch.assert_called_once()

    def test_open_webcrt_passes_interactive_flag(self) -> None:
        creds = {
            "device_type": "cisco_ios",
            "protocol": "ssh",
            "ip_address": "10.0.0.1",
            "port": 22,
            "username": "u",
            "password": "p",
        }
        with patch("netx_api.ne_session_connect._connect_direct") as direct:
            direct.return_value = MagicMock()
            open_netmiko_connection(creds, interactive=True)
            direct.assert_called_once()
            self.assertTrue(direct.call_args.kwargs.get("interactive"))


if __name__ == "__main__":
    unittest.main()
