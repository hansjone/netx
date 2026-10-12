"""Default monitor presets: port success condition and legacy upgrade (plan A4)."""
from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netx_api.biz_migration import monitor_templates as mt
from netx_api.biz_migration.evaluate import dual_verdict_ex
from netx_api.db import Base
from netx_api.models import BizCompareTemplate, BizMonitorTemplate

NEW_UP = {"admin": "up", "phy": "up"}

OLD_SIDES = {
    "fibre_pulled_no_shutdown": ("changed", {"admin": "up", "phy": "down"}),
    "shutdown_link_still_up": ("changed", {"admin": "down", "phy": "up"}),
    "both_down": ("changed", {"admin": "down", "phy": "down"}),
    "port_deleted": ("removed", {}),
}


def verdict(old_kind, old_row, new_row, *, acceptance, ov=None):
    ov = ov or mt.preset_override_for_metric("interface_brief")
    return dual_verdict_ex(
        old_kind=old_kind, new_kind="changed", in_expect=True,
        window_active=not acceptance, acceptance=acceptance,
        old_status="none", new_status="up",
        success_patterns=ov["success"], anomaly_patterns=ov["anomaly"],
        old_row=old_row, new_row=new_row,
    )


@pytest.mark.parametrize("acceptance", [False, True], ids=["window", "acceptance"])
@pytest.mark.parametrize("case", OLD_SIDES)
def test_old_port_out_of_service_with_new_up_is_migrated(case, acceptance):
    kind, row = OLD_SIDES[case]
    v, color, _hit = verdict(kind, row, NEW_UP, acceptance=acceptance)
    assert (v, color) == ("migrated", "green")


@pytest.mark.parametrize("acceptance", [False, True], ids=["window", "acceptance"])
def test_new_port_phy_down_is_never_green(acceptance):
    v, color, _ = verdict("changed", {"admin": "down", "phy": "down"},
                          {"admin": "up", "phy": "down"}, acceptance=acceptance)
    assert color != "green"


def test_old_port_still_in_service_is_not_migrated():
    v, color, _ = verdict("unchanged", {"admin": "up", "phy": "up"}, NEW_UP, acceptance=True)
    assert color != "green"


def test_legacy_form_still_requires_both_down():
    legacy = mt.preset_override_for_metric("interface_brief", legacy_port_and=True)
    v, color, _ = verdict("changed", {"admin": "up", "phy": "down"}, NEW_UP,
                          acceptance=True, ov=legacy)
    assert color != "green"  # the pre-A4 bug this task fixes


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _template(db, overrides):
    db.add(BizMonitorTemplate(id="m", name="m", compare_template_id="", sheet_overrides_json=overrides))
    db.commit()


def test_upgrade_replaces_exact_legacy_preset_only(db):
    legacy = mt.preset_override_for_metric("interface_brief", legacy_port_and=True)
    arp = mt.preset_override_for_metric("arp")
    _template(db, [legacy, arp])
    assert mt.upgrade_legacy_port_overrides(db) == 1
    stored = db.get(BizMonitorTemplate, "m").sheet_overrides_json
    assert stored[0] == mt.preset_override_for_metric("interface_brief")
    assert stored[1] == arp
    assert mt.upgrade_legacy_port_overrides(db) == 0  # idempotent


def test_customised_override_is_kept_and_warned(db):
    custom = mt.preset_override_for_metric("interface_brief", legacy_port_and=True)
    custom["down_values"] = ["down", "lowerlayerdown"]
    _template(db, [deepcopy(custom)])
    assert mt.upgrade_legacy_port_overrides(db) == 0
    assert db.get(BizMonitorTemplate, "m").sheet_overrides_json == [custom]
    db.add(BizCompareTemplate(id="ct", name="ct", metrics_json=[
        {"sheet_id": "interface_brief", "metric_id": "interface_brief",
         "key_fields": ["interface"], "compare_fields": ["admin"]}]))
    db.commit()
    warnings = mt.validate_sheet_overrides(db, compare_template_id="ct", sheet_overrides=[custom])
    assert {"sheet_id": "", "metric_id": "interface_brief",
            "reason": "port_success_legacy_and_semantics"} in warnings
    assert not any(w["reason"] == "port_success_legacy_and_semantics" for w in
                   mt.validate_sheet_overrides(db, compare_template_id="ct", sheet_overrides=[
                       mt.preset_override_for_metric("interface_brief")]))


def test_key_tuple_is_the_single_canonical_key_form():
    from netx_api.biz_state.compare_engine import key_tuple

    assert key_tuple({"a": None, "b": True, "c": "  x "}, ["a", "b", "c", "missing"]) == ("", "true", "x", "")


# ---- H8: scope_by_port_map is declared, not guessed from the metric name ----------------------

def _scoped(override):
    from netx_api.biz_migration.evaluate import evaluate_metric_dual, parse_expect_set

    return evaluate_metric_dual(
        metric_id="custom_t", key_fields=["ifname"], iface_fields=["ifname"], compare_fields=[],
        old_baseline_rows=[{"ifname": "a"}], old_current_rows=[], new_baseline_rows=[],
        new_current_rows=[{"ifname": "b"}], port_map={"a": "b"}, expect=parse_expect_set({}),
        window_active=True, sheet_override=override, allow_mapping_scope=True,
    )


def test_a_port_map_scopes_an_interface_sheet_unless_the_override_says_otherwise():
    assert _scoped({})["progress_total"] == 1
    assert _scoped({"scope_by_port_map": True})["progress_total"] == 1
    out = _scoped({"scope_by_port_map": False})
    assert out["progress_total"] == 0 and out["effective_scope_by_port_map"] is False


def test_presets_turn_scoping_off_for_bgp_and_pseudowire_only():
    assert mt.preset_override_for_metric("bgp_peer")["scope_by_port_map"] is False
    assert mt.preset_override_for_metric("l2vpn_pw")["scope_by_port_map"] is False
    assert "scope_by_port_map" not in mt.preset_override_for_metric("interface_brief")
    assert "scope_by_port_map" not in mt.preset_override_for_metric("arp")


def test_stored_overrides_get_the_old_name_based_default_written_down(db):
    _template(db, [{"metric_id": "bgp_peer", "sheet_id": "bgp_peer.vpnv4"}, {"metric_id": "arp"},
                   {"metric_id": "bgp_peer", "sheet_id": "x", "scope_by_port_map": True}])
    assert mt.fill_scope_by_port_map(db) == 1
    stored = db.get(BizMonitorTemplate, "m").sheet_overrides_json
    assert stored[0]["scope_by_port_map"] is False
    assert "scope_by_port_map" not in stored[1]
    assert stored[2]["scope_by_port_map"] is True  # an explicit choice is kept
    assert mt.fill_scope_by_port_map(db) == 0


def test_scope_flag_must_be_boolean():
    from fastapi import HTTPException

    from netx_api.biz_migration.validation import validate_monitor_rules

    with pytest.raises(HTTPException):
        validate_monitor_rules({}, [{"metric_id": "arp", "scope_by_port_map": "no"}])
    validate_monitor_rules({}, [{"metric_id": "arp", "scope_by_port_map": False}])
