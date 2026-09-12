"""Unit tests for reverse-proxy client IP resolution."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from netx_api.client_ip import resolve_client_ip_from


class ClientIpTests(unittest.TestCase):
    def test_direct_peer_when_not_trusted(self) -> None:
        with patch("netx_api.client_ip.settings.trusted_proxy_ips", "127.0.0.1"):
            ip = resolve_client_ip_from(
                "203.0.113.9",
                {"x-forwarded-for": "198.51.100.1", "x-real-ip": "198.51.100.1"},
            )
            self.assertEqual(ip, "203.0.113.9")

    def test_xff_when_peer_is_loopback(self) -> None:
        with patch("netx_api.client_ip.settings.trusted_proxy_ips", "127.0.0.1,::1"):
            ip = resolve_client_ip_from(
                "127.0.0.1",
                {"x-forwarded-for": "198.51.100.44, 10.0.0.1"},
            )
            self.assertEqual(ip, "198.51.100.44")

    def test_x_real_ip_preferred(self) -> None:
        with patch("netx_api.client_ip.settings.trusted_proxy_ips", "127.0.0.1"):
            ip = resolve_client_ip_from(
                "127.0.0.1",
                {"x-real-ip": "203.0.113.50", "x-forwarded-for": "198.51.100.1"},
            )
            self.assertEqual(ip, "203.0.113.50")

    def test_cf_connecting_ip(self) -> None:
        with patch("netx_api.client_ip.settings.trusted_proxy_ips", "127.0.0.1"):
            ip = resolve_client_ip_from(
                "127.0.0.1",
                {"cf-connecting-ip": "203.0.113.77", "x-forwarded-for": "198.51.100.1"},
            )
            self.assertEqual(ip, "203.0.113.77")


if __name__ == "__main__":
    unittest.main()
