"""NE management page: column filters, create/edit validation, batch and import fixes."""
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from netx_api.config import settings
from netx_api.db import Base, get_db
from netx_api.managed_ne_router import router
from netx_api.models import ManagedNE
from netx_api.ne_crypto import decrypt_secret, encrypt_secret
from netx_api.ne_service import import_managed_ne


@pytest.fixture
def engine():
    orig = settings.credential_secret_key
    settings.credential_secret_key = Fernet.generate_key().decode()
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    with Session(eng) as db:
        db.add_all([
            ManagedNE(id="m1", name="PE-1", ip_address="10.0.0.1", vendor="ZTE", device_type="zte_zxros",
                      username="u1", tags="core edge", connect_status="pass", source=""),
            ManagedNE(id="m2", name="", ip_address="10.0.0.2", vendor="Huawei", device_type="huawei",
                      username="u2", tags="edge", connect_status="fail", source="webcrt"),
            ManagedNE(id="m3", name="SW-3", ip_address="10.0.0.3", vendor="Cisco", device_type="cisco_ios",
                      username="u3", tags="", connect_status="unknown", source="lldp"),
            ManagedNE(id="m4", name="Lab_1", ip_address="10.0.0.4", vendor="Cisco", device_type="cisco_ios",
                      username="u4", tags="lab%x", connect_status="pass"),
        ])
        db.commit()
    yield eng
    eng.dispose()
    settings.credential_secret_key = orig


@pytest.fixture
def client(engine):
    app = FastAPI()
    app.include_router(router)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    with patch("netx_api.auth_deps.settings.auth_enabled", False), TestClient(app) as test_client:
        yield test_client


def _list(client, filters=None, **params):
    if filters:
        params["column_filters"] = json.dumps(filters)
    r = client.get("/v1/managed-ne", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _ids(body):
    return sorted(x["id"] for x in body["items"])


def test_column_filter_options_cover_full_dataset(client):
    opts = _list(client, filter_field="vendor", page_size=1)
    assert opts == {"field": "vendor", "options": ["Cisco", "Huawei", "ZTE"]}
    # Blank names fall back to the IP shown in the table; blank source shows as manual.
    assert "10.0.0.2" in _list(client, filter_field="name")["options"]
    assert _list(client, filter_field="source")["options"] == ["lldp", "manual", "webcrt"]


def test_column_filters_apply_before_pagination(client):
    body = _list(client, {"connectStatus": {"mode": "include", "values": ["pass"]}}, page_size=1)
    assert body["total"] == 2
    body = _list(client, {"vendor": {"mode": "exclude", "values": ["Cisco"]}})
    assert _ids(body) == ["m1", "m2"]
    body = _list(client, {"source": {"mode": "include", "values": ["manual"]}})
    assert _ids(body) == ["m1", "m4"]


def test_tags_filter_matches_individual_tags(client):
    assert _list(client, filter_field="tags")["options"] == ["", "core", "edge", "lab%x"]
    assert _ids(_list(client, {"tags": {"mode": "include", "values": ["edge"]}})) == ["m1", "m2"]
    assert _ids(_list(client, {"tags": {"mode": "include", "values": [""]}})) == ["m3"]
    assert _ids(_list(client, {"tags": {"mode": "exclude", "values": ["edge", ""]}})) == ["m4"]
    # LIKE wildcards inside a tag are literal.
    assert _ids(_list(client, {"tags": {"mode": "include", "values": ["lab%x"]}})) == ["m4"]
    # Tag options respect the other columns' filters.
    opts = _list(client, {"vendor": {"mode": "include", "values": ["Huawei"]}}, filter_field="tags")
    assert opts["options"] == ["edge"]


def test_invalid_filter_field_rejected(client):
    r = client.get("/v1/managed-ne", params={"filter_field": "password_enc"})
    assert r.status_code == 400


@pytest.mark.parametrize("ip", ["10.0.0.300", "a b", "http://x", "10.0.0.9:22"])
def test_create_rejects_bad_address(client, ip):
    r = client.post("/v1/managed-ne", json={"vendor": "ZTE", "device_type": "zte_zxros",
                                            "ip_address": ip, "username": "u"})
    assert r.status_code == 400 and r.json()["detail"] == "invalid_ip_address"


@pytest.mark.parametrize("ip", ["10.0.0.9", "2001:db8::1", "pe-01.example.net"])
def test_create_accepts_ip_or_hostname(client, ip):
    r = client.post("/v1/managed-ne", json={"vendor": "ZTE", "device_type": "zte_zxros",
                                            "ip_address": ip, "username": "u"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("device_type", ["linux", "generic"])
def test_create_accepts_form_device_types(client, device_type):
    r = client.post("/v1/managed-ne", json={"vendor": "Other", "device_type": device_type,
                                            "ip_address": "10.0.0.9", "username": "u"})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_create_rejects_bad_port(client, port):
    r = client.post("/v1/managed-ne", json={"vendor": "ZTE", "device_type": "zte_zxros",
                                            "ip_address": "10.0.0.9", "username": "u", "port": port})
    assert r.status_code == 422


def test_update_resets_status_when_login_changes_and_keeps_name(client):
    r = client.patch("/v1/managed-ne/m1", json={"remark": "note"})
    assert r.json()["connect_status"] == "pass"
    r = client.patch("/v1/managed-ne/m1", json={"port": 2222})
    assert r.json()["connect_status"] == "unknown"
    r = client.patch("/v1/managed-ne/m1", json={"name": "  "})
    assert r.json()["name"] == "10.0.0.1"


def test_batch_hop_requires_password_for_ne_without_one(client, engine):
    hop = {"hop_vendor": "zte", "hop_host": "1.1.1.1", "hop_username": "jump"}
    r = client.post("/v1/managed-ne/batch-hop", json={"ids": ["m1"], "hop": hop})
    assert r.status_code == 400 and r.json()["detail"].startswith("hop_password_required")
    with Session(engine) as db:
        db.get(ManagedNE, "m1").hop_password_enc = encrypt_secret("old")
        db.commit()
    # Blank password keeps the stored one.
    r = client.post("/v1/managed-ne/batch-hop", json={"ids": ["m1"], "hop": hop})
    assert r.status_code == 200, r.text
    with Session(engine) as db:
        row = db.get(ManagedNE, "m1")
        assert decrypt_secret(row.hop_password_enc) == "old"
        assert row.connect_status == "unknown"
    # A bastion that manages target auth may run without a hop password.
    bastion = {**hop, "hop_vendor": "bastion", "hop_target_auth_mode": "bastion_managed"}
    r = client.post("/v1/managed-ne/batch-hop", json={"ids": ["m2"], "hop": bastion})
    assert r.status_code == 200, r.text


def test_batch_account_resets_status(client, engine):
    r = client.post("/v1/managed-ne/batch-account", json={"ids": ["m1"], "account": {"username": "new"}})
    assert r.status_code == 200
    with Session(engine) as db:
        assert db.get(ManagedNE, "m1").connect_status == "unknown"


def test_import_keeps_password_skips_bad_rows_and_promotes_placeholder(engine):
    db = sessionmaker(bind=engine)()
    try:
        row = db.get(ManagedNE, "m1")
        row.password_enc = encrypt_secret("secret")
        db.commit()
        csv = (
            "device_type,ip,username,password,port,protocol,name,vendor,tags\n"
            "zte_zxros,10.0.0.1,u1,,22,ssh,PE-1,ZTE,core\n"   # blank password keeps stored one
            "zte_zxros,10.0.0.3,u3,,22,ssh,SW-3,ZTE,\n"       # LLDP placeholder becomes manual
            "zte_zxros,10.9.9.9,u,,70000,ssh,BAD,ZTE,\n"      # bad port
            "zte_zxros,999.1.1.1,u,,22,ssh,BAD,ZTE,\n"        # bad ip
            "zte_zxros,10.9.9.8,u,,22,ssh,NEW,ZTE,\n"
        ).encode("utf-8")
        result = import_managed_ne(db, csv, "devices.csv")
        assert (result.inserted, result.updated) == (1, 2)
        assert [(f.row, f.reason) for f in result.failed] == [(4, "invalid_port"), (5, "invalid_ip_address")]
        db.expire_all()
        assert decrypt_secret(db.get(ManagedNE, "m1").password_enc) == "secret"
        assert db.get(ManagedNE, "m3").source == ""
        assert db.query(ManagedNE).filter(ManagedNE.ip_address == "10.9.9.9").count() == 0
    finally:
        db.close()


def test_import_failure_mid_row_leaves_no_partial_ne(engine):
    db = sessionmaker(bind=engine)()
    try:
        csv = (
            "device_type,ip,username,password,port,protocol,name,vendor\n"
            "zte_zxros,10.7.7.7,u,pw,22,ssh,X,ZTE\n"
        ).encode("utf-8")
        with patch("netx_api.ne_service_import.encrypt_secret", side_effect=RuntimeError("boom")):
            result = import_managed_ne(db, csv, "devices.csv")
        assert (result.inserted, result.updated, len(result.failed)) == (0, 0, 1)
        assert db.query(ManagedNE).filter(ManagedNE.ip_address == "10.7.7.7").count() == 0
    finally:
        db.close()
