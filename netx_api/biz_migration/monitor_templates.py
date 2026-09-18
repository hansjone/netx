"""CRUD for BizMonitorTemplate (cutover overlay on compare templates)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import BizCompareTemplate, BizMonitorTemplate
from ..timeutil import utcnow_naive
from ..biz_state import compare_service as cmp_svc
from .evaluate import PORT_METRIC_ID, PORT_STATUS_FIELDS


def _utcnow():
    return utcnow_naive()


def _out(row: BizMonitorTemplate, compare_name: str = "") -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "compare_template_id": row.compare_template_id or "",
        "compare_template_name": compare_name,
        "collect_metric_ids": list(row.collect_metric_ids_json or []),
        "defaults": dict(row.defaults_json or {}),
        "sheet_overrides": list(row.sheet_overrides_json or []),
        "note": row.note or "",
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


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
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name=name,
        note="Built-in port status sheet for cutover monitor (admin/phy/prot)",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    cmp_svc._apply_sheets_to_row(row, [sheet])  # noqa: SLF001 — shared normalizer
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_monitor_templates(db: Session) -> None:
    """Seed once when monitor-template table is empty."""
    if db.query(BizMonitorTemplate.id).limit(1).first():
        return
    cmp_svc.ensure_default_templates(db)
    port_tpl = ensure_port_compare_template(db)
    zte = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "ZTE status default")
        .one_or_none()
    )
    seeds = [
        BizMonitorTemplate(
            id=uuid4().hex,
            name="端口割接监控",
            compare_template_id=port_tpl.id,
            collect_metric_ids_json=[PORT_METRIC_ID],
            defaults_json={"dual_mode": "migrate_pair", "out_of_expect": "strict"},
            sheet_overrides_json=[
                {
                    "metric_id": PORT_METRIC_ID,
                    "status_fields": list(PORT_STATUS_FIELDS),
                    "down_values": ["down"],
                    "up_values": ["up"],
                    "success": [
                        {
                            "old": ["removed", "down"],
                            "new": ["added", "up", "unchanged"],
                        }
                    ],
                }
            ],
            note="Default: port status dual-verdict with up/down semantics",
            created_at=_utcnow(),
            updated_at=_utcnow(),
        ),
    ]
    if zte:
        seeds.append(
            BizMonitorTemplate(
                id=uuid4().hex,
                name="ZTE 状态割接监控",
                compare_template_id=zte.id,
                collect_metric_ids_json=[],
                defaults_json={"dual_mode": "migrate_pair", "out_of_expect": "strict"},
                sheet_overrides_json=[],
                note="Uses ZTE status compare template; presence dual-verdict",
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
        )
    for s in seeds:
        db.add(s)
    db.commit()


def list_monitor_templates(db: Session) -> list[dict[str, Any]]:
    ensure_default_monitor_templates(db)
    names = _compare_name_map(db)
    rows = db.query(BizMonitorTemplate).order_by(BizMonitorTemplate.name.asc()).all()
    return [_out(r, names.get(r.compare_template_id or "", "")) for r in rows]


def get_monitor_template(db: Session, template_id: str) -> dict[str, Any]:
    row = db.get(BizMonitorTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="monitor_template_not_found")
    names = _compare_name_map(db)
    return _out(row, names.get(row.compare_template_id or "", ""))


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
    return _out(row, names.get(row.compare_template_id or "", ""))


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
    return _out(row, names.get(row.compare_template_id or "", ""))


def delete_monitor_template(db: Session, template_id: str) -> None:
    row = db.get(BizMonitorTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="monitor_template_not_found")
    db.delete(row)
    db.commit()
