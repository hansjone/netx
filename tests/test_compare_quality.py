"""Sheet data quality (V1) and directional compare modes (V3)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_state import compare_service as svc
from netx_api.biz_state.compare_rules import explain_diff, values_equal
from netx_api.biz_state.identity_pipeline import build_pipeline
from netx_api.db import Base
from netx_api.models import BizStateBatch, BizStateBatchCommand, BizStateMetricRow, BizStateTask

SHEET = {"sheet_id": "peers", "metric_id": "bgp_peer", "key_fields": ["neighbor"], "iface_fields": [],
         "compare_fields": ["state"], "field_rules": [], "row_filters": []}


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add_all([BizStateTask(id="t", source="managed", ne_id="n"),
                     BizStateBatch(id="b1", task_id="t", status="success"),
                     BizStateBatch(id="b2", task_id="t", status="success")])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def collect(db, batch, rows, *, lines=None, status="ok", command=True):
    if command:
        db.add(BizStateBatchCommand(id=f"c-{batch}", batch_id=batch, metric_id="bgp_peer", profile_id="zte.x",
                                    parse_status=status, row_count=len(rows),
                                    raw_line_count=len(rows) + 2 if lines is None else lines))
    for i, peer in enumerate(rows):
        db.add(BizStateMetricRow(id=f"{batch}-{i}", batch_id=batch, metric_id="bgp_peer", seq=i,
                                 data_json={"neighbor": peer, "state": "up"}))
    db.commit()


def run(db):
    return svc._run_sheet(db, sheet=SHEET, before_batch_id="b1", after_batch_id="b2",
                          pipeline=build_pipeline(SHEET, iface_rules=[], port_map={}))["summary"]


def enrich(summary):
    sheet = {"sheet_id": "peers", "metric_id": "bgp_peer", "status": "done", "summary": summary}
    return svc._enrich_summary({k: summary.get(k, 0) for k in
                                ("added", "removed", "changed", "unchanged", "before_count", "after_count")}, [sheet])


def test_empty_against_empty_is_no_data_not_a_hundred_percent(db):
    collect(db, "b1", [], lines=0)
    collect(db, "b2", [], lines=0)
    summary = run(db)
    assert summary["data_state"] == "no_data"
    out = enrich(summary)
    assert out["pass_rate"] is None and out["sheet_cards"][0]["pass_rate"] is None
    assert out["ok"] is None


def test_unparsed_output_is_suspect_parse_and_leaves_the_verdict_open(db):
    collect(db, "b1", [f"p{i}" for i in range(10)])
    collect(db, "b2", [], lines=40)  # 40 CLI lines, 0 rows parsed
    summary = run(db)
    assert summary["data_state"] == "suspect_parse"
    assert summary["data_state_reasons"] == ["after:parsed_0_of_40_lines"]
    out = enrich(summary)
    assert out["sheet_cards"][0]["removed"] == 10  # still shown
    assert out["ok"] is None and out["suspect_sheets"] == 1


def test_side_without_any_command_is_not_collected_and_leaves_the_totals(db):
    collect(db, "b1", ["a", "b"])
    db.add(BizStateBatchCommand(id="other", batch_id="b2", metric_id="other_metric", profile_id="zte.y",
                                parse_status="ok", row_count=1, raw_line_count=1))
    db.commit()
    summary = run(db)
    assert summary["data_state"] == "not_collected"
    out = enrich(summary)
    assert out["sheet_cards"][0]["pass_rate"] is None
    assert out["fail_count"] == 0 and out["ok"] is None and out["unjudged_sheets"] == 1


def test_batches_without_receipts_are_trusted(db):
    collect(db, "b1", ["a"], command=False)
    collect(db, "b2", ["a"], command=False)
    summary = run(db)
    assert summary["data_state"] == "ok"
    assert enrich(summary)["pass_rate"] == 100.0


def test_normal_comparison_keeps_the_verdict(db):
    collect(db, "b1", ["a", "b"])
    collect(db, "b2", ["a"])
    out = enrich(run(db))
    assert out["ok"] is False and out["fail_count"] == 1 and out["pass_rate"] == 50.0


# ---- V3 -----------------------------------------------------------------------------------

NOT_WORSE = {"compare": "not_worse", "rank": ["up", "down"]}


def test_not_worse_accepts_recovery_and_rejects_degradation():
    assert values_equal("down", "up", rule=NOT_WORSE)
    assert values_equal("up", "up", rule=NOT_WORSE)
    assert not values_equal("up", "down", rule=NOT_WORSE)
    assert explain_diff("up", "down", rule=NOT_WORSE) == "worse: up→down"


def test_not_worse_falls_back_to_equality_for_unranked_values():
    assert not values_equal("up", "flapping", rule=NOT_WORSE)
    assert values_equal("flapping", "flapping", rule=NOT_WORSE)


def test_numeric_min_allows_growth_and_bounded_loss():
    rule = {"compare": "numeric_min", "tolerance": 5}
    assert values_equal("1000", "1200", rule=rule)
    assert values_equal("1000", "960", rule=rule)
    assert not values_equal("1000", "900", rule=rule)
    assert explain_diff("1000", "900", rule=rule) == "drop 10% > 5%"


def test_numeric_max_is_the_mirror_image():
    rule = {"compare": "numeric_max", "tolerance": 10}
    assert values_equal("100", "60", rule=rule)
    assert values_equal("100", "110", rule=rule)
    assert not values_equal("100", "150", rule=rule)
    assert explain_diff("100", "150", rule=rule) == "rise 50% > 10%"


def test_directional_modes_pass_validation_and_keep_their_rank():
    from netx_api.biz_state.compare_validation import validate_template_body

    sheet = {"metric_id": "m", "key_fields": ["k"], "compare_fields": ["s"],
             "field_rules": [{"field": "s", "compare": "not_worse", "rank": ["up", "down"]}]}
    validate_template_body({"metrics": [sheet]})
    normalized = svc._normalize_sheet(sheet)
    assert normalized["field_rules"][0]["rank"] == ["up", "down"]
    with pytest.raises(Exception):
        validate_template_body({"metrics": [{**sheet, "field_rules": [{"field": "s", "compare": "not_worse"}]}]})


def test_directional_compare_runs_end_to_end_and_blocks_sql(db):
    from tests.test_identity_pipeline import pg_db
    from netx_api.biz_state.compare_engine import compare_rows
    from netx_api.biz_state.compare_sql import sql_compare_skip_reason

    sheet = {**SHEET, "field_rules": [{"field": "state", **NOT_WORSE}]}
    out = compare_rows(before_rows=[{"neighbor": "a", "state": "down"}, {"neighbor": "b", "state": "up"}],
                       after_rows=[{"neighbor": "a", "state": "up"}, {"neighbor": "b", "state": "down"}],
                       key_fields=["neighbor"], iface_fields=[], compare_fields=["state"],
                       field_rules=sheet["field_rules"])
    assert (out["summary"]["unchanged"], out["summary"]["changed"]) == (1, 1)
    assert sql_compare_skip_reason(pg_db(), sheet) == "directional_compare"


def test_sorted_pairing_is_the_default_for_new_runs_only():
    assert build_pipeline(SHEET, iface_rules=[], port_map={}).duplicate_pairing == "sorted"
    assert build_pipeline({**SHEET, "duplicate_pairing": "ordered"}, iface_rules=[],
                          port_map={}).duplicate_pairing == "ordered"
    assert build_pipeline(SHEET, iface_rules=[], port_map={}, legacy=True).duplicate_pairing == "ordered"
