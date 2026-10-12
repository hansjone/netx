"""Mapping pre-flight (B5), N:1 reporting (B7) and command-slice coverage (V2)."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_migration import service as mig
from netx_api.biz_state import compare_service as svc
from netx_api.biz_state.compare_quality import classify_sheet, covers_slice, sheet_slice
from netx_api.db import Base
from netx_api.models import (
    BizCompareTemplate, BizMigrationProject, BizMonitorTemplate, BizPortMapping, BizPortMappingRow,
    BizStateBatch, BizStateBatchCommand, BizStateMetricRow, BizStateTask,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


SHEET = {"sheet_id": "ifb", "metric_id": "iface_t", "key_fields": ["interface"], "iface_fields": ["interface"],
         "compare_fields": ["state"], "field_rules": [], "row_filters": []}


def _batches(db, before_ifaces, after_ifaces, *, receipts=True):
    db.add_all([BizStateTask(id="t1", source="managed", ne_id="a", vendor="ZTE"),
                BizStateTask(id="t2", source="managed", ne_id="b", vendor="ZTE"),
                BizStateBatch(id="b1", task_id="t1", status="success"),
                BizStateBatch(id="b2", task_id="t2", status="success"),
                BizCompareTemplate(id="tpl", name="t", metrics_json=[SHEET],
                                   iface_normalize_json=[{"from": "GE", "to": "gei"}])])
    for batch, names in (("b1", before_ifaces), ("b2", after_ifaces)):
        for i, name in enumerate(names):
            db.add(BizStateMetricRow(id=f"{batch}-{i}", batch_id=batch, metric_id="iface_t", seq=i,
                                     data_json={"interface": name, "state": "up"}))
        if receipts:
            db.add(BizStateBatchCommand(id=f"c-{batch}", batch_id=batch, metric_id="iface_t",
                                        profile_id="zte.x", parse_status="ok", row_count=len(names),
                                        raw_line_count=len(names) + 3))
    db.commit()


def _preflight(db, **extra):
    return svc.preflight_compare(db, {"template_id": "tpl", "before_batch_id": "b1", "after_batch_id": "b2", **extra})


def test_exact_parent_same_name_and_suspect_are_classified(db):
    _batches(db, ["GE0/1/0/1", "GE0/1/0/2.100", "GE0/1/0/3", "GE0/1/0/9"],
             ["gei0/2/0/1", "gei0/2/0/2.100", "gei0/1/0/3"])
    report = _preflight(db, mapping_rows=[{"before_if": "GE0/1/0/1", "after_if": "GE0/2/0/1"},
                                          {"before_if": "GE0/1/0/2", "after_if": "GE0/2/0/2"}])
    (sheet,) = report["sheets"]
    assert sheet["mapped_exact"] == ["gei0/1/0/1"]
    assert sheet["mapped_by_parent"] == ["gei0/1/0/2.100"]
    assert sheet["identity_same_name"] == ["gei0/1/0/3"]
    assert sheet["suspect_unmapped"] == ["gei0/1/0/9"]
    assert report["coverage"] == {"mapped": 2, "suspect_unmapped": 1, "identity_same_name": 1, "rate": 66.7}
    assert report["verdict"] == "risk" and "suspect_unmapped" in report["risk_reasons"]


def test_unsaved_rows_are_enough_and_clean_map_is_ready(db):
    _batches(db, ["GE0/1"], ["gei0/2"])
    report = _preflight(db, mapping_rows=[{"before_if": "GE0/1", "after_if": "GE0/2"}])
    assert report["verdict"] == "ready" and report["coverage"]["rate"] == 100.0
    assert report["mapping"]["unused_map_keys"] == []


def test_unused_keys_missing_targets_and_n_to_1_are_listed(db):
    _batches(db, ["GE0/1", "GE0/2"], ["gei0/9"])
    report = _preflight(db, mapping_rows=[
        {"before_if": "GE0/1", "after_if": "GE0/9"}, {"before_if": "GE0/2", "after_if": "GE0/9"},
        {"before_if": "GE0/77", "after_if": "GE0/88"}])
    m = report["mapping"]
    assert m["n_to_1_targets"] == [{"target": "gei0/9", "sources": ["gei0/1", "gei0/2"]}]
    assert m["unused_map_keys"] == ["gei0/77"]
    assert m["map_targets_missing_on_after"] == ["gei0/88"]
    assert report["verdict"] == "risk"


def test_duplicate_before_rows_are_reported(db):
    _batches(db, ["GE0/1"], ["gei0/2"])
    report = _preflight(db, mapping_rows=[{"before_if": "GE0/1", "after_if": "GE0/2"},
                                          {"before_if": "GE0/1", "after_if": "GE0/3"}])
    assert report["mapping"]["duplicate_before"][0]["dropped_to"] == "GE0/3"
    assert report["verdict"] == "risk"


def test_saved_mapping_id_is_used_when_no_rows_given(db):
    _batches(db, ["GE0/1"], ["gei0/2"])
    db.add_all([BizPortMapping(id="m", name="m"),
                BizPortMappingRow(mapping_id="m", before_if="GE0/1", after_if="GE0/2")])
    db.commit()
    assert _preflight(db, mapping_id="m")["coverage"]["mapped"] == 1


def test_missing_batch_is_404(db):
    _batches(db, ["GE0/1"], ["gei0/2"])
    with pytest.raises(HTTPException) as exc:
        svc.preflight_compare(db, {"template_id": "tpl", "before_batch_id": "nope", "after_batch_id": "b2"})
    assert exc.value.status_code == 404


def test_n_to_1_is_rejected_by_monitoring_with_the_colliding_rows():
    with pytest.raises(HTTPException) as exc:
        mig._reject_ambiguous_mapping({"a": "x", "b": "x", "c": "y"})
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "mapping_target_ambiguous"
    assert exc.value.detail["n_to_1"] == [{"target": "x", "sources": ["a", "b"]}]
    assert "a, b -> x" in exc.value.detail["message"]
    mig._reject_ambiguous_mapping({"a": "x", "b": "y"})


def test_project_preflight_uses_the_portrait_baselines(db):
    _batches(db, ["GE0/1", "GE0/5"], ["gei0/2"])
    db.add_all([BizPortMapping(id="m", name="m"),
                BizPortMappingRow(mapping_id="m", before_if="GE0/1", after_if="GE0/2"),
                BizMonitorTemplate(id="mt", name="mt", compare_template_id="tpl"),
                BizMigrationProject(id="p", name="p", mapping_id="m", monitor_template_id="mt", old_task_id="t1",
                                    new_task_id="t2", old_baseline_batch_id="b1", new_baseline_batch_id="b2")])
    db.commit()
    report = mig.preflight_project(db, "p")
    assert report["sheets"][0]["suspect_unmapped"] == ["gei0/5"]
    assert report["verdict"] == "risk"


# ---- V2: slices ---------------------------------------------------------------------------

def receipts(*profile_ids, status="ok"):
    return [{"profile_id": p, "parse_status": status, "row_count": 1, "raw_line_count": 4} for p in profile_ids]


def test_sheet_slice_reads_only_top_level_eq_filters():
    s = {"row_filters": [{"op": "eq", "field": "afi", "value": "IPv4"},
                         {"op": "contains", "field": "x", "value": "y"},
                         {"any": [{"op": "eq", "field": "z", "value": "1"}]}]}
    assert sheet_slice(s) == {"afi": "ipv4"}


def test_missing_afi_command_means_not_collected_not_lost():
    only_vpnv4 = receipts("zte.bgp_vpnv4_summary")
    both = receipts("zte.bgp_vpnv4_summary", "zte.bgp_ipv4_summary")
    state, reasons = classify_sheet(before=both, after=only_vpnv4, before_rows=3, after_rows=0,
                                    wanted_slice={"afi": "ipv4"})
    assert state == "not_collected" and reasons == ["after:slice_not_collected"]
    ok, _ = classify_sheet(before=both, after=both, before_rows=3, after_rows=3, wanted_slice={"afi": "ipv4"})
    assert ok == "ok"


def test_command_without_declared_slice_covers_everything():
    assert covers_slice(receipts("custom.unknown"), {"afi": "ipv4"})
    assert not covers_slice(receipts("zte.bgp_vpnv6_summary"), {"afi": "ipv4"})
    assert not covers_slice(receipts("zte.bgp_ipv4_summary", status="failed"), {"afi": "ipv4"})


def test_command_coverage_compares_what_commands_produce(db):
    _batches(db, ["GE0/1"], ["gei0/2"])
    db.add(BizStateBatchCommand(id="x", batch_id="b1", metric_id="bgp_peer", profile_id="zte.bgp_ipv4_summary",
                                parse_status="ok"))
    db.commit()
    cov = _preflight(db, mapping_rows=[])["command_coverage"]
    assert cov["only_before"] == ["bgp_peer{afi=ipv4}"]
