"""Exec job isolation and path tests: temporary files and in-memory ASGI only."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from urllib.parse import quote
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from netx_api import managed_ne_router as routes
from netx_api import ne_exec_jobs as jobs
from netx_api.auth_deps import AuthContext, require_user
from netx_api.db import get_db


JOB_ID = "a" * 32
OTHER_JOB_ID = "b" * 32


@pytest.fixture
def job_dir(tmp_path, monkeypatch):
    directory = tmp_path / "jobs"
    directory.mkdir()
    monkeypatch.setenv("NETX_NE_EXEC_JOB_DIR", str(directory))
    return directory


def write_record(directory, *, owner="alice", job_id=JOB_ID):
    record = {
        "job_id": job_id, "owner_user_id": owner, "status": "succeeded", "ne_count": 1,
        "result": {"ok": True, "output": "temporary-test-output"},
    }
    (directory / f"{job_id}.json").write_text(json.dumps(record), encoding="utf-8")
    return record


@pytest.mark.parametrize("job_id", [
    "../canary", r"..\canary", r"C:\temp\canary", r"\\server\share\canary",
    "/tmp/canary", "a" * 31, "a" * 33, "A" * 32,
    "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", " " + JOB_ID, JOB_ID + "\n", "not-a-job",
])
def test_invalid_ids_never_access_job_directory(job_id):
    with patch.object(jobs, "_jobs_dir") as directory:
        out = jobs.get_ne_exec_job(job_id, user_id="alice")
        assert out == {"ok": False, "error_code": "invalid_job_id", "error": "invalid_job_id"}
        with pytest.raises(ValueError, match="^invalid_job_id$"):
            jobs._job_path(job_id)
    directory.assert_not_called()
    assert job_id not in json.dumps(out)


def test_canary_outside_job_directory_cannot_be_read(job_dir):
    canary = job_dir.parent / "canary.json"
    canary.write_text(json.dumps({"status": "succeeded", "result": {"canary": "non-secret"}}), encoding="utf-8")
    for job_id in ("../canary", r"..\canary", str(canary.with_suffix(""))):
        out = jobs.get_ne_exec_job(job_id, user_id="alice", is_admin=True)
        assert out["error"] == "invalid_job_id"
        assert "result" not in out
        assert str(job_dir.parent) not in json.dumps(out)


def test_valid_id_symlink_cannot_escape_directory(job_dir):
    canary = job_dir.parent / "canary.json"
    canary.write_text(json.dumps({"job_id": JOB_ID, "owner_user_id": "alice", "status": "succeeded", "result": {"canary": "non-secret"}}), encoding="utf-8")
    link = job_dir / f"{JOB_ID}.json"
    try:
        link.symlink_to(canary)
    except (OSError, NotImplementedError):
        pytest.skip("Host does not permit temporary symlink creation")
    out = jobs.get_ne_exec_job(JOB_ID, user_id="alice", is_admin=True)
    assert out["error"] == "invalid_job_id"
    with pytest.raises(ValueError, match="^invalid_job_id$"):
        jobs._write_job({"job_id": JOB_ID, "owner_user_id": "alice"})
    assert json.loads(canary.read_text(encoding="utf-8"))["result"]["canary"] == "non-secret"


def test_resolved_candidate_must_stay_inside_job_directory(job_dir):
    """Exercise containment even on Windows hosts that cannot create symlinks."""
    from pathlib import Path

    original_resolve = Path.resolve
    candidate = job_dir / f"{JOB_ID}.json"
    outside = job_dir.parent / "canary.json"

    def resolve(path, *args, **kwargs):
        return outside if path == candidate else original_resolve(path, *args, **kwargs)

    with patch.object(Path, "resolve", resolve):
        assert jobs.get_ne_exec_job(JOB_ID, user_id="alice", is_admin=True)["error"] == "invalid_job_id"


def test_persisted_owner_is_required_and_other_user_is_denied(job_dir):
    write_record(job_dir)
    # Reader loads the on-disk record on each call; no in-memory owner fallback.
    assert jobs.get_ne_exec_job(JOB_ID, user_id="alice")["result"]["output"] == "temporary-test-output"
    denied = jobs.get_ne_exec_job(JOB_ID, user_id="bob")
    assert denied["error"] == "job_forbidden"
    assert "result" not in denied and "owner_user_id" not in denied
    assert jobs.get_ne_exec_job(JOB_ID, user_id="admin", is_admin=True)["ok"] is True
    record = json.loads((job_dir / f"{JOB_ID}.json").read_text(encoding="utf-8"))
    record["owner_user_id"] = "bob"
    (job_dir / f"{JOB_ID}.json").write_text(json.dumps(record), encoding="utf-8")
    assert jobs.get_ne_exec_job(JOB_ID, user_id="alice")["error"] == "job_forbidden"


@pytest.mark.parametrize("owner", [None, "", "   "])
def test_ownerless_legacy_records_are_admin_only(job_dir, owner):
    write_record(job_dir, owner=owner)
    assert jobs.get_ne_exec_job(JOB_ID, user_id="alice")["error"] == "job_forbidden"
    assert jobs.get_ne_exec_job(JOB_ID, user_id="admin", is_admin=True)["ok"] is True
    assert jobs.get_ne_exec_job(JOB_ID, user_id="", is_admin=True)["error"] == "job_forbidden"


def test_record_cannot_impersonate_another_job_id(job_dir):
    write_record(job_dir)
    record = json.loads((job_dir / f"{JOB_ID}.json").read_text(encoding="utf-8"))
    record["job_id"] = OTHER_JOB_ID
    (job_dir / f"{JOB_ID}.json").write_text(json.dumps(record), encoding="utf-8")
    assert jobs.get_ne_exec_job(JOB_ID, user_id="alice")["error"] == "job_not_found"


def test_counts_sum_mixed_batch_sources():
    args = {"targets": [{"ne_id": "a"}, {"ume_ne_id": "b"}], "ne_ids": ["c", "", None], "ume_ne_ids": ["d", "e"]}
    assert jobs.count_exec_ne_targets(args) == 5
    with patch.object(jobs, "async_min_ne_count", return_value=4):
        assert jobs.should_run_exec_async(args) is True


def test_combined_target_cap_is_enforced_before_scheduling(job_dir):
    with patch.object(jobs.threading, "Thread") as thread:
        out = jobs.start_ne_exec_job(kind="exec-batch", owner_user_id="alice", arguments={
            "ne_ids": [f"n{i}" for i in range(11)], "ume_ne_ids": [f"u{i}" for i in range(10)],
        }, runner=lambda: pytest.fail("Oversized batch must not execute"))
    assert out["error"] == "too_many_targets"
    assert out["max_targets"] == 20
    assert list(job_dir.iterdir()) == []
    thread.assert_not_called()


def test_twenty_mixed_targets_are_accepted_and_persisted(job_dir):
    ack = jobs.start_ne_exec_job(kind="exec-batch", owner_user_id="alice", arguments={
        "targets": [{"ne_id": f"t{i}"} for i in range(7)],
        "ne_ids": [f"n{i}" for i in range(6)], "ume_ne_ids": [f"u{i}" for i in range(7)],
    }, runner=lambda: {"ok": True, "summary": {"total": 20}, "results": []})
    assert ack["ok"] is True and ack["ne_count"] == 20
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        done = jobs.get_ne_exec_job(ack["job_id"], user_id="alice")
        if done.get("terminal"):
            break
        time.sleep(0.01)
    assert done["status"] == "succeeded"
    assert done["ne_count"] == 20
    assert done["result"]["summary"]["total"] == 20


def test_job_owner_cannot_be_empty(job_dir):
    with patch.object(jobs.threading, "Thread") as thread:
        out = jobs.start_ne_exec_job(kind="exec", owner_user_id="", arguments={"ne_id": "n"}, runner=lambda: {})
    assert out["error"] == "job_owner_required"
    assert list(job_dir.iterdir()) == []
    thread.assert_not_called()


@pytest.fixture
def api_client(job_dir, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    auth = [AuthContext(user=SimpleNamespace(id="alice", username="alice", role="user"), auth_via="api_token", scopes=frozenset({"ne:exec"}))]
    app.dependency_overrides[require_user] = lambda: auth[0]
    app.dependency_overrides[get_db] = lambda: MagicMock()
    monkeypatch.setattr(routes, "write_audit", lambda *args, **kwargs: None)
    # The fake DB context and exec results must never open a database or device.
    monkeypatch.setattr("netx_api.db.SessionLocal", MagicMock())
    monkeypatch.setattr(routes, "execute_managed_ne_commands", lambda *args, **kwargs: {"ok": True, "output": "mock-cli"})
    monkeypatch.setattr(routes, "execute_managed_ne_commands_batch", lambda *args, **kwargs: {"ok": True, "items": []})
    with TestClient(app) as client:
        yield client, auth


def test_asgi_owner_other_user_and_admin_access(api_client, job_dir):
    client, auth = api_client
    write_record(job_dir)
    path = f"/v1/managed-ne/exec-jobs/{JOB_ID}"
    assert client.get(path).json()["result"]["output"] == "temporary-test-output"
    auth[0].user.id = "bob"
    assert client.get(path).status_code == 403
    auth[0].user.role = "admin"
    assert client.get(path).status_code == 200
    auth[0].user.role = "user"
    auth[0].scopes = frozenset({"ne:exec", "admin:users"})
    assert client.get(path).status_code == 200


def test_asgi_scope_required_even_for_owner_and_admin(api_client, job_dir):
    client, auth = api_client
    write_record(job_dir)
    auth[0].user.role = "admin"
    auth[0].scopes = frozenset({"ne:read"})
    assert client.get(f"/v1/managed-ne/exec-jobs/{JOB_ID}").status_code == 403
    with patch.object(routes, "start_ne_exec_job") as start:
        response = client.post("/v1/managed-ne/exec-jobs", json={"ne_id": "n", "commands": ["show version"]})
    assert response.status_code == 403
    start.assert_not_called()


def test_asgi_invalid_windows_path_returns_structured_error(api_client, job_dir):
    client, _ = api_client
    response = client.get("/v1/managed-ne/exec-jobs/" + quote(r"..\canary", safe=""))
    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_job_id"
    assert str(job_dir) not in response.text


def test_asgi_create_persists_actor_and_mixed_count(api_client, job_dir):
    client, auth = api_client
    response = client.post("/v1/managed-ne/exec-jobs", json={
        "targets": [{"ne_id": "n1"}], "ne_ids": ["n2"], "ume_ne_ids": ["n3"],
        "commands": ["show version"], "owner_user_id": "forged-owner",
    })
    assert response.status_code == 200
    ack = response.json()
    assert ack["ok"] is True and ack["ne_count"] == 3
    job_id = ack["job_id"]
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        done = client.get(f"/v1/managed-ne/exec-jobs/{job_id}").json()
        if done.get("terminal"):
            break
        time.sleep(0.01)
    assert done["status"] == "succeeded"
    record = json.loads((job_dir / f"{job_id}.json").read_text(encoding="utf-8"))
    assert record["owner_user_id"] == "alice"
    assert done["ne_count"] == 3
    auth[0].user.id = "forged-owner"
    assert client.get(f"/v1/managed-ne/exec-jobs/{job_id}").status_code == 403
