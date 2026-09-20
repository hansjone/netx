"""Tests for declarative enrich + slim aux (profile_id only)."""

from __future__ import annotations

import unittest

from netx_api.biz_state.collect_session import (
    CachedCommand,
    CollectSession,
    build_parse_bundle,
    resolve_aux_command,
    run_primary_with_bundle,
)
from netx_api.biz_state.enrich import EnrichJoin, apply_enrich_joins
from netx_api.biz_state.parsers import run_parser
from netx_api.biz_state.profiles import AuxCommand, get_profile, reload_profiles
from netx_api.ntc_parse import _all_index_entries


IF_INTF_SAMPLE = """\
!<if-intf>
interface cdgei-0/1/0/1.1
  ip vrf forwarding IuB_UP-evpn
$
interface cdgei-0/1/1/2.1
  ip vrf forwarding 400G-1
$
"""

ARP_MATCHING = """\
IP                       Hardware                    Exter  Inter  Sub
Address         Age      Address        Interface    VlanID VlanID Interface
--------------------------------------------------------------------------------
131.1.1.2       03:22:07 0011.2233.4455 cdgei-0/1/0/1.1 N/A  N/A    cdgei-0/1/0/1.1
11.1.1.2        H        00aa.bbcc.ddee cdgei-0/1/1/2.1 N/A  N/A    N/A
10.0.0.1        01:00:00 aabb.ccdd.eeff gei-0/0/0/1  N/A    N/A    N/A
"""


class EnrichJoinTests(unittest.TestCase):
    def test_apply_enrich(self) -> None:
        rows = [
            {"interface": "a", "ip": "1.1.1.1", "vrf": ""},
            {"interface": "b", "ip": "2.2.2.2", "vrf": ""},
        ]
        aux = {"if_intf": [{"interface": "a", "vrf": "V1"}]}
        apply_enrich_joins(
            rows,
            aux,
            [EnrichJoin(from_aux="if_intf", on="interface", take=("vrf",))],
        )
        self.assertEqual(rows[0]["vrf"], "V1")
        self.assertEqual(rows[1]["vrf"], "")


class AuxResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_profiles()

    def test_resolve_from_profile_id(self) -> None:
        ra = resolve_aux_command(AuxCommand(key="if_intf", profile_id="zte.config_interface"))
        self.assertEqual(ra.command, "show running-config if-intf | one-line")
        self.assertEqual(ra.parser_id, "config_interface")

    def test_arp_profile_slim(self) -> None:
        p = get_profile("zte.arp")
        assert p is not None
        self.assertEqual(p.aux_commands[0].key, "if_intf")
        self.assertEqual(p.aux_commands[0].profile_id, "zte.config_interface")
        self.assertEqual(len(p.enrich_joins), 1)
        self.assertEqual(p.enrich_joins[0].take, ("vrf",))
        ii = get_profile("zte.if_intf")
        assert ii is not None
        self.assertFalse(ii.enabled)


class CollectSessionCacheTests(unittest.TestCase):
    def test_cache_hit(self) -> None:
        calls: list[str] = []

        def send(_conn, cmd, read_timeout=0):
            calls.append(cmd)
            return "RAW"

        sess = CollectSession(None, send_fn=send, read_timeout=1)
        e1, hit1 = sess.fetch_and_parse("show running-config if-intf", parser_id="")
        e2, hit2 = sess.fetch_and_parse("show running-config if-intf", parser_id="")
        self.assertFalse(hit1)
        self.assertTrue(hit2)
        self.assertEqual(calls, ["show running-config if-intf"])
        self.assertEqual(e2.raw, "RAW")


class ArpEnrichPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _all_index_entries.cache_clear()
        reload_profiles()

    def test_run_primary_with_enrich(self) -> None:
        if_recs, if_fsm, _ = run_parser(
            "config_interface",
            raw_text=IF_INTF_SAMPLE,
            vendor="zte",
            device_type="zte_zxros",
            command="show running-config if-intf",
        )
        ra = resolve_aux_command(AuxCommand(key="if_intf", profile_id="zte.config_interface"))
        bundle = build_parse_bundle(
            primary_raw=ARP_MATCHING,
            primary_parser_id="arp",
            aux_results={
                "if_intf": CachedCommand(
                    raw=IF_INTF_SAMPLE, fsm_tables=if_fsm, records=if_recs, ok=True
                )
            },
            resolved_aux=[ra],
        )
        p = get_profile("zte.arp")
        assert p is not None
        records, _tables, _keys = run_primary_with_bundle(
            "arp",
            bundle=bundle,
            vendor="zte",
            device_type="zte_zxros",
            command="show arp",
            enrich_joins=list(p.enrich_joins),
        )
        by_ip = {r["ip"]: r for r in records}
        self.assertEqual(by_ip["131.1.1.2"]["vrf"], "IuB_UP-evpn")
        self.assertEqual(by_ip["11.1.1.2"]["vrf"], "400G-1")
        self.assertEqual(by_ip["10.0.0.1"]["vrf"], "")


if __name__ == "__main__":
    unittest.main()
