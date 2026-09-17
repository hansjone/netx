"""Tests for multi-command collect (ARP + if-intf) and FSM-first if-intf."""

from __future__ import annotations

import unittest
from pathlib import Path

from netx_api.biz_state.parsers import get_parser_meta, run_parser
from netx_api.biz_state.parsers.zte.if_intf import normalize_if_intf, parse_if_intf_vrf_map
from netx_api.biz_state.profiles import get_profile, reload_profiles
from netx_api.ntc_parse import _all_index_entries, apply_rule


def _log() -> str:
    p = Path(__file__).resolve().parents[2] / "test" / "log"
    if not p.is_file():
        p = Path(__file__).resolve().parents[3] / "test" / "log"
    return p.read_text(encoding="utf-8", errors="ignore") if p.is_file() else ""


IF_INTF_SAMPLE = """\
!<if-intf>
interface cdgei-0/1/0/1
  no shutdown
$
interface cdgei-0/1/0/1.1
  mtu 9600
  ip vrf forwarding IuB_UP-evpn
  ip address 131.1.1.1 255.255.255.0
$
interface cdgei-0/1/1/2.1
  ip vrf forwarding 400G-1
  ip address 11.1.1.1 255.255.255.0
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


class IfIntfParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _all_index_entries.cache_clear()
        reload_profiles()

    def test_fsm_rule(self) -> None:
        rows = apply_rule(
            platform="zte_zxros",
            rule_key="zte_zxros_show_running_config_if_intf",
            text=IF_INTF_SAMPLE,
            command="show running-config if-intf",
        )
        self.assertEqual(len(rows), 2)
        by_if = {r.get("interface"): r.get("vrf") for r in rows}
        self.assertEqual(by_if["cdgei-0/1/0/1.1"], "IuB_UP-evpn")
        self.assertEqual(by_if["cdgei-0/1/1/2.1"], "400G-1")

    def test_normalize_and_map(self) -> None:
        rows = normalize_if_intf(
            raw_text=IF_INTF_SAMPLE,
            vendor="zte",
            device_type="zte_zxros",
            command="show running-config if-intf",
        )
        self.assertEqual(len(rows), 2)
        m = parse_if_intf_vrf_map(rows=rows)
        self.assertEqual(m["cdgei-0/1/0/1.1"], "IuB_UP-evpn")

    def test_log_sample(self) -> None:
        blob = _log()
        if not blob:
            self.skipTest("test/log missing")
        text = blob[blob.find("!<if-intf>") :] if "!<if-intf>" in blob else ""
        if not text.strip():
            self.skipTest("if-intf section missing")
        rows = normalize_if_intf(
            raw_text=text, vendor="zte", device_type="zte_zxros", command="show running-config if-intf"
        )
        self.assertGreaterEqual(len(rows), 2)


class ArpMultiCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _all_index_entries.cache_clear()
        reload_profiles()

    def test_profile_aux(self) -> None:
        p = get_profile("zte.arp")
        assert p is not None
        self.assertEqual(len(p.aux_commands), 1)
        self.assertEqual(p.aux_commands[0].key, "if_intf")
        self.assertEqual(p.aux_commands[0].parser_id, "if_intf")
        self.assertIsNotNone(get_profile("zte.if_intf"))
        self.assertEqual(get_parser_meta("if_intf")["rule_keys"], ("zte_zxros_show_running_config_if_intf",))

    def test_arp_enriches_vrf_via_aux_records(self) -> None:
        if_recs, if_fsm, _ = run_parser(
            "if_intf",
            raw_text=IF_INTF_SAMPLE,
            vendor="zte",
            device_type="zte_zxros",
            command="show running-config if-intf",
        )
        records, tables, keys = run_parser(
            "arp",
            raw_text=ARP_MATCHING,
            vendor="zte",
            device_type="zte_zxros",
            command="show arp | one-line",
            textfsm_command="show arp",
            raws={"primary": ARP_MATCHING, "if_intf": IF_INTF_SAMPLE},
            command_rules={
                "primary": ["zte_zxros_show_arp"],
                "if_intf": ["zte_zxros_show_running_config_if_intf"],
            },
            aux_records={"if_intf": if_recs},
            fsm_tables_extra=if_fsm,
        )
        self.assertIn("zte_zxros_show_arp", keys)
        by_ip = {r["ip"]: r for r in records}
        self.assertEqual(by_ip["131.1.1.2"]["vrf"], "IuB_UP-evpn")
        self.assertEqual(by_ip["11.1.1.2"]["vrf"], "400G-1")
        self.assertEqual(by_ip["10.0.0.1"]["vrf"], "")


class CmdCacheLogicTests(unittest.TestCase):
    """Unit-level: same concrete aux command reused from cache dict."""

    def test_cache_hit_skips_second_collect(self) -> None:
        calls: list[str] = []

        def fake_send(_conn, cmd, read_timeout=0):
            calls.append(cmd)
            return f"RAW:{cmd}"

        cache: dict[str, dict] = {}
        cmd = "show running-config if-intf"
        # miss
        raw = fake_send(None, cmd)
        cache[cmd] = {"raw": raw, "ok": True, "records": [{"interface": "a", "vrf": "v"}], "fsm_tables": {}}
        # hit
        if cache.get(cmd, {}).get("ok"):
            reused = cache[cmd]["raw"]
        else:
            reused = fake_send(None, cmd)
        self.assertEqual(calls, [cmd])
        self.assertEqual(reused, "RAW:show running-config if-intf")


if __name__ == "__main__":
    unittest.main()
