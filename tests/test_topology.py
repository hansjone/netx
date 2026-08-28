"""Unit tests for fabric topology + LLDP parsers (no CDP discovery)."""

from __future__ import annotations

import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api import topology_lldp as lldp
from netx_api import topology_service as svc
from netx_api.db import Base
from netx_api.device_types import LLDP_DISCOVERED_NE_SOURCE, TOPOLOGY_NE_SOURCE, WEBCRT_NE_SOURCE
from netx_api.models import (
    ManagedNE,
    TopoDiscoverJob,
    TopoDiscoverJobItem,
    TopoFabricEdge,
    TopoFabricNode,
    TopoFolder,
    TopoView,
    TopoViewEdgeStyle,
    TopoViewNode,
)
from netx_api.topology_schemas import (
    FabricDiscoverRequest,
    TopologyFolderCreate,
    TopologyPlaceholderCreate,
    TopologyViewCreate,
    TopologyViewUpdate,
    ViewNodesAdd,
    ViewPopulateRequest,
    ViewPositionsPatch,
    ViewProjectNeighborsRequest,
    ViewNodeIn,
)


CISCO_LLDP_BRIEF = """
R2#show lldp neighbors
Capability codes:
    (R) Router, (B) Bridge, (T) Telephone, (C) DOCSIS Cable Device
    (W) WLAN Access Point, (P) Repeater, (S) Station, (O) Other

Device ID           Local Intf     Hold-time  Capability      Port ID
r1                  Gi0/1          120        B,R             Ethernet1/0/1

Total entries displayed: 1
"""

CISCO_LLDP_DETAIL = """
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 707b.5c6e.d130
Port id: Ethernet1/0/1
System Name: r1
Management Addresses:
    IP: 192.168.0.1
"""

HUAWEI_LLDP = """
GigabitEthernet0/0/1 has 1 neighbor(s):

Neighbor index : 1
Chassis ID     : 00e0-fc12-3456
Port ID        : GigabitEthernet0/0/2
System name    : r1
Management address : 192.168.0.127
Local Interface: GigabitEthernet0/0/1
"""

ZTE_LLDP_BRIEF = """
Local Interface      Chassis ID         Port ID              System Name
gei-0/1/0/1          0011.2233.4455     gei-0/1/0/2          R1
"""

ZTE_LLDP_SCOPE = """
Total neighbors: 12
Local Interface   Scope  Chassis ID      Port ID           Holdtime  System Name
-----------------------------------------------------------------------------------------
xgei-1/1/0/1     NB     026e.8219.bc57  xgei-1/1/0/26     100       CSR1_6120HSC
xgei-1/1/0/3     NB     00d0.0000.081f  gei-1/2/0/3       113       OLT/CPE_6180H
cgei-1/1/0/34    NB     0022.9354.6e60  cgei-0/3/0/34     110       AG5
"""


class LldpParserTests(unittest.TestCase):
    def test_cisco_brief(self) -> None:
        hits = lldp.parse_cisco_lldp(CISCO_LLDP_BRIEF)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name.lower(), "r1")
        self.assertEqual(hits[0].local_port, "Gi0/1")
        self.assertEqual(hits[0].remote_port, "Ethernet1/0/1")

    def test_cisco_detail(self) -> None:
        hits = lldp.parse_cisco_lldp(CISCO_LLDP_DETAIL)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name.lower(), "r1")
        self.assertEqual(hits[0].remote_ip, "192.168.0.1")

    def test_no_vendor_skips_discover(self) -> None:
        self.assertFalse(lldp.can_discover_lldp(vendor="", device_type=""))
        self.assertFalse(lldp.can_discover_lldp(vendor="", device_type="generic"))
        self.assertTrue(lldp.can_discover_lldp(vendor="Cisco", device_type="cisco_ios"))
        hits = lldp.parse_neighbor_output(
            CISCO_LLDP_BRIEF,
            vendor="",
            device_type="generic",
            command="show lldp neighbors",
        )
        self.assertEqual(hits, [])

    def test_huawei(self) -> None:
        hits = lldp.parse_huawei_lldp(HUAWEI_LLDP)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name.lower(), "r1")
        self.assertEqual(hits[0].local_port, "GigabitEthernet0/0/1")

    def test_zte_brief(self) -> None:
        hits = lldp.parse_zte_lldp(ZTE_LLDP_BRIEF)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].remote_name, "R1")

    def test_zte_scope_holdtime(self) -> None:
        hits = lldp.parse_zte_lldp(ZTE_LLDP_SCOPE)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0].remote_name, "CSR1_6120HSC")
        self.assertEqual(hits[1].remote_name, "OLT/CPE_6180H")
        self.assertEqual(hits[1].remote_port, "gei-1/2/0/3")

    def test_pick_command_lldp_only(self) -> None:
        cmd, tag = lldp.pick_neighbor_command(protocol="cdp", vendor="Cisco", device_type="cisco_ios")
        self.assertEqual(tag, "lldp")
        self.assertEqual(cmd, "show lldp neighbors detail")

    def test_pick_command_community_vendors(self) -> None:
        self.assertEqual(
            lldp.pick_neighbor_command(vendor="H3C", device_type="hp_comware")[0],
            "display lldp neighbor-information list",
        )
        self.assertEqual(
            lldp.pick_neighbor_command(vendor="Juniper", device_type="juniper_junos")[0],
            "show lldp neighbors",
        )
        self.assertEqual(
            lldp.pick_neighbor_command(vendor="Nokia", device_type="alcatel_aos")[0],
            "show lldp remote-system",
        )
        key, stub = lldp.parser_meta(vendor="H3C", device_type="hp_comware")
        self.assertEqual(key, "h3c")
        self.assertFalse(stub)
        key, stub = lldp.parser_meta(vendor="Ericsson", device_type="ericsson_ipos")
        self.assertTrue(stub)

    def test_pick_command_vendor_wins_on_dtype_conflict(self) -> None:
        # Stale/default zte_zxros must not force ZTE LLDP on a Huawei-labeled NE.
        self.assertEqual(
            lldp.pick_neighbor_command(vendor="Huawei", device_type="zte_zxros")[0],
            "display lldp neighbor",
        )
        self.assertEqual(lldp.resolve_vendor_key("Huawei", "zte_zxros"), "huawei")
        self.assertEqual(
            lldp.pick_neighbor_command(vendor="Huawei", device_type="huawei")[0],
            "display lldp neighbor",
        )


class DeadlockHelperTests(unittest.TestCase):
    def test_is_deadlock_error_detects_pg_message(self) -> None:
        exc = Exception(
            '(psycopg.errors.DeadlockDetected) 检测到死锁 DETAIL: 进程28484等待'
        )
        self.assertTrue(svc._is_deadlock_error(exc))

    def test_is_deadlock_error_ignores_other(self) -> None:
        self.assertFalse(svc._is_deadlock_error(ValueError("unique violation")))


class FabricTopologyTests(unittest.TestCase):
    """Uses an in-memory SQLite engine — never touch the live NETX_DATABASE_URL."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=cls.engine)
        cls.Session = sessionmaker(
            bind=cls.engine, autoflush=False, autocommit=False, expire_on_commit=False
        )

    def setUp(self) -> None:
        self.db = self.Session()
        # Wipe between cases on the isolated in-memory DB only.
        for model in (
            TopoViewEdgeStyle,
            TopoViewNode,
            TopoView,
            TopoFolder,
            TopoFabricEdge,
            TopoFabricNode,
            TopoDiscoverJobItem,
            TopoDiscoverJob,
        ):
            self.db.query(model).delete()
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_apply_discover_hits_retries_deadlock(self) -> None:
        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"nea-{suffix}",
            name=f"R2-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 80) + 20}",
        )
        peer_ne = ManagedNE(
            id=f"neb-{suffix}",
            name="r1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[2:4], 16) % 80) + 120}",
        )
        self.db.add(ne)
        self.db.add(peer_ne)
        self.db.commit()
        node = svc.ensure_fabric_node_for_managed(self.db, ne)
        svc.ensure_fabric_node_for_managed(self.db, peer_ne)
        self.db.commit()
        hits = [
            lldp.NeighborHit(
                remote_name="r1",
                local_port="Gi0/1",
                remote_port="Ethernet1/0/1",
            )
        ]
        calls = {"n": 0}
        real_upsert = svc.upsert_fabric_edge

        def flaky_upsert(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError(
                    "INSERT",
                    {},
                    Exception("DeadlockDetected: fake"),
                )
            return real_upsert(*args, **kwargs)

        with (
            patch.object(svc, "upsert_fabric_edge", side_effect=flaky_upsert),
            patch("netx_api.topology_discover_scan.upsert_fabric_edge", side_effect=flaky_upsert),
            patch("netx_api.topology_discover_scan._sleep_deadlock_backoff", return_value=None),
        ):
            out = svc._apply_discover_hits(
                self.db,
                fabric_node_id=node.id,
                hits=hits,
                auto_add_unmatched=False,
            )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(calls["n"], 2)
        self.assertGreaterEqual(int(out.get("edges_added") or 0), 1)

    def test_apply_discover_hits_skips_self_loop(self) -> None:
        """LLDP advertising itself must not fail the whole apply; other peers still count."""
        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"nea-{suffix}",
            name=f"R-self-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 80) + 20}",
        )
        peer_ne = ManagedNE(
            id=f"neb-{suffix}",
            name=f"r-peer-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[2:4], 16) % 80) + 120}",
        )
        self.db.add(ne)
        self.db.add(peer_ne)
        self.db.commit()
        node = svc.ensure_fabric_node_for_managed(self.db, ne)
        peer = svc.ensure_fabric_node_for_managed(self.db, peer_ne)
        self.db.commit()
        hits = [
            lldp.NeighborHit(
                remote_name=ne.name,
                remote_ip=ne.ip_address,
                local_port="Gi0/0",
                remote_port="Gi0/0",
            ),
            lldp.NeighborHit(
                remote_name=peer_ne.name,
                remote_ip=peer_ne.ip_address,
                local_port="Gi0/1",
                remote_port="Ethernet1/0/1",
            ),
        ]
        out = svc._apply_discover_hits(
            self.db,
            fabric_node_id=node.id,
            hits=hits,
            auto_add_unmatched=False,
        )
        self.assertTrue(out.get("ok"), out)
        self.assertEqual(int(out.get("edges_added") or 0), 1)
        edges = (
            self.db.query(TopoFabricEdge)
            .filter(
                (TopoFabricEdge.a_node_id == node.id) | (TopoFabricEdge.b_node_id == node.id)
            )
            .all()
        )
        self.assertEqual(len(edges), 1)
        ids = {edges[0].a_node_id, edges[0].b_node_id}
        self.assertEqual(ids, {node.id, peer.id})

        skipped, action = svc.upsert_fabric_edge(
            self.db,
            a_node_id=node.id,
            b_node_id=node.id,
            a_port="Lo0",
            b_port="Lo0",
            source="lldp",
        )
        self.assertIsNone(skipped)
        self.assertEqual(action, "skipped_self_loop")

    def _region(self, name: str = "Test-Region") -> str:
        """Return the unique L2「根图」canvas folder id under a new top-level「根」."""
        top = svc.create_folder(
            self.db, TopologyFolderCreate(name=name, kind="region")
        )
        child = (
            self.db.query(TopoFolder)
            .filter(TopoFolder.parent_id == top.id, TopoFolder.kind == "region")
            .order_by(TopoFolder.sort_order.asc(), TopoFolder.created_at.asc())
            .first()
        )
        return child.id if child is not None else top.id

    def _physical_view(self, folder_id: str) -> TopoView:
        row = (
            self.db.query(TopoView)
            .filter(
                TopoView.folder_id == folder_id,
                TopoView.kind == "physical",
            )
            .first()
        )
        assert row is not None, f"no physical view on folder {folder_id}"
        return row

    def _canvas(
        self,
        name: str = "Test-Region",
        *,
        filter: dict | None = None,
        role: str | None = None,
    ) -> str:
        """createTopologyFolder path: return 根图 physical view_id (optional filter patch)."""
        folder_id = self._region(name)
        view = self._physical_view(folder_id)
        if filter is not None or role is not None:
            body = TopologyViewUpdate(
                filter=filter,
                role=role,
            )
            svc.update_view(self.db, view.id, body)
            self.db.refresh(view)
        return view.id

    def test_view_crud_and_positions(self) -> None:
        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"ne-{suffix}",
            name=f"R-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.0.0.{(int(suffix[:2], 16) % 200) + 1}",
        )
        self.db.add(ne)
        self.db.commit()

        view_id = self._canvas(f"R-{suffix}")
        graph = svc.add_nodes_to_view(
            self.db, view_id, ViewNodesAdd(managed_ne_ids=[ne.id])
        )
        self.assertEqual(len(graph.nodes), 1)
        fid = graph.nodes[0].fabric_node_id
        graph2 = svc.patch_view_positions(
            self.db,
            view_id,
            ViewPositionsPatch(positions=[ViewNodeIn(fabric_node_id=fid, x=120, y=80)]),
        )
        self.assertEqual(graph2.nodes[0].x, 120)
        self.assertEqual(graph2.nodes[0].y, 80)

        summary = svc.get_fabric_summary(self.db)
        self.assertGreaterEqual(summary.node_count, 1)

        svc.delete_view(self.db, view_id, force=True)
        self.db.delete(ne)
        self.db.commit()

    def test_create_topology_placeholder_on_view(self) -> None:
        suffix = uuid4().hex[:8]
        view_id = self._canvas(f"Rph-{suffix}")
        graph = svc.create_topology_placeholder_on_view(
            self.db,
            view_id,
            TopologyPlaceholderCreate(name=f"SW-{suffix}", ip_address="", x=42, y=77),
        )
        self.assertEqual(len(graph.nodes), 1)
        node = graph.nodes[0]
        self.assertEqual(node.name, f"SW-{suffix}")
        self.assertEqual(node.x, 42)
        self.assertEqual(node.y, 77)
        self.assertTrue(node.managed_ne_id)
        ne = self.db.get(ManagedNE, node.managed_ne_id)
        self.assertIsNotNone(ne)
        assert ne is not None
        self.assertEqual(ne.source, TOPOLOGY_NE_SOURCE)
        self.assertEqual(ne.ip_address, "")
        self.assertEqual(ne.device_type, "generic")

    def test_legacy_single_map_heals_into_root_map(self) -> None:
        """Pre-architecture: physical view hung on top-level region (no「根图」)."""
        from uuid import uuid4

        from netx_api.models import TopoFolder, TopoView
        from netx_api.topology_common import _utcnow
        from netx_api.topology_membership import VIEW_KIND_PHYSICAL

        now = _utcnow()
        boot = svc.bootstrap_topology_tree(self.db)
        root = self.db.get(TopoFolder, boot["root_id"])
        self.assertIsNotNone(root)
        assert root is not None
        legacy = TopoFolder(
            id=uuid4().hex,
            parent_id=root.id,
            kind="region",
            name="旧单图站点",
            sort_order=0,
            is_system=False,
            created_at=now,
            updated_at=now,
        )
        old_view = TopoView(
            id=uuid4().hex,
            folder_id=legacy.id,
            parent_view_id=None,
            kind=VIEW_KIND_PHYSICAL,
            role="core",
            name="拓扑图",
            remark="",
            sort_order=0,
            filter={},
            viewport={},
            created_at=now,
            updated_at=now,
        )
        self.db.add(legacy)
        self.db.add(old_view)
        self.db.commit()

        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        site = next(c for c in tree.root.children if c.id == legacy.id)
        self.assertEqual(site.views, [])
        self.assertEqual(len(site.children), 1)
        root_map = site.children[0]
        self.assertEqual(root_map.name, "根图")
        self.assertTrue(any(v.id == old_view.id for v in root_map.views))
        # Creating another top-level root still works beside the healed legacy site.
        created = svc.create_folder(
            self.db, TopologyFolderCreate(name="新根", kind="region")
        )
        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        self.assertTrue(any(c.id == created.id for c in tree2.root.children))
        self.assertTrue(any(c.id == legacy.id for c in tree2.root.children))

    def test_topology_tree_root_region_and_leaf(self) -> None:
        tree = svc.get_topology_tree(self.db)
        self.assertIsNotNone(tree.root)
        assert tree.root is not None
        self.assertEqual(tree.root.kind, "root")
        # No default Unassigned region — only user-created regions under hidden root.
        self.assertEqual(tree.root.children, [])

        region = svc.create_folder(
            self.db, TopologyFolderCreate(name="East", kind="region")
        )
        # Top-level「根」is nav-only; unique L2「根图」holds the canvas.
        tree1 = svc.get_topology_tree(self.db)
        assert tree1.root is not None
        east0 = next(c for c in tree1.root.children if c.id == region.id)
        self.assertEqual(east0.views, [])
        self.assertEqual(len(east0.children), 1)
        root_map = east0.children[0]
        self.assertEqual(root_map.name, "根图")
        self.assertEqual(len(root_map.views), 1)
        self.assertEqual(root_map.views[0].name, "根图")

        # Sibling custom maps are forbidden — nest a sub-region folder instead.
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            svc.create_view(
                self.db,
                TopologyViewCreate(
                    name="East-Custom",
                    folder_id=root_map.id,
                    kind="custom",
                ),
            )
        self.assertEqual(ctx.exception.detail, "use_create_subregion_folder")

        sub = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="East-Zone", kind="region", parent_id=root_map.id),
        )
        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        east = next(c for c in tree2.root.children if c.id == region.id)
        rm = east.children[0]
        self.assertEqual(len(rm.views), 1)
        self.assertEqual(rm.views[0].kind, "physical")
        self.assertTrue(any(c.id == sub.id for c in rm.children))

    def test_tree_ne_count_distinct_membership_and_home(self) -> None:
        """Directory N = distinct fabric ids (membership ∪ region_folder_id); parent unions."""
        site = svc.create_folder(
            self.db, TopologyFolderCreate(name="NE-Count-Site", kind="region")
        )
        tree0 = svc.get_topology_tree(self.db)
        assert tree0.root is not None
        site_f = next(c for c in tree0.root.children if c.id == site.id)
        root_map = site_f.children[0]
        self.assertEqual(site_f.ne_count, 0)
        self.assertEqual(root_map.ne_count, 0)
        self.assertEqual(root_map.views[0].node_count, 0)

        a = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="Zone-A", kind="region", parent_id=site.id),
        )
        b = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="Zone-B", kind="region", parent_id=site.id),
        )
        tree1 = svc.get_topology_tree(self.db)
        assert tree1.root is not None
        site1 = next(c for c in tree1.root.children if c.id == site.id)
        rm1 = site1.children[0]
        za = next(c for c in rm1.children if c.id == a.id)
        zb = next(c for c in rm1.children if c.id == b.id)
        # Empty nested regions (+ region icons on parent) still 0 NE.
        self.assertEqual(za.ne_count, 0)
        self.assertEqual(zb.ne_count, 0)
        self.assertEqual(rm1.ne_count, 0)

        suffix = uuid4().hex[:8]
        shared = TopoFabricNode(
            id=f"ne-shared-{suffix}",
            name=f"Shared-{suffix}",
            ip="10.66.0.1",
            managed_ne_id=f"mne-shared-{suffix}",
        )
        only_a = TopoFabricNode(
            id=f"ne-a-{suffix}",
            name=f"OnlyA-{suffix}",
            ip="10.66.0.2",
            managed_ne_id=f"mne-a-{suffix}",
        )
        home_only = TopoFabricNode(
            id=f"ne-home-{suffix}",
            name=f"Home-{suffix}",
            ip="10.66.0.3",
            managed_ne_id=f"mne-home-{suffix}",
            region_folder_id=a.id,
        )
        self.db.add_all([shared, only_a, home_only])
        self.db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=za.views[0].id,
                fabric_node_id=shared.id,
                x=0,
                y=0,
            )
        )
        self.db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=zb.views[0].id,
                fabric_node_id=shared.id,
                x=1,
                y=1,
            )
        )
        self.db.add(
            TopoViewNode(
                id=uuid4().hex,
                view_id=za.views[0].id,
                fabric_node_id=only_a.id,
                x=2,
                y=2,
            )
        )
        self.db.commit()

        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        site2 = next(c for c in tree2.root.children if c.id == site.id)
        rm2 = site2.children[0]
        za2 = next(c for c in rm2.children if c.id == a.id)
        zb2 = next(c for c in rm2.children if c.id == b.id)

        # Zone-A: shared + only_a + home_only (region_folder_id, no membership)
        self.assertEqual(za2.ne_count, 3)
        self.assertEqual(za2.views[0].node_count, 2)
        # Zone-B: shared only
        self.assertEqual(zb2.ne_count, 1)
        self.assertEqual(zb2.views[0].node_count, 1)
        # Parent 根图: distinct union → shared, only_a, home_only = 3 (not 3+1)
        self.assertEqual(rm2.ne_count, 3)
        self.assertEqual(site2.ne_count, 3)

    def test_manual_root_map_name_follows_locale(self) -> None:
        """English UI creates Root map; zh (default) keeps 根图."""
        en_root = svc.create_folder(
            self.db, TopologyFolderCreate(name="West-EN", kind="region", locale="en")
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        west = next(c for c in tree.root.children if c.id == en_root.id)
        self.assertEqual(len(west.children), 1)
        self.assertEqual(west.children[0].name, "Root map")
        self.assertEqual(west.children[0].views[0].name, "Root map")

        zh_root = svc.create_folder(
            self.db, TopologyFolderCreate(name="East-ZH", kind="region", locale="zh")
        )
        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        east = next(c for c in tree2.root.children if c.id == zh_root.id)
        self.assertEqual(east.children[0].name, "根图")

    def test_physical_view_default_max_nodes_uses_kind(self) -> None:
        """Empty-filter physical canvases must not inherit role core=80."""
        from netx_api.topology_views_graph import _membership_for_view

        root = svc.create_folder(
            self.db, TopologyFolderCreate(name="Cap-Site", kind="region", locale="zh")
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        nav = next(c for c in tree.root.children if c.id == root.id)
        phys_id = nav.children[0].views[0].id
        view = self.db.get(TopoView, phys_id)
        assert view is not None
        self.assertEqual(view.kind, "physical")
        self.assertEqual(view.role, "core")
        self.assertEqual(view.filter or {}, {})
        mem = _membership_for_view(view)
        self.assertEqual(int(mem.get("max_nodes") or 0), 2000)

        from fastapi import HTTPException
        from netx_api.topology_membership import default_membership

        with self.assertRaises(HTTPException) as ctx:
            svc.create_view(
                self.db,
                TopologyViewCreate(
                    name="Cap-Custom", folder_id=nav.children[0].id, kind="custom"
                ),
            )
        self.assertEqual(ctx.exception.detail, "use_create_subregion_folder")
        self.assertEqual(int(default_membership("core", kind="custom")["max_nodes"]), 80)

    def test_site_rejects_sibling_custom_allows_subregion(self) -> None:
        from fastapi import HTTPException

        top = svc.create_folder(
            self.db, TopologyFolderCreate(name="Site-R", kind="region")
        )
        tree0 = svc.get_topology_tree(self.db)
        assert tree0.root is not None
        nav = next(c for c in tree0.root.children if c.id == top.id)
        root_map = nav.children[0]
        self.assertEqual(root_map.name, "根图")
        auto_phys = root_map.views[0]
        self.assertEqual(auto_phys.kind, "physical")

        with self.assertRaises(HTTPException) as ctx:
            svc.create_view(
                self.db,
                TopologyViewCreate(
                    name="Custom-A",
                    folder_id=root_map.id,
                    kind="custom",
                ),
            )
        self.assertEqual(ctx.exception.detail, "use_create_subregion_folder")

        sub = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="Site-Zone", kind="region", parent_id=root_map.id),
        )
        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        reg2 = next(c for c in tree2.root.children if c.id == top.id).children[0]
        self.assertEqual(len(reg2.views), 1)
        self.assertEqual(reg2.views[0].id, auto_phys.id)
        self.assertTrue(any(c.id == sub.id for c in reg2.children))

        with self.assertRaises(Exception):
            svc.delete_view(self.db, auto_phys.id)
        # force-delete physical does not recreate another map
        svc.delete_view(self.db, auto_phys.id, force=True)
        tree3 = svc.get_topology_tree(self.db)
        assert tree3.root is not None
        reg3 = next(c for c in tree3.root.children if c.id == top.id).children[0]
        self.assertEqual(sum(1 for v in reg3.views if v.kind == "physical"), 0)

    def test_delete_region_cascades_views(self) -> None:
        region = svc.create_folder(
            self.db, TopologyFolderCreate(name="Del-Region", kind="region")
        )
        tree0 = svc.get_topology_tree(self.db)
        assert tree0.root is not None
        nav = next(c for c in tree0.root.children if c.id == region.id)
        root_map = nav.children[0]
        phys_id = root_map.views[0].id
        sub = svc.create_folder(
            self.db,
            TopologyFolderCreate(
                name="Del-Zone", kind="region", parent_id=root_map.id
            ),
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        reg = next(c for c in tree.root.children if c.id == region.id)
        self.assertTrue(any(c.id == sub.id for c in reg.children[0].children))

        out = svc.delete_folder(self.db, region.id)
        self.assertTrue(out.get("deleted"))
        tree2 = svc.get_topology_tree(self.db)
        assert tree2.root is not None
        self.assertFalse(any(c.id == region.id for c in tree2.root.children))
        self.assertIsNone(self.db.get(TopoView, phys_id))
        self.assertEqual(
            self.db.query(TopoView).filter(TopoView.folder_id == root_map.id).count(),
            0,
        )

    def test_nested_region_places_icon_on_parent_canvas(self) -> None:
        # Top-level「根」is nav-only; canvas work happens under unique「根图」.
        container = svc.create_folder(
            self.db, TopologyFolderCreate(name="Site-Group", kind="region")
        )
        parent = svc.create_folder(
            self.db,
            TopologyFolderCreate(
                name="Parent-Canvas", kind="region", parent_id=container.id
            ),
        )
        child = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="Child-Icon", kind="region", parent_id=parent.id),
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        group = next(c for c in tree.root.children if c.id == container.id)
        self.assertEqual(group.views, [])
        self.assertEqual(len(group.children), 1)
        root_map = group.children[0]
        self.assertEqual(root_map.name, "根图")
        # Creating under the nav「根」remounts onto「根图」.
        p = next(c for c in root_map.children if c.id == parent.id)
        self.assertTrue(p.views)
        graph = svc.get_view_graph(self.db, p.views[0].id)
        regions = [n for n in graph.nodes if n.kind == "region"]
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0].folder_id, child.id)
        self.assertEqual(regions[0].label, "Child-Icon")
        self.assertTrue(str(regions[0].fabric_node_id).startswith("region:"))

        # Drag/save positions for synthetic region nodes.
        moved = svc.patch_view_positions(
            self.db,
            p.views[0].id,
            ViewPositionsPatch(
                positions=[
                    ViewNodeIn(
                        fabric_node_id=regions[0].fabric_node_id, x=321, y=210
                    )
                ],
                return_graph=True,
            ),
        )
        if hasattr(moved, "nodes"):
            hit = next(n for n in moved.nodes if n.kind == "region")
        else:
            hit = next(
                n
                for n in svc.get_view_graph(self.db, p.views[0].id).nodes
                if n.kind == "region"
            )
        self.assertEqual(hit.x, 321)
        self.assertEqual(hit.y, 210)

        svc.delete_folder(self.db, child.id)
        graph2 = svc.get_view_graph(self.db, p.views[0].id)
        self.assertFalse(any(n.kind == "region" for n in graph2.nodes))

    def test_top_level_stays_nav_when_adding_child(self) -> None:
        """Creating under「根」remounts onto unique「根图」; top stays nav-only."""
        top = svc.create_folder(
            self.db, TopologyFolderCreate(name="East-Nav", kind="region")
        )
        child = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="Site-A", kind="region", parent_id=top.id),
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        east = next(c for c in tree.root.children if c.id == top.id)
        self.assertEqual(east.views, [])
        self.assertEqual(len(east.children), 1)
        root_map = east.children[0]
        self.assertEqual(root_map.name, "根图")
        self.assertEqual(len(root_map.views), 1)
        site = next(c for c in root_map.children if c.id == child.id)
        self.assertEqual(len(site.views), 1)
        self.assertEqual(site.views[0].name, "Site-A")
        self.assertNotEqual(site.views[0].name, "Physical topology")

    def test_root_auto_spawns_unique_root_map(self) -> None:
        top = svc.create_folder(
            self.db, TopologyFolderCreate(name="West", kind="region")
        )
        # Second create under the same「根」must remount under existing「根图」.
        a = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="A", kind="region", parent_id=top.id),
        )
        b = svc.create_folder(
            self.db,
            TopologyFolderCreate(name="B", kind="region", parent_id=top.id),
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        west = next(c for c in tree.root.children if c.id == top.id)
        self.assertEqual(len(west.children), 1)
        root_map = west.children[0]
        self.assertEqual(root_map.name, "根图")
        child_ids = {c.id for c in root_map.children}
        self.assertEqual(child_ids, {a.id, b.id})

    def test_root_map_rename_ok_but_not_direct_delete(self) -> None:
        from fastapi import HTTPException

        from netx_api.topology_schemas import TopologyFolderUpdate

        top = svc.create_folder(
            self.db, TopologyFolderCreate(name="North", kind="region")
        )
        tree = svc.get_topology_tree(self.db)
        assert tree.root is not None
        north = next(c for c in tree.root.children if c.id == top.id)
        root_map = north.children[0]
        self.assertTrue(root_map.is_system)
        renamed = svc.update_folder(
            self.db, root_map.id, TopologyFolderUpdate(name="主画布")
        )
        self.assertEqual(renamed.name, "主画布")
        with self.assertRaises(HTTPException) as ctx:
            svc.delete_folder(self.db, root_map.id)
        self.assertEqual(ctx.exception.detail, "cannot_delete_system_folder")
        # Deleting the manual「根」cascades the「根图」.
        out = svc.delete_folder(self.db, top.id)
        self.assertTrue(out.get("deleted"))
        self.assertIsNone(self.db.get(TopoFolder, root_map.id))

    def test_classify_role_region_and_slices(self) -> None:
        from netx_api import topology_classify as clf
        from netx_api.topology_schemas import (
            ClassifyRuleCreate,
            SliceGenerateRequest,
        )

        suffix = uuid4().hex[:6]
        region = svc.create_folder(
            self.db, TopologyFolderCreate(name=f"East-{suffix}", kind="region")
        )
        nes = []
        for name, ip in (
            (f"CORE-A-{suffix}", "10.1.1.1"),
            (f"CORE-B-{suffix}", "10.1.1.2"),
            (f"AGG-A-{suffix}", "10.1.2.1"),
            (f"ACC-A-{suffix}", "10.1.3.1"),
        ):
            ne = ManagedNE(
                id=f"clf-{suffix}-{name[:8]}",
                name=name,
                vendor="Cisco",
                device_type="cisco_ios",
                ip_address=ip,
            )
            self.db.add(ne)
            nes.append(ne)
        self.db.commit()
        nodes = [svc.ensure_fabric_node_for_managed(self.db, ne) for ne in nes]
        self.db.commit()
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[0].id,
            b_node_id=nodes[2].id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[2].id,
            b_node_id=nodes[3].id,
            a_port="Gi0/2",
            b_port="Gi0/3",
            source="lldp",
        )
        self.db.commit()

        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="role",
                name="core",
                pattern=r"^CORE-",
                priority=10,
                payload={"role": "core"},
            ),
        )
        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="role",
                name="agg",
                pattern=r"^AGG-",
                priority=20,
                payload={"role": "aggregation"},
            ),
        )
        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="role",
                name="acc",
                pattern=r"^ACC-",
                priority=30,
                payload={"role": "access"},
            ),
        )
        clf.create_rule(
            self.db,
            ClassifyRuleCreate(
                scope="region",
                name="east",
                pattern=rf"-{suffix}$",
                priority=10,
                payload={"folder_id": region.id},
            ),
        )
        prev = clf.preview_classify(self.db)
        self.assertGreaterEqual(prev.level_matched, 4)
        self.assertGreaterEqual(prev.role_matched, 4)
        applied = clf.apply_classify(self.db)
        self.assertGreaterEqual(applied.level_updated, 4)
        self.assertGreaterEqual(applied.role_updated, 4)
        self.db.refresh(nodes[0])
        self.assertEqual(nodes[0].role, "core")
        self.assertEqual(nodes[0].level, 1.0)
        self.assertEqual(nodes[0].region_folder_id, region.id)

        dry = clf.generate_slices(
            self.db,
            SliceGenerateRequest(
                folder_id=region.id,
                template="core_agg",
                dry_run=True,
                max_nodes=50,
            ),
        )
        self.assertGreaterEqual(dry.map_count, 1)
        self.assertTrue(dry.dry_run)
        real = clf.generate_slices(
            self.db,
            SliceGenerateRequest(
                folder_id=region.id,
                template="core_agg",
                dry_run=False,
                max_nodes=50,
            ),
        )
        self.assertFalse(real.dry_run)
        self.assertTrue(real.created_view_ids)
        search = clf.search_fabric_nodes_with_views(self.db, keyword=f"CORE-A-{suffix}")
        self.assertGreaterEqual(search["total"], 1)
        self.assertTrue(search["items"][0].get("views"))

        from netx_api.topology_schemas import FabricNodesBulkTagRequest, FabricNodesMatchRequest

        matched = clf.match_fabric_nodes(
            self.db,
            FabricNodesMatchRequest(pattern=rf"^AGG-A-{suffix}$", match_field="name"),
        )
        self.assertEqual(matched.total_matched, 1)
        bulk = clf.bulk_tag_fabric_nodes(
            self.db,
            FabricNodesBulkTagRequest(
                pattern=rf"^ACC-A-{suffix}$",
                role="access",
                region_folder_id=region.id,
            ),
        )
        self.assertEqual(bulk.updated, 1)
        self.assertEqual(bulk.level, 3.0)
        acc = next(n for n in nodes if n.name.startswith("ACC-"))
        self.db.refresh(acc)
        self.assertEqual(acc.level, 3.0)
        self.assertEqual(acc.role, "access")

    def test_project_neighbors_respects_max_nodes(self) -> None:
        suffix = uuid4().hex[:8]
        nes = []
        for i in range(4):
            ne = ManagedNE(
                id=f"cap-{suffix}-{i}",
                name=f"N{i}-{suffix}",
                vendor="Cisco",
                device_type="cisco_ios",
                ip_address=f"10.9.{(int(suffix[:2], 16) % 200)}.{i + 1}",
            )
            self.db.add(ne)
            nes.append(ne)
        self.db.commit()
        nodes = [svc.ensure_fabric_node_for_managed(self.db, ne) for ne in nes]
        self.db.commit()
        # Line: 0-1-2-3
        for a, b in ((0, 1), (1, 2), (2, 3)):
            svc.upsert_fabric_edge(
                self.db,
                a_node_id=nodes[a].id,
                b_node_id=nodes[b].id,
                a_port=f"Gi0/{a}",
                b_port=f"Gi0/{b}",
                source="lldp",
            )
        self.db.commit()

        view_id = self._canvas(
            f"CapR-{suffix}",
            role="core",
            filter={
                "membership": {
                    "expand_hops": 3,
                    "max_nodes": 2,
                    "frozen": False,
                }
            },
        )
        svc.add_nodes_to_view(self.db, view_id, ViewNodesAdd(managed_ne_ids=[nes[0].id]))
        g = svc.project_fabric_neighbors_to_view(self.db, view_id)
        self.assertLessEqual(len(g.nodes), 2)
        self.assertTrue(g.truncated or len(g.nodes) == 2)

        # Seed-scoped project: expand only from node 0 → only node 1 among line peers.
        view2_id = self._canvas(
            f"CapSeedR-{suffix}",
            role="core",
            filter={"membership": {"expand_hops": 1, "max_nodes": 50, "frozen": False}},
        )
        # Place endpoints 0 and 2 (not adjacent); seed from 0 should add 1, not 3.
        svc.add_nodes_to_view(
            self.db, view2_id, ViewNodesAdd(managed_ne_ids=[nes[0].id, nes[2].id])
        )
        g2 = svc.project_fabric_neighbors_to_view(
            self.db,
            view2_id,
            ViewProjectNeighborsRequest(seed_fabric_node_ids=[nodes[0].id]),
        )
        ids2 = {n.fabric_node_id for n in g2.nodes}
        self.assertIn(nodes[0].id, ids2)
        self.assertIn(nodes[1].id, ids2)
        self.assertIn(nodes[2].id, ids2)
        self.assertNotIn(nodes[3].id, ids2)

        # Peers already on canvas must not raise a false truncated banner.
        g3 = svc.project_fabric_neighbors_to_view(
            self.db,
            view2_id,
            ViewProjectNeighborsRequest(seed_fabric_node_ids=[nodes[0].id]),
        )
        self.assertFalse(g3.truncated)

        pop = svc.populate_view(
            self.db,
            view_id,
            ViewPopulateRequest(
                dry_run=True,
                membership={"seed_fabric_node_ids": [nodes[0].id], "expand_hops": 3, "max_nodes": 2},
            ),
        )
        self.assertLessEqual(pop.candidate_count, 4)
        self.assertTrue(pop.truncated or pop.candidate_count <= 2 or pop.would_add <= 2)

    def test_project_neighbors_region_folder_filter(self) -> None:
        suffix = uuid4().hex[:8]
        region_a = self._region(f"RegA-{suffix}")
        region_b = self._region(f"RegB-{suffix}")
        nes = []
        for i in range(3):
            ne = ManagedNE(
                id=f"regf-{suffix}-{i}",
                name=f"RF{i}-{suffix}",
                vendor="Cisco",
                device_type="cisco_ios",
                ip_address=f"10.11.{(int(suffix[:2], 16) % 200)}.{i + 1}",
            )
            self.db.add(ne)
            nes.append(ne)
        self.db.commit()
        nodes = [svc.ensure_fabric_node_for_managed(self.db, ne) for ne in nes]
        nodes[0].region_folder_id = region_a
        nodes[1].region_folder_id = region_a
        nodes[2].region_folder_id = region_b
        self.db.commit()
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[0].id,
            b_node_id=nodes[1].id,
            a_port="Gi0/0",
            b_port="Gi0/0",
            source="lldp",
        )
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[0].id,
            b_node_id=nodes[2].id,
            a_port="Gi0/1",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()

        view = self._physical_view(region_a)
        svc.update_view(
            self.db,
            view.id,
            TopologyViewUpdate(
                role="core",
                filter={"membership": {"expand_hops": 1, "max_nodes": 50, "frozen": False}},
            ),
        )
        svc.add_nodes_to_view(self.db, view.id, ViewNodesAdd(managed_ne_ids=[nes[0].id]))
        g = svc.project_fabric_neighbors_to_view(
            self.db,
            view.id,
            ViewProjectNeighborsRequest(region_folder_id=region_a),
        )
        ids = {n.fabric_node_id for n in g.nodes}
        self.assertIn(nodes[0].id, ids)
        self.assertIn(nodes[1].id, ids)
        self.assertNotIn(nodes[2].id, ids)
        self.assertEqual(g.out_of_region_skipped, 1)
        self.assertTrue(g.out_of_region_sample)
        self.assertEqual(g.out_of_region_sample[0]["fabric_node_id"], nodes[2].id)

    def test_project_neighbors_dry_run_does_not_persist(self) -> None:
        suffix = uuid4().hex[:8]
        nes = []
        for i in range(3):
            ne = ManagedNE(
                id=f"dry-{suffix}-{i}",
                name=f"D{i}-{suffix}",
                vendor="Cisco",
                device_type="cisco_ios",
                ip_address=f"10.19.{(int(suffix[:2], 16) % 200)}.{i + 1}",
            )
            self.db.add(ne)
            nes.append(ne)
        self.db.commit()
        nodes = [svc.ensure_fabric_node_for_managed(self.db, ne) for ne in nes]
        self.db.commit()
        for a, b in ((0, 1), (1, 2)):
            svc.upsert_fabric_edge(
                self.db,
                a_node_id=nodes[a].id,
                b_node_id=nodes[b].id,
                a_port=f"Gi0/{a}",
                b_port=f"Gi0/{b}",
                source="lldp",
            )
        self.db.commit()
        view_id = self._canvas(
            f"DryR-{suffix}",
            role="core",
            filter={"membership": {"expand_hops": 1, "max_nodes": 50, "frozen": False}},
        )
        svc.add_nodes_to_view(self.db, view_id, ViewNodesAdd(managed_ne_ids=[nes[0].id]))
        before = (
            self.db.query(TopoViewNode).filter(TopoViewNode.view_id == view_id).count()
        )
        g = svc.project_fabric_neighbors_to_view(
            self.db,
            view_id,
            ViewProjectNeighborsRequest(seed_fabric_node_ids=[nodes[0].id], dry_run=True),
        )
        ids = {n.fabric_node_id for n in g.nodes}
        self.assertIn(nodes[0].id, ids)
        self.assertIn(nodes[1].id, ids)
        after = (
            self.db.query(TopoViewNode).filter(TopoViewNode.view_id == view_id).count()
        )
        self.assertEqual(after, before)

    def test_lldp_discover_writes_fabric_edge(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name="R2",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 100) + 1}",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name="R1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[2:4], 16) % 100) + 101}",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        # Ensure fabric nodes exist for matching
        svc.ensure_fabric_node_for_managed(self.db, ne_a)
        svc.ensure_fabric_node_for_managed(self.db, ne_b)
        self.db.commit()

        fake_exec = {
            "ok": True,
            "output": CISCO_LLDP_DETAIL,
            "commands": ["show lldp neighbors detail"],
        }
        with patch("netx_api.topology_discover_scan.execute_managed_ne_commands", return_value=fake_exec):
            job = svc.start_discover_job(
                self.db,
                FabricDiscoverRequest(scope="ne_ids", ne_ids=[ne_a.id], concurrency=1),
            )
            # Wait for background thread
            for _ in range(50):
                out = svc.get_discover_job(self.db, job.id)
                if out.status in {"done", "failed"}:
                    break
                time.sleep(0.05)
            out = svc.get_discover_job(self.db, job.id)
        self.assertEqual(out.status, "done", out.error)
        edges = svc.list_fabric_edges(self.db, page=1, page_size=50)
        self.assertGreaterEqual(edges["total"], 1)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_merge_duplicate_and_project_skips_orphans(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name="R2",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 80) + 10}",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name="R1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[2:4], 16) % 80) + 100}",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        orphan = TopoFabricNode(
            id=uuid4().hex,
            managed_ne_id=None,
            ume_ne_id=None,
            name="r1",
            ip="",
            vendor="",
            device_type="",
            attrs={"from_lldp_unmatched": True},
        )
        # Simulate raced duplicate of R2 (same managed id not allowed by constraint —
        # use name/ip collision path with a second orphan twin).
        orphan_r2 = TopoFabricNode(
            id=uuid4().hex,
            managed_ne_id=None,
            ume_ne_id=None,
            name="R2",
            ip=ne_a.ip_address,
            vendor="",
            device_type="",
            attrs={"from_lldp_unmatched": True},
        )
        self.db.add(orphan)
        self.db.add(orphan_r2)
        self.db.commit()
        # Path: orphan_r2 -- fb -- fa -- orphan  (the bug shape)
        svc.upsert_fabric_edge(
            self.db, a_node_id=orphan_r2.id, b_node_id=fb.id, a_port="g0", b_port="g1", source="lldp"
        )
        svc.upsert_fabric_edge(
            self.db, a_node_id=fb.id, b_node_id=fa.id, a_port="g2", b_port="g3", source="lldp"
        )
        svc.upsert_fabric_edge(
            self.db, a_node_id=fa.id, b_node_id=orphan.id, a_port="g4", b_port="g5", source="lldp"
        )
        self.db.commit()

        view_id = self._canvas(f"MR-{suffix}")
        graph = svc.add_nodes_to_view(
            self.db,
            view_id,
            ViewNodesAdd(fabric_node_ids=[fa.id, fb.id, orphan.id, orphan_r2.id]),
        )
        self.assertEqual(len(graph.nodes), 4)

        merged = svc.merge_duplicate_fabric_nodes(self.db)
        self.assertGreaterEqual(merged["merged"], 2)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, orphan.id))
        self.assertIsNone(self.db.get(TopoFabricNode, orphan_r2.id))

        # Re-place inventory nodes only, then project should stay at 2.
        graph2 = svc.add_nodes_to_view(
            self.db, view_id, ViewNodesAdd(managed_ne_ids=[ne_a.id, ne_b.id])
        )
        # View may still have stale placements pointing at deleted ids — project cleans.
        projected = svc.project_fabric_neighbors_to_view(self.db, view_id)
        self.assertEqual(len(projected.nodes), 2)
        labels = sorted((n.name or n.label).upper() for n in projected.nodes)
        self.assertEqual(labels, ["R1", "R2"])
        self.assertGreaterEqual(len(projected.edges), 1)
        endpoint_ids = {n.fabric_node_id for n in projected.nodes}
        for e in projected.edges:
            self.assertIn(e.a_node_id, endpoint_ids)
            self.assertIn(e.b_node_id, endpoint_ids)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_match_hit_prefers_inventory(self) -> None:
        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"ne-{suffix}",
            name="R1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 20}",
        )
        self.db.add(ne)
        self.db.commit()
        inv = svc.ensure_fabric_node_for_managed(self.db, ne)
        orphan = TopoFabricNode(
            id=uuid4().hex,
            name="r1",
            ip="",
            attrs={"from_lldp_unmatched": True},
        )
        self.db.add(orphan)
        self.db.commit()
        hit = lldp.NeighborHit(
            remote_name="r1",
            remote_ip="",
            local_port="Gi0/0",
            remote_port="Gi0/1",
        )
        peer = svc._match_hit_to_fabric_node(self.db, hit, self_id="self")
        self.assertEqual(peer.id, inv.id)
        self.db.delete(ne)
        self.db.commit()

    def test_lldp_unmatched_creates_ssh_placeholder_ne(self) -> None:
        suffix = uuid4().hex[:8]
        peer_name = f"Peer-{suffix}"
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name=f"Core-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"203.0.113.{(int(suffix[:2], 16) % 80) + 40}",
            source="manual",
        )
        self.db.add(ne_a)
        self.db.commit()
        svc.ensure_fabric_node_for_managed(self.db, ne_a)
        self.db.commit()

        lldp_out = f"""
------------------------------------------------
Local Intf: Gi0/1
Chassis id: 707b.5c6e.d130
Port id: Ethernet1/0/1
System Name: {peer_name}
Management Addresses:
    IP: 198.51.100.200
"""
        fake_exec = {
            "ok": True,
            "output": lldp_out,
            "commands": ["show lldp neighbors detail"],
        }
        with patch("netx_api.topology_discover_scan.execute_managed_ne_commands", return_value=fake_exec):
            job = svc.start_discover_job(
                self.db,
                FabricDiscoverRequest(
                    scope="ne_ids", ne_ids=[ne_a.id], concurrency=1, auto_add_unmatched=True
                ),
            )
            for _ in range(50):
                out = svc.get_discover_job(self.db, job.id)
                if out.status in {"done", "failed"}:
                    break
                time.sleep(0.05)
            out = svc.get_discover_job(self.db, job.id)
        self.assertEqual(out.status, "done", out.error)

        placeholders = (
            self.db.query(ManagedNE)
            .filter(ManagedNE.source == LLDP_DISCOVERED_NE_SOURCE)
            .all()
        )
        placeholder = next(
            (p for p in placeholders if svc._norm_host(p.name) == svc._norm_host(peer_name)),
            None,
        )
        self.assertIsNotNone(placeholder)
        assert placeholder is not None
        self.assertEqual(placeholder.ip_address, "")
        self.assertEqual(placeholder.username, "")
        self.assertEqual(placeholder.password_enc, "")
        self.assertEqual(placeholder.protocol, "ssh")
        self.assertEqual(placeholder.device_type, "generic")
        self.assertEqual(placeholder.source_ref, "198.51.100.200")

        view_id = self._canvas(f"PR-{suffix}")
        svc.add_nodes_to_view(self.db, view_id, ViewNodesAdd(managed_ne_ids=[ne_a.id]))
        projected = svc.project_fabric_neighbors_to_view(self.db, view_id)
        self.assertEqual(len(projected.nodes), 2)
        self.assertGreaterEqual(len(projected.edges), 1)

        self.db.delete(ne_a)
        self.db.delete(placeholder)
        self.db.commit()

    def test_match_prefers_real_ne_over_webcrt_same_ip(self) -> None:
        suffix = uuid4().hex[:8]
        ip = f"198.51.100.{(int(suffix[:2], 16) % 80) + 30}"
        real = ManagedNE(
            id=f"real-{suffix}",
            name="R2",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=ip,
            source="manual",
        )
        webcrt = ManagedNE(
            id=f"wcrt-{suffix}",
            name=f"{ip} (1)",
            vendor="Other",
            device_type="generic",
            ip_address=ip,
            source=WEBCRT_NE_SOURCE,
        )
        self.db.add(real)
        self.db.add(webcrt)
        self.db.commit()
        inv = svc.ensure_fabric_node_for_managed(self.db, real)
        ghost = svc.ensure_fabric_node_for_managed(self.db, webcrt)
        self.db.commit()
        # LLDP management IP alone is not identity — need System Name.
        ip_only = lldp.NeighborHit(
            remote_name="", remote_ip=ip, local_port="Gi0/0", remote_port="Gi0/1"
        )
        self.assertIsNone(svc._match_hit_to_fabric_node(self.db, ip_only, self_id="self"))

        hit = lldp.NeighborHit(
            remote_name="R2", remote_ip=ip, local_port="Gi0/0", remote_port="Gi0/1"
        )
        peer = svc._match_hit_to_fabric_node(self.db, hit, self_id="self")
        self.assertEqual(peer.id, inv.id)
        self.assertNotEqual(peer.id, ghost.id)

        merged = svc.merge_duplicate_fabric_nodes(self.db)
        self.assertGreaterEqual(merged["merged"], 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, ghost.id))
        self.db.delete(real)
        self.db.delete(webcrt)
        self.db.commit()

    def test_match_ignores_lldp_mgmt_ip_prefers_hostname(self) -> None:
        """Two inventory NEs both named r1 with different IPs — match by name, not LLDP IP."""
        suffix = uuid4().hex[:8]
        ne_real = ManagedNE(
            id=f"real-{suffix}",
            name="r1",
            vendor="Huawei",
            device_type="huawei",
            ip_address="192.168.0.127",
            source="manual",
        )
        ne_wrong = ManagedNE(
            id=f"wrong-{suffix}",
            name="r1-lab",  # different hostname key
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address="203.0.113.184",
            source="manual",
        )
        self.db.add(ne_real)
        self.db.add(ne_wrong)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_real)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_wrong)
        self.db.commit()
        hit = lldp.NeighborHit(
            remote_name="r1",
            remote_ip="203.0.113.184",  # misleading interface/mgmt IP
            local_port="Gi0/1",
            remote_port="Ethernet1/0/1",
        )
        peer = svc._match_hit_to_fabric_node(self.db, hit, self_id="self")
        self.assertEqual(peer.id, fa.id)
        self.assertNotEqual(peer.id, fb.id)
        self.db.delete(ne_real)
        self.db.delete(ne_wrong)
        self.db.commit()

    def test_same_hostname_port_cutover_merges_not_missing(self) -> None:
        """Duplicate fabric rows for same hostname must not leave a red replaced edge."""
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name=f"R2-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"192.168.0.{(int(suffix[:2], 16) % 80) + 10}",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name="r1",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address="192.168.0.127",
            source="manual",
        )
        ne_c = ManagedNE(
            id=f"nec-{suffix}",
            name="r1",
            vendor="Other",
            device_type="generic",
            ip_address="203.0.113.184",
            source=LLDP_DISCOVERED_NE_SOURCE,
        )
        self.db.add_all([ne_a, ne_b, ne_c])
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        fc = svc.ensure_fabric_node_for_managed(self.db, ne_c)
        self.db.commit()

        old, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/1",
            b_port="Ethernet1/0/1",
            source="lldp",
        )
        new, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fc.id,
            a_port="Gi0/1",
            b_port="Ethernet1/0/1",
            source="lldp",
        )
        self.db.commit()

        handled = svc._mark_replaced_port_peers(
            self.db,
            self_id=fa.id,
            local_port="Gi0/1",
            peer_id=fc.id,
            new_edge_id=new.id,
        )
        self.db.commit()
        self.db.expire_all()

        # Placeholder fabric absorbed into real inventory; no missing/replaced link.
        self.assertIsNone(self.db.get(TopoFabricNode, fc.id))
        edges = (
            self.db.query(TopoFabricEdge)
            .filter(
                or_(TopoFabricEdge.a_node_id == fa.id, TopoFabricEdge.b_node_id == fa.id)
            )
            .all()
        )
        active = [e for e in edges if e.status == "active"]
        missing = [e for e in edges if e.status == "missing"]
        self.assertEqual(len(active), 1)
        self.assertEqual(len(missing), 0)
        peer_id = active[0].b_node_id if active[0].a_node_id == fa.id else active[0].a_node_id
        self.assertEqual(peer_id, fb.id)
        self.assertTrue(handled)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.delete(ne_c)
        self.db.commit()

    def test_neighborhood(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name=f"A-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 10}",
            vendor="ZTE",
            device_type="zte_zxros",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name=f"B-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[2:4], 16) % 80) + 100}",
            vendor="ZTE",
            device_type="zte_zxros",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        self.db.commit()
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="gei-0/1/0/1",
            b_port="gei-0/1/0/2",
            source="lldp",
        )
        self.db.commit()
        nb = svc.get_fabric_neighborhood(self.db, fa.id, depth=1)
        self.assertEqual(len(nb.nodes), 2)
        self.assertEqual(len(nb.edges), 1)
        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def _pair_nodes(self, suffix: str) -> tuple[TopoFabricNode, TopoFabricNode, ManagedNE, ManagedNE]:
        ne_a = ManagedNE(
            id=f"nea-{suffix}",
            name=f"A-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 10}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        ne_b = ManagedNE(
            id=f"neb-{suffix}",
            name=f"B-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[2:4], 16) % 80) + 100}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        self.db.commit()
        return fa, fb, ne_a, ne_b

    def test_merge_lldp_placeholder_into_real_inventory(self) -> None:
        suffix = uuid4().hex[:8]
        # Real inventory NE + fabric node.
        real = ManagedNE(
            id=f"real-{suffix}",
            name=f"R1-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[:2], 16) % 80) + 10}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(real)
        self.db.commit()
        fr = svc.ensure_fabric_node_for_managed(self.db, real)
        # LLDP placeholder with same hostname key + seen mgmt IP.
        ph = svc.ensure_lldp_discovered_managed_ne(
            self.db,
            remote_name=f"R1-{suffix}",
            remote_ip=real.ip_address,
        )
        fp = svc.ensure_fabric_node_for_managed(self.db, ph)
        self.db.commit()
        # Edge hanging off placeholder should retarget to real.
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fr.id,
            b_node_id=fp.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        # Need a third node so edge isn't self-loop after merge… actually A=real B=placeholder
        # after absorb B→A becomes self-loop and edge is deleted. Use external peer.
        peer_ne = ManagedNE(
            id=f"peer-{suffix}",
            name=f"P-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[2:4], 16) % 80) + 100}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(peer_ne)
        self.db.commit()
        fpeer = svc.ensure_fabric_node_for_managed(self.db, peer_ne)
        edge2, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fp.id,
            b_node_id=fpeer.id,
            a_port="Gi1/0",
            b_port="Gi1/1",
            source="lldp",
        )
        self.db.commit()
        edge2_id = edge2.id

        out = svc.merge_duplicate_fabric_nodes(self.db)
        self.assertGreaterEqual(out["merged"], 1)
        self.assertGreaterEqual(out.get("placeholders_removed", 0), 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, fp.id))
        self.assertIsNone(self.db.get(ManagedNE, ph.id))
        # Edge from placeholder→peer should now be real→peer.
        moved = self.db.get(TopoFabricEdge, edge2_id)
        self.assertIsNotNone(moved)
        assert moved is not None
        ends = {moved.a_node_id, moved.b_node_id}
        self.assertEqual(ends, {fr.id, fpeer.id})

        self.db.delete(real)
        self.db.delete(peer_ne)
        self.db.commit()

    def test_list_fabric_edges_missing_filter_and_names(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        svc._apply_missing_and_purge(
            self.db, scanned_ok={fa.id}, touched_edge_ids=set()
        )
        self.db.commit()

        missing = svc.list_fabric_edges(self.db, status="missing", page=1, page_size=50)
        self.assertGreaterEqual(missing["total"], 1)
        hit = next(i for i in missing["items"] if i["id"] == edge.id)
        self.assertEqual(hit["status"], "missing")
        self.assertTrue(hit["a_name"] or hit["b_name"])
        self.assertGreaterEqual(int((hit.get("attrs") or {}).get("miss_count") or 0), 1)

        by_kw = svc.list_fabric_edges(
            self.db, keyword=ne_a.name[:6], status="missing", page=1, page_size=50
        )
        self.assertTrue(any(i["id"] == edge.id for i in by_kw["items"]))

        active = svc.list_fabric_edges(self.db, status="active", page=1, page_size=50)
        self.assertFalse(any(i["id"] == edge.id for i in active["items"]))

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_edge_missing_after_one_absent_cycle(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()

        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.assertEqual(newly, 1)
        self.assertEqual(purged, 0)
        self.db.refresh(edge)
        self.assertEqual(edge.status, "missing")
        self.assertEqual(int((edge.attrs or {}).get("miss_count") or 0), 1)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_edge_purge_after_four_missing_cycles(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        edge_id = edge.id
        self.db.commit()

        for cycle in range(1, 5):
            newly, purged = svc._apply_missing_and_purge(
                self.db,
                scanned_ok={fa.id},
                touched_edge_ids=set(),
            )
            self.db.commit()
            if cycle < 4:
                self.assertEqual(purged, 0)
                row = self.db.get(TopoFabricEdge, edge_id)
                self.assertIsNotNone(row)
                self.assertEqual(int((row.attrs or {}).get("miss_count") or 0), cycle)
            else:
                self.assertEqual(purged, 1)
                self.assertIsNone(self.db.get(TopoFabricEdge, edge_id))

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_peer_replace_marks_old_edge_missing(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        ne_c = ManagedNE(
            id=f"nec-{suffix}",
            name=f"C-{suffix}",
            ip_address=f"198.51.100.{(int(suffix[4:6], 16) % 80) + 50}",
            vendor="Cisco",
            device_type="cisco_ios",
        )
        self.db.add(ne_c)
        self.db.commit()
        fc = svc.ensure_fabric_node_for_managed(self.db, ne_c)
        self.db.commit()

        old, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        new, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fc.id,
            a_port="Gi0/0",
            b_port="Gi0/2",
            source="lldp",
        )
        self.db.commit()

        handled = svc._mark_replaced_port_peers(
            self.db,
            self_id=fa.id,
            local_port="Gi0/0",
            peer_id=fc.id,
            new_edge_id=new.id,
        )
        self.db.commit()
        self.assertIn(old.id, handled)
        self.db.refresh(old)
        self.assertEqual(old.status, "missing")
        self.assertEqual((old.attrs or {}).get("replaced_by_edge_id"), new.id)
        self.assertEqual(int((old.attrs or {}).get("miss_count") or 0), 1)

        # Same-job: replaced id in touched → no second miss bump.
        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids={new.id, *handled},
        )
        self.db.commit()
        self.assertEqual(newly, 0)
        self.assertEqual(purged, 0)
        self.db.refresh(old)
        self.assertEqual(int((old.attrs or {}).get("miss_count") or 0), 1)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.delete(ne_c)
        self.db.commit()

    def test_edge_reactivate_clears_miss_attrs(self) -> None:
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        svc._apply_missing_and_purge(
            self.db, scanned_ok={fa.id}, touched_edge_ids=set()
        )
        self.db.commit()
        self.db.refresh(edge)
        self.assertEqual(edge.status, "missing")

        edge2, action = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        self.assertEqual(action, "updated")
        self.assertEqual(edge2.id, edge.id)
        self.assertEqual(edge2.status, "active")
        attrs = edge2.attrs or {}
        self.assertNotIn("miss_count", attrs)
        self.assertNotIn("first_missing_at", attrs)
        self.assertNotIn("replaced_by_edge_id", attrs)

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_scan_fail_does_not_mark_missing(self) -> None:
        """Endpoint not in scanned_ok (SSH/parse fail) → leave edge active."""
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()

        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok=set(),  # scan failed — nothing judged
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.assertEqual(newly, 0)
        self.assertEqual(purged, 0)
        self.db.refresh(edge)
        self.assertEqual(edge.status, "active")

        # Manual edges never auto-missing.
        manual, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi1/0",
            b_port="Gi1/1",
            source="manual",
        )
        self.db.commit()
        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.db.refresh(manual)
        self.assertEqual(manual.status, "active")
        self.assertEqual(manual.source, "manual")

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_ume_edge_not_marked_missing_by_lldp_miss(self) -> None:
        """Valid LLDP scan on endpoint must not red-line UME-authority edges."""
        suffix = uuid4().hex[:8]
        fa, fb, ne_a, ne_b = self._pair_nodes(suffix)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="xxvgei-1/1/0/1",
            b_port="xxvgei-1/1/0/2",
            source="ume",
        )
        self.db.commit()
        self.assertEqual(edge.source, "ume")

        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.assertEqual(newly, 0)
        self.assertEqual(purged, 0)
        self.db.refresh(edge)
        self.assertEqual(edge.status, "active")
        self.assertEqual(edge.source, "ume")

        # Dual provenance still UME-protected; LLDP mark is cleared.
        edge.attrs = {"sources": ["lldp", "ume"]}
        edge.source = "ume"
        self.db.commit()
        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.assertEqual(newly, 0)
        self.db.refresh(edge)
        self.assertEqual(edge.status, "active")
        self.assertEqual(edge.source, "ume")
        self.assertNotIn("lldp", (edge.attrs or {}).get("sources", []))
        self.assertIn("ume", (edge.attrs or {}).get("sources", []))

        # Pure LLDP on same node still miss-eligible.
        lldp_edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        newly, purged = svc._apply_missing_and_purge(
            self.db,
            scanned_ok={fa.id},
            touched_edge_ids=set(),
        )
        self.db.commit()
        self.assertEqual(newly, 1)
        self.db.refresh(lldp_edge)
        self.assertEqual(lldp_edge.status, "missing")
        self.db.refresh(edge)
        self.assertEqual(edge.status, "active")

        self.db.delete(ne_a)
        self.db.delete(ne_b)
        self.db.commit()

    def test_delete_managed_purges_orphan_fabric_and_edges(self) -> None:
        """Managed-only delete → detach then purge fabric node + incident edges."""
        from netx_api import ne_service
        from netx_api.topology_inventory_lifecycle import reconcile_dangling_fabric_links

        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"detach-{suffix}",
            name=f"DET-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.66.{(int(suffix[:2], 16) % 200) + 1}.1",
        )
        self.db.add(ne)
        self.db.commit()
        region_id = self._region(f"Detach-{suffix}")
        view_id = self._physical_view(region_id).id
        graph = svc.add_nodes_to_view(
            self.db, view_id, ViewNodesAdd(managed_ne_ids=[ne.id])
        )
        fid = graph.nodes[0].fabric_node_id
        peer = TopoFabricNode(
            id=f"peer-{suffix}",
            name=f"PEER-{suffix}",
            ip=f"10.66.{(int(suffix[:2], 16) % 200) + 1}.2",
            ume_ne_id=f"ume-peer-{suffix}",
        )
        self.db.add(peer)
        self.db.commit()
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fid,
            b_node_id=peer.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="lldp",
        )
        self.db.commit()
        edge_id = str(edge.id)

        ne_service.delete_managed_ne(self.db, ne.id)
        self.db.expire_all()

        self.assertIsNone(self.db.get(TopoFabricNode, fid))
        self.assertIsNone(self.db.get(TopoFabricEdge, edge_id))
        self.assertEqual(
            self.db.query(TopoViewNode)
            .filter(TopoViewNode.view_id == view_id, TopoViewNode.fabric_node_id == fid)
            .count(),
            0,
        )
        # Peer still UME-linked — not purged.
        self.assertIsNotNone(self.db.get(TopoFabricNode, peer.id))

        # Historical dangling ref → detach then purge (fully orphaned).
        ghost_id = f"ghost-{suffix}"
        dangling = TopoFabricNode(
            id=f"dang-{suffix}",
            managed_ne_id=ghost_id,
            name=f"DANG-{suffix}",
            ip="10.66.9.9",
        )
        self.db.add(dangling)
        self.db.commit()
        stats = reconcile_dangling_fabric_links(self.db)
        self.db.commit()
        self.db.expire_all()
        self.assertGreaterEqual(int(stats["detached_managed_nodes"]), 1)
        self.assertGreaterEqual(int(stats["purged_orphans"]), 1)
        self.assertIsNone(self.db.get(TopoFabricNode, dangling.id))

    def test_both_bound_keeps_fabric_until_fully_orphaned(self) -> None:
        from netx_api import ne_service
        from netx_api.topology_inventory_lifecycle import detach_fabric_from_ume

        suffix = uuid4().hex[:8]
        ne = ManagedNE(
            id=f"both-m-{suffix}",
            name=f"BOTH-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.67.{(int(suffix[:2], 16) % 200) + 1}.1",
        )
        self.db.add(ne)
        self.db.commit()
        fab = svc.ensure_fabric_node_for_managed(self.db, ne)
        fab.ume_ne_id = f"ume-both-{suffix}"
        self.db.commit()
        peer = TopoFabricNode(
            id=f"both-peer-{suffix}",
            name=f"BP-{suffix}",
            ip=f"10.67.{(int(suffix[:2], 16) % 200) + 1}.2",
            ume_ne_id=f"ume-bp-{suffix}",
        )
        self.db.add(peer)
        self.db.commit()
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fab.id,
            b_node_id=peer.id,
            a_port="Gi1/0",
            b_port="Gi1/1",
            source="ume",
        )
        self.db.commit()
        edge_id = str(edge.id)
        fab_id = str(fab.id)
        ume_id = str(fab.ume_ne_id)

        ne_service.delete_managed_ne(self.db, ne.id)
        self.db.expire_all()
        kept = self.db.get(TopoFabricNode, fab_id)
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertFalse(str(kept.managed_ne_id or "").strip())
        self.assertEqual(str(kept.ume_ne_id or "").strip(), ume_id)
        self.assertIsNotNone(self.db.get(TopoFabricEdge, edge_id))

        detach_fabric_from_ume(self.db, [ume_id])
        self.db.commit()
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, fab_id))
        self.assertIsNone(self.db.get(TopoFabricEdge, edge_id))

    def test_ume_only_detach_purges_orphan(self) -> None:
        from netx_api.topology_inventory_lifecycle import detach_fabric_from_ume

        suffix = uuid4().hex[:8]
        ume_id = f"ume-only-{suffix}"
        fab = TopoFabricNode(
            id=f"uo-{suffix}",
            name=f"UO-{suffix}",
            ip="10.68.0.1",
            ume_ne_id=ume_id,
        )
        peer = TopoFabricNode(
            id=f"uo-peer-{suffix}",
            name=f"UOP-{suffix}",
            ip="10.68.0.2",
            ume_ne_id=f"ume-uop-{suffix}",
        )
        self.db.add(fab)
        self.db.add(peer)
        self.db.commit()
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fab.id,
            b_node_id=peer.id,
            a_port="Eth1",
            b_port="Eth2",
            source="ume",
        )
        self.db.commit()
        edge_id = str(edge.id)
        fab_id = str(fab.id)

        out = detach_fabric_from_ume(self.db, [ume_id])
        self.db.commit()
        self.db.expire_all()
        self.assertEqual(out["purged_orphans"], 1)
        self.assertGreaterEqual(out["edges_deleted"], 1)
        self.assertIsNone(self.db.get(TopoFabricNode, fab_id))
        self.assertIsNone(self.db.get(TopoFabricEdge, edge_id))
        self.assertIsNotNone(self.db.get(TopoFabricNode, peer.id))

    def test_fabric_reconcile_scheduler_once_sweeps_orphans(self) -> None:
        from netx_api.fabric_reconcile_scheduler import run_fabric_reconcile_once

        suffix = uuid4().hex[:8]
        orphan_id = f"gc-{suffix}"
        orphan = TopoFabricNode(
            id=orphan_id,
            name=f"GC-{suffix}",
            ip="10.69.0.1",
        )
        self.db.add(orphan)
        self.db.commit()

        stats = run_fabric_reconcile_once()
        self.assertGreaterEqual(int(stats.get("purged_orphans") or 0), 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, orphan_id))

    def test_delete_fabric_node_only_orphans_and_placeholders(self) -> None:
        from fastapi import HTTPException

        from netx_api.topology_inventory_lifecycle import delete_fabric_nodes

        suffix = uuid4().hex[:8]
        real = ManagedNE(
            id=f"real-{suffix}",
            name=f"REAL-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.77.{(int(suffix[:2], 16) % 200) + 1}.1",
            source="",
        )
        ph = ManagedNE(
            id=f"ph-{suffix}",
            name=f"PH-{suffix}",
            vendor="Other",
            device_type="generic",
            ip_address="",
            source=LLDP_DISCOVERED_NE_SOURCE,
        )
        self.db.add(real)
        self.db.add(ph)
        self.db.commit()
        fab_real = svc.ensure_fabric_node_for_managed(self.db, real)
        fab_ph = svc.ensure_fabric_node_for_managed(self.db, ph)
        orphan = TopoFabricNode(
            id=f"orp-{suffix}",
            name=f"ORP-{suffix}",
            ip="10.77.0.9",
        )
        ume_only = TopoFabricNode(
            id=f"ume-{suffix}",
            name=f"UME-{suffix}",
            ip="10.77.0.8",
            ume_ne_id=f"ume-ne-{suffix}",
        )
        self.db.add(orphan)
        self.db.add(ume_only)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            delete_fabric_nodes(self.db, [fab_real.id])
        self.assertEqual(ctx.exception.status_code, 400)

        with self.assertRaises(HTTPException) as ctx_ume:
            delete_fabric_nodes(self.db, [ume_only.id])
        self.assertEqual(ctx_ume.exception.status_code, 400)

        out = delete_fabric_nodes(self.db, [fab_ph.id, orphan.id])
        self.assertEqual(out["deleted"], 2)
        self.assertIsNone(self.db.get(TopoFabricNode, fab_ph.id))
        self.assertIsNone(self.db.get(TopoFabricNode, orphan.id))
        self.assertIsNotNone(self.db.get(TopoFabricNode, fab_real.id))
        self.assertIsNotNone(self.db.get(TopoFabricNode, ume_only.id))
        self.assertIsNotNone(self.db.get(ManagedNE, real.id))
        self.assertIsNotNone(self.db.get(ManagedNE, ph.id))

    def test_purge_placeholder_deletes_managed_and_edges(self) -> None:
        from fastapi import HTTPException

        from netx_api.topology_inventory_lifecycle import purge_placeholder_fabric_nodes

        suffix = uuid4().hex[:8]
        topo_ph = ManagedNE(
            id=f"topo-{suffix}",
            name=f"TOPO-{suffix}",
            vendor="Other",
            device_type="generic",
            ip_address="",
            source=TOPOLOGY_NE_SOURCE,
        )
        real = ManagedNE(
            id=f"real2-{suffix}",
            name=f"REAL2-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.78.{(int(suffix[:2], 16) % 200) + 1}.1",
            source="",
        )
        self.db.add(topo_ph)
        self.db.add(real)
        self.db.commit()
        fab_ph = svc.ensure_fabric_node_for_managed(self.db, topo_ph)
        fab_real = svc.ensure_fabric_node_for_managed(self.db, real)
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fab_ph.id,
            b_node_id=fab_real.id,
            a_port="Gi0/1",
            b_port="Gi0/2",
            source="manual",
        )
        self.db.commit()
        edge_id = str(edge.id)

        with self.assertRaises(HTTPException) as ctx:
            purge_placeholder_fabric_nodes(self.db, [fab_real.id])
        self.assertEqual(ctx.exception.status_code, 400)

        out = purge_placeholder_fabric_nodes(self.db, [fab_ph.id])
        self.assertEqual(out["deleted"], 1)
        self.assertEqual(out["managed_deleted"], 1)
        self.assertGreaterEqual(out["edges_deleted"], 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricNode, fab_ph.id))
        self.assertIsNone(self.db.get(ManagedNE, topo_ph.id))
        self.assertIsNone(self.db.get(TopoFabricEdge, edge_id))
        self.assertIsNotNone(self.db.get(TopoFabricNode, fab_real.id))
        self.assertIsNotNone(self.db.get(ManagedNE, real.id))

    def test_filter_bulk_add_layout_and_remove(self) -> None:
        from netx_api.topology_schemas import ViewMutationOut, ViewNodesRemove

        suffix = uuid4().hex[:8]
        region = self._region(f"Bulk-{suffix}")
        view_id = self._physical_view(region).id
        nodes = []
        for i in range(5):
            n = TopoFabricNode(
                id=f"bf-{suffix}-{i}",
                name=f"BJ-SW-{suffix}-{i}",
                ip=f"10.88.{i}.1",
                vendor="Cisco",
                role="access",
            )
            self.db.add(n)
            nodes.append(n)
        other = TopoFabricNode(
            id=f"bf-other-{suffix}",
            name=f"SH-SW-{suffix}",
            ip="10.89.0.1",
            vendor="Huawei",
            role="core",
        )
        self.db.add(other)
        self.db.commit()

        summary = svc.add_nodes_to_view(
            self.db,
            view_id,
            ViewNodesAdd(keyword="BJ-SW-", limit=3, offset=0, return_graph=False),
        )
        self.assertIsInstance(summary, ViewMutationOut)
        assert isinstance(summary, ViewMutationOut)
        self.assertEqual(summary.added, 3)
        self.assertEqual(summary.matched, 5)
        self.assertEqual(summary.next_offset, 3)
        self.assertTrue(summary.truncated)

        more = svc.add_nodes_to_view(
            self.db,
            view_id,
            ViewNodesAdd(keyword="BJ-SW-", limit=10, offset=3, return_graph=False),
        )
        assert isinstance(more, ViewMutationOut)
        self.assertEqual(more.added, 2)
        self.assertIsNone(more.next_offset)

        laid = svc.patch_view_positions(
            self.db,
            view_id,
            ViewPositionsPatch(layout="grid", keyword="BJ-SW-", origin_x=10, origin_y=20, return_graph=False),
        )
        assert isinstance(laid, ViewMutationOut)
        self.assertEqual(laid.updated, 5)

        removed = svc.remove_view_nodes(
            self.db,
            view_id,
            body=ViewNodesRemove(keyword=f"BJ-SW-{suffix}-1", return_graph=False),
        )
        assert isinstance(removed, ViewMutationOut)
        self.assertGreaterEqual(removed.removed, 1)
        self.assertLess(removed.view_node_count, 5)

    def test_delete_fabric_edge_removes_from_view_graph(self) -> None:
        suffix = uuid4().hex[:8]
        ne_a = ManagedNE(
            id=f"dea-{suffix}",
            name=f"DEA-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.77.{(int(suffix[:2], 16) % 200) + 1}.1",
            source="manual",
        )
        ne_b = ManagedNE(
            id=f"deb-{suffix}",
            name=f"DEB-{suffix}",
            vendor="Cisco",
            device_type="cisco_ios",
            ip_address=f"10.77.{(int(suffix[:2], 16) % 200) + 1}.2",
            source="manual",
        )
        self.db.add(ne_a)
        self.db.add(ne_b)
        self.db.commit()
        fa = svc.ensure_fabric_node_for_managed(self.db, ne_a)
        fb = svc.ensure_fabric_node_for_managed(self.db, ne_b)
        self.db.commit()
        edge, _ = svc.upsert_fabric_edge(
            self.db,
            a_node_id=fa.id,
            b_node_id=fb.id,
            a_port="Gi0/0",
            b_port="Gi0/1",
            source="manual",
        )
        self.db.commit()
        region_id = self._region(f"DelEdge-{suffix}")
        view_id = self._physical_view(region_id).id
        svc.add_nodes_to_view(
            self.db, view_id, ViewNodesAdd(managed_ne_ids=[ne_a.id, ne_b.id])
        )
        graph = svc.get_view_graph(self.db, view_id)
        self.assertTrue(any(e.id == edge.id for e in graph.edges))
        self.assertTrue(any(n.managed_source == "manual" for n in graph.nodes))

        edge_id = edge.id
        out = svc.delete_fabric_edges(self.db, [edge_id])
        self.assertEqual(out["deleted"], 1)
        self.db.expire_all()
        self.assertIsNone(self.db.get(TopoFabricEdge, edge_id))
        graph2 = svc.get_view_graph(self.db, view_id)
        self.assertFalse(any(e.id == edge_id for e in graph2.edges))

    def test_find_fabric_paths_prefers_shortest(self) -> None:
        """BFS should return the 1-hop path before a longer detour."""
        suffix = uuid4().hex[:8]
        nes = []
        for label, ip_tail in (("A", 1), ("B", 2), ("C", 3)):
            ne = ManagedNE(
                id=f"path-{suffix}-{label}",
                name=f"PATH-{label}-{suffix}",
                vendor="Cisco",
                device_type="cisco_ios",
                ip_address=f"198.51.100.{ip_tail}",
            )
            self.db.add(ne)
            nes.append(ne)
        self.db.commit()
        nodes = [svc.ensure_fabric_node_for_managed(self.db, ne) for ne in nes]
        self.db.commit()
        # Short: A—B ; Long: A—C—B
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[0].id,
            b_node_id=nodes[1].id,
            a_port="Gi0/0",
            b_port="Gi0/0",
            source="manual",
        )
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[0].id,
            b_node_id=nodes[2].id,
            a_port="Gi0/1",
            b_port="Gi0/0",
            source="manual",
        )
        svc.upsert_fabric_edge(
            self.db,
            a_node_id=nodes[2].id,
            b_node_id=nodes[1].id,
            a_port="Gi0/1",
            b_port="Gi0/1",
            source="manual",
        )
        self.db.commit()

        out = svc.find_fabric_paths(
            self.db,
            from_managed_ne_id=nes[0].id,
            to_managed_ne_id=nes[1].id,
            max_paths=2,
            max_hops=6,
        )
        self.assertEqual(out["path_count"], 2)
        self.assertEqual(out["detail"], "summary")
        self.assertEqual(out["paths"][0]["hops"], 1)
        self.assertEqual(out["paths"][1]["hops"], 2)
        self.assertEqual(len(out["paths"][0]["nodes"]), 2)
        self.assertNotIn("attrs", out["paths"][0]["nodes"][0])
        self.assertIn("label", out["paths"][0])
        self.assertIn("gi0/0", out["paths"][0]["label"].lower())

        full = svc.find_fabric_paths(
            self.db,
            from_managed_ne_id=nes[0].id,
            to_managed_ne_id=nes[1].id,
            max_paths=1,
            max_hops=6,
            detail="full",
        )
        self.assertEqual(full["detail"], "full")
        self.assertIn("attrs", full["paths"][0]["nodes"][0])


if __name__ == "__main__":
    unittest.main()
