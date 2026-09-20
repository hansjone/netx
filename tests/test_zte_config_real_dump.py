"""Regression: config-intent parsers vs real ``test/show-zte/show-config``.

Skipped when the dump is absent. Validates counts and field fidelity against
raw MIM section lines (not synthetic fixtures).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from netx_api.biz_state.parsers.zte.config_bgp_peer import normalize_config_bgp_peer
from netx_api.biz_state.parsers.zte.config_common import extract_mim_section
from netx_api.biz_state.parsers.zte.config_interface import normalize_config_interface
from netx_api.biz_state.parsers.zte.config_isis import normalize_config_isis
from netx_api.biz_state.parsers.zte.config_l2vpn_pw import normalize_config_l2vpn_pw
from netx_api.biz_state.parsers.zte.config_ospf import normalize_config_ospf
from netx_api.biz_state.parsers.zte.config_static_route import (
    _V4_HEAD,
    _parse_v4,
    _parse_v4_rest,
    normalize_config_static_route,
)
from netx_api.biz_state.parsers.zte.config_vrf import normalize_config_vrf

_CONFIG = Path(__file__).resolve().parents[2] / "test" / "show-zte" / "show-config"


@unittest.skipUnless(_CONFIG.is_file(), "test/show-zte/show-config not present")
class ZteConfigRealDumpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = _CONFIG.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")

    def test_vrf_count_and_rd(self) -> None:
        body = extract_mim_section(self.text, "vrf")
        raw = re.findall(r"(?im)^\s*ip\s+vrf\s+(\S+)\s*$", body)
        rows = normalize_config_vrf(raw_text=self.text, command="show running-config vrf")
        self.assertEqual(len(rows), len(raw))
        by = {r["vrf_name"]: r for r in rows}
        cur = None
        expect_rd: dict[str, str] = {}
        for line in body.splitlines():
            m = re.match(r"(?i)^\s*ip\s+vrf\s+(\S+)\s*$", line)
            if m:
                cur = m.group(1)
                continue
            m = re.match(r"(?i)^\s*rd\s+(\S+)\s*$", line)
            if m and cur:
                expect_rd[cur] = m.group(1)
        for name, rd in expect_rd.items():
            self.assertEqual(by[name]["rd"], rd, msg=name)

    def test_interface_merged_fields(self) -> None:
        body = extract_mim_section(self.text, "if-intf")
        rows = normalize_config_interface(
            raw_text=self.text, command="show running-config if-intf"
        )
        by = {r["interface"]: r for r in rows}
        exp: dict[str, dict] = {}
        cur = None
        block_ips: list[str] = []
        block_ip6s: list[str] = []
        block_admin = None
        block_vrf = ""
        block_mtu = ""

        def commit(name: str) -> None:
            nonlocal block_admin, block_vrf, block_mtu, block_ips, block_ip6s
            e = exp[name]
            if block_admin is not None:
                e["admin"] = block_admin
            if block_vrf:
                e["vrf"] = block_vrf
            if block_mtu:
                e["mtu"] = block_mtu
            for ip in block_ips:
                if ip not in e["ips"]:
                    e["ips"].append(ip)
            for ip in block_ip6s:
                if ip not in e["ip6s"]:
                    e["ip6s"].append(ip)

        for line in body.splitlines():
            m = re.match(r"(?i)^\s*interface\s+(\S+)\s*$", line)
            if m:
                if cur:
                    commit(cur)
                cur = m.group(1)
                exp.setdefault(cur, {"vrf": "", "admin": "up", "ips": [], "ip6s": [], "mtu": ""})
                block_ips, block_ip6s = [], []
                block_admin, block_vrf, block_mtu = None, "", ""
                continue
            if not cur:
                continue
            if re.match(r"^\$\s*$", line):
                commit(cur)
                cur = None
                continue
            low = line.strip().lower()
            if low == "shutdown":
                block_admin = "down"
            elif low == "no shutdown":
                block_admin = "up"
            m = re.match(r"(?i)^\s*ip\s+vrf\s+forwarding\s+(\S+)\s*$", line)
            if m:
                block_vrf = m.group(1)
                continue
            m = re.match(
                r"(?i)^\s*ip\s+address\s+(\S+)(?:\s+(\S+))?(?:\s+secondary)?\s*$",
                line,
            )
            if m:
                a, mask = m.group(1), m.group(2) or ""
                if mask.lower() == "secondary":
                    mask = ""
                block_ips.append(f"{a}/{mask}" if mask else a)
                continue
            m = re.match(r"(?i)^\s*ipv6\s+address\s+(\S+)(?:\s+secondary)?\s*$", line)
            if m:
                block_ip6s.append(m.group(1))
                continue
            m = re.match(r"(?i)^\s*mtu\s+(\d+)\s*$", line)
            if m:
                block_mtu = m.group(1)
        if cur:
            commit(cur)

        self.assertEqual(len(rows), len(exp))
        for name, e in exp.items():
            g = by[name]
            self.assertEqual(g["vrf"], e["vrf"], msg=name)
            self.assertEqual(g["admin"], e["admin"], msg=name)
            self.assertEqual(
                [x for x in (g["ip_address"] or "").split(",") if x],
                e["ips"],
                msg=name,
            )

    def test_static_route_line_coverage(self) -> None:
        body = extract_mim_section(self.text, "static")
        raw = [ln for ln in body.splitlines() if re.match(r"(?i)^\s*ip\s+route\s+", ln)]
        rows = normalize_config_static_route(
            raw_text=self.text, command="show running-config static"
        )
        self.assertEqual(len(rows), len(raw))
        self.assertEqual(rows, _parse_v4(body))
        for ln in raw:
            m = _V4_HEAD.match(ln)
            self.assertIsNotNone(m, msg=ln[:120])
            assert m is not None
            extra = _parse_v4_rest(m.group("rest") or "")
            hit = any(
                r["vrf"] == (m.group("vrf") or "").strip()
                and r["prefix"] == m.group("prefix")
                and r["mask"] == m.group("mask")
                and r["next_hop"] == extra["next_hop"]
                and r["interface"] == extra["interface"]
                and r["nexthop_vrf"] == extra["nexthop_vrf"]
                for r in rows
            )
            self.assertTrue(hit, msg=ln[:140])
        bfd_raw = sum(1 for ln in raw if re.search(r"(?i)\bbfd\b", ln))
        self.assertEqual(sum(1 for r in rows if r["bfd"]), bfd_raw)

    def test_static_route_v6(self) -> None:
        body = extract_mim_section(self.text, "ipv6-static-route")
        raw = [ln for ln in body.splitlines() if re.match(r"(?i)^\s*ipv6\s+route\s+", ln)]
        rows = normalize_config_static_route(
            raw_text=self.text, command="show running-config ipv6-static-route"
        )
        self.assertEqual(len(rows), len(raw))

    def test_bgp_activate_and_no_secrets(self) -> None:
        body = extract_mim_section(self.text, "bgp")
        activates = {
            m.group(1)
            for m in re.finditer(
                r"(?im)^\s*neighbor\s+(\S+)\s+activate(?:\s+disable)?\s*$", body
            )
        }
        rows = normalize_config_bgp_peer(
            raw_text=self.text, command="show running-config bgp"
        )
        blob = " ".join(str(v) for r in rows for v in r.values()).lower()
        self.assertNotIn("password", blob)
        self.assertNotIn("cipher", blob)
        # Every activate token appears as neighbor (IP) or peer_group (name)
        covered = {r["neighbor"] for r in rows if r["neighbor"]} | {
            r["peer_group"] for r in rows if r["peer_group"]
        }
        self.assertTrue(activates <= covered)
        # neighbor column is IP-only
        for r in rows:
            if r["neighbor"]:
                self.assertTrue(
                    re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", r["neighbor"])
                    or ":" in r["neighbor"],
                    msg=r["neighbor"],
                )

    def test_l2vpn_neighbour_coverage(self) -> None:
        body = extract_mim_section(self.text, "l2vpn")
        neis = set(
            re.findall(r"(?im)^\s*neighbour\s+(\S+)\s+vcid\s+(\S+)\s*$", body)
        )
        rows = normalize_config_l2vpn_pw(
            raw_text=self.text, command="show running-config l2vpn"
        )
        parsed = {(r["peer"], r["vcid"]) for r in rows if r["peer"] and r["vcid"]}
        self.assertEqual(parsed & neis, neis)
        for r in rows:
            if r["peer"] and r["vcid"]:
                self.assertIn((r["peer"], r["vcid"]), neis)

    def test_ospf_v2_interface_coverage(self) -> None:
        body = extract_mim_section(self.text, "ospfv2")
        procs = re.findall(
            r"(?im)^\s*router\s+ospf\s+(\S+)(?:\s+vrf\s+(\S+))?\s*$", body
        )
        ifaces = re.findall(r"(?im)^\s*interface\s+(\S+)\s*$", body)
        rows = normalize_config_ospf(
            raw_text=self.text, command="show running-config ospfv2"
        )
        self.assertTrue(all(r["af"] == "ipv4" for r in rows))
        parsed_if = {r["interface"] for r in rows if r["interface"]}
        self.assertEqual(parsed_if, set(ifaces))
        self.assertEqual(len({(r["process_id"], r["vrf"]) for r in rows}), len(procs))
        blob = " ".join(str(v) for r in rows for v in r.values()).lower()
        self.assertNotIn("encrypted", blob)
        self.assertNotIn("message-digest-key", blob)

    def test_ospf_v3_and_isis(self) -> None:
        v3_body = extract_mim_section(self.text, "ospfv3")
        v3_if = re.findall(r"(?im)^\s*interface\s+(\S+)\s*$", v3_body)
        v3 = normalize_config_ospf(
            raw_text=self.text, command="show running-config ospfv3"
        )
        self.assertEqual({r["interface"] for r in v3 if r["interface"]}, set(v3_if))

        isis_body = extract_mim_section(self.text, "isis")
        isis_if = re.findall(r"(?im)^\s*interface\s+(\S+)\s*$", isis_body)
        rows = normalize_config_isis(
            raw_text=self.text, command="show running-config isis"
        )
        self.assertEqual({r["interface"] for r in rows}, set(isis_if))
        blob = " ".join(str(v) for r in rows for v in r.values()).lower()
        self.assertNotIn("encrypted", blob)


if __name__ == "__main__":
    unittest.main()
