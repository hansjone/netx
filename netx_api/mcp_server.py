from __future__ import annotations

import json
import sys
from typing import Any

from .db import Base, SessionLocal, engine
from .importer import aggregate_alarms, query_alarms
from .models import AlarmBatch, UmeAlarmCurrent, UmeAlarmHistory, UmeInventoryNE
from .ume_client import UMEClient
from .ume_sync_service import sync_alarms_current, sync_alarms_history_full, sync_inventory_full
from .ume_token_store import (
    clear_shared_token,
    load_shared_token,
    release_refresh_lock,
    save_shared_token,
    try_acquire_refresh_lock,
    wait_for_token_update,
)

_UME_CLIENT_SINGLETON = UMEClient(
    token_loader=lambda: load_shared_token(),
    token_saver=lambda token, exp: save_shared_token(token, exp),
    token_clearer=lambda: clear_shared_token(),
    lock_acquirer=lambda: try_acquire_refresh_lock(),
    lock_releaser=lambda: release_refresh_lock(),
    token_waiter=lambda min_exp: wait_for_token_update(min_expires_at_epoch_s=float(min_exp)),
)


def _ok(rid: Any, result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _err(rid: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, ensure_ascii=False)
        + "\n"
    )
    sys.stdout.flush()


def _tool_list() -> list[dict[str, Any]]:
    return [
        {
            "name": "queryAlarms",
            "description": "Query normalized alarms with filters and pagination.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "batch_id": {"type": "string"},
                    "alarm_code": {"type": "string"},
                    "severity": {"type": "string"},
                    "ne_name": {"type": "string"},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "aggregateAlarms",
            "description": "Aggregate alarms by severity_norm, alarm_code or ne_name.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "group_by": {
                        "type": "string",
                        "enum": ["severity_norm", "alarm_code", "ne_name"],
                    },
                    "batch_id": {"type": "string"},
                },
                "required": ["group_by"],
                "additionalProperties": False,
            },
        },
        {
            "name": "getImportBatch",
            "description": "Get imported batch summary by batch_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"batch_id": {"type": "string"}},
                "required": ["batch_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "runDiagnostics",
            "description": "Generate quick diagnostics summary by batch.",
            "inputSchema": {
                "type": "object",
                "properties": {"batch_id": {"type": "string"}},
                "required": ["batch_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "umeSync",
            "description": "Trigger UME sync for inventory/current/history domains.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "domains": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["inventory", "alarms_current", "alarms_history"]},
                    },
                    "trigger_mode": {"type": "string", "enum": ["manual", "schedule"]},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "umeListNE",
            "description": "List UME network elements from inventory table.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "umeGetNE",
            "description": "Get UME network element by ne_id.",
            "inputSchema": {
                "type": "object",
                "properties": {"ne_id": {"type": "string"}},
                "required": ["ne_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "umeListCurrentAlarms",
            "description": "List current UME alarms from current table.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "is_cleared": {"type": "string"},
                    "ne_id": {"type": "string"},
                    "keyword": {"type": "string"},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "umeListHistoryAlarms",
            "description": "List historical UME alarms from history table.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string"},
                    "ne_id": {"type": "string"},
                    "keyword": {"type": "string"},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "additionalProperties": False,
            },
        },
    ]


def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        if name == "queryAlarms":
            total, rows = query_alarms(
                db,
                batch_id=str(args.get("batch_id") or "").strip() or None,
                alarm_code=str(args.get("alarm_code") or "").strip() or None,
                severity=str(args.get("severity") or "").strip() or None,
                ne_name=str(args.get("ne_name") or "").strip() or None,
                page=max(1, int(args.get("page") or 1)),
                page_size=min(200, max(1, int(args.get("page_size") or 50))),
            )
            payload = {
                "total": total,
                "items": [
                    {
                        "id": r.id,
                        "batch_id": r.batch_id,
                        "alarm_time": r.alarm_time.isoformat(),
                        "severity_norm": r.severity_norm,
                        "ne_name": r.ne_name,
                        "alarm_code": r.alarm_code,
                        "alarm_name": r.alarm_name,
                    }
                    for r in rows
                ],
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "aggregateAlarms":
            group_by = str(args.get("group_by") or "").strip()
            rows = aggregate_alarms(
                db,
                group_by=group_by,
                batch_id=str(args.get("batch_id") or "").strip() or None,
            )
            payload = {"group_by": group_by, "buckets": [{"key": k, "count": v} for k, v in rows]}
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "getImportBatch":
            batch_id = str(args.get("batch_id") or "").strip()
            batch = db.get(AlarmBatch, batch_id)
            if not batch:
                return {"content": [{"type": "text", "text": json.dumps({"error": "batch_not_found"})}], "isError": True}
            payload = {
                "batch_id": batch.batch_id,
                "total_rows": batch.total_rows,
                "success_rows": batch.success_rows,
                "failed_rows": batch.failed_rows,
                "status": batch.status,
                "created_at": batch.created_at.isoformat(),
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "runDiagnostics":
            batch_id = str(args.get("batch_id") or "").strip()
            sev_rows = aggregate_alarms(db, group_by="severity_norm", batch_id=batch_id)
            code_rows = aggregate_alarms(db, group_by="alarm_code", batch_id=batch_id)[:5]
            ne_rows = aggregate_alarms(db, group_by="ne_name", batch_id=batch_id)[:5]
            sev_map = {k: v for k, v in sev_rows}
            findings: list[str] = []
            actions: list[str] = []
            risk_level = "low"
            if int(sev_map.get("critical", 0)) > 0:
                findings.append("critical 告警存在，建议优先确认核心网元影响。")
                actions.append("优先处理 critical 告警，确认影响面并升级。")
                risk_level = "high"
            if int(sev_map.get("warning", 0)) > int(sev_map.get("major", 0)):
                findings.append("warning 占比较高，疑似阈值型告警风暴。")
                actions.append("检查高频 warning 告警码是否集中在单一阈值策略。")
                if risk_level != "high":
                    risk_level = "medium"
            if not findings:
                findings.append("分布相对均衡，建议按 top 告警码进一步排查。")
                actions.append("按 top 告警码和网元继续下钻分析。")
            payload = {
                "batch_id": batch_id,
                "risk_level": risk_level,
                "severity_summary": [{"key": k, "count": v} for k, v in sev_rows],
                "top_alarm_codes": [{"key": k, "count": v} for k, v in code_rows],
                "top_ne": [{"key": k, "count": v} for k, v in ne_rows],
                "findings": findings,
                "actions": actions,
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "umeSync":
            domains_raw = args.get("domains")
            domains = []
            if isinstance(domains_raw, list):
                domains = [str(x).strip() for x in domains_raw if str(x).strip()]
            if not domains:
                domains = ["inventory", "alarms_current", "alarms_history"]
            trigger_mode = str(args.get("trigger_mode") or "manual").strip().lower()
            if trigger_mode not in {"manual", "schedule"}:
                trigger_mode = "manual"
            client = _UME_CLIENT_SINGLETON
            results: list[dict[str, Any]] = []
            if "inventory" in domains:
                j = sync_inventory_full(db, client, trigger_mode=trigger_mode)
                results.append(
                    {
                        "domain": "inventory",
                        "status": j.status,
                        "pulled_count": int(j.pulled_count or 0),
                        "inserted_count": int(j.inserted_count or 0),
                        "updated_count": int(j.updated_count or 0),
                        "error_message": str(j.error_message or ""),
                    }
                )
            if "alarms_current" in domains:
                j, b = sync_alarms_current(db, client, trigger_mode=trigger_mode)
                results.append(
                    {
                        "domain": "alarms_current",
                        "status": j.status,
                        "batch_id": str(b.batch_id),
                        "pulled_count": int(j.pulled_count or 0),
                        "inserted_count": int(j.inserted_count or 0),
                        "updated_count": int(j.updated_count or 0),
                        "error_message": str(j.error_message or ""),
                    }
                )
            if "alarms_history" in domains:
                j, b = sync_alarms_history_full(db, client, trigger_mode=trigger_mode)
                results.append(
                    {
                        "domain": "alarms_history",
                        "status": j.status,
                        "batch_id": str(b.batch_id),
                        "pulled_count": int(j.pulled_count or 0),
                        "inserted_count": int(j.inserted_count or 0),
                        "updated_count": int(j.updated_count or 0),
                        "error_message": str(j.error_message or ""),
                    }
                )
            return {"content": [{"type": "text", "text": json.dumps({"ok": True, "jobs": results}, ensure_ascii=False)}]}
        if name == "umeListNE":
            stmt = db.query(UmeInventoryNE)
            keyword = str(args.get("keyword") or "").strip()
            if keyword:
                stmt = stmt.filter(
                    UmeInventoryNE.ne_id.contains(keyword)
                    | UmeInventoryNE.ne_name.contains(keyword)
                    | UmeInventoryNE.user_label.contains(keyword)
                    | UmeInventoryNE.ip_address.contains(keyword)
                )
            page = max(1, int(args.get("page") or 1))
            page_size = min(500, max(1, int(args.get("page_size") or 50)))
            total = int(stmt.count())
            rows = stmt.order_by(UmeInventoryNE.ne_id.asc()).offset((page - 1) * page_size).limit(page_size).all()
            payload = {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [
                    {
                        "ne_id": str(x.ne_id or ""),
                        "ne_name": str(x.ne_name or ""),
                        "user_label": str(x.user_label or ""),
                        "ip_address": str(x.ip_address or ""),
                        "ne_type": str(x.ne_type or ""),
                    }
                    for x in rows
                ],
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "umeGetNE":
            ne_id = str(args.get("ne_id") or "").strip()
            row = db.get(UmeInventoryNE, ne_id)
            if not row:
                return {"content": [{"type": "text", "text": json.dumps({"error": "ume_ne_not_found"})}], "isError": True}
            payload = {
                "ne_id": str(row.ne_id or ""),
                "ne_name": str(row.ne_name or ""),
                "user_label": str(row.user_label or ""),
                "ip_address": str(row.ip_address or ""),
                "ne_type": str(row.ne_type or ""),
                "vendor": str(row.vendor or ""),
                "raw_json": str(row.raw_json or "{}"),
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "umeListCurrentAlarms":
            stmt = db.query(UmeAlarmCurrent)
            severity = str(args.get("severity") or "").strip()
            is_cleared = str(args.get("is_cleared") or "").strip()
            ne_id = str(args.get("ne_id") or "").strip()
            keyword = str(args.get("keyword") or "").strip()
            if severity:
                stmt = stmt.filter(UmeAlarmCurrent.perceived_severity == severity)
            if is_cleared:
                stmt = stmt.filter(UmeAlarmCurrent.is_cleared == is_cleared)
            if ne_id:
                stmt = stmt.filter(UmeAlarmCurrent.ne_id == ne_id)
            if keyword:
                stmt = stmt.filter(
                    UmeAlarmCurrent.alarm_key.contains(keyword)
                    | UmeAlarmCurrent.object_name.contains(keyword)
                    | UmeAlarmCurrent.ne_name.contains(keyword)
                    | UmeAlarmCurrent.user_label.contains(keyword)
                )
            page = max(1, int(args.get("page") or 1))
            page_size = min(500, max(1, int(args.get("page_size") or 50)))
            total = int(stmt.count())
            rows = stmt.order_by(UmeAlarmCurrent.last_seen_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
            payload = {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [
                    {
                        "alarm_key": str(x.alarm_key or ""),
                        "ne_id": str(x.ne_id or ""),
                        "ne_name": str(x.ne_name or ""),
                        "user_label": str(x.user_label or ""),
                        "object_name": str(x.object_name or ""),
                        "perceived_severity": str(x.perceived_severity or ""),
                        "is_cleared": str(x.is_cleared or ""),
                        "time_created": str(x.time_created or ""),
                    }
                    for x in rows
                ],
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if name == "umeListHistoryAlarms":
            stmt = db.query(UmeAlarmHistory)
            severity = str(args.get("severity") or "").strip()
            ne_id = str(args.get("ne_id") or "").strip()
            keyword = str(args.get("keyword") or "").strip()
            if severity:
                stmt = stmt.filter(UmeAlarmHistory.perceived_severity == severity)
            if ne_id:
                stmt = stmt.filter(UmeAlarmHistory.ne_id == ne_id)
            if keyword:
                stmt = stmt.filter(
                    UmeAlarmHistory.alarm_key.contains(keyword)
                    | UmeAlarmHistory.object_name.contains(keyword)
                    | UmeAlarmHistory.ne_name.contains(keyword)
                    | UmeAlarmHistory.user_label.contains(keyword)
                )
            page = max(1, int(args.get("page") or 1))
            page_size = min(500, max(1, int(args.get("page_size") or 50)))
            total = int(stmt.count())
            rows = stmt.order_by(UmeAlarmHistory.last_seen_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
            payload = {
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": [
                    {
                        "alarm_key": str(x.alarm_key or ""),
                        "ne_id": str(x.ne_id or ""),
                        "ne_name": str(x.ne_name or ""),
                        "user_label": str(x.user_label or ""),
                        "object_name": str(x.object_name or ""),
                        "perceived_severity": str(x.perceived_severity or ""),
                        "is_cleared": str(x.is_cleared or ""),
                        "time_created": str(x.time_created or ""),
                    }
                    for x in rows
                ],
            }
            return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    raise ValueError(f"unknown tool: {name}")


def main() -> None:
    try:
        Base.metadata.create_all(bind=engine)
    except Exception:
        pass
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception:
            continue
        rid = req.get("id")
        method = str(req.get("method") or "")
        params = req.get("params") if isinstance(req.get("params"), dict) else {}

        try:
            if method == "initialize":
                _ok(
                    rid,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "netx-mcp", "version": "0.1.0"},
                    },
                )
                continue
            if method == "notifications/initialized":
                continue
            if method == "tools/list":
                _ok(rid, {"tools": _tool_list()})
                continue
            if method == "tools/call":
                name = str(params.get("name") or "")
                args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                _ok(rid, _call_tool(name, args))
                continue
            _err(rid, -32601, f"method not found: {method}")
        except Exception as exc:
            _err(rid, -32000, str(exc))


if __name__ == "__main__":
    main()
