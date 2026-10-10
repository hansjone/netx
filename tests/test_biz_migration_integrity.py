"""Business continuity, wave scope, sample uncertainty and live-monitor regressions."""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.models import (BizCompareTemplate, BizMonitorTemplate, BizMigrationProject,
    BizMigrationBatch, BizMigrationRun, BizMigrationDiff, BizStateTask, BizStateBatch,
    BizStateBatchCommand, BizStateMetricRow)
from netx_api.timeutil import utcnow_naive
from netx_api.biz_migration import service as svc
from netx_api.biz_migration.auto_monitor import try_auto_monitor_for_task
from netx_api.biz_migration.evaluate import evaluate_metric_dual, parse_expect_set, _eval_field_op
from netx_api.biz_migration.validation import validate_monitor_rules


def evaluate(**changes):
    args = dict(metric_id="arp", sheet_id="arp", key_fields=["ip"], iface_fields=["port"],
                compare_fields=["mac", "port"], old_baseline_rows=[{"ip": "a", "mac": "good", "port": "old"}],
                old_current_rows=[], new_baseline_rows=[],
                new_current_rows=[{"ip": "a", "mac": "good", "port": "new"}],
                port_map={"old": "new"}, expect=parse_expect_set({"items": [{"metric_id": "arp", "key": "a"}]}),
                window_active=True, allow_mapping_scope=False)
    return evaluate_metric_dual(**{**args, **changes})


def test_target_content_checked_against_old_business():
    out = evaluate(new_current_rows=[{"ip": "a", "mac": "wrong", "port": "new"}])
    assert out["progress_ok"] == 0
    assert out["rows"][0]["rule_hit"] == "service_content_changed"
    assert out["rows"][0]["integrity_changes"][0]["field"] == "mac"


def test_content_normalization_and_ignore_follow_compare_template():
    out = evaluate(new_current_rows=[{"ip": "a", "mac": "GOOD", "port": "new"}],
                   field_rules=[{"field": "mac", "normalize": "upper"}])
    assert out["progress_ok"] == 1


def test_down_target_cannot_pass_permissive_success_rule():
    out = evaluate(new_current_rows=[{"ip": "a", "state": "down"}],
                   sheet_override={"status_fields": ["state"], "up_values": ["up"], "down_values": ["down"],
                                   "success": [{"old": ["removed"], "new": ["added"]}]})
    assert out["rows"][0]["verdict"] == "lost"
    assert out["rows"][0]["color"] == "red"


def test_mapping_does_not_expand_wave_selection():
    out = evaluate(old_baseline_rows=[{"ip": "a", "port": "old"}, {"ip": "b", "port": "old2"}],
                   new_current_rows=[{"ip": "a", "port": "new"}], compare_fields=[],
                   port_map={"old": "new", "old2": "new2"})
    assert out["progress_total"] == 1
    assert next(r for r in out["rows"] if r["old_key"] == "b")["color"] == "red"


def test_port_selection_covers_business_with_nonkey_interface():
    out = evaluate(expect=parse_expect_set({"ports": ["old"]}))
    assert out["progress_total"] == out["progress_ok"] == 1


@pytest.mark.parametrize("healthy", [True, False])
def test_previous_wave_remains_protected_without_changing_current_progress(healthy):
    out = evaluate(expect={}, previous_expect=parse_expect_set({"items": [{"metric_id": "arp", "key": "a"}]}),
                   new_current_rows=[{"ip": "a", "mac": "good", "port": "new"}] if healthy else [])
    assert out["progress_total"] == 0
    assert out["rows"][0]["verdict"] == ("migrated_previous" if healthy else "lost")
    assert out["anomaly"] == (0 if healthy else 1)


@pytest.mark.parametrize("value", [0, False])
def test_zero_false_are_not_blank_in_keys_or_conditions(value):
    assert _eval_field_op({"id": value}, "id", "eq", value)
    out = evaluate(old_baseline_rows=[{"ip": value}], new_current_rows=[{"ip": value}],
                   compare_fields=[], expect=parse_expect_set({"items": [{"metric_id": "arp", "key": [str(value).lower()]}]}))
    assert out["rows"][0]["old_key"] == str(value).lower()


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)() as session:
        now = utcnow_naive() - timedelta(seconds=1)
        sheet = {"metric_id": "arp", "sheet_id": "s", "key_fields": ["ip"], "compare_fields": ["mac"]}
        session.add(BizCompareTemplate(id="ct", name="cmp", metrics_json=[sheet]))
        session.add(BizMonitorTemplate(id="mt", name="mon", compare_template_id="ct",
                                      collect_metric_ids_json=["arp"], defaults_json={}, sheet_overrides_json=[]))
        for tid in ("op", "np", "oh", "nh"):
            session.add(BizStateTask(id=tid, ne_id=tid, ne_name=tid, purpose="cutover_hf" if tid.endswith("h") else ""))
        session.add(BizMigrationProject(id="p", name="p", old_task_id="op", new_task_id="np",
                    old_hf_task_id="oh", new_hf_task_id="nh", old_baseline_batch_id="ob", new_baseline_batch_id="nb",
                    monitor_template_id="mt", hf_interval_sec=60, status="active"))
        session.add(BizMigrationBatch(id="wave", project_id="p", batch_label="wave", status="active",
                    expect_set_json={"items": [{"metric_id": "arp", "sheet_id": "s", "key": "a"}]}))
        for i, (bid, tid) in enumerate((("ob", "op"), ("nb", "np"), ("oc", "oh"), ("nc", "nh"))):
            at = now - timedelta(milliseconds=100 * (4 - i))
            session.add(BizStateBatch(id=bid, task_id=tid, status="success", started_at=at, ended_at=at))
            session.add(BizStateBatchCommand(id="cmd-" + bid, batch_id=bid, metric_id="arp", parse_status="ok", raw_text="secret CLI payload"))
        for bid in ("ob", "nc"):
            session.add(BizStateMetricRow(id="row-" + bid, batch_id=bid, task_id="op" if bid == "ob" else "nh",
                        batch_command_id="cmd-" + bid, metric_id="arp", seq=0, data_json={"ip": "a", "mac": "good"}))
        session.commit()
        yield session


def test_collected_empty_baseline_can_pass_acceptance(db):
    finished = svc.finish_batch(db, "wave")
    assert finished["accept_summary"]["passed"]
    assert finished["run"]["summary"]["config_snapshot"]["expect_set"]


@pytest.mark.parametrize("problem", ["missing_receipt", "failed_receipt", "stale", "skew", "duplicate", "skipped", "unknown_scope"])
def test_uncertain_coverage_cannot_pass_acceptance(db, problem):
    if problem == "missing_receipt":
        db.delete(db.get(BizStateBatchCommand, "cmd-nb"))
    elif problem == "failed_receipt":
        db.get(BizStateBatchCommand, "cmd-nc").parse_status = "failed"
    elif problem in ("stale", "skew"):
        batch = db.get(BizStateBatch, "nc")
        batch.started_at = batch.ended_at = utcnow_naive() - timedelta(minutes=10)
    elif problem == "duplicate":
        db.add(BizStateMetricRow(id="dup", batch_id="nc", metric_id="arp", data_json={"ip": "a", "mac": "good"}))
    elif problem == "skipped":
        db.get(BizMigrationProject, "p").collect_metric_ids_json = ["other"]
    else:
        db.get(BizMigrationBatch, "wave").expect_set_json = {"items": [{"sheet_id": "deleted", "metric_id": "arp", "key": "a"}]}
    db.commit()
    assert not svc.finish_batch(db, "wave")["accept_summary"]["passed"]


def test_invalid_baseline_and_pinned_batch_do_not_fallback(db):
    with pytest.raises(HTTPException) as error:
        svc.run_evaluate(db, batch_id="wave", old_batch_id="nb")
    assert error.value.detail == "old_current_batch_task_mismatch"
    db.get(BizMigrationProject, "p").old_baseline_batch_id = "nc"
    db.commit()
    with pytest.raises(HTTPException):
        svc.finish_batch(db, "wave")
    assert db.get(BizMigrationBatch, "wave").status == "active"
    assert db.query(BizMigrationRun).count() == 0


def test_latest_failed_collection_does_not_reuse_previous_success(db):
    db.add(BizStateBatch(id="latest-failed", task_id="nh", status="failed", started_at=utcnow_naive(), ended_at=utcnow_naive()))
    db.commit()
    run = svc.run_evaluate(db, batch_id="wave")
    assert run["summary"]["current_missing_metrics"] == ["s"]
    assert not run["summary"]["coverage_complete"]


def test_target_older_than_source_is_uncertain_instead_of_false_loss(db):
    db.delete(db.get(BizStateMetricRow, "row-nc"))
    db.get(BizStateBatch, "oc").started_at = utcnow_naive()
    db.get(BizStateBatch, "oc").ended_at = utcnow_naive()
    db.commit()
    run = svc.run_evaluate(db, batch_id="wave")
    row = svc.list_run_diffs(db, run["id"])["items"][0]
    assert row["verdict"] == "awaiting_peer"
    assert run["summary"]["anomaly"] == 0
    assert not run["summary"]["coverage_complete"]


def test_auto_monitor_deduplicates_receipts_and_respects_closed_window(db):
    assert try_auto_monitor_for_task(db, "oh") == 1
    assert try_auto_monitor_for_task(db, "nh") == 0
    assert db.query(BizMigrationRun).one().purpose == "auto"
    db.get(BizMigrationBatch, "wave").status = "done"
    db.commit()
    assert try_auto_monitor_for_task(db, "oh") == 0


def test_two_active_waves_are_rejected(db):
    with pytest.raises(HTTPException) as error:
        svc.create_batch(db, "p", {"status": "active", "batch_label": "overlap"})
    assert error.value.detail == "another_batch_active"
    assert db.query(BizMigrationBatch).count() == 1


def test_sheets_with_same_key_keep_separate_red_tickets(db):
    run = svc.run_evaluate(db, batch_id="wave")
    for sid in ("one", "two"):
        db.add(BizMigrationDiff(id=sid, run_id=run["id"], metric_id="arp", color="red",
                               key_json={"sheet_id": sid, "match_old_key": "a"}, verdict="lost"))
    db.flush()
    tickets = svc._persist_red_tickets_from_run(db, project_id="p", batch_id="wave", run_id=run["id"])
    assert len(tickets) == 2
    assert {t.detail_json["sheet_id"] for t in tickets} == {"one", "two"}


def test_port_mapping_does_not_remap_business_key_with_same_text():
    out = evaluate(old_baseline_rows=[{"ip": "old", "port": "old"}],
                   new_current_rows=[{"ip": "old", "port": "new"}], compare_fields=["port"],
                   expect=parse_expect_set({"items": [{"metric_id": "arp", "key": "old"}]}))
    assert out["progress_ok"] == 1


def test_unmapped_same_name_target_is_not_used_as_mapped_target_evidence():
    out = evaluate(metric_id="interface_brief", key_fields=["port"], compare_fields=[],
        old_baseline_rows=[{"port": "old"}], new_current_rows=[{"port": "old"}],
        expect=parse_expect_set({"ports": ["old"]}))
    row = next(r for r in out["rows"] if r["in_expect"])
    assert row["verdict"] == "lost"
    assert row["new"] == {}
    assert row["new_key"] == ""
    assert row["match_new_key"] == "new"


def test_legacy_raw_interface_expect_uses_same_alias_normalization():
    out = evaluate(metric_id="interface_brief", key_fields=["port"],
        old_baseline_rows=[{"port": "GE-1"}], new_current_rows=[{"port": "GE-2"}],
        compare_fields=[], port_map={"gei-1": "gei-2"},
        expect=parse_expect_set({"ports": ["GE-1"]}),
        iface_normalize_rules=[{"from": "GE", "to": "gei"}])
    assert out["progress_ok"] == 1


def test_baseline_picker_matches_normalized_keys_but_displays_originals(db):
    ct = db.get(BizCompareTemplate, "ct")
    ct.metrics_json = [{"metric_id": "arp", "sheet_id": "s", "key_fields": ["ip", "port"], "iface_fields": ["port"]}]
    ct.iface_normalize_json = [{"from": "GE", "to": "gei"}]
    db.get(BizStateMetricRow, "row-ob").data_json = {"ip": 0, "port": "GE-1"}
    db.commit()
    item = svc.list_baseline_expect_objects(db, "p")["sheets"][0]["items"][0]
    assert item["keys"] == ["0", "gei-1"]
    assert item["label"] == "0|GE-1"
    assert parse_expect_set({"items": [{"metric_id": "arp", "key": item["keys"]}]})["arp"] == {"0\x1fgei-1"}


def test_empty_composite_segments_are_preserved_and_false_is_not_blank():
    assert parse_expect_set({"items": [{"metric_id": "x", "key": [0, "", False]}]})["x"] == {"0\x1f\x1ffalse"}


def test_missing_status_field_cannot_match_negative_success_condition():
    assert not _eval_field_op({"ip": "a"}, "state", "ne", "down")
    assert not _eval_field_op({"ip": "a"}, "state", "not_in", ["down"])
    assert _eval_field_op({"ip": "a"}, "state", "empty", None)


def test_stable_outside_inventory_is_counted_without_repeated_evidence_cards():
    row = {"ip": "a", "mac": "good", "port": "old"}
    out = evaluate(expect={}, old_current_rows=[row], new_current_rows=[])
    assert out["rows"] == []
    assert out["steady_outside"] == 1
    assert out["anomaly"] == 0


def test_removed_previous_wave_sheet_blocks_full_coverage_acceptance(db):
    db.add(BizMigrationBatch(id="prior", project_id="p", status="done",
        created_at=utcnow_naive() - timedelta(days=1),
        expect_set_json={"items": [{"sheet_id": "removed-sheet", "metric_id": "arp", "key": "old-customer"}]}))
    db.commit()
    finished = svc.finish_batch(db, "wave")
    assert not finished["accept_summary"]["passed"]
    assert finished["run"]["summary"]["uncovered_scope"] == ["removed-sheet"]


def test_split_sheets_share_loaded_rows_and_command_metadata(db):
    from unittest.mock import patch

    ct = db.get(BizCompareTemplate, "ct")
    sheet = ct.metrics_json[0]
    ct.metrics_json = [sheet, {**sheet, "sheet_id": "s2"}]
    for i in range(500):
        for bid in ("ob", "nc"):
            db.add(BizStateMetricRow(id=f"bulk-{bid}-{i}", batch_id=bid,
                batch_command_id="cmd-" + bid, metric_id="arp", data_json={"ip": f"bulk-{i}", "mac": "good"}))
    db.commit()
    with patch.object(svc, "_load_metric_rows", wraps=svc._load_metric_rows) as load, patch.object(svc, "_command_brief", wraps=svc._command_brief) as command:
        run = svc.run_evaluate(db, batch_id="wave")
    assert load.call_count == 4  # once for each (batch, metric), not once per sheet
    assert command.call_count == 2  # baseline + current metadata, independent of row count
    assert svc.list_run_diffs(db, run["id"], limit=1)["total"] == 1002


def test_wave_acceptance_rolls_back_whole_transaction_on_ticket_failure(db):
    from unittest.mock import patch

    with patch.object(svc, "_persist_red_tickets_from_run", side_effect=RuntimeError("write failed")):
        with pytest.raises(RuntimeError):
            svc.finish_batch(db, "wave")
    db.rollback()
    assert db.query(BizMigrationRun).count() == 0
    assert db.get(BizMigrationBatch, "wave").status == "active"


def test_command_metadata_is_cached_and_raw_cli_is_not_selected(db):
    statements = []
    def capture(_conn, _cursor, statement, *_args):
        statements.append(statement)
    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        run = svc.run_evaluate(db, batch_id="wave")
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    command_selects = [q for q in statements if q.startswith("SELECT") and "biz_state_batch_command" in q]
    assert all("raw_text" not in q for q in command_selects)
    assert run["summary"]["coverage_complete"]


def test_diff_filters_apply_before_pagination_and_escape_wildcards(db):
    run = svc.run_evaluate(db, batch_id="wave")
    db.add(BizMigrationDiff(id="outside", run_id=run["id"], metric_id="arp", seq=999, color="red",
                           key_json={"sheet_id": "outside"}, in_expect=False, search_text="a_100%"))
    db.commit()
    result = svc.list_run_diffs(db, run["id"], sheet_id="outside", color="red", limit=1)
    assert result["total"] == 1 and result["items"][0]["id"] == "outside"
    assert svc.list_run_diffs(db, run["id"], only_expect=True)["total"] == 1
    assert svc.list_run_diffs(db, run["id"], kw="_")["total"] == 1


@pytest.mark.parametrize("condition", [{"type": "value", "field": "state", "op": "typo", "value": "up"}, {"type": "presence", "value": "typo"}])
def test_invalid_correction_rules_fail_before_evaluation(condition):
    with pytest.raises(HTTPException):
        validate_monitor_rules({}, [{"metric_id": "arp", "success": [{"old_groups": [[condition]], "new": ["added"]}]}])
