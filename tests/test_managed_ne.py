from __future__ import annotations

import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.config import settings
from netx_api.db import Base, get_db
from netx_api.main import app
from netx_api.models import CliConnectProfile, ManagedNE, UmeInventoryNE  # noqa: F401 — register table on Base
from netx_api.ne_connect import hostname_probe_command, parse_hostname_from_output
from netx_api.ne_crypto import decrypt_secret, encrypt_secret
from netx_api.cli_service import list_cli_targets
from netx_api.device_types import WEBCRT_DEVICE_TYPES, WEBCRT_NE_SOURCE
from netx_api.ne_service import (
    UME_SYNC_SOURCE,
    create_managed_ne,
    import_managed_ne,
    upsert_webcrt_managed_ne,
    upsert_webcrt_session_host,
)
from netx_api.ne_schemas import ManagedNeCreate


class ManagedNeHostnameParseTests(unittest.TestCase):
    def test_huawei_sysname(self):
        out = " sysname PE-CORE-01\n"
        self.assertEqual(parse_hostname_from_output("huawei", "Huawei", out), "PE-CORE-01")

    def test_juniper_hostname(self):
        out = "host-name ROUTER-A;\nname ROUTER-A\n"
        self.assertEqual(parse_hostname_from_output("juniper", "Juniper", out), "ROUTER-A")

    def test_zte_last_line(self):
        out = "line1\nZXR10-PE1#"
        self.assertEqual(parse_hostname_from_output("zte_zxros", "ZTE", out), "ZXR10-PE1#")

    def test_cisco_hostname(self):
        out = "hostname R2\nR2#"
        self.assertEqual(parse_hostname_from_output("cisco_ios", "Cisco", out), "R2")

    def test_probe_commands(self):
        self.assertIn("sysname", hostname_probe_command("huawei", "Huawei") or "")
        self.assertEqual(hostname_probe_command("zte_zxros", "ZTE"), None)
        self.assertEqual(
            hostname_probe_command("cisco_ios", "Cisco"),
            "show configuration | include hostname",
        )


class ManagedNeCryptoTests(unittest.TestCase):
    def setUp(self):
        self._orig = settings.credential_secret_key
        settings.credential_secret_key = Fernet.generate_key().decode()

    def tearDown(self):
        settings.credential_secret_key = self._orig

    def test_encrypt_roundtrip(self):
        enc = encrypt_secret("secret-pass")
        self.assertEqual(decrypt_secret(enc), "secret-pass")


class ManagedNeApiTests(unittest.TestCase):
    def setUp(self):
        self._orig_key = settings.credential_secret_key
        settings.credential_secret_key = Fernet.generate_key().decode()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ManagedNE.__table__.create(bind=self.engine, checkfirst=True)
        UmeInventoryNE.__table__.create(bind=self.engine, checkfirst=True)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self._session_patch = patch("netx_api.ne_connect.SessionLocal", self.Session)
        self._session_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self._session_patch.stop()
        settings.credential_secret_key = self._orig_key

    def test_crud_flow(self):
        r = self.client.post(
            "/v1/managed-ne",
            json={
                "name": "PE-01",
                "vendor": "ZTE",
                "device_type": "zte_zxros",
                "ip_address": "10.0.0.1",
                "port": 22,
                "protocol": "ssh",
                "username": "admin",
                "password": "pass123",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        ne_id = r.json()["id"]
        self.assertNotIn("password", r.json())

        r2 = self.client.get(f"/v1/managed-ne/{ne_id}")
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["ip_address"], "10.0.0.1")

        r3 = self.client.patch(f"/v1/managed-ne/{ne_id}", json={"name": "PE-01-upd"})
        self.assertEqual(r3.status_code, 200)
        self.assertEqual(r3.json()["name"], "PE-01-upd")

        r4 = self.client.get("/v1/managed-ne", params={"keyword": "10.0.0"})
        self.assertEqual(r4.status_code, 200)
        self.assertEqual(r4.json()["total"], 1)

        r4b = self.client.get("/v1/managed-ne", params={"keyword": "pe-01"})
        self.assertEqual(r4b.status_code, 200)
        self.assertEqual(r4b.json()["total"], 1)

        r5 = self.client.delete(f"/v1/managed-ne/{ne_id}")
        self.assertEqual(r5.status_code, 200)

    def test_list_keyword_case_insensitive(self):
        r = self.client.post(
            "/v1/managed-ne",
            json={
                "name": "Core-R1",
                "vendor": "Cisco",
                "device_type": "cisco_ios",
                "ip_address": "192.168.0.11",
                "username": "admin",
                "password": "pass123",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        ne_id = r.json()["id"]
        for kw in ("R1", "r1", "core-r1"):
            with self.subTest(keyword=kw):
                listed = self.client.get("/v1/managed-ne", params={"keyword": kw})
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()["total"], 1, listed.text)
        self.client.delete(f"/v1/managed-ne/{ne_id}")

    def test_list_allows_empty_and_short_keyword(self):
        listed = self.client.get("/v1/managed-ne")
        self.assertEqual(listed.status_code, 200)
        short_kw = self.client.get("/v1/managed-ne", params={"keyword": "r"})
        self.assertEqual(short_kw.status_code, 200)

    def test_list_allows_vendor_only_filter(self):
        created = self.client.post(
            "/v1/managed-ne",
            json={
                "name": "Agg-R2",
                "vendor": "Cisco",
                "device_type": "cisco_ios",
                "ip_address": "192.168.0.12",
                "username": "admin",
                "password": "pass123",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        ne_id = created.json()["id"]
        listed = self.client.get("/v1/managed-ne", params={"vendor": "Cisco"})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["total"], 1, listed.text)
        self.client.delete(f"/v1/managed-ne/{ne_id}")

    def test_create_without_crypto_key(self):
        settings.credential_secret_key = ""
        r = self.client.post(
            "/v1/managed-ne",
            json={
                "vendor": "ZTE",
                "device_type": "zte_zxros",
                "ip_address": "10.0.0.2",
                "username": "admin",
                "password": "x",
            },
        )
        self.assertEqual(r.status_code, 503, r.text)

    def test_delete_ume_sync_route_not_captured_by_ne_id(self):
        """DELETE /ume-sync must not match DELETE /{ne_id} with ne_id='ume-sync'."""
        db = self.Session()
        db.add(
            ManagedNE(
                ip_address="10.0.0.99",
                source=UME_SYNC_SOURCE,
                source_ref="ume-ne-1",
                tags="UME",
            )
        )
        db.commit()
        db.close()

        r = self.client.delete("/v1/managed-ne/ume-sync")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted"], 1)

        r2 = self.client.delete("/v1/managed-ne/ume-sync")
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["deleted"], 0)

    def test_ume_sync_prefers_host_name_for_display_name(self):
        db = self.Session()
        db.add(
            UmeInventoryNE(
                ne_id="ume-ne-100",
                ip_address="10.0.0.100",
                ne_name="Resource-Name-100",
                host_name="Host-Name-100",
                vendor="ZTE",
                ne_type="ZXR10",
            )
        )
        db.commit()
        db.close()

        synced = self.client.post("/v1/managed-ne/ume-sync")
        self.assertEqual(synced.status_code, 200, synced.text)

        listed = self.client.get("/v1/managed-ne", params={"keyword": "10.0.0.100"})
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["total"], 1, listed.text)
        self.assertEqual(listed.json()["items"][0]["name"], "Host-Name-100")

    @patch("netx_api.ne_connect._probe_device", return_value=("pass", "ok", None))
    def test_connect_test(self, _mock_probe):
        r = self.client.post(
            "/v1/managed-ne",
            json={
                "vendor": "Huawei",
                "device_type": "huawei",
                "ip_address": "10.0.0.3",
                "username": "admin",
                "password": "pass",
            },
        )
        ne_id = r.json()["id"]
        r2 = self.client.post("/v1/managed-ne/connect-test", json={"ids": [ne_id]})
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["submitted"], 1)


class ManagedNeServiceImportTests(unittest.TestCase):
    def setUp(self):
        self._orig = settings.credential_secret_key
        settings.credential_secret_key = Fernet.generate_key().decode()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ManagedNE.__table__.create(bind=self.engine, checkfirst=True)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        settings.credential_secret_key = self._orig

    def test_csv_import(self):
        csv = (
            "device_type,ip,username,password,port,protocol,name,vendor\n"
            "zte_zxros,10.1.1.1,u1,p1,22,ssh,NE-A,ZTE\n"
        ).encode("utf-8")
        result = import_managed_ne(self.db, csv, "devices.csv")
        self.assertEqual(result.inserted, 1)
        self.assertEqual(result.updated, 0)
        self.assertEqual(len(result.failed), 0)

    def test_csv_import_without_password(self):
        csv = (
            "device_type,ip,username,password,port,protocol,name,vendor\n"
            "zte_zxros,10.1.1.2,target-user,,22,ssh,NE-B,ZTE\n"
        ).encode("utf-8")
        result = import_managed_ne(self.db, csv, "devices.csv")
        self.assertEqual(result.inserted, 1)
        self.assertEqual(len(result.failed), 0)
        row = self.db.query(ManagedNE).filter(ManagedNE.ip_address == "10.1.1.2").one()
        self.assertEqual(row.username, "target-user")
        self.assertEqual(row.password_enc, "")


class ManagedNeCreateOptionalPasswordTests(unittest.TestCase):
    def setUp(self):
        self._orig = settings.credential_secret_key
        settings.credential_secret_key = Fernet.generate_key().decode()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ManagedNE.__table__.create(bind=self.engine, checkfirst=True)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        settings.credential_secret_key = self._orig

    def test_create_without_password(self):
        out = create_managed_ne(
            self.db,
            ManagedNeCreate(
                vendor="ZTE",
                device_type="zte_zxros",
                ip_address="10.9.9.9",
                username="target-user",
                password="",
            ),
        )
        self.assertEqual(out.username, "target-user")
        row = self.db.query(ManagedNE).filter(ManagedNE.ip_address == "10.9.9.9").one()
        self.assertEqual(row.password_enc, "")


class WebcrtUpsertAndTargetsTests(unittest.TestCase):
    def setUp(self):
        self._orig = settings.credential_secret_key
        settings.credential_secret_key = Fernet.generate_key().decode()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        ManagedNE.__table__.create(bind=self.engine, checkfirst=True)
        UmeInventoryNE.__table__.create(bind=self.engine, checkfirst=True)
        CliConnectProfile.__table__.create(bind=self.engine, checkfirst=True)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        settings.credential_secret_key = self._orig

    def test_webcrt_device_types_include_linux(self):
        self.assertIn("linux", WEBCRT_DEVICE_TYPES)
        self.assertIn("generic", WEBCRT_DEVICE_TYPES)
        self.assertIn("zte_zxros", WEBCRT_DEVICE_TYPES)

    def test_upsert_webcrt_create_update_reuse(self):
        created, action = upsert_webcrt_managed_ne(
            self.db,
            ManagedNeCreate(
                name="linux-1",
                vendor="Other",
                device_type="linux",
                ip_address="10.8.8.8",
                username="root",
                password="secret",
            ),
        )
        self.assertEqual(action, "created")
        row = self.db.query(ManagedNE).filter(ManagedNE.id == created.id).one()
        self.assertEqual(row.source, WEBCRT_NE_SOURCE)
        self.assertEqual(row.device_type, "linux")

        updated, action2 = upsert_webcrt_managed_ne(
            self.db,
            ManagedNeCreate(
                name="linux-1b",
                vendor="Other",
                device_type="linux_ssh",
                ip_address="10.8.8.8",
                username="root",
                password="secret2",
            ),
        )
        self.assertEqual(action2, "updated")
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.name, "linux-1b")
        self.assertEqual(updated.device_type, "linux")

        inv = create_managed_ne(
            self.db,
            ManagedNeCreate(
                vendor="ZTE",
                device_type="zte_zxros",
                ip_address="10.8.8.9",
                username="admin",
                password="p",
            ),
        )
        reused, action3 = upsert_webcrt_managed_ne(
            self.db,
            ManagedNeCreate(
                vendor="ZTE",
                device_type="zte_zxros",
                ip_address="10.8.8.9",
                username="admin",
                password="ignored",
            ),
        )
        self.assertEqual(action3, "reused")
        self.assertEqual(reused.id, inv.id)
        # Inventory row must not be rewritten as webcrt.
        keep = self.db.query(ManagedNE).filter(ManagedNE.id == inv.id).one()
        self.assertNotEqual(keep.source, WEBCRT_NE_SOURCE)

    def test_list_cli_targets_webcrt_source(self):
        upsert_webcrt_managed_ne(
            self.db,
            ManagedNeCreate(
                name="sess-a",
                vendor="Other",
                device_type="linux",
                ip_address="10.7.7.7",
                username="u",
                password="p",
            ),
        )
        create_managed_ne(
            self.db,
            ManagedNeCreate(
                vendor="ZTE",
                device_type="zte_zxros",
                ip_address="10.7.7.8",
                username="admin",
                password="p",
            ),
        )
        webcrt = list_cli_targets(self.db, source="webcrt", page=1, page_size=50)
        self.assertEqual(webcrt["total"], 1)
        self.assertEqual(webcrt["items"][0]["source"], "webcrt")
        self.assertEqual(webcrt["items"][0]["ip_address"], "10.7.7.7")
        self.assertTrue(webcrt["items"][0]["has_password"])
        self.assertIn("hop_enabled", webcrt["items"][0])
        self.assertFalse(webcrt["items"][0]["hop_enabled"])

        managed = list_cli_targets(self.db, source="managed", page=1, page_size=50)
        ips = {x["ip_address"] for x in managed["items"]}
        self.assertIn("10.7.7.8", ips)
        self.assertNotIn("10.7.7.7", ips)
        zte = next(x for x in managed["items"] if x["ip_address"] == "10.7.7.8")
        self.assertIn("hop_enabled", zte)

    def test_upsert_session_host_telnet_no_password(self):
        out, action = upsert_webcrt_session_host(
            self.db,
            name="tn",
            ip_address="10.6.6.6",
            port=23,
            protocol="telnet",
        )
        self.assertEqual(action, "created")
        row = self.db.query(ManagedNE).filter(ManagedNE.id == out.id).one()
        self.assertEqual(row.protocol, "telnet")
        self.assertEqual(row.password_enc, "")
        self.assertEqual(row.device_type, "generic")
        self.assertEqual(row.source, WEBCRT_NE_SOURCE)

    def test_upsert_session_host_ssh_unsaved_password(self):
        out, action = upsert_webcrt_session_host(
            self.db,
            name="ssh1",
            ip_address="10.6.6.7",
            port=22,
            protocol="ssh",
            username="root",
            password="ephemeral",
            save_password=False,
        )
        self.assertEqual(action, "created")
        row = self.db.query(ManagedNE).filter(ManagedNE.id == out.id).one()
        self.assertEqual(row.username, "root")
        self.assertEqual(row.password_enc, "")

        out2, action2 = upsert_webcrt_session_host(
            self.db,
            name="ssh1",
            ip_address="10.6.6.7",
            protocol="ssh",
            username="root",
            password="secret",
            save_password=True,
        )
        self.assertEqual(action2, "created")
        self.assertNotEqual(out2.id, out.id)
        self.assertEqual(out2.name, "ssh1 (1)")
        row2 = self.db.query(ManagedNE).filter(ManagedNE.id == out2.id).one()
        self.assertTrue(str(row2.password_enc or "").strip())

    def test_session_host_same_ip_name_suffix(self):
        a, _ = upsert_webcrt_session_host(
            self.db, ip_address="10.5.5.5", protocol="ssh", username="u", password="p", save_password=True
        )
        b, _ = upsert_webcrt_session_host(
            self.db, ip_address="10.5.5.5", protocol="ssh", username="u", password="p", save_password=True
        )
        c, _ = upsert_webcrt_session_host(
            self.db, ip_address="10.5.5.5", protocol="telnet"
        )
        self.assertEqual(a.name, "10.5.5.5")
        self.assertEqual(b.name, "10.5.5.5 (1)")
        self.assertEqual(c.name, "10.5.5.5 (2)")
        self.assertEqual(a.ip_address, b.ip_address)


if __name__ == "__main__":
    unittest.main()
