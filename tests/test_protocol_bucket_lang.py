import unittest

from netx_api.main import _classify_protocol_bucket, _protocol_bucket_label


class ProtocolBucketLangTests(unittest.TestCase):
    def test_english_labels(self) -> None:
        blob = "BGP VPN OTN CLOCK POWER misc"
        self.assertEqual(_protocol_bucket_label(blob, lang="en"), "IP/MPLS")
        self.assertEqual(_classify_protocol_bucket("OTN ODU"), "OTN/Optical")
        self.assertEqual(_protocol_bucket_label("OTN ODU", lang="en"), "OTN/Optical")
        self.assertEqual(_protocol_bucket_label("OTN ODU", lang="zh"), "OTN/光")
        self.assertEqual(_protocol_bucket_label("misc", lang="en"), "Other")
        self.assertEqual(_protocol_bucket_label("misc", lang="zh"), "其他")


if __name__ == "__main__":
    unittest.main()
