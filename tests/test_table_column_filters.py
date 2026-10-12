"""Column filters must cover the full dataset and run before pagination."""
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from netx_api.cli_router import router as cli_router
from netx_api.config_sync_router import router as config_router
from netx_api.db import Base, get_db
from netx_api.lldp_collect_router import router as lldp_router
from netx_api.models import (
    ManagedNE, NeConfigSnapshot, TopoDiscoverJob, TopoDiscoverJobItem,
    TopoFabricEdge, TopoFabricNode, UmeAlarmCurrent, UmeInventoryNE,
)
from netx_api.topology_router import router as topology_router
from netx_api.ume_alarms_router import router as alarm_router


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            ManagedNE(id="m1", name="Device A", ip_address="10.0.0.1", vendor="Huawei", source="manual"),
            ManagedNE(id="m2", name="Device B", ip_address="10.0.0.2", vendor="Cisco", source="manual"),
            UmeInventoryNE(ne_id="u1", host_name="Fallback Host", ne_type="Router"),
            NeConfigSnapshot(source="managed", target_id="m1", ne_name="Device A", vendor="Huawei", plain_size=100),
            NeConfigSnapshot(source="ume", target_id="u1", ne_name="Device B", vendor="Cisco", plain_size=200),
            UmeAlarmCurrent(alarm_key="a1", ne_id="u1", host_name="", perceived_severity="major", is_cleared="false"),
            UmeAlarmCurrent(alarm_key="a2", host_name="Other Host", perceived_severity="minor", is_cleared="true"),
            TopoFabricNode(id="n1", name="Device A", ip="10.0.0.1", managed_ne_id="m1", level=1),
            TopoFabricNode(id="n2", name="Device B", ip="10.0.0.2", level=None),
            TopoFabricEdge(id="e1", a_node_id="n1", b_node_id="n2", a_port="p1", status="stale", attrs={"miss_count": 2}),
            TopoFabricEdge(id="e2", a_node_id="n1", b_node_id="n2", a_port="p2", status="active"),
            TopoDiscoverJob(id="j1", status="done", total=2, done=2),
            TopoDiscoverJob(id="j2", status="failed", total=1, done=1),
            TopoDiscoverJobItem(id="i1", job_id="j1", ne_name="Device A", ok=True, neighbors=2),
            TopoDiscoverJobItem(id="i2", job_id="j1", ne_name="Device B", ok=True, error="empty_cli_output"),
        ])
        db.commit()
    app = FastAPI()
    for router in (cli_router, config_router, topology_router, lldp_router, alarm_router):
        app.include_router(router)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    with patch("netx_api.auth_deps.settings.auth_enabled", False), TestClient(app) as test_client:
        yield test_client
    engine.dispose()


CASES = [
    ("/v1/cli/targets", {"source": "inventory"}, "name", "Device B", "items", "total"),
    ("/v1/config-sync/snapshots", {}, "vendor", "Cisco", "items", "total"),
    ("/v1/ume/alarms", {}, "hostName", "Fallback Host", "items", "total"),
    ("/v1/topology/fabric/nodes", {}, "level", "", "items", "total"),
    ("/v1/topology/fabric/edges", {}, "status", "missing", "items", "total"),
    ("/v1/topology/lldp-collect/jobs", {}, "status", "failed", "items", "total"),
    ("/v1/topology/lldp-collect/jobs/j1", {}, "status", "fail", "items", "items_total"),
]


@pytest.mark.parametrize("path,scope,field,value,items_key,total_key", CASES)
def test_options_and_selection_before_pagination(client, path, scope, field, value, items_key, total_key):
    options = client.get(path, params={**scope, "filter_field": field, "page_size": 1})
    assert options.status_code == 200, options.text
    assert value in options.json()["options"]
    assert len(options.json()["options"]) >= 2
    for mode, selected, expected_total in [("include", [value], 1), ("include", [], 0), ("exclude", [], len(client.get(path, params=scope).json()[items_key]))]:
        response = client.get(path, params={**scope, "page_size": 1, "column_filters": json.dumps({field: {"mode": mode, "values": selected}})})
        assert response.status_code == 200, response.text
        assert response.json()[total_key] == expected_total
        assert len(response.json()[items_key]) == min(expected_total, 1)
    # Exclusion is the complement of inclusion, including blank values.
    all_total = client.get(path, params=scope).json()[total_key]
    excluded = client.get(path, params={**scope, "column_filters": json.dumps({field: {"mode": "exclude", "values": [value]}})})
    assert excluded.json()[total_key] == all_total - 1


def test_multiple_columns_and_invalid_payload(client):
    path = "/v1/config-sync/snapshots"
    response = client.get(path, params={"column_filters": json.dumps({"source": {"mode": "include", "values": ["managed"]}, "vendor": {"mode": "include", "values": ["Cisco"]}})})
    assert response.status_code == 200
    assert response.json()["total"] == 0
    for payload in ("[]", "bad-json", '{"name":{"mode":"bad","values":[]}}', '{"name":{"mode":"include","values":[1]}}'):
        assert client.get(path, params={"column_filters": payload}).status_code == 400
    assert client.get(path, params={"filter_field": "nonexistent"}).status_code == 400


@pytest.mark.parametrize("path,field", [
    ("/v1/config-sync/snapshots", "size"), ("/v1/topology/fabric/nodes", "link"),
    ("/v1/topology/fabric/edges", "aSide"), ("/v1/topology/fabric/edges", "missCount"),
    ("/v1/topology/lldp-collect/jobs", "progress"), ("/v1/topology/lldp-collect/jobs/j1", "edges"),
])
def test_computed_column_values_roundtrip(client, path, field):
    response = client.get(path, params={"filter_field": field})
    assert response.status_code == 200, response.text
    options = response.json()["options"]
    assert options
    for value in options:
        filtered = client.get(path, params={"column_filters": json.dumps({field: {"mode": "include", "values": [value]}})})
        assert filtered.status_code == 200, filtered.text
        assert filtered.json().get("total", filtered.json().get("items_total")) > 0
