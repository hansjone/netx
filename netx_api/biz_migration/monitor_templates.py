"""CRUD for BizMonitorTemplate (cutover overlay on compare templates)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import BizCompareTemplate, BizMonitorTemplate
from ..timeutil import utcnow_naive
from ..biz_state import compare_service as cmp_svc
from ..biz_state.iface_normalize import default_zte_iface_normalize_rules
from .evaluate import PORT_METRIC_ID, PORT_STATUS_FIELDS


def _utcnow():
    return utcnow_naive()


def _out(row: BizMonitorTemplate, compare_name: str = "", *, db: Session | None = None) -> dict[str, Any]:
    collect = [str(x).strip() for x in (row.collect_metric_ids_json or []) if str(x).strip()]
    effective = list(collect)
    if not effective and db is not None:
        ct = db.get(BizCompareTemplate, row.compare_template_id) if row.compare_template_id else None
        if ct:
            seen: list[str] = []
            for s in cmp_svc.template_metrics(ct):
                mid = str(s.get("metric_id") or "").strip()
                if mid and mid not in seen:
                    seen.append(mid)
            effective = seen
    if not effective:
        effective = [PORT_METRIC_ID]
    out: dict[str, Any] = {
        "id": row.id,
        "name": row.name,
        "compare_template_id": row.compare_template_id or "",
        "compare_template_name": compare_name,
        "collect_metric_ids": collect,
        "collect_metric_ids_effective": effective,
        "defaults": dict(row.defaults_json or {}),
        "sheet_overrides": list(row.sheet_overrides_json or []),
        "note": row.note or "",
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }
    if db is not None:
        warnings = validate_sheet_overrides(
            db,
            compare_template_id=str(row.compare_template_id or ""),
            sheet_overrides=list(row.sheet_overrides_json or []),
        )
        if warnings:
            out["override_warnings"] = warnings
    return out


def validate_sheet_overrides(
    db: Session,
    *,
    compare_template_id: str,
    sheet_overrides: list[Any] | None,
) -> list[dict[str, str]]:
    """Return warnings when overrides no longer match compare template sheets.

    Does not block save — surfaces ``override_warnings`` so UI/ops can fix drift.
    """
    warnings: list[dict[str, str]] = []
    overrides = [o for o in (sheet_overrides or []) if isinstance(o, dict)]
    if not overrides:
        return warnings
    ct = db.get(BizCompareTemplate, compare_template_id) if compare_template_id else None
    if not ct:
        for ov in overrides:
            sid = str(ov.get("sheet_id") or "").strip()
            mid = str(ov.get("metric_id") or "").strip()
            warnings.append(
                {
                    "sheet_id": sid,
                    "metric_id": mid,
                    "reason": "compare_template_missing",
                }
            )
        return warnings
    sheets = cmp_svc.template_metrics(ct)
    sheet_ids = {str(s.get("sheet_id") or s.get("metric_id") or "").strip() for s in sheets}
    metric_ids = {str(s.get("metric_id") or "").strip() for s in sheets}
    for ov in overrides:
        sid = str(ov.get("sheet_id") or "").strip()
        mid = str(ov.get("metric_id") or "").strip()
        if sid:
            if sid not in sheet_ids:
                warnings.append(
                    {
                        "sheet_id": sid,
                        "metric_id": mid,
                        "reason": "sheet_id_not_in_compare_template",
                    }
                )
        elif mid:
            if mid not in metric_ids and mid not in sheet_ids:
                warnings.append(
                    {
                        "sheet_id": "",
                        "metric_id": mid,
                        "reason": "metric_id_not_in_compare_template",
                    }
                )
            elif mid in metric_ids:
                # Legacy metric-only override applies to every split of that metric
                split_count = sum(
                    1 for s in sheets if str(s.get("metric_id") or "").strip() == mid
                )
                if split_count > 1:
                    warnings.append(
                        {
                            "sheet_id": "",
                            "metric_id": mid,
                            "reason": "legacy_metric_override_applies_to_all_splits",
                        }
                    )
    return warnings


def _compare_name_map(db: Session) -> dict[str, str]:
    rows = db.query(BizCompareTemplate.id, BizCompareTemplate.name).all()
    return {str(r.id): str(r.name or "") for r in rows}


def ensure_port_compare_template(db: Session) -> BizCompareTemplate:
    """Built-in HOW template for interface_brief (matches former port_sheet_def)."""
    name = "Cutover port status"
    row = db.query(BizCompareTemplate).filter(BizCompareTemplate.name == name).one_or_none()
    sheet = {
        "metric_id": PORT_METRIC_ID,
        "key_fields": ["interface"],
        "iface_fields": ["interface"],
        "compare_fields": list(PORT_STATUS_FIELDS),
        "display_fields": ["interface", *PORT_STATUS_FIELDS, "description"],
        "row_filters": [],
        "field_rules": [],
    }
    if row:
        if not cmp_svc.template_iface_normalize(row):
            cmp_svc._set_template_iface_normalize(row, default_zte_iface_normalize_rules())  # noqa: SLF001
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name=name,
        note="Built-in port status sheet for cutover monitor (admin/phy/prot)",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    cmp_svc._apply_sheets_to_row(row, [sheet])  # noqa: SLF001 — shared normalizer
    cmp_svc._set_template_iface_normalize(row, default_zte_iface_normalize_rules())  # noqa: SLF001
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _anomaly_both_gone() -> dict[str, Any]:
    return {
        "old_groups": [[{"type": "presence", "value": "removed"}]],
        "new_groups": [[{"type": "presence", "value": "removed"}]],
    }


def _anomaly_side_only(side: str, groups: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "old_groups": groups if side == "old" else [],
        "new_groups": groups if side == "new" else [],
    }


def _default_anomaly_for_state(field: str, down_values: list[str]) -> list[dict[str, Any]]:
    down = [{"type": "value", "field": field, "op": "in", "value": list(down_values)}]
    gone = [{"type": "presence", "value": "removed"}]
    return [
        _anomaly_both_gone(),
        _anomaly_side_only("old", [gone, down]),
        _anomaly_side_only("new", [gone, down]),
    ]


def _default_anomaly_presence_only() -> list[dict[str, Any]]:
    gone = [{"type": "presence", "value": "removed"}]
    return [
        _anomaly_both_gone(),
        _anomaly_side_only("old", [gone]),
        _anomaly_side_only("new", [gone]),
    ]


def _success_presence_migrate() -> dict[str, Any]:
    """Old gone → new appeared (no bare unchanged = false green)."""
    return {
        "old_groups": [[{"type": "presence", "value": "removed"}]],
        "new_groups": [[{"type": "presence", "value": "added"}]],
    }


def _success_stateful(
    *,
    old_down_groups: list[list[dict[str, Any]]],
    new_up_conds: list[dict[str, Any]],
) -> dict[str, Any]:
    """Success requires healthy new side — bare unchanged alone is not enough."""
    return {
        "old_groups": [
            [{"type": "presence", "value": "removed"}],
            *old_down_groups,
        ],
        "new_groups": [
            [{"type": "presence", "value": "added"}, *new_up_conds],
            [{"type": "presence", "value": "unchanged"}, *new_up_conds],
            list(new_up_conds),
        ],
    }


def preset_override_for_metric(metric_id: str, *, sheet_id: str = "") -> dict[str, Any]:
    """Default dual-verdict overlay for a compare-template sheet.

    ``metric_id`` is the collected source table; ``sheet_id`` identifies a
    filtered split (e.g. bgp_peer.vpnv4). Presets key off the source metric.
    """
    mid = str(metric_id or "").strip()
    sid = str(sheet_id or "").strip()
    # Callers may pass only a split id like "bgp_peer.vpnv4"
    if not mid and sid:
        mid = sid.split(".", 1)[0]
    if not sid:
        sid = mid
    look = mid.split(".", 1)[0] if "." in mid else mid

    def _out(body: dict[str, Any]) -> dict[str, Any]:
        body["metric_id"] = mid or look
        if sid and sid != (mid or look):
            body["sheet_id"] = sid
        return body

    if look == PORT_METRIC_ID:
        up = [
            {"type": "value", "field": "admin", "op": "in", "value": ["up"]},
            {"type": "value", "field": "phy", "op": "in", "value": ["up"]},
        ]
        return _out(
            {
                "status_fields": list(PORT_STATUS_FIELDS),
                "down_values": ["down"],
                "up_values": ["up"],
                "success": [
                    _success_stateful(
                        old_down_groups=[
                            [
                                {"type": "value", "field": "admin", "op": "in", "value": ["down"]},
                                {"type": "value", "field": "phy", "op": "in", "value": ["down"]},
                            ]
                        ],
                        new_up_conds=up,
                    )
                ],
                "anomaly": _default_anomaly_for_state("admin", ["down"]),
            }
        )
    if look == "bgp_peer":
        up = [{"type": "value", "field": "state", "op": "eq", "value": "established"}]
        return _out(
            {
                "status_fields": ["state"],
                "down_values": ["idle", "active", "connect", "down"],
                "up_values": ["established"],
                "success": [
                    _success_stateful(
                        old_down_groups=[
                            [
                                {
                                    "type": "value",
                                    "field": "state",
                                    "op": "in",
                                    "value": ["idle", "active", "connect", "down"],
                                }
                            ]
                        ],
                        new_up_conds=up,
                    )
                ],
                "anomaly": _default_anomaly_for_state(
                    "state", ["idle", "active", "connect", "down"]
                ),
            }
        )
    if look in ("arp", "nd6_cache", "lldp_neighbor"):
        return _out(
            {
                "status_fields": [],
                "down_values": [],
                "up_values": [],
                "success": [_success_presence_migrate()],
                "anomaly": _default_anomaly_presence_only(),
            }
        )
    if "isis" in look or "ospf" in look or "adjacency" in look:
        up = [
            {
                "type": "value",
                "field": "state",
                "op": "in",
                "value": ["up", "full", "2way"],
            }
        ]
        return _out(
            {
                "status_fields": ["state"],
                "down_values": ["down", "init", "idle"],
                "up_values": ["up", "full", "2way"],
                "success": [
                    _success_stateful(
                        old_down_groups=[
                            [
                                {
                                    "type": "value",
                                    "field": "state",
                                    "op": "in",
                                    "value": ["down", "init", "idle"],
                                }
                            ]
                        ],
                        new_up_conds=up,
                    )
                ],
                "anomaly": _default_anomaly_for_state("state", ["down", "init", "idle"]),
            }
        )
    if "route" in look or "vrf" in look:
        return _out(
            {
                "status_fields": [],
                "down_values": [],
                "up_values": [],
                "success": [_success_presence_migrate()],
                "anomaly": _default_anomaly_presence_only(),
            }
        )
    return _out(
        {
            "status_fields": [],
            "down_values": [],
            "up_values": [],
            "success": [_success_presence_migrate()],
            "anomaly": _default_anomaly_presence_only(),
        }
    )


DEFAULT_MONITOR_TEMPLATE_NAME = "默认割接监控"


def ensure_default_monitor_templates(db: Session) -> None:
    """Seed once when monitor-template table is empty — one template on the full status compare."""
    if db.query(BizMonitorTemplate.id).limit(1).first():
        return
    cmp_svc.ensure_default_templates(db)
    zte = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "ZTE status default")
        .one_or_none()
    )
    if not zte:
        return
    zte_sheets = cmp_svc.template_metrics(zte)
    zte_overrides = [
        preset_override_for_metric(
            str(s.get("metric_id") or ""),
            sheet_id=str(s.get("sheet_id") or s.get("metric_id") or ""),
        )
        for s in zte_sheets
        if s.get("metric_id")
    ]
    db.add(
        BizMonitorTemplate(
            id=uuid4().hex,
            name=DEFAULT_MONITOR_TEMPLATE_NAME,
            compare_template_id=zte.id,
            collect_metric_ids_json=[],
            defaults_json={"dual_mode": "migrate_pair", "out_of_expect": "strict"},
            sheet_overrides_json=zte_overrides,
            note="Default: full status dual-verdict (port/ARP/BGP/…)",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        )
    )
    db.commit()


def default_port_monitor_template_id(db: Session) -> str:
    """Ensure seeds exist and return the default monitor template id."""
    ensure_default_monitor_templates(db)
    for name in (
        DEFAULT_MONITOR_TEMPLATE_NAME,
        "ZTE 状态割接监控",
        "端口割接监控",
    ):
        row = (
            db.query(BizMonitorTemplate)
            .filter(BizMonitorTemplate.name == name)
            .one_or_none()
        )
        if row:
            return row.id
    first = db.query(BizMonitorTemplate).order_by(BizMonitorTemplate.created_at.asc()).first()
    return first.id if first else ""


def get_monitor_template_row(db: Session, template_id: str) -> BizMonitorTemplate | None:
    tid = str(template_id or "").strip()
    if not tid:
        return None
    return db.get(BizMonitorTemplate, tid)


def list_monitor_templates(db: Session) -> list[dict[str, Any]]:
    ensure_default_monitor_templates(db)
    names = _compare_name_map(db)
    rows = db.query(BizMonitorTemplate).order_by(BizMonitorTemplate.name.asc()).all()
    return [_out(r, names.get(r.compare_template_id or "", ""), db=db) for r in rows]


def get_monitor_template(db: Session, template_id: str) -> dict[str, Any]:
    row = db.get(BizMonitorTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="monitor_template_not_found")
    names = _compare_name_map(db)
    return _out(row, names.get(row.compare_template_id or "", ""), db=db)


def create_monitor_template(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name_required")
    compare_id = str(body.get("compare_template_id") or "").strip()
    if compare_id and not db.get(BizCompareTemplate, compare_id):
        raise HTTPException(status_code=404, detail="compare_template_not_found")
    collect = body.get("collect_metric_ids")
    if not isinstance(collect, list):
        collect = []
    defaults = body.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {"dual_mode": "migrate_pair", "out_of_expect": "strict"}
    overrides = body.get("sheet_overrides")
    if not isinstance(overrides, list):
        overrides = []
    row = BizMonitorTemplate(
        id=uuid4().hex,
        name=name[:256],
        compare_template_id=compare_id,
        collect_metric_ids_json=[str(x) for x in collect if str(x).strip()],
        defaults_json=defaults,
        sheet_overrides_json=overrides,
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    names = _compare_name_map(db)
    out = _out(row, names.get(row.compare_template_id or "", ""), db=db)
    return out


def update_monitor_template(db: Session, template_id: str, body: dict[str, Any]) -> dict[str, Any]:
    row = db.get(BizMonitorTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="monitor_template_not_found")
    if "name" in body and body["name"] is not None:
        name = str(body["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name_required")
        row.name = name[:256]
    if "compare_template_id" in body and body["compare_template_id"] is not None:
        compare_id = str(body["compare_template_id"] or "").strip()
        if compare_id and not db.get(BizCompareTemplate, compare_id):
            raise HTTPException(status_code=404, detail="compare_template_not_found")
        row.compare_template_id = compare_id
    if "collect_metric_ids" in body and body["collect_metric_ids"] is not None:
        collect = body["collect_metric_ids"]
        if not isinstance(collect, list):
            raise HTTPException(status_code=400, detail="collect_metric_ids_invalid")
        row.collect_metric_ids_json = [str(x) for x in collect if str(x).strip()]
    if "defaults" in body and body["defaults"] is not None:
        if not isinstance(body["defaults"], dict):
            raise HTTPException(status_code=400, detail="defaults_invalid")
        row.defaults_json = body["defaults"]
    if "sheet_overrides" in body and body["sheet_overrides"] is not None:
        if not isinstance(body["sheet_overrides"], list):
            raise HTTPException(status_code=400, detail="sheet_overrides_invalid")
        row.sheet_overrides_json = body["sheet_overrides"]
    if "note" in body and body["note"] is not None:
        row.note = str(body["note"] or "")[:512]
    row.updated_at = _utcnow()
    db.commit()
    db.refresh(row)
    names = _compare_name_map(db)
    return _out(row, names.get(row.compare_template_id or "", ""), db=db)


def delete_monitor_template(db: Session, template_id: str) -> None:
    row = db.get(BizMonitorTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="monitor_template_not_found")
    db.delete(row)
    db.commit()
