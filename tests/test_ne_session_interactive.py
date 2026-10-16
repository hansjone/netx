from __future__ import annotations

import unittest
import re
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from netmiko.base_connection import BaseConnection

from netx_api.ne_session_connect import _collection_driver_class, _zte_collection_driver_class, _cisco_ios_collection_driver_class, _base_connect_kwargs
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


class CiscoCollectionPrepTests(unittest.TestCase):
    def test_only_cisco_ssh_uses_cr_enter_on_the_actual_driver(self):
        for device_type in ("cisco_ios", "cisco_ios_ssh", "cisco_ios_telnet", "huawei", "linux_ssh", "mikrotik_routeros"):
            with self.subTest(device_type=device_type):
                kwargs = _base_connect_kwargs(device_type=device_type, host="127.0.0.1", port=22,
                                              username="fixture", password="fixture", enable_secret="")
                if device_type in ("cisco_ios", "cisco_ios_ssh"):
                    self.assertEqual(kwargs["default_enter"], "\r")
                    driver = BaseConnection(**kwargs, auto_connect=False)
                    self.assertEqual(driver.normalize_cmd("show version"), "show version\r")
                else:
                    self.assertNotIn("default_enter", kwargs)

    def session(self):
        cls = _cisco_ios_collection_driver_class(_netmiko_driver_class("cisco_ios"))
        sess = cls.__new__(cls)
        sess.base_prompt = "R2.lab"
        sess.RETURN = "\r"
        sess._test_channel_read = MagicMock()  # type: ignore[method-assign]
        sess.set_base_prompt = MagicMock()  # type: ignore[method-assign]
        sess.write_channel = MagicMock()  # type: ignore[method-assign]
        sess.read_until_pattern = MagicMock()  # type: ignore[method-assign]
        sess.set_terminal_width = MagicMock()  # type: ignore[method-assign]
        sess.disable_paging = MagicMock()  # type: ignore[method-assign]
        sess.send_command_timing = MagicMock()  # type: ignore[method-assign]
        return sess

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_setup_only_establishes_hostname_prompt(self, drain: MagicMock):
        sess = self.session()
        order: list[str] = []
        drain.side_effect = lambda *_a, **_k: order.append("drain")
        sess.set_base_prompt.side_effect = lambda **kw: order.append("prompt")
        sess.session_preparation()
        self.assertEqual(order, ["prompt", "drain"])
        sess.set_base_prompt.assert_called_once()
        initial = sess._test_channel_read.call_args.kwargs["pattern"]
        self.assertIsNotNone(re.search(initial, "R2.lab#"))
        self.assertIsNone(re.search(initial, "R2.lab#terminal length 0"))
        channel = SimpleNamespace(read_timeout_override=None, _read_buffer="", read_channel=lambda: "R2.lab#")
        self.assertEqual(BaseConnection.read_until_pattern(channel, pattern=initial), "R2.lab#")
        sess.set_terminal_width.assert_not_called()
        sess.disable_paging.assert_not_called()
        sess.write_channel.assert_not_called()
        sess.send_command_timing.assert_not_called()
        drain.assert_called_once()
        self.assertEqual(drain.call_args.kwargs.get("idle_reads"), 8)

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_setup_timeout_stops_before_drain(self, drain: MagicMock):
        sess = self.session()
        sess.set_base_prompt.side_effect = TimeoutError("late prompt")
        with self.assertRaises(TimeoutError):
            sess.session_preparation()
        drain.assert_not_called()
        sess.write_channel.assert_not_called()
        sess.send_command_timing.assert_not_called()

    @patch("netx_api.ne_netmiko.drain_read_channel")
    def test_setup_never_sends_terminal_commands(self, drain: MagicMock):
        sess = self.session()
        sess.session_preparation()
        sess.write_channel.assert_not_called()
        sess.set_terminal_width.assert_not_called()
        sess.disable_paging.assert_not_called()
        drain.assert_called_once_with(sess, idle_reads=8, pause_sec=0.1)


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
