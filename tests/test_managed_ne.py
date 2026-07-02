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
from netx_api.models import ManagedNE  # noqa: F401 — register table on Base
from netx_api.ne_connect import hostname_probe_command, parse_hostname_from_output
from netx_api.ne_crypto import decrypt_secret, encrypt_secret
from netx_api.ne_service import create_managed_ne, import_managed_ne
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

    def test_list_requires_filter(self):
        listed = self.client.get("/v1/managed-ne")
        self.assertEqual(listed.status_code, 400)
        self.assertEqual(listed.json()["detail"], "managed_ne_filter_required")

    def test_list_rejects_short_keyword(self):
        listed = self.client.get("/v1/managed-ne", params={"keyword": "r"})
        self.assertEqual(listed.status_code, 400)
        self.assertEqual(listed.json()["detail"], "managed_ne_keyword_too_short")

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


if __name__ == "__main__":
    unittest.main()
