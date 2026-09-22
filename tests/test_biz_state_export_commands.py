"""Plan/export collect commands for a biz-state task (no device)."""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_state.service import export_task_commands_text, plan_task_collect_commands
from netx_api.db import Base
from netx_api.models import BizStateTask, BizStateTaskItem, BizStateTaskItemBinding


class BizStateExportCommandsTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.db = TestingSession()
        self.task = BizStateTask(
            id="t_export",
            source="managed",
            ne_id="ne1",
            ne_name="PE1",
            ne_ip="10.0.0.1",
            vendor="zte",
            device_type="zte_zxros",
            status="running",
            interval_sec=300,
        )
        self.db.add(self.task)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_plan_and_export_lldp_and_bound_vrf(self) -> None:
        lldp = BizStateTaskItem(
            id="i_lldp",
            task_id=self.task.id,
            source_profile_id="zte.lldp_neighbors",
            kind="catalog",
            enabled=True,
            title="LLDP",
            sort_order=1,
        )
        vrf = BizStateTaskItem(
            id="i_vrf",
            task_id=self.task.id,
            source_profile_id="zte.bgp_vpnv4_vrf_summary",
            kind="catalog",
            enabled=True,
            title="BGP VRF",
            sort_order=2,
        )
        disabled = BizStateTaskItem(
            id="i_off",
            task_id=self.task.id,
            source_profile_id="zte.isis_neighbors",
            kind="catalog",
            enabled=False,
            title="ISIS off",
            sort_order=3,
        )
        self.db.add_all([lldp, vrf, disabled])
        self.db.add(
            BizStateTaskItemBinding(
                id="b1",
                item_id=vrf.id,
                placeholder="vrf",
                value="CUST_A",
            )
        )
        self.db.commit()

        plan = plan_task_collect_commands(self.db, self.task.id)
        self.assertEqual(plan["ne_name"], "PE1")
        self.assertGreaterEqual(plan["command_count"], 2)
        cmds = plan["commands"]
        self.assertTrue(any("lldp" in c.lower() for c in cmds))
        self.assertTrue(any("CUST_A" in c and "summary" in c for c in cmds))
        # Default export includes discover-backed aux (config_vrf), not FIB
        self.assertTrue(any("running-config vrf" in c for c in cmds))
        self.assertFalse(any("forwarding route" in c for c in cmds))
        # Disabled item excluded by default
        self.assertFalse(any("isis" in c.lower() for c in cmds))

        primary_only = plan_task_collect_commands(
            self.db, self.task.id, include_aux=False
        )
        self.assertFalse(any("running-config vrf" in c for c in primary_only["commands"]))

        text = export_task_commands_text(self.db, self.task.id)
        self.assertIn("task_id=t_export", text)
        self.assertIn("show lldp neighbor brief", text)
        self.assertIn("CUST_A", text)
        self.assertIn("# aux:", text)
        self.assertIn("running-config vrf", text)
        self.assertIn("# ---- flat unique commands ----", text)

    def test_shared_aux_repeated_per_item_deduped_in_flat(self) -> None:
        """Each item shows its aux; flat summary dedupes shared CLIs."""
        in_item = BizStateTaskItem(
            id="i_in",
            task_id=self.task.id,
            source_profile_id="zte.bgp_vpnv4_neighbor_in",
            kind="catalog",
            enabled=True,
            title="Neighbor In",
            sort_order=1,
        )
        out_item = BizStateTaskItem(
            id="i_out",
            task_id=self.task.id,
            source_profile_id="zte.bgp_vpnv4_neighbor_out",
            kind="catalog",
            enabled=True,
            title="Neighbor Out",
            sort_order=2,
        )
        self.db.add_all([in_item, out_item])
        self.db.add_all(
            [
                BizStateTaskItemBinding(
                    id="b_in",
                    item_id=in_item.id,
                    placeholder="neighbor",
                    value="10.0.0.1",
                ),
                BizStateTaskItemBinding(
                    id="b_out",
                    item_id=out_item.id,
                    placeholder="neighbor",
                    value="10.0.0.1",
                ),
            ]
        )
        self.db.commit()

        plan = plan_task_collect_commands(self.db, self.task.id)
        aux_cli = "show running-config bgp | one-line"
        # Both item sections must list the aux
        for sec in plan["items"]:
            aux = [c for c in sec["commands"] if c.get("role") == "aux"]
            self.assertEqual(len(aux), 1, sec["title"])
            self.assertEqual(aux[0]["command"], aux_cli)

        # Flat list has the shared aux only once
        self.assertEqual(sum(1 for c in plan["commands"] if c == aux_cli), 1)

        text = export_task_commands_text(self.db, self.task.id)
        # Two section aux markers + one flat occurrence
        self.assertEqual(text.count("# aux:"), 2)
        flat = text.split("# ---- flat unique commands ----", 1)[-1]
        self.assertEqual(flat.count(aux_cli), 1)

    def test_unbound_required_placeholder_noted(self) -> None:
        item = BizStateTaskItem(
            id="i_exp",
            task_id=self.task.id,
            source_profile_id="zte.bgp_vpnv4_vrf_summary",
            kind="catalog",
            enabled=True,
            title="BGP unbound",
            sort_order=1,
        )
        self.db.add(item)
        self.db.commit()

        plan = plan_task_collect_commands(self.db, self.task.id)
        sec = plan["items"][0]
        notes = " ".join(sec.get("notes") or [])
        self.assertIn("requires parameter bindings", notes)
        # Primary is template-only; aux (config_vrf) still counts as executable
        self.assertEqual(plan["command_count"], 1)
        self.assertTrue(any("running-config vrf" in c for c in plan["commands"]))
        tmpl_cmds = [c for c in sec.get("commands") or [] if c.get("role") == "template"]
        self.assertEqual(len(tmpl_cmds), 1)
        self.assertIn("<vrf>", tmpl_cmds[0]["command"])
        aux_cmds = [c for c in sec.get("commands") or [] if c.get("role") == "aux"]
        self.assertEqual(len(aux_cmds), 1)

        text = export_task_commands_text(self.db, self.task.id)
        self.assertIn("# show bgp vpnv4 unicast vrf <vrf> summary", text)
        self.assertIn("# aux:", text)
        self.assertIn("running-config vrf", text)
        # Template must not appear in the flat executable list
        flat = text.split("# ---- flat unique commands ----", 1)[-1]
        self.assertNotIn("<vrf>", flat)
        self.assertIn("running-config vrf", flat)


if __name__ == "__main__":
    unittest.main()
