"""Tests for vendor paging-disable helpers used by LLDP / collect / hop."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from netx_api.ne_netmiko import disable_target_paging, paging_disable_commands


class PagingDisableTests(unittest.TestCase):
    def test_commands_by_vendor(self) -> None:
        self.assertEqual(
            paging_disable_commands(vendor="Huawei", device_type="huawei"),
            ["screen-length 0 temporary"],
        )
        self.assertEqual(
            paging_disable_commands(vendor="ZTE", device_type="zte_zxros"),
            ["terminal length 0"],
        )
        self.assertEqual(
            paging_disable_commands(vendor="Cisco", device_type="cisco_ios"),
            ["terminal length 0"],
        )
        self.assertEqual(
            paging_disable_commands(vendor="H3C", device_type="hp_comware"),
            ["screen-length disable"],
        )

    def test_disable_target_paging_uses_timing(self) -> None:
        conn = MagicMock()
        conn.send_command_timing.return_value = "ok\n<HUAWEI>"
        out = disable_target_paging(conn, vendor="Huawei", device_type="huawei")
        conn.send_command_timing.assert_called_once_with(
            "screen-length 0 temporary", read_timeout=15
        )
        self.assertIn("ok", out)


if __name__ == "__main__":
    unittest.main()
