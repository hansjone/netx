"""Compare templates, port mappings, jobs, and runs."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    BizCompareDiff,
    BizCompareJob,
    BizCompareRun,
    BizCompareTemplate,
    BizPortMapping,
    BizPortMappingRow,
    BizStateBatch,
    BizStateLldpNeighbor,
)
from ..timeutil import utcnow_naive
from .compare_engine import compare_rows, mapping_stats
from .profiles import metric_field_map


def _utcnow() -> datetime:
    return utcnow_naive()


_DIFF_CHUNK = 2000
_SEARCH_TEXT_MAX = 4000


def _diff_search_text(d: dict[str, Any]) -> str:
    parts = [str(d.get("kind") or "")]
    for key in ("key", "before", "after", "mapped_before", "changes"):
        val = d.get(key)
        if val:
            try:
                parts.append(json.dumps(val, ensure_ascii=False, default=str, separators=(",", ":")))
            except Exception:
                parts.append(str(val))
    return " ".join(parts)[:_SEARCH_TEXT_MAX]


def _top_changed_fields(diffs: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    field_counts: dict[str, int] = {}
    for d in diffs:
        if str(d.get("kind") or "") != "changed":
            continue
        for fname in d.get("changes") or {}:
            field_counts[str(fname)] = field_counts.get(str(fname), 0) + 1
    return sorted(
        [{"field": k, "count": v} for k, v in field_counts.items()],
        key=lambda x: (-int(x["count"]), str(x["field"])),
    )[:limit]


def _persist_sheet_diffs(
    db: Session,
    *,
    run_id: str,
    metric_id: str,
    diffs: list[dict[str, Any]],
) -> None:
    """Bulk-insert diff rows; avoids embedding million-row arrays in summary_json."""
    buf: list[dict[str, Any]] = []
    for i, d in enumerate(diffs):
        buf.append(
            {
                "id": uuid4().hex,
                "run_id": run_id,
                "metric_id": metric_id,
                "seq": i,
                "kind": str(d.get("kind") or ""),
                "key_json": dict(d.get("key") or {}),
                "before_json": dict(d.get("before") or {}),
                "after_json": dict(d.get("after") or {}),
                "mapped_before_json": dict(d.get("mapped_before") or {}),
                "changes_json": dict(d.get("changes") or {}),
                "search_text": _diff_search_text(d),
            }
        )
        if len(buf) >= _DIFF_CHUNK:
            db.bulk_insert_mappings(BizCompareDiff, buf)
            buf.clear()
    if buf:
        db.bulk_insert_mappings(BizCompareDiff, buf)


def _diff_row_out(r: BizCompareDiff) -> dict[str, Any]:
    return {
        "kind": r.kind,
        "key": r.key_json or {},
        "before": r.before_json or {},
        "after": r.after_json or {},
        "mapped_before": r.mapped_before_json or {},
        "changes": r.changes_json or {},
    }


def _run_has_diff_rows(db: Session, run_id: str) -> bool:
    return (
        db.query(BizCompareDiff.id).filter(BizCompareDiff.run_id == run_id).limit(1).first()
        is not None
    )


def _filter_inline_diffs(
    diffs: list[dict[str, Any]],
    *,
    kind: str,
    kw: str,
) -> list[dict[str, Any]]:
    kind_n = (kind or "diff").strip().lower()
    kw_n = (kw or "").strip().lower()
    out: list[dict[str, Any]] = []
    for d in diffs:
        dk = str(d.get("kind") or "")
        if kind_n == "diff":
            if dk == "unchanged":
                continue
        elif kind_n != "all" and dk != kind_n:
            continue
        if kw_n:
            blob = _diff_search_text(d).lower()
            if kw_n not in blob:
                continue
        out.append(d)
    return out


def _sheet_meta_from_summary(summary: dict[str, Any], run: BizCompareRun, tpl: Any) -> list[dict[str, Any]]:
    sheets = list(summary.get("sheets") or [])
    if sheets:
        return sheets
    return [
        {
            "metric_id": run.metric_id,
            "key_fields": list((tpl.key_fields if tpl else None) or []),
            "iface_fields": list((tpl.iface_fields if tpl else None) or []),
            "compare_fields": list((tpl.compare_fields if tpl else None) or []),
            "mode": "fields",
            "summary": {
                k: summary.get(k, 0)
                for k in ("added", "removed", "changed", "unchanged", "before_count", "after_count")
            },
            "diffs": list(run.diffs_json or []),
        }
    ]


def _str_list(raw: Any) -> list[str]:
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _sheet_def(
    *,
    metric_id: str,
    key_fields: list[str],
    iface_fields: list[str] | None = None,
    compare_fields: list[str] | None = None,
) -> dict[str, Any]:
    mid = str(metric_id or "").strip()
    keys = _str_list(key_fields)
    ifaces = _str_list(iface_fields)
    # compare_fields empty → presence-only mode (intentional)
    compare = _str_list(compare_fields) if compare_fields is not None else []
    # Keys are identity only; strip them from compare so UI/engine stay clear
    key_set = set(keys)
    compare = [f for f in compare if f not in key_set]
    return {
        "metric_id": mid,
        "key_fields": keys,
        "iface_fields": ifaces,
        "compare_fields": compare,
    }


def _default_lldp_sheet() -> dict[str, Any]:
    fields = metric_field_map().get("lldp_neighbor") or []
    keys = [f.name for f in fields if f.is_key] or ["local_if", "remote_sys", "remote_if"]
    ifaces = [f.name for f in fields if f.is_interface] or ["local_if"]
    # Value checks: non-key state/meta (e.g. remote_ip / protocol)
    compare = [f.name for f in fields if not f.is_key and f.role in ("state", "meta", "identity")]
    if not compare:
        compare = [n for n in ("remote_ip", "protocol") if n not in keys]
    return _sheet_def(
        metric_id="lldp_neighbor",
        key_fields=keys,
        iface_fields=ifaces,
        compare_fields=compare,
    )


def _default_vrf_sheet() -> dict[str, Any]:
    fields = metric_field_map().get("vrf_route_summary") or []
    keys = [f.name for f in fields if f.is_key] or ["vrf", "source"]
    ifaces = [f.name for f in fields if f.is_interface]
    compare = [f.name for f in fields if not f.is_key and f.role in ("state", "meta", "identity")]
    if not compare:
        compare = [n for n in ("networks",) if n not in keys]
    return _sheet_def(
        metric_id="vrf_route_summary",
        key_fields=keys,
        iface_fields=ifaces,
        compare_fields=compare,
    )


def _default_sheet_for_metric(metric_id: str, *, compare_roles: tuple[str, ...] = ("state",)) -> dict[str, Any]:
    fields = metric_field_map().get(metric_id) or []
    keys = [f.name for f in fields if f.is_key]
    ifaces = [f.name for f in fields if f.is_interface]
    compare = [f.name for f in fields if (not f.is_key) and f.role in compare_roles]
    return _sheet_def(
        metric_id=metric_id,
        key_fields=keys,
        iface_fields=ifaces,
        compare_fields=compare,
    )


def _default_zte_status_sheets() -> list[dict[str, Any]]:
    return [
        _default_sheet_for_metric("isis_adjacency", compare_roles=("state",)),
        _default_sheet_for_metric("interface_brief", compare_roles=("state",)),
        _default_sheet_for_metric("arp", compare_roles=("state",)),
        _default_sheet_for_metric("nd6_cache", compare_roles=("state",)),
        _default_sheet_for_metric("bgp_peer", compare_roles=("state",)),
    ]


def _normalize_sheet(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    mid = str(raw.get("metric_id") or "").strip()
    keys = _str_list(raw.get("key_fields"))
    if not mid or not keys:
        return None
    return _sheet_def(
        metric_id=mid,
        key_fields=keys,
        iface_fields=_str_list(raw.get("iface_fields")),
        compare_fields=_str_list(raw.get("compare_fields")),
    )


def _legacy_sheets(t: BizCompareTemplate) -> list[dict[str, Any]]:
    mid = str(t.metric_id or "").strip()
    keys = _str_list(t.key_fields)
    if not mid or not keys:
        return []
    ignore = set(_str_list(t.ignore_fields))
    compare = [f for f in _str_list(t.compare_fields) if f not in ignore]
    return [
        _sheet_def(
            metric_id=mid,
            key_fields=keys,
            iface_fields=_str_list(t.iface_fields),
            compare_fields=compare,
        )
    ]


def template_metrics(t: BizCompareTemplate) -> list[dict[str, Any]]:
    """Resolved metric sheets for a template (metrics_json or legacy single)."""
    raw = list(t.metrics_json or [])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        sheet = _normalize_sheet(item)
        if not sheet:
            continue
        mid = sheet["metric_id"]
        if mid in seen:
            continue
        seen.add(mid)
        out.append(sheet)
    if out:
        return out
    return _legacy_sheets(t)


def _apply_sheets_to_row(t: BizCompareTemplate, sheets: list[dict[str, Any]]) -> None:
    t.metrics_json = sheets
    first = sheets[0] if sheets else None
    if first:
        t.metric_id = first["metric_id"]
        t.key_fields = list(first["key_fields"])
        t.iface_fields = list(first["iface_fields"])
        t.compare_fields = list(first["compare_fields"])
        t.ignore_fields = []
    else:
        t.metric_id = ""
        t.key_fields = []
        t.iface_fields = []
        t.compare_fields = []
        t.ignore_fields = []


def _parse_metrics_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Accept ``metrics`` list or legacy single-metric fields."""
    if "metrics" in body and body.get("metrics") is not None:
        sheets: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in list(body.get("metrics") or []):
            sheet = _normalize_sheet(raw)
            if not sheet:
                continue
            mid = sheet["metric_id"]
            if mid in seen:
                raise HTTPException(status_code=400, detail=f"duplicate_metric:{mid}")
            seen.add(mid)
            sheets.append(sheet)
        if not sheets:
            raise HTTPException(status_code=400, detail="metrics_required")
        return sheets

    mid = str(body.get("metric_id") or "").strip()
    keys = _str_list(body.get("key_fields"))
    if not mid:
        raise HTTPException(status_code=400, detail="metric_id_required")
    if not keys:
        raise HTTPException(status_code=400, detail="key_fields_required")
    ignore = set(_str_list(body.get("ignore_fields")))
    compare = [f for f in _str_list(body.get("compare_fields")) if f not in ignore]
    return [
        _sheet_def(
            metric_id=mid,
            key_fields=keys,
            iface_fields=_str_list(body.get("iface_fields")),
            compare_fields=compare,
        )
    ]


def _template_out(t: BizCompareTemplate) -> dict[str, Any]:
    sheets = template_metrics(t)
    first = sheets[0] if sheets else None
    return {
        "id": t.id,
        "name": t.name,
        "metrics": sheets,
        "metric_ids": [s["metric_id"] for s in sheets],
        # legacy mirrors (first sheet)
        "metric_id": (first or {}).get("metric_id") or t.metric_id or "",
        "key_fields": list((first or {}).get("key_fields") or t.key_fields or []),
        "iface_fields": list((first or {}).get("iface_fields") or t.iface_fields or []),
        "compare_fields": list((first or {}).get("compare_fields") or t.compare_fields or []),
        "ignore_fields": [],
        "note": t.note,
        "updated_at": t.updated_at.isoformat() + "Z" if t.updated_at else None,
    }


def ensure_default_cutover_template(db: Session) -> BizCompareTemplate:
    row = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "Cutover default")
        .one_or_none()
    )
    if row:
        # Upgrade legacy single-sheet cutover if needed
        sheets = template_metrics(row)
        if len(sheets) < 2:
            _apply_sheets_to_row(row, [_default_lldp_sheet(), _default_vrf_sheet()])
            row.note = "Built-in multi-metric cutover template (LLDP + VRF)"
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    sheets = [_default_lldp_sheet(), _default_vrf_sheet()]
    row = BizCompareTemplate(
        id=uuid4().hex,
        name="Cutover default",
        note="Built-in multi-metric cutover template (LLDP + VRF)",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, sheets)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_lldp_template(db: Session) -> BizCompareTemplate:
    row = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "LLDP default")
        .one_or_none()
    )
    if row:
        if not template_metrics(row):
            _apply_sheets_to_row(row, [_default_lldp_sheet()])
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name="LLDP default",
        note="Built-in template for LLDP neighbor cutover compare",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, [_default_lldp_sheet()])
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_vrf_template(db: Session) -> BizCompareTemplate:
    row = (
        db.query(BizCompareTemplate)
        .filter(BizCompareTemplate.name == "VRF route summary default")
        .one_or_none()
    )
    if row:
        if not template_metrics(row):
            _apply_sheets_to_row(row, [_default_vrf_sheet()])
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name="VRF route summary default",
        note="Built-in template for per-VRF route summary cutover compare",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, [_default_vrf_sheet()])
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_zte_status_template(db: Session) -> BizCompareTemplate:
    name = "ZTE status default"
    row = db.query(BizCompareTemplate).filter(BizCompareTemplate.name == name).one_or_none()
    sheets = _default_zte_status_sheets()
    if row:
        existing = template_metrics(row)
        want = {s["metric_id"] for s in sheets}
        have = {s["metric_id"] for s in existing}
        if want - have:
            _apply_sheets_to_row(row, sheets)
            row.note = "Built-in ZTE status cutover (ISIS/IF/ARP/ND6/BGP)"
            row.updated_at = _utcnow()
            db.commit()
            db.refresh(row)
        return row
    row = BizCompareTemplate(
        id=uuid4().hex,
        name=name,
        note="Built-in ZTE status cutover (ISIS/IF/ARP/ND6/BGP)",
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(row, sheets)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_default_templates(db: Session) -> None:
    ensure_default_cutover_template(db)
    ensure_default_lldp_template(db)
    ensure_default_vrf_template(db)
    ensure_default_zte_status_template(db)


def list_templates(db: Session) -> list[dict[str, Any]]:
    ensure_default_templates(db)
    rows = db.query(BizCompareTemplate).order_by(BizCompareTemplate.name.asc()).all()
    return [_template_out(t) for t in rows]


def list_metric_schemas() -> list[dict[str, Any]]:
    """Field catalog for template editors (key / iface / compare pickers)."""
    out: list[dict[str, Any]] = []
    for metric_id, fields in sorted(metric_field_map().items()):
        if metric_id in ("vrf_list",):
            continue
        out.append(
            {
                "metric_id": metric_id,
                "fields": [
                    {
                        "name": f.name,
                        "display_name": f.display_name or f.name,
                        "dtype": f.dtype,
                        "is_key": bool(f.is_key),
                        "is_interface": bool(f.is_interface),
                        "role": f.role,
                    }
                    for f in fields
                ],
            }
        )
    return out


def create_template(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    sheets = _parse_metrics_body(body)
    t = BizCompareTemplate(
        id=uuid4().hex,
        name=str(body.get("name") or sheets[0]["metric_id"])[:256],
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    _apply_sheets_to_row(t, sheets)
    db.add(t)
    db.commit()
    return _template_out(t)


def update_template(db: Session, template_id: str, body: dict[str, Any]) -> dict[str, Any]:
    t = db.get(BizCompareTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="template_not_found")
    if "name" in body:
        t.name = str(body.get("name") or "")[:256]
    if "note" in body:
        t.note = str(body.get("note") or "")[:512]
    if any(k in body for k in ("metrics", "metric_id", "key_fields", "iface_fields", "compare_fields", "ignore_fields")):
        # Prefer explicit metrics; otherwise merge into current sheets from legacy keys
        if "metrics" in body and body.get("metrics") is not None:
            sheets = _parse_metrics_body(body)
        else:
            # Patch first sheet (or create) from legacy fields
            sheets = list(template_metrics(t))
            if not sheets:
                sheets = _parse_metrics_body(body)
            else:
                first = dict(sheets[0])
                if "metric_id" in body and body.get("metric_id") is not None:
                    mid = str(body.get("metric_id") or "").strip()
                    if mid:
                        first["metric_id"] = mid
                if "key_fields" in body:
                    keys = _str_list(body.get("key_fields"))
                    if not keys:
                        raise HTTPException(status_code=400, detail="key_fields_required")
                    first["key_fields"] = keys
                if "iface_fields" in body:
                    first["iface_fields"] = _str_list(body.get("iface_fields"))
                if "compare_fields" in body or "ignore_fields" in body:
                    ignore = set(_str_list(body.get("ignore_fields"))) if "ignore_fields" in body else set()
                    compare = _str_list(body.get("compare_fields")) if "compare_fields" in body else list(first.get("compare_fields") or [])
                    first["compare_fields"] = [f for f in compare if f not in ignore]
                sheets[0] = _normalize_sheet(first) or first
        _apply_sheets_to_row(t, sheets)
    t.updated_at = _utcnow()
    db.commit()
    return _template_out(t)


def delete_template(db: Session, template_id: str) -> None:
    t = db.get(BizCompareTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="template_not_found")
    db.delete(t)
    db.commit()


def _mapping_out(db: Session, m: BizPortMapping) -> dict[str, Any]:
    rows = (
        db.query(BizPortMappingRow)
        .filter(BizPortMappingRow.mapping_id == m.id)
        .order_by(BizPortMappingRow.before_if.asc())
        .all()
    )
    return {
        "id": m.id,
        "name": m.name,
        "note": m.note,
        "rows": [{"id": r.id, "before_if": r.before_if, "after_if": r.after_if} for r in rows],
        "updated_at": m.updated_at.isoformat() + "Z" if m.updated_at else None,
    }


def list_mappings(db: Session) -> list[dict[str, Any]]:
    rows = db.query(BizPortMapping).order_by(BizPortMapping.name.asc()).all()
    return [_mapping_out(db, m) for m in rows]


def create_mapping(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    m = BizPortMapping(
        id=uuid4().hex,
        name=str(body.get("name") or "port map")[:256],
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    db.add(m)
    db.flush()
    _replace_mapping_rows(db, m.id, list(body.get("rows") or []))
    db.commit()
    return _mapping_out(db, m)


def update_mapping(db: Session, mapping_id: str, body: dict[str, Any]) -> dict[str, Any]:
    m = db.get(BizPortMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="mapping_not_found")
    if "name" in body:
        m.name = str(body.get("name") or "")[:256]
    if "note" in body:
        m.note = str(body.get("note") or "")[:512]
    if "rows" in body:
        _replace_mapping_rows(db, m.id, list(body.get("rows") or []))
    m.updated_at = _utcnow()
    db.commit()
    return _mapping_out(db, m)


def _replace_mapping_rows(db: Session, mapping_id: str, rows_in: list[dict[str, Any]]) -> None:
    db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).delete()
    seen: set[str] = set()
    for raw in rows_in:
        before = str(raw.get("before_if") or "").strip()
        after = str(raw.get("after_if") or "").strip()
        if not before or not after:
            continue
        if before in seen:
            raise HTTPException(status_code=400, detail=f"duplicate_before_if:{before}")
        seen.add(before)
        db.add(
            BizPortMappingRow(
                id=uuid4().hex,
                mapping_id=mapping_id,
                before_if=before[:128],
                after_if=after[:128],
            )
        )


def delete_mapping(db: Session, mapping_id: str) -> None:
    m = db.get(BizPortMapping, mapping_id)
    if not m:
        raise HTTPException(status_code=404, detail="mapping_not_found")
    db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).delete()
    db.delete(m)
    db.commit()


def _port_map_dict(db: Session, mapping_id: str) -> dict[str, str]:
    if not mapping_id:
        return {}
    rows = db.query(BizPortMappingRow).filter(BizPortMappingRow.mapping_id == mapping_id).all()
    return {str(r.before_if): str(r.after_if) for r in rows if r.before_if and r.after_if}


def _load_metric_rows(db: Session, *, batch_id: str, metric_id: str) -> list[dict[str, Any]]:
    if metric_id == "lldp_neighbor":
        rows = (
            db.query(BizStateLldpNeighbor)
            .filter(BizStateLldpNeighbor.batch_id == batch_id)
            .all()
        )
        return [
            {
                "local_if": n.local_if,
                "remote_sys": n.remote_sys,
                "remote_if": n.remote_if,
                "remote_ip": n.remote_ip,
                "protocol": n.protocol,
            }
            for n in rows
        ]
    if metric_id == "vrf_route_summary":
        from ..models import BizStateVrfRouteSummary

        rows = (
            db.query(BizStateVrfRouteSummary)
            .filter(BizStateVrfRouteSummary.batch_id == batch_id)
            .all()
        )
        return [
            {"vrf": r.vrf, "source": r.source, "networks": r.networks}
            for r in rows
        ]
    # Generic tabular metrics (ISIS / interface / ARP / ND6 / BGP …)
    from ..models import BizStateMetricRow

    rows = (
        db.query(BizStateMetricRow)
        .filter(
            BizStateMetricRow.batch_id == batch_id,
            BizStateMetricRow.metric_id == metric_id,
        )
        .order_by(BizStateMetricRow.seq.asc(), BizStateMetricRow.id.asc())
        .all()
    )
    if rows:
        out = [dict(r.data_json or {}) for r in rows]
        if metric_id == "arp":
            from .parsers.zte_status import is_valid_arp_age

            # Compare only dynamic ARP (Age is HH:MM:SS); drop static H / incomplete flags
            out = [
                r
                for r in out
                if str(r.get("entry_type") or "").lower() == "dynamic"
                or (
                    not r.get("entry_type")
                    and is_valid_arp_age(str(r.get("age") or ""))
                )
            ]
        return out
    # Known metric with zero rows is OK; unknown metric still errors
    if metric_id in metric_field_map():
        return []
    raise HTTPException(status_code=400, detail=f"unsupported_metric:{metric_id}")


def validate_mapping(
    db: Session,
    *,
    mapping_id: str,
    before_batch_id: str,
    after_batch_id: str,
    template_id: str = "",
) -> dict[str, Any]:
    ensure_default_templates(db)
    tpl = db.get(BizCompareTemplate, template_id) if template_id else ensure_default_cutover_template(db)
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    sheets = template_metrics(tpl)
    if not sheets:
        raise HTTPException(status_code=400, detail="template_has_no_metrics")
    pmap = _port_map_dict(db, mapping_id)
    # Validate against first sheet that has iface fields (or first sheet)
    target = next((s for s in sheets if s.get("iface_fields")), sheets[0])
    before = _load_metric_rows(db, batch_id=before_batch_id, metric_id=target["metric_id"])
    after = _load_metric_rows(db, batch_id=after_batch_id, metric_id=target["metric_id"])
    return mapping_stats(
        before_rows=before,
        after_rows=after,
        iface_fields=list(target.get("iface_fields") or []),
        port_map=pmap,
    )


def _job_out(j: BizCompareJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "name": j.name,
        "template_id": j.template_id,
        "mapping_id": j.mapping_id,
        "before_task_id": j.before_task_id,
        "after_task_id": j.after_task_id,
        "before_batch_id": j.before_batch_id,
        "after_batch_id": j.after_batch_id,
        "mode": j.mode,
        "status": j.status,
        "note": j.note,
        "updated_at": j.updated_at.isoformat() + "Z" if j.updated_at else None,
    }


def list_jobs(db: Session) -> list[dict[str, Any]]:
    rows = db.query(BizCompareJob).order_by(BizCompareJob.updated_at.desc()).all()
    return [_job_out(j) for j in rows]


def create_job(db: Session, body: dict[str, Any]) -> dict[str, Any]:
    ensure_default_templates(db)
    template_id = str(body.get("template_id") or "").strip()
    if not template_id:
        tpl = ensure_default_cutover_template(db)
        template_id = tpl.id
    else:
        if not db.get(BizCompareTemplate, template_id):
            raise HTTPException(status_code=404, detail="template_not_found")
    j = BizCompareJob(
        id=uuid4().hex,
        name=str(body.get("name") or "compare")[:256],
        template_id=template_id,
        mapping_id=str(body.get("mapping_id") or ""),
        before_task_id=str(body.get("before_task_id") or ""),
        after_task_id=str(body.get("after_task_id") or ""),
        before_batch_id=str(body.get("before_batch_id") or ""),
        after_batch_id=str(body.get("after_batch_id") or ""),
        mode=str(body.get("mode") or "manual")[:16],
        status="ready",
        note=str(body.get("note") or "")[:512],
        created_at=_utcnow(),
        updated_at=_utcnow(),
    )
    if j.mode == "auto":
        j.status = "auto"
    db.add(j)
    db.commit()
    return _job_out(j)


def update_job(db: Session, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    for key in (
        "name",
        "template_id",
        "mapping_id",
        "before_task_id",
        "after_task_id",
        "before_batch_id",
        "after_batch_id",
        "mode",
        "status",
        "note",
    ):
        if key in body and body.get(key) is not None:
            setattr(j, key, str(body.get(key) or ""))
    j.updated_at = _utcnow()
    db.commit()
    return _job_out(j)


def delete_job(db: Session, job_id: str) -> None:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    run_ids = [
        rid for (rid,) in db.query(BizCompareRun.id).filter(BizCompareRun.job_id == job_id).all()
    ]
    if run_ids:
        db.query(BizCompareDiff).filter(BizCompareDiff.run_id.in_(run_ids)).delete(
            synchronize_session=False
        )
    db.query(BizCompareRun).filter(BizCompareRun.job_id == job_id).delete()
    db.delete(j)
    db.commit()


def _resolve_after_batch(db: Session, job: BizCompareJob) -> str:
    if job.mode != "auto":
        return str(job.after_batch_id or "")
    task_id = str(job.after_task_id or "")
    if not task_id:
        return str(job.after_batch_id or "")
    latest = (
        db.query(BizStateBatch)
        .filter(
            BizStateBatch.task_id == task_id,
            BizStateBatch.status.in_(("success", "partial")),
        )
        .order_by(BizStateBatch.started_at.desc())
        .first()
    )
    return str(latest.id) if latest else ""


def _run_sheet(
    db: Session,
    *,
    sheet: dict[str, Any],
    before_batch_id: str,
    after_batch_id: str,
    port_map: dict[str, str],
) -> dict[str, Any]:
    key_fields = list(sheet.get("key_fields") or [])
    iface_fields = list(sheet.get("iface_fields") or [])
    compare_fields = list(sheet.get("compare_fields") or [])
    mode = "presence" if not compare_fields else "fields"
    before_rows = _load_metric_rows(db, batch_id=before_batch_id, metric_id=sheet["metric_id"])
    after_rows = _load_metric_rows(db, batch_id=after_batch_id, metric_id=sheet["metric_id"])
    result = compare_rows(
        before_rows=before_rows,
        after_rows=after_rows,
        key_fields=key_fields,
        iface_fields=iface_fields,
        compare_fields=compare_fields,
        port_map=port_map,
    )
    return {
        "metric_id": sheet["metric_id"],
        "key_fields": key_fields,
        "iface_fields": iface_fields,
        "compare_fields": compare_fields,
        "mode": mode,
        "summary": result["summary"],
        "diffs": result["diffs"],
        "mapping_stats": result["mapping_stats"],
    }


def run_compare(db: Session, job_id: str, *, force_after_batch_id: str = "") -> dict[str, Any]:
    j = db.get(BizCompareJob, job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job_not_found")
    tpl = db.get(BizCompareTemplate, j.template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="template_not_found")
    before_batch_id = str(j.before_batch_id or "")
    after_batch_id = str(force_after_batch_id or "").strip() or _resolve_after_batch(db, j)
    if not before_batch_id or not after_batch_id:
        raise HTTPException(status_code=400, detail="before_and_after_batch_required")
    if not db.get(BizStateBatch, before_batch_id) or not db.get(BizStateBatch, after_batch_id):
        raise HTTPException(status_code=404, detail="batch_not_found")

    sheets_cfg = template_metrics(tpl)
    if not sheets_cfg:
        raise HTTPException(status_code=400, detail="template_has_no_metrics")

    pmap = _port_map_dict(db, j.mapping_id)
    sheet_results: list[dict[str, Any]] = []
    agg = {
        "before_count": 0,
        "after_count": 0,
        "added": 0,
        "removed": 0,
        "changed": 0,
        "unchanged": 0,
    }
    mapping_by_metric: dict[str, Any] = {}
    for sheet in sheets_cfg:
        one = _run_sheet(
            db,
            sheet=sheet,
            before_batch_id=before_batch_id,
            after_batch_id=after_batch_id,
            port_map=pmap,
        )
        sheet_results.append(one)
        s = one["summary"]
        for k in agg:
            agg[k] += int(s.get(k) or 0)
        mapping_by_metric[one["metric_id"]] = one["mapping_stats"]

    first = sheet_results[0]
    field_counts: dict[str, int] = {}
    for s in sheet_results:
        for d in list(s.get("diffs") or []):
            if str(d.get("kind") or "") != "changed":
                continue
            for fname in d.get("changes") or {}:
                field_counts[str(fname)] = field_counts.get(str(fname), 0) + 1
    top_fields = sorted(
        [{"field": k, "count": v} for k, v in field_counts.items()],
        key=lambda x: (-int(x["count"]), str(x["field"])),
    )[:8]

    run_id = uuid4().hex
    # Persist counts/meta only — diffs go to biz_compare_diff rows
    summary_payload = {
        **agg,
        "sheet_count": len(sheet_results),
        "top_changed_fields": top_fields,
        "sheets": [
            {
                "metric_id": s["metric_id"],
                "key_fields": s["key_fields"],
                "iface_fields": s["iface_fields"],
                "compare_fields": s["compare_fields"],
                "mode": s["mode"],
                "summary": s["summary"],
            }
            for s in sheet_results
        ],
    }

    run = BizCompareRun(
        id=run_id,
        job_id=j.id,
        template_id=tpl.id,
        mapping_id=j.mapping_id,
        before_batch_id=before_batch_id,
        after_batch_id=after_batch_id,
        metric_id=first["metric_id"],
        status="success",
        summary_json=summary_payload,
        diffs_json=[],
        mapping_stats_json=mapping_by_metric,
        message="",
        created_at=_utcnow(),
    )
    db.add(run)
    db.flush()
    for s in sheet_results:
        _persist_sheet_diffs(
            db,
            run_id=run_id,
            metric_id=str(s["metric_id"]),
            diffs=list(s.get("diffs") or []),
        )
    j.updated_at = _utcnow()
    if j.mode == "manual":
        j.after_batch_id = after_batch_id
    db.commit()
    return get_run(db, run.id)


def _csv_cell(v: Any) -> str:
    s = "" if v is None else str(v)
    if any(ch in s for ch in ",\"\n\r"):
        return '"' + s.replace('"', '""') + '"'
    return s


def _sheet_csv(sheet: dict[str, Any]) -> str:
    keys = list(sheet.get("key_fields") or [])
    compare = list(sheet.get("compare_fields") or [])
    headers = ["kind", *keys]
    for f in compare:
        headers.append(f"{f}__pre")
        headers.append(f"{f}__post")
    lines = [",".join(_csv_cell(h) for h in headers)]
    for d in list(sheet.get("diffs") or []):
        kind = str(d.get("kind") or "")
        pre = dict(d.get("mapped_before") or d.get("before") or {})
        post = dict(d.get("after") or {})
        key = dict(d.get("key") or {})
        row = [kind]
        for k in keys:
            row.append(key.get(k, pre.get(k, post.get(k, ""))))
        for f in compare:
            if kind == "added":
                row.append("")
                row.append(post.get(f, ""))
            elif kind == "removed":
                row.append(pre.get(f, ""))
                row.append("")
            else:
                row.append(pre.get(f, ""))
                row.append(post.get(f, ""))
        lines.append(",".join(_csv_cell(x) for x in row))
    return "\ufeff" + "\n".join(lines) + "\n"


def _enrich_summary(summary: dict[str, Any], sheets: list[dict[str, Any]]) -> dict[str, Any]:
    added = int(summary.get("added") or 0)
    removed = int(summary.get("removed") or 0)
    changed = int(summary.get("changed") or 0)
    unchanged = int(summary.get("unchanged") or 0)
    before_count = int(summary.get("before_count") or 0)
    after_count = int(summary.get("after_count") or 0)
    total = added + removed + changed + unchanged
    matched = changed + unchanged
    diff_count = added + removed + changed
    pass_rate = round((unchanged / matched) * 100, 1) if matched else (100.0 if total == 0 else 0.0)
    diff_rate = round((diff_count / total) * 100, 1) if total else 0.0

    sheet_cards: list[dict[str, Any]] = []
    for sh in sheets:
        ss = dict(sh.get("summary") or {})
        sa = int(ss.get("added") or 0)
        sr = int(ss.get("removed") or 0)
        sc = int(ss.get("changed") or 0)
        su = int(ss.get("unchanged") or 0)
        st = sa + sr + sc + su
        sm = sc + su
        sheet_cards.append(
            {
                "metric_id": sh.get("metric_id") or "",
                "mode": sh.get("mode") or ("presence" if not sh.get("compare_fields") else "fields"),
                "added": sa,
                "removed": sr,
                "changed": sc,
                "unchanged": su,
                "before_count": int(ss.get("before_count") or 0),
                "after_count": int(ss.get("after_count") or 0),
                "diff_count": sa + sr + sc,
                "pass_rate": round((su / sm) * 100, 1) if sm else (100.0 if st == 0 else 0.0),
            }
        )

    top_fields = list(summary.get("top_changed_fields") or [])
    if not top_fields:
        # Legacy runs that still embed diffs in summary_json
        field_counts: dict[str, int] = {}
        for sh in sheets:
            for d in list(sh.get("diffs") or []):
                if str(d.get("kind") or "") != "changed":
                    continue
                for fname in d.get("changes") or {}:
                    field_counts[str(fname)] = field_counts.get(str(fname), 0) + 1
        top_fields = sorted(
            [{"field": k, "count": v} for k, v in field_counts.items()],
            key=lambda x: (-int(x["count"]), str(x["field"])),
        )[:8]

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
        "before_count": before_count,
        "after_count": after_count,
        "sheet_count": int(summary.get("sheet_count") or len(sheets) or 0),
        "total_rows": total,
        "matched_rows": matched,
        "diff_count": diff_count,
        "pass_rate": pass_rate,
        "diff_rate": diff_rate,
        "ok": diff_count == 0,
        "sheet_cards": sheet_cards,
        "top_changed_fields": top_fields,
    }


def get_run(db: Session, run_id: str) -> dict[str, Any]:
    r = db.get(BizCompareRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    tpl = db.get(BizCompareTemplate, r.template_id) if r.template_id else None
    summary = dict(r.summary_json or {})
    raw_sheets = _sheet_meta_from_summary(summary, r, tpl)
    # Never return full diffs in run detail (million-row safe)
    sheets = [
        {
            "metric_id": sh.get("metric_id") or "",
            "key_fields": list(sh.get("key_fields") or []),
            "iface_fields": list(sh.get("iface_fields") or []),
            "compare_fields": list(sh.get("compare_fields") or []),
            "mode": sh.get("mode") or ("presence" if not sh.get("compare_fields") else "fields"),
            "summary": dict(sh.get("summary") or {}),
        }
        for sh in raw_sheets
    ]
    enriched = _enrich_summary(summary, raw_sheets)
    stored = "rows" if _run_has_diff_rows(db, run_id) else "inline"
    return {
        "id": r.id,
        "job_id": r.job_id,
        "template_id": r.template_id,
        "mapping_id": r.mapping_id,
        "before_batch_id": r.before_batch_id,
        "after_batch_id": r.after_batch_id,
        "metric_id": r.metric_id,
        "status": r.status,
        "summary": enriched,
        "sheets": sheets,
        "diffs": [],
        "diffs_stored": stored,
        "mapping_stats": r.mapping_stats_json or {},
        "message": r.message,
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "template": _template_out(tpl) if tpl else None,
    }


def list_run_diffs(
    db: Session,
    run_id: str,
    *,
    metric_id: str = "",
    kind: str = "diff",
    kw: str = "",
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    r = db.get(BizCompareRun, run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run_not_found")
    page_n = max(1, int(page or 1))
    size_n = max(1, min(500, int(page_size or 100)))
    kind_n = (kind or "diff").strip().lower()
    kw_n = (kw or "").strip()

    summary = dict(r.summary_json or {})
    tpl = db.get(BizCompareTemplate, r.template_id) if r.template_id else None
    sheets = _sheet_meta_from_summary(summary, r, tpl)
    mid = (metric_id or "").strip() or (sheets[0].get("metric_id") if sheets else r.metric_id) or ""

    if _run_has_diff_rows(db, run_id):
        q = db.query(BizCompareDiff).filter(
            BizCompareDiff.run_id == run_id,
            BizCompareDiff.metric_id == mid,
        )
        if kind_n == "diff":
            q = q.filter(BizCompareDiff.kind.in_(("added", "removed", "changed")))
        elif kind_n != "all":
            q = q.filter(BizCompareDiff.kind == kind_n)
        if kw_n:
            q = q.filter(BizCompareDiff.search_text.ilike(f"%{kw_n}%"))
        total = q.count()
        rows = (
            q.order_by(BizCompareDiff.seq.asc(), BizCompareDiff.id.asc())
            .offset((page_n - 1) * size_n)
            .limit(size_n)
            .all()
        )
        return {
            "total": total,
            "page": page_n,
            "page_size": size_n,
            "metric_id": mid,
            "items": [_diff_row_out(x) for x in rows],
        }

    # Legacy: diffs embedded in summary_json / diffs_json
    sheet = next((s for s in sheets if str(s.get("metric_id") or "") == mid), None)
    if sheet is None and sheets:
        sheet = sheets[0]
        mid = str(sheet.get("metric_id") or mid)
    inline = list((sheet or {}).get("diffs") or [])
    if not inline and mid == r.metric_id:
        inline = list(r.diffs_json or [])
    filtered = _filter_inline_diffs(inline, kind=kind_n, kw=kw_n)
    total = len(filtered)
    start = (page_n - 1) * size_n
    page_items = filtered[start : start + size_n]
    return {
        "total": total,
        "page": page_n,
        "page_size": size_n,
        "metric_id": mid,
        "items": page_items,
    }


def _iter_sheet_diffs(db: Session, run_id: str, metric_id: str) -> list[dict[str, Any]]:
    """Load all diffs for one sheet (export). Prefer row table; fall back to inline."""
    if _run_has_diff_rows(db, run_id):
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            rows = (
                db.query(BizCompareDiff)
                .filter(BizCompareDiff.run_id == run_id, BizCompareDiff.metric_id == metric_id)
                .order_by(BizCompareDiff.seq.asc(), BizCompareDiff.id.asc())
                .offset(offset)
                .limit(_DIFF_CHUNK)
                .all()
            )
            if not rows:
                break
            out.extend(_diff_row_out(x) for x in rows)
            offset += len(rows)
            if len(rows) < _DIFF_CHUNK:
                break
        return out
    r = db.get(BizCompareRun, run_id)
    if not r:
        return []
    summary = dict(r.summary_json or {})
    sheets = list(summary.get("sheets") or [])
    for sh in sheets:
        if str(sh.get("metric_id") or "") == metric_id:
            return list(sh.get("diffs") or [])
    if metric_id == r.metric_id:
        return list(r.diffs_json or [])
    return []


def export_run_zip(db: Session, run_id: str) -> bytes:
    detail = get_run(db, run_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        s = detail.get("summary") or {}
        manifest = [
            f"run_id={detail.get('id')}",
            f"job_id={detail.get('job_id')}",
            f"before_batch_id={detail.get('before_batch_id')}",
            f"after_batch_id={detail.get('after_batch_id')}",
            f"created_at={detail.get('created_at')}",
            f"pass_rate={s.get('pass_rate')}%",
            f"diff_count={s.get('diff_count')}",
            f"added={s.get('added')} removed={s.get('removed')} "
            f"changed={s.get('changed')} unchanged={s.get('unchanged')}",
            f"before_count={s.get('before_count')} after_count={s.get('after_count')}",
            "",
            "sheets:",
        ]
        for card in list(s.get("sheet_cards") or []):
            manifest.append(
                f"- {card.get('metric_id')}: diff={card.get('diff_count')} "
                f"pass={card.get('pass_rate')}% "
                f"+{card.get('added')}/-{card.get('removed')}/~{card.get('changed')}/= {card.get('unchanged')}"
            )
        zf.writestr("manifest.txt", "\n".join(manifest) + "\n")
        for sheet in list(detail.get("sheets") or []):
            mid = str(sheet.get("metric_id") or "sheet")
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in mid)[:80] or "sheet"
            sheet_full = {
                **sheet,
                "diffs": _iter_sheet_diffs(db, run_id, mid),
            }
            zf.writestr(f"tables/{safe}.csv", _sheet_csv(sheet_full))
        sum_lines = ["metric_id,mode,before,after,added,removed,changed,unchanged,diff_count,pass_rate"]
        for card in list(s.get("sheet_cards") or []):
            sum_lines.append(
                ",".join(
                    _csv_cell(x)
                    for x in (
                        card.get("metric_id"),
                        card.get("mode"),
                        card.get("before_count"),
                        card.get("after_count"),
                        card.get("added"),
                        card.get("removed"),
                        card.get("changed"),
                        card.get("unchanged"),
                        card.get("diff_count"),
                        card.get("pass_rate"),
                    )
                )
            )
        zf.writestr("tables/_summary.csv", "\ufeff" + "\n".join(sum_lines) + "\n")
    return buf.getvalue()


def list_runs(db: Session, job_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        db.query(BizCompareRun)
        .filter(BizCompareRun.job_id == job_id)
        .order_by(BizCompareRun.created_at.desc())
        .limit(max(1, min(100, int(limit))))
        .all()
    )
    return [
        {
            "id": r.id,
            "before_batch_id": r.before_batch_id,
            "after_batch_id": r.after_batch_id,
            "status": r.status,
            "summary": {
                k: (r.summary_json or {}).get(k, 0)
                for k in ("added", "removed", "changed", "unchanged", "sheet_count")
            },
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]


def try_auto_compare_for_task(db: Session, task_id: str, batch_id: str) -> int:
    """When a new after batch lands, run auto jobs pinned to that after task."""
    jobs = (
        db.query(BizCompareJob)
        .filter(BizCompareJob.mode == "auto", BizCompareJob.after_task_id == task_id)
        .all()
    )
    n = 0
    for j in jobs:
        if not j.before_batch_id:
            continue
        try:
            run_compare(db, j.id, force_after_batch_id=batch_id)
            n += 1
        except Exception:
            continue
    return n
