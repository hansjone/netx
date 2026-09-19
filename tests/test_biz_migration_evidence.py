"""Integration: evaluate persists evidence cards (raw A/B + command) on diffs/reds."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_migration import service as mig
from netx_api.biz_state import service as biz_svc
from netx_api.db import Base
from netx_api.models import (
    BizCompareTemplate,
    BizMigrationDiff,
    BizMigrationRedTicket,
    BizMonitorTemplate,
    BizPortMapping,
    BizPortMappingRow,
    BizStateBatch,
    BizStateBatchCommand,
    BizStateMetricRow,
    BizStateTask,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stub_catalog(*_a, **_k):
    return {
        "source_profile_id": "zte.interface_brief",
        "kind": "catalog",
        "enabled": True,
        "title": "IF",
    }


def _stub_ne_meta(*_a, **_k):
    return {
        "ne_name": "ne",
        "ne_ip": "10.0.0.1",
        "vendor": "zte",
        "device_type": "router",
    }


PORT_SHEET = {
    "sheet_id": "interface_brief",
    "title": "ports",
    "metric_id": "interface_brief",
    "key_fields": ["interface"],
    "iface_fields": ["interface"],
    "compare_fields": ["admin", "phy", "prot"],
    "row_filters": [],
    "field_rules": [],
}

PORT_OVERRIDES = [
    {
        "metric_id": "interface_brief",
        "sheet_id": "interface_brief",
        "status_fields": ["admin", "phy", "prot"],
        "down_values": ["down"],
        "up_values": ["up"],
        "success": [{"old": ["removed", "down"], "new": ["added", "up", "unchanged"]}],
        "anomaly": [],
    }
]

NORM_RULES = [{"from": "GE", "to": "gei"}]


class EvidencePersistTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        TestingSession = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
        )
        Base.metadata.create_all(bind=engine)
        self.db = TestingSession()

        self.ct = BizCompareTemplate(
            id="ct1",
            name="port-cmp",
            metric_id="interface_brief",
            key_fields=["interface"],
            iface_fields=["interface"],
            compare_fields=["admin", "phy", "prot"],
            metrics_json=[PORT_SHEET],
            iface_normalize_json=NORM_RULES,
        )
        self.db.add(self.ct)
        self.mt = BizMonitorTemplate(
            id="mt1",
            name="port-mon",
            compare_template_id="ct1",
            collect_metric_ids_json=["interface_brief"],
            defaults_json={"out_of_expect": "strict"},
            sheet_overrides_json=PORT_OVERRIDES,
        )
        self.db.add(self.mt)

        self.mapping = BizPortMapping(id="map1", name="ports", note="")
        self.db.add(self.mapping)
        self.db.flush()
        self.db.add(
            BizPortMappingRow(
                id="mr1", mapping_id="map1", before_if="gei-old", after_if="xgei-new"
            )
        )

        self.old_p = self._mk_task("old_p", ne_id="ne-old", ne_name="OLD-RTR")
        self.new_p = self._mk_task("new_p", ne_id="ne-new", ne_name="NEW-RTR")
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def _mk_task(self, tid: str, *, ne_id: str, ne_name: str) -> BizStateTask:
        t = BizStateTask(
            id=tid,
            source="managed",
            ne_id=ne_id,
            ne_name=ne_name,
            ne_ip="10.0.0.1",
            vendor="zte",
            device_type="router",
            purpose="",
            status="running",
            interval_sec=3600,
            retention_days=30,
        )
        self.db.add(t)
        return t

    def _mk_batch(self, bid: str, task_id: str) -> BizStateBatch:
        now = _utcnow()
        b = BizStateBatch(
            id=bid,
            task_id=task_id,
            status="success",
            started_at=now,
            ended_at=now,
            row_count=1,
            command_count=1,
        )
        self.db.add(b)
        self.db.commit()
        return b

    def _seed_if_row(
        self,
        *,
        batch_id: str,
        task_id: str,
        ne_id: str,
        command_id: str,
        raw_command: str,
        interface: str,
        admin: str = "up",
        phy: str = "up",
        prot: str = "up",
    ) -> None:
        now = _utcnow()
        self.db.add(
            BizStateBatchCommand(
                id=command_id,
                batch_id=batch_id,
                profile_id="zte.interface_brief",
                parser_id="zte.interface_brief",
                metric_id="interface_brief",
                raw_command=raw_command,
                parse_status="ok",
                row_count=1,
                raw_text=f"Interface {interface}  {admin}  {phy}  {prot}\n",
                created_at=now,
            )
        )
        self.db.add(
            BizStateMetricRow(
                id=f"row-{command_id}",
                batch_id=batch_id,
                batch_command_id=command_id,
                task_id=task_id,
                ne_id=ne_id,
                metric_id="interface_brief",
                seq=0,
                data_json={
                    "interface": interface,
                    "admin": admin,
                    "phy": phy,
                    "prot": prot,
                },
                collected_at=now,
            )
        )
        self.db.commit()

    def _create_project(self) -> dict:
        body = {
            "name": "evidence-night",
            "old_task_id": self.old_p.id,
            "new_task_id": self.new_p.id,
            "monitor_template_id": self.mt.id,
            "mapping_id": self.mapping.id,
            "collect_now": False,
        }
        with mock.patch("netx_api.biz_state.collect_runner.dispatch_collect"), mock.patch.object(
            mig, "_catalog_item_for_metric", side_effect=_stub_catalog
        ), mock.patch.object(biz_svc, "_ne_meta", side_effect=_stub_ne_meta):
            return mig.create_project(self.db, body)

    def test_evaluate_persists_display_ab_and_evidence_with_command(self) -> None:
        proj = self._create_project()
        old_hf = proj["old_hf_task_id"]
        new_hf = proj["new_hf_task_id"]

        self._mk_batch("bl_old", self.old_p.id)
        self._mk_batch("bl_new", self.new_p.id)
        self._mk_batch("cur_old", old_hf)
        self._mk_batch("cur_new", new_hf)

        # Baseline old: raw GE-old (normalize → gei-old)
        self._seed_if_row(
            batch_id="bl_old",
            task_id=self.old_p.id,
            ne_id="ne-old",
            command_id="cmd-bl-old",
            raw_command="show interface brief",
            interface="GE-old",
        )
        # New baseline empty → port_mapped mode uses mapped old
        # Old current: gone (no rows) — migration off old
        # New current: xgei-new up
        self._seed_if_row(
            batch_id="cur_new",
            task_id=new_hf,
            ne_id="ne-new",
            command_id="cmd-cur-new",
            raw_command="show interface brief",
            interface="xgei-new",
        )

        mig.patch_project(
            self.db,
            proj["id"],
            {"old_baseline_batch_id": "bl_old", "new_baseline_batch_id": "bl_new"},
        )
        batch = mig.create_batch(
            self.db,
            proj["id"],
            {"batch_label": "wave1", "expect_set": {"ports": ["gei-old"]}},
        )
        # Activate window so dual verdict uses migrating/migrated path
        mig.patch_batch(self.db, batch["id"], {"status": "active"})

        out = mig.run_evaluate(self.db, batch_id=batch["id"])
        run_id = out["id"]
        self.assertTrue(run_id)

        diffs = (
            self.db.query(BizMigrationDiff)
            .filter(BizMigrationDiff.run_id == run_id)
            .order_by(BizMigrationDiff.seq.asc())
            .all()
        )
        self.assertTrue(diffs, "expected persisted diffs")
        d = diffs[0]
        kj = d.key_json if isinstance(d.key_json, dict) else {}
        self.assertEqual(kj.get("old_key"), "GE-old")
        self.assertEqual(kj.get("new_key"), "xgei-new")
        self.assertEqual(kj.get("key_str"), "GE-old")
        self.assertEqual(kj.get("new_key_str"), "xgei-new")
        self.assertEqual(kj.get("match_old_key"), "gei-old")
        self.assertEqual(kj.get("match_new_key"), "xgei-new")
        # Display rows stay raw A/B, not mapped BB
        self.assertEqual((d.old_json or {}).get("interface"), "GE-old")
        self.assertEqual((d.new_json or {}).get("interface"), "xgei-new")

        ev = kj.get("evidence") or {}
        pm = ev.get("port_map") or {}
        self.assertTrue(pm.get("applied"))
        self.assertEqual(pm.get("display_before"), "GE-old")
        self.assertEqual(pm.get("display_after"), "xgei-new")
        self.assertEqual(pm.get("match_before"), "gei-old")
        self.assertEqual(pm.get("match_after"), "xgei-new")

        old_iface = (ev.get("old") or {}).get("iface") or []
        self.assertTrue(old_iface)
        self.assertEqual(old_iface[0].get("raw"), "GE-old")
        self.assertEqual(old_iface[0].get("normalized"), "gei-old")
        self.assertTrue(old_iface[0].get("mapped"))
        self.assertEqual(old_iface[0].get("map_to"), "xgei-new")

        # Enrichment: device + show command on new side (current row)
        new_ev = ev.get("new") or {}
        self.assertEqual((new_ev.get("command") or {}).get("raw_command"), "show interface brief")
        self.assertEqual((new_ev.get("command") or {}).get("parse_status"), "ok")
        self.assertEqual((new_ev.get("device") or {}).get("ne_name"), "NEW-RTR")
        self.assertEqual((new_ev.get("collect") or {}).get("batch_id"), "cur_new")

        old_ev = ev.get("old") or {}
        self.assertEqual((old_ev.get("command") or {}).get("raw_command"), "show interface brief")
        self.assertEqual((old_ev.get("collect") or {}).get("batch_id"), "bl_old")

        # API serialize path
        api = mig.diff_to_dict(d)
        self.assertEqual(api["old_key"], "GE-old")
        self.assertEqual(api["new_key"], "xgei-new")
        self.assertIn("evidence", api)
        self.assertEqual(
            api["evidence"]["new"]["command"]["raw_command"], "show interface brief"
        )

        listed = mig.list_run_diffs(self.db, run_id, color="", limit=50)
        self.assertGreaterEqual(listed["total"], 1)
        item = listed["items"][0]
        self.assertEqual(item["old_key"], "GE-old")
        self.assertEqual(item["new_key"], "xgei-new")
        self.assertEqual(
            item["evidence"]["old"]["iface"][0]["raw"], "GE-old"
        )
    def test_finish_batch_red_tickets_carry_evidence(self) -> None:
        """Acceptance red path: anomaly persists evidence onto red tickets."""
        proj = self._create_project()
        old_hf = proj["old_hf_task_id"]
        new_hf = proj["new_hf_task_id"]

        self._mk_batch("bl_old", self.old_p.id)
        self._mk_batch("bl_new", self.new_p.id)
        self._mk_batch("cur_old", old_hf)
        self._mk_batch("cur_new", new_hf)

        # Expect port still up on old (not migrated) + missing/down on new → red unfinished/anomaly
        self._seed_if_row(
            batch_id="bl_old",
            task_id=self.old_p.id,
            ne_id="ne-old",
            command_id="cmd-bl-old2",
            raw_command="show interface brief",
            interface="GE-old",
        )
        self._seed_if_row(
            batch_id="cur_old",
            task_id=old_hf,
            ne_id="ne-old",
            command_id="cmd-cur-old2",
            raw_command="show interface brief",
            interface="GE-old",
            admin="up",
            phy="up",
            prot="up",
        )
        # new side: no matching port → unfinished on acceptance

        mig.patch_project(
            self.db,
            proj["id"],
            {"old_baseline_batch_id": "bl_old", "new_baseline_batch_id": "bl_new"},
        )
        batch = mig.create_batch(
            self.db,
            proj["id"],
            {"batch_label": "wave-red", "expect_set": {"ports": ["gei-old"]}},
        )
        mig.patch_batch(self.db, batch["id"], {"status": "active"})

        finished = mig.finish_batch(self.db, batch["id"], mark_done=False)
        self.assertIn("red_tickets", finished)
        tickets = (
            self.db.query(BizMigrationRedTicket)
            .filter(BizMigrationRedTicket.project_id == proj["id"])
            .all()
        )
        self.assertTrue(tickets, f"expected red tickets, finish={finished}")
        t = tickets[0]
        detail = t.detail_json if isinstance(t.detail_json, dict) else {}
        self.assertIn("evidence", detail)
        self.assertEqual(detail.get("old_key") or t.key_str, "GE-old")
        # API
        listed = mig.list_red_tickets(self.db, proj["id"])
        self.assertGreaterEqual(listed["open_count"], 1)
        item = listed["items"][0]
        self.assertEqual(item.get("old_key") or item.get("key_str"), "GE-old")
        self.assertIn("evidence", item)


if __name__ == "__main__":
    unittest.main()
