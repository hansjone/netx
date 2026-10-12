"""Template validation, rule semantics, protected references and bounded queries."""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from netx_api.db import Base
from netx_api.models import BizCompareJob, BizCompareRun, BizCompareTemplate, BizStateBatchCommand, BizStateBatch
from netx_api.biz_state import compare_service as svc
from netx_api.biz_state.compare_engine import compare_rows, row_key
from netx_api.biz_state.compare_rules import eval_leaf_filter, values_equal
from netx_api.biz_state.compare_sql import compile_row_filters_sql, _rk_sql, _field_differs_sql
from netx_api.biz_state_router import TemplateIn, TemplatePatchIn


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def body():
    return {"name": "Test", "metrics": [{"sheet_id": "s", "metric_id": "custom", "key_fields": ["id"],
        "compare_fields": ["count"], "iface_fields": ["port"], "ignore_port_changes": False,
        "row_filters": [], "field_rules": []}]}


@pytest.mark.parametrize("bad", [
    {"key_fields": []}, {"key_fields": ["id", "id"]}, {"metric_id": ""},
    {"row_filters": [{"field": "state", "op": "typo", "value": "up"}]},
    {"row_filters": [{"field": "state", "op": "regex", "value": "["}]},
    {"row_filters": [{"any": []}]}, {"row_filters": [{"field": "id", "op": "in", "value": "a,b"}]},
    {"field_rules": [{"field": "count", "compare": "typo"}]},
    {"field_rules": [{"field": "count", "normalize": "typo"}]},
    {"field_rules": [{"field": "count", "tolerance": -1}]},
    {"field_rules": [{"field": "count", "tolerance": float("inf")}]},
    {"field_rules": [{"field": "count", "tolerance": float("nan")}]},
    {"field_rules": [{"field": "count"}, {"field": "count"}]},
])
def test_invalid_sheet_never_silently_drops_or_saves(db, bad):
    data = body()
    data["metrics"].append({**deepcopy(data["metrics"][0]), "sheet_id": "second", **bad})
    with pytest.raises(HTTPException) as exc:
        svc.create_template(db, data)
    assert exc.value.status_code == 400
    assert "metrics[1]" in exc.value.detail["path"]
    assert db.query(BizCompareTemplate).count() == 0


def test_template_validation_precedes_mutation_and_rejects_duplicate_alias(db):
    tpl = svc.create_template(db, body())
    data = body()
    data["name"] = "Wrong name"
    data["metrics"][0]["key_fields"] = []
    with pytest.raises(HTTPException):
        svc.update_template(db, tpl["id"], data)
    assert db.get(BizCompareTemplate, tpl["id"]).name == "Test"
    with pytest.raises(HTTPException):
        svc.create_template(db, {**body(), "iface_normalize_rules": [{"from": "GE", "to": "a"}, {"from": "ge", "to": "b"}]})


def test_api_port_policy_roundtrip_and_legacy_patch(db):
    saved = svc.create_template(db, TemplateIn.model_validate(body()).model_dump())
    assert saved["metrics"][0]["ignore_port_changes"] is False
    updated = svc.update_template(db, saved["id"], TemplatePatchIn(ignore_port_changes=True).model_dump(exclude_unset=True))
    assert updated["metrics"][0]["ignore_port_changes"] is True
    legacy = svc.create_template(db, {"name": "legacy", "metric_id": "custom", "key_fields": ["id"], "ignore_port_changes": False})
    assert legacy["metrics"][0]["ignore_port_changes"] is False


@pytest.mark.parametrize("value", [0, False])
def test_filter_and_identity_preserve_false_and_zero(value):
    assert not eval_leaf_filter({"x": value}, {"field": "x", "op": "empty"})
    assert eval_leaf_filter({"x": value}, {"field": "x", "op": "eq", "value": value})
    assert row_key({"id": value}, ["id"]) != row_key({"id": None}, ["id"])
    result = compare_rows(before_rows=[{"id": value}], after_rows=[{"id": None}], key_fields=["id"], iface_fields=[], compare_fields=[], port_map={})
    assert result["summary"]["removed"] == 1 and result["summary"]["added"] == 1
    _, params = compile_row_filters_sql([{"field": "x", "op": "eq", "value": value}])
    assert list(params.values()) == ["0" if value is not False else "false"]


@pytest.mark.parametrize("mode", ["numeric", "percent"])
def test_missing_numeric_value_is_not_zero(mode):
    assert not values_equal(None, 0, rule={"compare": mode, "tolerance": 5})
    assert values_equal(None, "", rule={"compare": mode})
    assert values_equal("+.5", "0.5", rule={"compare": mode})
    assert values_equal("1.", "1", rule={"compare": mode})
    assert values_equal("NaN", "NaN", rule={"compare": mode})  # textual fallback


def test_sql_filter_literal_contains_and_collision_free_composite_keys():
    sql, params = compile_row_filters_sql([{"field": "x", "op": "contains", "value": "a%_\\b"}])
    assert "ESCAPE" in sql
    assert list(params.values()) == ["%a\\%\\_\\\\b%"]
    sql, _ = compile_row_filters_sql([{"field": "x", "op": "contains", "value": ""}])
    assert "FALSE" in sql
    assert "jsonb_build_array" in _rk_sql(["a", "b"])
    assert "concat_ws" not in _rk_sql(["a", "b"])
    assert "[+-]?" in _field_differs_sql("b", "a", {"compare": "numeric"})


def test_sheet_order_uses_one_aggregate_query_without_raw_text(db):
    for bid, metric, count in (("b", "large", 20), ("a", "large", 30), ("b", "small", 3)):
        db.add(BizStateBatchCommand(id=f"{bid}-{metric}", batch_id=bid, metric_id=metric, row_count=count, raw_text="x" * 100000))
    db.commit()
    sql = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda c, cur, statement, p, ctx, many: sql.append(statement))
    sheets = [{"metric_id": "large", "sheet_id": "l1"}, {"metric_id": "small", "sheet_id": "s"}, {"metric_id": "large", "sheet_id": "l2"}]
    ordered = svc._order_sheets_small_first(db, sheets, before_batch_id="b", after_batch_id="a")
    assert [s["sheet_id"] for s in ordered] == ["s", "l1", "l2"]
    assert len(sql) == 1 and "raw_text" not in sql[0]


def test_referenced_template_missing_sheets_and_running_delete_protected(db):
    tpl = svc.create_template(db, body())
    db.add(BizCompareJob(id="j", template_id=tpl["id"], enabled_sheet_ids=["s"]))
    db.add(BizCompareRun(id="r", job_id="j", status="running"))
    db.commit()
    for action in (lambda: svc.delete_template(db, tpl["id"]), lambda: svc.delete_job(db, "j"), lambda: svc.delete_run(db, "r")):
        with pytest.raises(HTTPException) as exc:
            action()
        assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        svc.update_job(db, "j", {"name": "invalid", "enabled_sheet_ids": ["missing"]})
    assert exc.value.detail["error"] == "unknown_enabled_sheets"
    assert db.get(BizCompareJob, "j").name != "invalid"


def test_template_usage_counts_are_batched(db, monkeypatch):
    monkeypatch.setattr(svc, "ensure_default_templates", lambda db: None)
    monkeypatch.setattr(svc, "upgrade_builtin_split_sheets", lambda db: None)
    used = svc.create_template(db, body())
    unused = svc.create_template(db, {**body(), "name": "Unused"})
    db.add_all([BizCompareJob(id="j1", template_id=used["id"]), BizCompareJob(id="j2", template_id=used["id"])])
    db.commit()
    sql = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda c, cur, statement, p, ctx, many: sql.append(statement))
    counts = {t["id"]: t["job_count"] for t in svc.list_templates(db)}
    assert counts == {used["id"]: 2, unused["id"]: 0}
    assert len(sql) == 3  # template query + one grouped query each for jobs and monitor templates


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled", "partial"])
def test_compare_rejects_incomplete_sources_before_enqueue(db, status):
    tpl = svc.create_template(db, body())
    db.add(BizStateBatch(id="before", status="success"))
    db.add(BizStateBatch(id="after", status=status))
    db.add(BizCompareJob(id="j", template_id=tpl["id"], before_batch_id="before", after_batch_id="after"))
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc._validate_compare_job(db, "j")
    assert exc.value.detail == "source_batch_not_complete"
    assert db.query(BizCompareRun).count() == 0


def test_compare_rejects_partially_stale_sheet_scope_after_template_edit(db):
    tpl = svc.create_template(db, body())
    db.add_all([BizStateBatch(id="before", status="success"), BizStateBatch(id="after", status="success"),
        BizCompareJob(id="j", template_id=tpl["id"], before_batch_id="before", after_batch_id="after", enabled_sheet_ids=["s", "removed"])])
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc._validate_compare_job(db, "j")
    assert exc.value.detail == {"error": "unknown_enabled_sheets", "sheet_ids": ["removed"]}
    assert db.query(BizCompareRun).count() == 0


@pytest.mark.parametrize("bad", [
    {"key_fields": []},
    {"row_filters": [{"field": "id", "op": "typo", "value": "a"}]},
    {"row_filters": [{"field": "id", "op": "regex", "value": "["}]},
    {"field_rules": [{"field": "count", "compare": "typo"}]},
])
def test_legacy_invalid_template_cannot_execute_as_partial_or_fail_open(db, bad):
    tpl = svc.create_template(db, body())
    row = db.get(BizCompareTemplate, tpl["id"])
    row.metrics_json = [*row.metrics_json, {**body()["metrics"][0], "sheet_id": "invalid", **bad}]
    db.add_all([BizStateBatch(id="before", status="success"), BizStateBatch(id="after", status="success"),
        BizCompareJob(id="j", template_id=tpl["id"], before_batch_id="before", after_batch_id="after")])
    db.commit()
    with pytest.raises(HTTPException) as exc:
        svc._validate_compare_job(db, "j")
    assert exc.value.detail["error"] == "invalid_template"
    assert "metrics[1]" in exc.value.detail["path"]
    assert db.query(BizCompareRun).count() == 0


def _builtin_sheet(row, metric_id):
    return next(s for s in svc.template_metrics(row) if s["metric_id"] == metric_id)


def _rule(sheet, field):
    return next(r for r in sheet["field_rules"] if r["field"] == field)


def test_packaged_status_template_uses_corrected_defaults(db):
    row = svc.ensure_default_zte_status_template(db)
    detail = _builtin_sheet(row, "interface_detail")
    assert _rule(detail, "output_bps")["compare"] == "percent"
    for f in ("input_bps", "output_bps", "in_util", "out_util"):
        assert f not in detail["compare_fields"] and f in detail["display_fields"]
    rx = _rule(_builtin_sheet(row, "optical_brief"), "rx_power")
    assert (rx["compare"], rx["tolerance"]) == ("numeric", 2)
    assert _builtin_sheet(row, "l2vpn_mac")["iface_fields"] == ["ac_port"]


def _store_old_packaged_values(db, row):
    sheets = svc.template_metrics(row)
    for s in sheets:
        if s["metric_id"] == "interface_detail":
            _rule(s, "output_bps").update(compare="numeric", tolerance=5)
            s["compare_fields"] = list(svc._INTERFACE_DETAIL_OLD_COMPARE)
        elif s["metric_id"] == "optical_brief":
            _rule(s, "rx_power").update(compare="percent", tolerance=5)
        elif s["metric_id"] == "l2vpn_mac":
            s["iface_fields"] = []
    svc._apply_sheets_to_row(row, sheets)
    db.commit()


def test_old_packaged_values_are_upgraded_in_place(db):
    row = svc.ensure_default_zte_status_template(db)
    _store_old_packaged_values(db, row)
    row = svc.ensure_default_zte_status_template(db)
    detail = _builtin_sheet(row, "interface_detail")
    assert _rule(detail, "output_bps")["compare"] == "percent"
    assert "output_bps" not in detail["compare_fields"]
    assert _rule(_builtin_sheet(row, "optical_brief"), "rx_power")["compare"] == "numeric"
    assert _builtin_sheet(row, "l2vpn_mac")["iface_fields"] == ["ac_port"]


def test_operator_customised_values_survive_upgrade(db):
    row = svc.ensure_default_zte_status_template(db)
    _store_old_packaged_values(db, row)
    sheets = svc.template_metrics(row)
    for s in sheets:
        if s["metric_id"] == "optical_brief":
            _rule(s, "rx_power").update(compare="percent", tolerance=1)  # operator tightened it
        elif s["metric_id"] == "interface_detail":
            s["compare_fields"] = ["admin", "output_bps"]  # operator trimmed the list
        elif s["metric_id"] == "l2vpn_mac":
            s["iface_fields"] = ["neighbor"]
    svc._apply_sheets_to_row(row, sheets)
    db.commit()
    row = svc.ensure_default_zte_status_template(db)
    rx = _rule(_builtin_sheet(row, "optical_brief"), "rx_power")
    assert (rx["compare"], rx["tolerance"]) == ("percent", 1)
    assert _builtin_sheet(row, "interface_detail")["compare_fields"] == ["admin", "output_bps"]
    assert _builtin_sheet(row, "l2vpn_mac")["iface_fields"] == ["neighbor"]


def test_packaged_arp_filter_uses_canonical_entry_type_and_old_filter_is_upgraded(db):
    from netx_api.biz_state.compare_rules import arp_dynamic_row_filters

    row = svc.ensure_default_zte_status_template(db)
    assert _builtin_sheet(row, "arp")["row_filters"] == arp_dynamic_row_filters()
    sheets = svc.template_metrics(row)
    for s in sheets:
        if s["metric_id"] == "arp":
            s["row_filters"] = [{"op": "age_timer", "field": "age", "value": ""}]
    svc._apply_sheets_to_row(row, sheets)
    db.commit()
    assert _builtin_sheet(svc.ensure_default_zte_status_template(db), "arp")["row_filters"] == arp_dynamic_row_filters()


def test_customised_arp_filter_is_left_alone(db):
    row = svc.ensure_default_zte_status_template(db)
    sheets = svc.template_metrics(row)
    mine = [{"op": "eq", "field": "entry_type", "value": "static"}]
    for s in sheets:
        if s["metric_id"] == "arp":
            s["row_filters"] = mine
    svc._apply_sheets_to_row(row, sheets)
    db.commit()
    assert _builtin_sheet(svc.ensure_default_zte_status_template(db), "arp")["row_filters"] == mine
