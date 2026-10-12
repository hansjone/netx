"""Reference protection and input validation for compare/cutover (plan A2, A5)."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_migration import service as mig
from netx_api.biz_state import compare_service as svc
from netx_api.db import Base
from netx_api.models import (
    BizCompareJob, BizCompareTemplate, BizMigrationProject, BizMonitorTemplate,
    BizPortMapping, BizPortMappingRow, BizStateBatch, BizStateTask,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _template(db, tid="tpl"):
    db.add(BizCompareTemplate(id=tid, name=f"name-{tid}", metrics_json=[]))
    db.commit()


def _mapping(db, mid="map"):
    db.add(BizPortMapping(id=mid, name=f"map-{mid}"))
    db.add(BizPortMappingRow(mapping_id=mid, before_if="a", after_if="b"))
    db.commit()


def test_mapping_used_by_job_cannot_be_deleted(db):
    _mapping(db)
    db.add(BizCompareJob(id="j", name="job-1", template_id="tpl", mapping_id="map"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc.delete_mapping(db, "map")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "mapping_in_use"
    assert exc.value.detail["jobs"] == ["job-1"]
    assert db.get(BizPortMapping, "map") is not None


def test_mapping_used_by_project_cannot_be_deleted(db):
    _mapping(db)
    db.add(BizMigrationProject(id="p", name="proj-1", mapping_id="map"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc.delete_mapping(db, "map")
    assert exc.value.status_code == 409
    assert exc.value.detail["projects"] == ["proj-1"]
    assert "proj-1" in exc.value.detail["message"]


def test_unreferenced_mapping_is_deleted_with_rows(db):
    _mapping(db)
    svc.delete_mapping(db, "map")
    assert db.get(BizPortMapping, "map") is None
    assert db.query(BizPortMappingRow).count() == 0


def test_list_mappings_reports_reference_counts(db):
    _mapping(db)
    db.add_all([BizCompareJob(id="j1", template_id="tpl", mapping_id="map"),
                BizCompareJob(id="j2", template_id="tpl", mapping_id="map"),
                BizMigrationProject(id="p", name="p", mapping_id="map")])
    db.commit()
    (item,) = svc.list_mappings(db)
    assert (item["job_count"], item["project_count"]) == (2, 1)


def test_template_used_by_monitor_template_cannot_be_deleted(db):
    _template(db)
    db.add(BizMonitorTemplate(id="mt", name="monitor-1", compare_template_id="tpl"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc.delete_template(db, "tpl")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "template_in_use"
    assert exc.value.detail["monitor_templates"] == ["monitor-1"]
    assert exc.value.detail["jobs"] == []


def test_template_used_by_job_reports_job_names(db):
    _template(db)
    db.add(BizCompareJob(id="j", name="job-1", template_id="tpl"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc.delete_template(db, "tpl")
    assert exc.value.detail["jobs"] == ["job-1"]


def test_list_templates_counts_monitor_templates(db, monkeypatch):
    monkeypatch.setattr(svc, "ensure_default_templates", lambda db: None)
    monkeypatch.setattr(svc, "upgrade_builtin_split_sheets", lambda db: None)
    _template(db)
    db.add_all([BizMonitorTemplate(id="m1", compare_template_id="tpl"),
                BizMonitorTemplate(id="m2", compare_template_id="tpl")])
    db.commit()
    (item,) = svc.list_templates(db)
    assert item["monitor_template_count"] == 2


def test_job_with_deleted_mapping_is_rejected_not_silently_unmapped(db):
    _template(db)
    db.add(BizCompareJob(id="j", template_id="tpl", mapping_id="gone",
                         before_batch_id="b", after_batch_id="a"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc._validate_compare_job(db, "j")
    assert exc.value.status_code == 400
    assert exc.value.detail == "mapping_missing"


def _project_tasks(db):
    db.add_all([BizStateTask(id="old_p", source="managed", ne_id="ne-old", status="running"),
                BizStateTask(id="new_p", source="managed", ne_id="ne-new", status="running"),
                BizStateTask(id="other", source="managed", ne_id="ne-x", status="running")])
    db.add_all([BizStateBatch(id="old_ok", task_id="old_p", status="success"),
                BizStateBatch(id="new_ok", task_id="new_p", status="partial"),
                BizStateBatch(id="foreign", task_id="other", status="success"),
                BizStateBatch(id="old_failed", task_id="old_p", status="failed")])
    db.commit()


def _body(**extra):
    return {"name": "n", "old_task_id": "old_p", "new_task_id": "new_p", **extra}


@pytest.mark.parametrize("extra,code", [
    ({"old_baseline_batch_id": "foreign"}, "old_baseline_invalid"),
    ({"new_baseline_batch_id": "foreign"}, "new_baseline_invalid"),
    ({"old_baseline_batch_id": "old_failed"}, "old_baseline_invalid"),
    ({"new_baseline_batch_id": "old_ok"}, "new_baseline_invalid"),
])
def test_create_project_rejects_baseline_from_wrong_task(db, extra, code):
    _project_tasks(db)
    with pytest.raises(HTTPException) as exc:
        mig.create_project(db, _body(**extra))
    assert exc.value.status_code == 400
    assert exc.value.detail == code
    assert db.query(BizMigrationProject).count() == 0


def test_baseline_ownership_accepts_success_and_partial(db):
    _project_tasks(db)
    mig._validate_baseline_ownership(db, "old", "old_ok", "old_p")
    mig._validate_baseline_ownership(db, "new", "new_ok", "new_p")
    mig._validate_baseline_ownership(db, "old", "", "old_p")


# ---- H9: reads do not write; project lists do not fan out --------------------------------------

def _project(db, n, **extra):
    db.add(BizStateTask(id=f"to{n}", source="managed", ne_id=f"o{n}", status="running"))
    db.add(BizStateTask(id=f"tn{n}", source="managed", ne_id=f"n{n}", status="running"))
    db.add(BizStateBatch(id=f"bo{n}", task_id=f"to{n}", status="success"))
    db.add(BizStateBatch(id=f"bn{n}", task_id=f"tn{n}", status="success"))
    db.add(BizMigrationProject(id=f"p{n}", name=f"p{n}", old_task_id=f"to{n}", new_task_id=f"tn{n}",
                               old_baseline_batch_id=f"bo{n}", new_baseline_batch_id=f"bn{n}", **extra))


def _statements(db, work):
    from sqlalchemy import event

    seen: list[str] = []
    listener = lambda conn, cur, stmt, params, ctx, many: seen.append(stmt)  # noqa: E731
    event.listen(db.get_bind(), "before_cursor_execute", listener)
    try:
        work()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listener)
    return seen


def test_get_project_with_unbound_monitor_template_does_not_write(db):
    db.add_all([BizCompareTemplate(id="ct", name="ZTE status default", metrics_json=[]),
                BizMonitorTemplate(id="mt", name="默认割接监控", compare_template_id="ct")])
    _project(db, 1)
    db.commit()
    seen = _statements(db, lambda: mig.get_project(db, "p1"))
    assert not [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert db.get(BizMigrationProject, "p1").monitor_template_id == ""


def test_project_list_query_count_does_not_grow_with_projects(db):
    db.add_all([BizCompareTemplate(id="ct", name="c", metrics_json=[]),
                BizMonitorTemplate(id="mt", name="默认割接监控", compare_template_id="ct")])
    for n in range(3):
        _project(db, n, monitor_template_id="mt")
    db.commit()
    few = len(_statements(db, lambda: mig.list_projects(db)))
    db.expire_all()
    for n in range(3, 15):
        _project(db, n, monitor_template_id="mt")
    db.commit()
    db.expire_all()
    many = len(_statements(db, lambda: mig.list_projects(db)))
    assert many <= few + 2, (few, many)
