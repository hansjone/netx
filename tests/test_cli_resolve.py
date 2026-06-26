from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from netx_api.cli_resolve import infer_device_type_vendor
from netx_api.models import CliConnectProfile, UmeInventoryNE


class CliResolveTests(unittest.TestCase):
    def test_infer_zte_ne_type(self) -> None:
        profile = CliConnectProfile(device_type_default="zte_zxros", vendor_default="ZTE")
        dt, vendor = infer_device_type_vendor("ZXCTN 6180H", profile)
        self.assertEqual(dt, "zte_zxros")
        self.assertEqual(vendor, "ZTE")

    def test_resolve_ume_target(self) -> None:
        from netx_api.cli_resolve import resolve_cli_target

        db = MagicMock()
        inv = UmeInventoryNE(ne_id="ume-1", ip_address="10.0.0.1", ne_type="ZXCTN", user_label="NE-A")
        profile = CliConnectProfile(
            id="p1",
            name="default",
            is_default=True,
            username="ca-oper",
            password_enc="",
            device_type_default="zte_zxros",
            vendor_default="ZTE",
            hop_enabled=True,
            hop_vendor="bastion",
            hop_host="10.34.145.27",
            hop_username="ZTE-FIVIE",
            hop_password_enc="enc",
            hop_command_template="{hop_user}@{target_user}@{target_ip}",
        )
        db.get.side_effect = lambda model, key: {
            (UmeInventoryNE, "ume-1"): inv,
            (type(None), "ume-1"): None,
        }.get((model, key))
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = profile

        with unittest.mock.patch("netx_api.cli_resolve.decrypt_secret", return_value="vault-pass"):
            creds, device = resolve_cli_target(db, ume_ne_id="ume-1")

        self.assertEqual(creds["ip_address"], "10.0.0.1")
        self.assertEqual(creds["username"], "ca-oper")
        self.assertEqual(creds["hop_host"], "10.34.145.27")
        self.assertEqual(device["source"], "ume")
        self.assertEqual(device["ume_ne_id"], "ume-1")


if __name__ == "__main__":
    unittest.main()
