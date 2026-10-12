"""Identity pipeline: one match key everywhere, per-side rules, value maps, post-normalize filters."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_migration.evaluate import evaluate_metric_dual, parse_expect_set
from netx_api.biz_state import compare_service as svc
from netx_api.biz_state.compare_engine import compare_rows
from netx_api.biz_state.compare_sql import dup_pairing_order_sql, sql_compare_skip_reason
from netx_api.biz_state.identity_pipeline import build_pipeline, legacy_pipeline
from netx_api.db import Base
from netx_api.models import (
    BizCompareRun, BizCompareTemplate, BizStateBatch, BizStateBatchCommand, BizStateMetricRow, BizStateTask,
)

ZTE_RULES = [{"from": "GE", "to": "gei"}, {"from": "XGE", "to": "xgei"}]


def sheet(**over):
    base = {
        "sheet_id": "s", "metric_id": "iface_t", "key_fields": ["interface"], "iface_fields": ["interface"],
        "compare_fields": ["state"], "field_rules": [], "row_filters": [],
    }
    return {**base, **over}


def pg_db():
    db = MagicMock()
    db.get_bind.return_value = MagicMock(dialect=MagicMock())
    db.get_bind.return_value.dialect.name = "postgresql"
    return db


# ---- B2: per-side rules + vendor canonical -------------------------------------------------

def test_huawei_long_and_short_names_are_the_same_interface():
    p = build_pipeline(sheet(), iface_rules=ZTE_RULES, port_map={}, before_vendor="huawei", after_vendor="huawei")
    a = p.match_key({"interface": "GigabitEthernet0/0/1"}, "before")
    b = p.match_key({"interface": "GE0/0/1"}, "before")
    assert a == b == ("gei0/0/1",)


def test_inner_spaces_are_removed():
    p = build_pipeline(sheet(), iface_rules=[], port_map={}, before_vendor="cisco")
    assert p.match_key({"interface": "Gigabit Ethernet 0/0/1"}, "before") == p.match_key(
        {"interface": "GigabitEthernet0/0/1"}, "before")


def test_cisco_and_h3c_canonical_forms():
    p = build_pipeline(sheet(), iface_rules=[], port_map={}, before_vendor="cisco", after_vendor="h3c")
    assert p.normalize({"interface": "TenGigE0/0/0/1"}, "before")["interface"] == "Te0/0/0/1"
    assert p.normalize({"interface": "Ten-GigabitEthernet1/0/1"}, "after")["interface"] == "XGE1/0/1"


def test_builtin_canonical_can_be_switched_off_per_sheet():
    p = build_pipeline(sheet(builtin_vendor_canonical=False), iface_rules=[], port_map={}, before_vendor="huawei")
    assert p.normalize({"interface": "GigabitEthernet0/0/1"}, "before")["interface"] == "GigabitEthernet0/0/1"


def test_side_scoped_rule_only_touches_its_side():
    rules = [{"from": "GE", "to": "gei", "side": "before"}]
    p = build_pipeline(sheet(), iface_rules=rules, port_map={}, before_vendor="huawei", after_vendor="zte")
    assert p.normalize({"interface": "GE0/1"}, "before")["interface"] == "gei0/1"
    assert p.normalize({"interface": "GE0/1"}, "after")["interface"] == "GE0/1"


def test_vendor_scoped_rule_follows_the_batch_vendor():
    rules = [{"from": "Eth-Trunk", "to": "ae", "vendor": "huawei"}]
    p = build_pipeline(sheet(), iface_rules=rules, port_map={}, before_vendor="huawei", after_vendor="zte")
    assert p.normalize({"interface": "Eth-Trunk1"}, "before")["interface"] == "ae1"
    assert p.normalize({"interface": "Eth-Trunk1"}, "after")["interface"] == "Eth-Trunk1"


def test_port_map_keys_use_before_rules_and_values_use_after_rules():
    rules = [{"from": "GE", "to": "gei", "side": "after"}]
    p = build_pipeline(sheet(), iface_rules=rules, port_map={"GE0/1": "GE0/2"},
                       before_vendor="huawei", after_vendor="zte")
    # before-side name stays "GE0/1" (rule is after-only); after-side value is normalized.
    assert p.port_map == {"GigabitEthernet0/1".replace("GigabitEthernet", "GE"): "gei0/2"}
    row = p.transform({"interface": "GigabitEthernet0/1"}, "before")
    assert row["interface"] == "gei0/2"


def test_zte_to_huawei_numbering_change_with_alias_rules():
    p = build_pipeline(
        sheet(), iface_rules=ZTE_RULES, port_map={"GE0/1/0/1": "GigabitEthernet0/2/0/1"},
        before_vendor="zte", after_vendor="huawei",
    )
    out = compare_rows(
        before_rows=[{"interface": "gei0/1/0/1", "state": "up"}],
        after_rows=[{"interface": "GE0/2/0/1", "state": "up"}],
        key_fields=["interface"], iface_fields=["interface"], compare_fields=["state"], pipeline=p,
    )
    assert out["summary"]["unchanged"] == 1


# ---- B3: value maps ----------------------------------------------------------------------

def test_value_map_aligns_vendor_state_names():
    rules = [{"field": "state", "value_map": {"estab": "established", "*down": "down"}}]
    p = build_pipeline(sheet(iface_fields=[], key_fields=["peer"], field_rules=rules), iface_rules=[],
                       port_map={})
    out = compare_rows(
        before_rows=[{"peer": "1.1.1.1", "state": "Estab"}, {"peer": "2.2.2.2", "state": "*down"}],
        after_rows=[{"peer": "1.1.1.1", "state": "established"}, {"peer": "2.2.2.2", "state": "down"}],
        key_fields=["peer"], iface_fields=[], compare_fields=["state"], pipeline=p,
    )
    assert out["summary"]["unchanged"] == 2 and out["summary"]["changed"] == 0


def test_value_map_can_be_scoped_to_one_side():
    rules = [{"field": "state", "side": "before", "value_map": {"estab": "established"}}]
    p = build_pipeline(sheet(iface_fields=[], key_fields=["peer"], field_rules=rules), iface_rules=[], port_map={})
    assert p.normalize({"peer": "a", "state": "Estab"}, "before")["state"] == "established"
    assert p.normalize({"peer": "a", "state": "Estab"}, "after")["state"] == "Estab"


def test_mac_key_with_different_formats_pairs():
    rules = [{"field": "mac", "normalize": "mac"}]
    p = build_pipeline(sheet(metric_id="mac_t", iface_fields=[], key_fields=["mac"], compare_fields=["port"],
                             field_rules=rules), iface_rules=[], port_map={})
    out = compare_rows(
        before_rows=[{"mac": "00e0-fc12-3456", "port": "1"}], after_rows=[{"mac": "00e0.fc12.3456", "port": "1"}],
        key_fields=["mac"], iface_fields=[], compare_fields=["port"], pipeline=p,
    )
    assert out["summary"]["unchanged"] == 1 and out["summary"]["added"] == out["summary"]["removed"] == 0


def test_legacy_pipeline_never_normalizes_keys():
    rules = [{"field": "mac", "normalize": "mac"}]
    p = legacy_pipeline(key_fields=["mac"], iface_fields=[], compare_fields=["port"], field_rules=rules)
    out = compare_rows(
        before_rows=[{"mac": "00e0-fc12-3456", "port": "1"}], after_rows=[{"mac": "00e0.fc12.3456", "port": "1"}],
        key_fields=["mac"], iface_fields=[], compare_fields=["port"], field_rules=rules, pipeline=p,
    )
    assert out["summary"]["added"] == out["summary"]["removed"] == 1


def test_value_map_and_key_normalize_block_sql():
    s = sheet(metric_id="bgp_route", iface_fields=[], key_fields=["mac"],
              field_rules=[{"field": "state", "value_map": {"a": "b"}}], compare_fields=["state"])
    p = build_pipeline(s, iface_rules=[], port_map={})
    assert sql_compare_skip_reason(pg_db(), s, pipeline=p) == "value_map"
    s2 = sheet(metric_id="m", iface_fields=[], key_fields=["mac"], compare_fields=[],
               field_rules=[{"field": "mac", "normalize": "lower"}])
    assert sql_compare_skip_reason(pg_db(), s2, pipeline=build_pipeline(s2, iface_rules=[], port_map={})) \
        == "key_normalize"


# ---- B4: filters run after normalization -----------------------------------------------------

CONTAINS_GEI = [{"field": "interface", "op": "contains", "value": "gei"}]


def test_v2_filter_sees_normalized_names_but_legacy_sees_raw():
    s = sheet(row_filters=CONTAINS_GEI)
    v2 = build_pipeline(s, iface_rules=ZTE_RULES, port_map={}, before_vendor="huawei")
    assert [raw["interface"] for raw, _ in v2.prepare([{"interface": "GE0/0/1"}], "before")] == ["GE0/0/1"]
    old = legacy_pipeline(key_fields=["interface"], iface_fields=["interface"], compare_fields=[],
                          iface_normalize_rules=ZTE_RULES, row_filters=CONTAINS_GEI)
    assert old.prepare([{"interface": "GE0/0/1"}], "before") == []  # the cross-vendor bug, kept for v1


def test_filter_can_target_one_side():
    s = sheet(iface_fields=[], row_filters=[{"field": "type", "op": "eq", "value": "dynamic", "side": "before"}])
    p = build_pipeline(s, iface_rules=[], port_map={})
    rows = [{"interface": "a", "type": "static"}]
    assert p.prepare(rows, "before") == []
    assert len(p.prepare(rows, "after")) == 1


def test_pushdown_only_for_filters_that_read_untransformed_fields():
    s = sheet(row_filters=[CONTAINS_GEI[0], {"field": "afi", "op": "eq", "value": "ipv4"}])
    p = build_pipeline(s, iface_rules=ZTE_RULES, port_map={})
    assert p.pushdown_filters("before") == [{"field": "afi", "op": "eq", "value": "ipv4"}]
    assert sql_compare_skip_reason(pg_db(), sheet(metric_id="m", iface_fields=["interface"], key_fields=["id"],
                                                  compare_fields=[], row_filters=CONTAINS_GEI,
                                                  ignore_port_changes=False),
                                   pipeline=build_pipeline(sheet(row_filters=CONTAINS_GEI), iface_rules=[],
                                                           port_map={})) == "row_filters_post_normalize"


# ---- B6: a port map no longer pushes interface-free sheets to Python ----------------------------

def test_port_map_only_blocks_sql_for_sheets_that_use_interfaces():
    route = {"metric_id": "bgp_route", "key_fields": ["network"], "iface_fields": [],
             "compare_fields": ["path"], "field_rules": [], "row_filters": []}
    assert sql_compare_skip_reason(pg_db(), route, port_map={"a": "b"}) == ""
    brief = {**route, "metric_id": "interface_brief", "key_fields": ["interface"], "iface_fields": ["interface"]}
    assert sql_compare_skip_reason(pg_db(), brief, port_map={"a": "b"}) == "port_map"
    # a map on a sheet whose interface column is only displayed does not matter either
    shown = {**route, "iface_fields": ["interface"]}
    assert sql_compare_skip_reason(pg_db(), shown, port_map={"a": "b"}) == ""


# ---- V4: duplicate keys pair by value ----------------------------------------------------------

def test_same_key_rows_pair_by_value_not_output_order():
    s = sheet(iface_fields=[], key_fields=["prefix"], compare_fields=["hop"])
    rows_b = [{"prefix": "p", "hop": "a"}, {"prefix": "p", "hop": "b"}]
    rows_a = [{"prefix": "p", "hop": "b"}, {"prefix": "p", "hop": "a"}]
    kw = dict(before_rows=rows_b, after_rows=rows_a, key_fields=["prefix"], iface_fields=[], compare_fields=["hop"])
    new = compare_rows(**kw, pipeline=build_pipeline(s, iface_rules=[], port_map={}))
    assert new["summary"]["unchanged"] == 2 and new["summary"]["changed"] == 0
    old = compare_rows(**kw)  # ordered, legacy
    assert old["summary"]["changed"] == 2


def test_sql_dup_order_mirrors_python_sort_key():
    sql = dup_pairing_order_sql(["hop", "metric"], {"hop": {"normalize": "lower"}})
    assert 'lower(trim(both from coalesce(data_json->>\'hop\', \'\'))) COLLATE "C" ASC' in sql.replace("(", "", 1) \
        or 'COLLATE "C"' in sql
    assert sql.endswith(", ")
    assert dup_pairing_order_sql([], {}) == ""


# ---- B7: partial maps ---------------------------------------------------------------------------

def test_ignore_port_policy_keeps_unmapped_ports_matching_by_content():
    s = sheet(iface_fields=["interface"], key_fields=["interface", "ip"], compare_fields=["mac"],
              unmapped_port_policy="ignore_port")
    p = build_pipeline(s, iface_rules=[], port_map={"gei-1": "gei-9"})
    out = compare_rows(
        before_rows=[{"interface": "gei-1", "ip": "10.0.0.1", "mac": "m1"},
                     {"interface": "gei-2", "ip": "10.0.0.2", "mac": "m2"}],
        after_rows=[{"interface": "gei-9", "ip": "10.0.0.1", "mac": "m1"},
                    {"interface": "gei-7", "ip": "10.0.0.2", "mac": "m2"}],
        key_fields=["interface", "ip"], iface_fields=["interface"], compare_fields=["mac"], pipeline=p,
    )
    assert out["summary"]["unchanged"] == 2
    by_name = build_pipeline({**s, "unmapped_port_policy": "by_name"}, iface_rules=[], port_map={"gei-1": "gei-9"})
    out2 = compare_rows(
        before_rows=[{"interface": "gei-2", "ip": "10.0.0.2", "mac": "m2"}],
        after_rows=[{"interface": "gei-7", "ip": "10.0.0.2", "mac": "m2"}],
        key_fields=["interface", "ip"], iface_fields=["interface"], compare_fields=["mac"], pipeline=by_name,
    )
    assert out2["summary"]["added"] == out2["summary"]["removed"] == 1


# ---- B1: every path derives the same match key --------------------------------------------------

@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _seed_two_batches(db):
    db.add_all([
        BizStateTask(id="t-old", source="managed", ne_id="o", vendor="Huawei", device_type="huawei"),
        BizStateTask(id="t-new", source="managed", ne_id="n", vendor="ZTE", device_type="zte_zxros"),
        BizStateBatch(id="b-old", task_id="t-old", status="success", vendor="Huawei"),
        BizStateBatch(id="b-new", task_id="t-new", status="success", vendor="ZTE"),
    ])
    db.add_all([
        BizStateMetricRow(id="r-old", batch_id="b-old", metric_id="iface_t", seq=0,
                          data_json={"interface": "GigabitEthernet0/1/0/1", "state": "up"}),
        BizStateMetricRow(id="r-new", batch_id="b-new", metric_id="iface_t", seq=0,
                          data_json={"interface": "gei-0/2/0/1", "state": "up"}),
    ])
    db.commit()


def test_all_paths_agree_on_the_match_key(db):
    _seed_two_batches(db)
    raw_map = {"GigabitEthernet0/1/0/1": "gei-0/2/0/1"}
    rules = [{"from": "GE", "to": "gei"}]
    s = sheet(sheet_id="s")
    tpl = BizCompareTemplate(id="tpl", name="t", metrics_json=[s], iface_normalize_json=rules)
    db.add(tpl)
    db.commit()
    from netx_api.biz_state.compare_service import _create_running_run, template_metrics
    from netx_api.models import BizCompareJob, BizPortMapping, BizPortMappingRow

    db.add_all([BizPortMapping(id="m", name="m"),
                BizPortMappingRow(mapping_id="m", before_if="GigabitEthernet0/1/0/1", after_if="gei-0/2/0/1"),
                BizCompareJob(id="j", name="j", template_id="tpl", mapping_id="m")])
    db.commit()
    job = db.get(BizCompareJob, "j")
    run = _create_running_run(db, job=job, tpl=tpl, before_batch_id="b-old", after_batch_id="b-new",
                              sheets_cfg=template_metrics(tpl))
    assert run.summary_json["config_snapshot"]["vendors"] == {"before": "huawei", "after": "zte"}
    pipe = svc._run_pipeline(db, run, s)
    expected = ("gei-0/2/0/1",)

    # 1. execution
    result = compare_rows(before_rows=[{"interface": "GigabitEthernet0/1/0/1", "state": "up"}],
                          after_rows=[{"interface": "gei-0/2/0/1", "state": "up"}],
                          key_fields=["interface"], iface_fields=["interface"], compare_fields=["state"],
                          pipeline=pipe)
    assert result["summary"]["unchanged"] == 1
    # 2. pipeline directly
    assert pipe.match_key({"interface": "GigabitEthernet0/1/0/1"}, "before") == expected
    assert pipe.match_key({"interface": "gei-0/2/0/1"}, "after") == expected
    # 3. search key groups
    rows, complete = svc._load_search_key_groups(
        db, batch_id="b-old", metric_id="iface_t", keys={expected}, match_keys=["interface"],
        pipeline=pipe, side="before")
    assert [r["interface"] for r in rows] == ["GigabitEthernet0/1/0/1"] and complete == {expected}
    # 4. hydrate
    item = {"before": {"interface": "GigabitEthernet0/1/0/1"}, "after": {}, "mapped_before": {},
            "after_row_id": "r-new", "before_row_id": ""}
    hydrated = svc._hydrate_diff_rows(db, [item], run=run, sheet=s)[0]
    assert hydrated["mapped_before"]["interface"] == hydrated["after"]["interface"] == "gei-0/2/0/1"
    # 5. cutover evaluation (old = before, new = after)
    dual = evaluate_metric_dual(
        metric_id="iface_t", sheet_id="s", key_fields=["interface"], iface_fields=["interface"],
        compare_fields=["state"], old_baseline_rows=[{"interface": "GigabitEthernet0/1/0/1", "state": "up"}],
        old_current_rows=[], new_baseline_rows=[{"interface": "gei-0/2/0/1", "state": "down"}],
        new_current_rows=[{"interface": "gei-0/2/0/1", "state": "up"}], port_map=pipe.port_map,
        expect=parse_expect_set({"items": [{"metric_id": "iface_t", "key": "GE0/1/0/1"}]}),
        window_active=False, acceptance=True, pipeline=pipe,
    )
    (row,) = dual["rows"]
    assert (row["match_old_key"], row["match_new_key"]) == ("gei0/1/0/1", "gei-0/2/0/1")
    assert row["old_key"] == "GigabitEthernet0/1/0/1" and row["verdict"] == "migrated"
    # 6. baseline expect picker
    assert pipe.transform({"interface": "GigabitEthernet0/1/0/1"}, "before")["interface"] == "gei-0/2/0/1"


# ---- v1 replay must not change -----------------------------------------------------------------

def test_v1_snapshot_run_replays_with_legacy_semantics(db):
    _seed_two_batches(db)
    s = sheet(sheet_id="s", row_filters=CONTAINS_GEI)
    run = BizCompareRun(id="r1", job_id="j", template_id="", before_batch_id="b-old", after_batch_id="b-new",
                        summary_json={"config_snapshot": {
                            "version": 1, "template": {"iface_normalize_rules": [{"from": "GE", "to": "gei"}]},
                            "sheets": [s], "port_map": {"gei-0/1/0/1": "gei-0/2/0/1"}}})
    pipe = svc._run_pipeline(db, run, s)
    assert pipe.legacy and pipe.duplicate_pairing == "ordered"
    assert pipe.port_map == {"gei-0/1/0/1": "gei-0/2/0/1"}
    assert pipe.prepare([{"interface": "GE0/1"}], "before") == []
