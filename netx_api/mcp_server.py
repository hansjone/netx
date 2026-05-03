from __future__ import annotations

import json
import sys
from typing import Any

from .db import SessionLocal
from .importer import aggregate_alarms, query_alarms
from .models import AlarmBatch


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
    raise ValueError(f"unknown tool: {name}")


def main() -> None:
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
