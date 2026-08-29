from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from netx_api.ne_session_connect import _collection_driver_class, _zte_collection_driver_class
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


class ZteCollectionPrepTests(unittest.TestCase):
    def test_collection_driver_selects_zte_wrapper(self) -> None:
        base = _netmiko_driver_class("zte_zxros_ssh")
        wrapped = _collection_driver_class("zte_zxros_ssh", base)
        self.assertIsNot(wrapped, base)
        self.assertTrue(wrapped.__name__.startswith("ZteCollection"))

    def test_collection_driver_selects_huawei_wrapper(self) -> None:
        base = _netmiko_driver_class("huawei")
        wrapped = _collection_driver_class("huawei", base)
        self.assertIsNot(wrapped, base)
        self.assertTrue(wrapped.__name__.startswith("HuaweiCollection"))

    def test_prompt_timeout_sends_return_then_retries(self) -> None:
        base = _netmiko_driver_class("zte_zxros_ssh")
        cls = _zte_collection_driver_class(base)
        sess = cls.__new__(cls)
        calls: list[str] = []

        def _test_channel_read(*, pattern: str):
            calls.append(f"read:{pattern}")
            if calls.count(f"read:{pattern}") == 1:
                raise TimeoutError("ReadTimeout: Pattern not detected: '[>#]'")

        sess._test_channel_read = _test_channel_read  # type: ignore[method-assign]
        sess.write_channel = MagicMock(side_effect=lambda data: calls.append(f"write:{data!r}"))  # type: ignore[method-assign]
        sess.set_base_prompt = MagicMock()  # type: ignore[method-assign]
        sess.disable_paging = MagicMock(return_value="")  # type: ignore[method-assign]
        sess.clear_buffer = MagicMock()  # type: ignore[method-assign]
        sess.global_delay_factor = 0.01

        sess.session_preparation()

        self.assertEqual(calls[0], "read:[>#]")
        self.assertEqual(calls[1], "write:'\\n'")
        self.assertEqual(calls[2], "read:[>#]")
        sess.disable_paging.assert_called_once()
        sess.set_base_prompt.assert_called_once()


if __name__ == "__main__":
    unittest.main()
