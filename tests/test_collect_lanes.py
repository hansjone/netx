"""Light/heavy collect lane partition and dual-lane scheduling."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from netx_api.biz_state.collect_runner import (
    partition_work,
    work_item_lane,
)
from netx_api.biz_state.profiles import get_profile, reload_profiles


class CollectLaneProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_profiles()

    def test_interface_detail_is_heavy(self) -> None:
        p = get_profile("zte.interface_detail")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.collect_lane, "heavy")

    def test_route_profiles_are_heavy(self) -> None:
        for pid in (
            "zte.ip_route_vrf",
            "zte.ip_route",
            "zte.ipv6_route_vrf",
            "zte.ipv6_route",
        ):
            p = get_profile(pid)
            self.assertIsNotNone(p, pid)
            assert p is not None
            self.assertEqual(p.collect_lane, "heavy", pid)

    def test_arp_is_light(self) -> None:
        p = get_profile("zte.arp")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.collect_lane, "light")


class PartitionWorkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        reload_profiles()

    def test_custom_raw_always_light(self) -> None:
        self.assertEqual(work_item_lane("", "custom"), "light")
        self.assertEqual(work_item_lane("zte.interface_detail", "custom"), "light")

    def test_partition_mixed(self) -> None:
        work = [
            ("show arp", {}, "zte.arp", "i1", "normal"),
            (
                "show interface | include ifindex",
                {},
                "zte.interface_detail",
                "i2",
                "normal",
            ),
            ("show ip forwarding route vrf a", {}, "zte.ip_route_vrf", "i3", "normal"),
            ("show version", {}, "", "i4", "custom"),
        ]
        light, heavy = partition_work(work)
        self.assertEqual(len(light), 2)
        self.assertEqual({x[2] for x in light}, {"zte.arp", ""})
        self.assertEqual(len(heavy), 2)
        self.assertEqual(
            {x[2] for x in heavy},
            {"zte.interface_detail", "zte.ip_route_vrf"},
        )

    def test_partition_light_only(self) -> None:
        work = [("show arp", {}, "zte.arp", "i1", "normal")]
        light, heavy = partition_work(work)
        self.assertEqual(len(light), 1)
        self.assertEqual(heavy, [])

    def test_partition_heavy_only(self) -> None:
        work = [
            (
                "show interface | include ifindex",
                {},
                "zte.interface_detail",
                "i1",
                "normal",
            )
        ]
        light, heavy = partition_work(work)
        self.assertEqual(light, [])
        self.assertEqual(len(heavy), 1)


class DualLaneParallelTests(unittest.TestCase):
    def test_light_not_blocked_by_heavy_sleep(self) -> None:
        """When both lanes run, light completes without waiting for heavy."""
        from netx_api.biz_state import collect_runner as cr

        order: list[str] = []

        def fake_lane(*, work, label, **_kwargs):
            if "heavy" in label:
                time.sleep(0.35)
                order.append("heavy_done")
                return (0, len(work), False, True)
            order.append("light_done")
            return (0, len(work), False, True)

        light_work = [("show arp", {}, "zte.arp", "i1", "normal")]
        heavy_work = [
            (
                "show interface | include ifindex",
                {},
                "zte.interface_detail",
                "i2",
                "normal",
            )
        ]

        with patch.object(cr, "_run_collect_lane", side_effect=fake_lane):
            t0 = time.perf_counter()
            heavy_fut = cr._heavy_cli_pool().submit(
                lambda: cr._run_collect_lane(
                    work=heavy_work,
                    batch_id="b",
                    creds={},
                    vendor_eff="zte",
                    device_type_eff="",
                    vendor_key="zte",
                    per_cmd=1,
                    cap=10,
                    label="biz_state_heavy",
                )
            )
            light_res = cr._run_collect_lane(
                work=light_work,
                batch_id="b",
                creds={},
                vendor_eff="zte",
                device_type_eff="",
                vendor_key="zte",
                per_cmd=1,
                cap=10,
                label="biz_state_light",
            )
            light_elapsed = time.perf_counter() - t0
            heavy_res = heavy_fut.result()
            total_elapsed = time.perf_counter() - t0

        self.assertEqual(light_res[1], 1)
        self.assertEqual(heavy_res[1], 1)
        self.assertEqual(order[0], "light_done")
        self.assertLess(light_elapsed, 0.25)
        self.assertGreaterEqual(total_elapsed, 0.3)


if __name__ == "__main__":
    unittest.main()
