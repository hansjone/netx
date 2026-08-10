"""MCP tool schemas and HTTP-backed handlers (UME + managed NE)."""

from __future__ import annotations

import os
from typing import Any, Callable

from .http_client import http_json, http_post_json, mcp_from_handler_result, quote_ne_id

_EXEC_MAX_COMMANDS_CAP = 50
_EXEC_MAX_COMMANDS_DEFAULT = 5


def exec_max_commands() -> int:
    """Mirror netx API NETX_NE_EXEC_MAX_COMMANDS (default 5, hard cap 50)."""
    try:
        raw = int(os.getenv("NETX_NE_EXEC_MAX_COMMANDS") or _EXEC_MAX_COMMANDS_DEFAULT)
    except ValueError:
        raw = _EXEC_MAX_COMMANDS_DEFAULT
    return max(1, min(_EXEC_MAX_COMMANDS_CAP, raw))


UME_RAW_GROUP_FIELDS = [
    "alarm_alarm_key",
    "alarm_host_name",
    "alarm_ne_id",
    "alarm_object_name",
    "alarm_event_type",
    "alarm_native_probable_cause",
    "alarm_perceived_severity",
    "alarm_is_cleared",
    "alarm_time_created",
    "alarm_root_cause_alarm_indication",
    "ne_ne_id",
    "ne_ne_name",
    "ne_user_label",
    "ne_ip_address",
    "ne_ipv6_address",
    "ne_ne_type",
    "ne_device_level",
    "ne_host_name",
    "ne_location",
    "ne_hardware_version",
    "ne_loopback",
    "ne_consistent_state",
    "ne_interface_version",
    "ne_mac",
    "ne_admin_status",
    "ne_address_type",
    "ne_connection_status",
    "ne_maintain_status",
    "ne_net_mask",
    "ne_create_time",
    "ne_creator",
    "ne_vendor",
    "ne_source_type",
    "ne_exists",
]

_UME_RAW_FIELD_PRESETS: dict[str, list[str]] = {
    "brief": [
        "alarm_alarm_key",
        "alarm_host_name",
        "alarm_perceived_severity",
        "alarm_event_type",
        "alarm_last_seen_at",
        "ne_host_name",
        "ne_user_label",
        "ne_ne_name",
        "ne_ip_address",
        "ne_exists",
    ],
    "evidence": [
        "alarm_alarm_key",
        "alarm_host_name",
        "alarm_object_name",
        "alarm_event_type",
        "alarm_native_probable_cause",
        "alarm_perceived_severity",
        "alarm_is_cleared",
        "alarm_time_created",
        "alarm_last_seen_at",
        "ne_host_name",
        "ne_user_label",
        "ne_ne_name",
        "ne_ip_address",
        "ne_connection_status",
        "ne_exists",
    ],
    "ne_debug": [
        "alarm_alarm_key",
        "alarm_ne_id",
        "alarm_perceived_severity",
        "alarm_last_seen_at",
        "ne_user_label",
        "ne_ne_name",
        "ne_ip_address",
        "ne_ipv6_address",
        "ne_device_level",
        "ne_host_name",
        "ne_connection_status",
        "ne_admin_status",
        "ne_address_type",
        "ne_maintain_status",
        "ne_exists",
    ],
}


def _query_ume_alarms(args: dict[str, Any]) -> dict[str, Any]:
    page = max(1, int(args.get("page") or 1))
    if page > 2:
        page = 2
    page_size = min(500, max(1, int(args.get("page_size") or 50)))
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if str(args.get("severity") or "").strip():
        params["severity"] = str(args.get("severity")).strip()
    keyword = str(args.get("keyword") or "").strip()
    ne_name = str(args.get("ne_name") or "").strip()
    if keyword:
        params["keyword"] = keyword
    elif ne_name:
        params["keyword"] = ne_name
    if str(args.get("ne_id") or "").strip():
        params["ne_id"] = str(args.get("ne_id")).strip()
    if str(args.get("host_name") or "").strip():
        params["host_name"] = str(args.get("host_name")).strip()
    for k in ("time_from", "time_to"):
        if str(args.get(k) or "").strip():
            params[k] = str(args.get(k)).strip()
    return http_json("GET", "/v1/ume/alarms", params=params)


def _aggregate_ume_alarms(args: dict[str, Any]) -> dict[str, Any]:
    # Agents often pass group_by here (docs historically mixed tools). Route to raw aggregate.
    group_by = str(args.get("group_by") or "").strip()
    if group_by:
        return _aggregate_ume_alarms_raw(args)
    # Default top 50 named NEs — missing host buckets reported separately.
    top_ne = max(0, min(500, int(args.get("top_ne") if args.get("top_ne") is not None else 50)))
    params: dict[str, Any] = {"top_ne": top_ne}
    if "exclude_missing_host" in args:
        params["exclude_missing_host"] = bool(args.get("exclude_missing_host"))
    for k in ("severity", "time_from", "time_to"):
        if str(args.get(k) or "").strip():
            params[k] = str(args.get(k)).strip()
    return http_json("GET", "/v1/ume/alarms/aggregate", params=params)


def _run_ume_diagnostics(args: dict[str, Any]) -> dict[str, Any]:
    _ = args
    return http_json("GET", "/v1/ume/diagnostics", params=None)


def _query_ume_ne_inventory(args: dict[str, Any]) -> dict[str, Any]:
    page = max(1, int(args.get("page") or 1))
    page_size = min(500, max(1, int(args.get("page_size") or 50)))
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if str(args.get("keyword") or "").strip():
        params["keyword"] = str(args.get("keyword")).strip()
    return http_json("GET", "/v1/ume/inventory/ne", params=params)


def _get_ume_ne(args: dict[str, Any]) -> dict[str, Any]:
    ne_id = str(args.get("ne_id") or "").strip()
    if not ne_id:
        return {"ok": False, "error": "ne_id_required", "error_code": "ne_id_required"}
    return http_json("GET", f"/v1/ume/inventory/ne/{quote_ne_id(ne_id)}", params=None)


def _query_ume_alarms_raw(args: dict[str, Any]) -> dict[str, Any]:
    page = max(1, int(args.get("page") or 1))
    page_size = min(500, max(1, int(args.get("page_size") or 50)))
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    for k in ("severity", "is_cleared", "ne_id", "event_type", "keyword", "time_from", "time_to", "order_by", "order"):
        v = str(args.get(k) or "").strip()
        if v:
            params[k] = v
    sf = args.get("select_fields")
    fields: list[str] = []
    if isinstance(sf, list):
        fields = [str(x).strip() for x in sf if str(x).strip()]
    if not fields:
        preset = str(args.get("field_preset") or "").strip().lower()
        fields = list(_UME_RAW_FIELD_PRESETS.get(preset) or [])
    if fields:
        params["select_fields"] = ",".join(fields)
    return http_json("GET", "/v1/ume/alarms/raw", params=params)


def _aggregate_ume_alarms_raw(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for k in (
        "group_by",
        "group_by2",
        "severity",
        "is_cleared",
        "ne_id",
        "event_type",
        "keyword",
        "time_from",
        "time_to",
        "limit",
    ):
        v = args.get(k)
        if v is None:
            continue
        sv = str(v).strip()
        if sv:
            params[k] = sv
    if "exclude_missing_host" in args:
        params["exclude_missing_host"] = bool(args.get("exclude_missing_host"))
    return http_json("GET", "/v1/ume/alarms/aggregate/raw", params=params)


def _list_ume_alarm_fields(args: dict[str, Any]) -> dict[str, Any]:
    _ = args
    return http_json("GET", "/v1/ume/alarms/fields", params=None)


def _sql_query_ume(args: dict[str, Any]) -> dict[str, Any]:
    sql = str(args.get("sql") or "").strip()
    limit = max(1, min(2000, int(args.get("limit") or 200)))
    statement_timeout_ms = max(0, min(30000, int(args.get("statement_timeout_ms") or 0)))
    if not sql:
        return {"ok": False, "error": "sql_required"}
    return http_post_json(
        "/v1/sql/ume_query",
        {"sql": sql, "limit": limit, "statement_timeout_ms": statement_timeout_ms},
        timeout=60.0,
    )


def _list_managed_ne(args: dict[str, Any]) -> dict[str, Any]:
    page = max(1, int(args.get("page") or 1))
    page_size = min(100, max(1, int(args.get("page_size") or 20)))
    keyword = str(args.get("keyword") or "").strip()
    vendor = str(args.get("vendor") or "").strip()
    connect_status = str(args.get("connect_status") or "").strip()
    if not (keyword or vendor or connect_status):
        return {"ok": False, "error": "managed_ne_filter_required", "error_code": "managed_ne_filter_required"}
    if keyword and len(keyword) < 2:
        return {"ok": False, "error": "managed_ne_keyword_too_short", "error_code": "managed_ne_keyword_too_short"}
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if vendor:
        params["vendor"] = vendor
    if connect_status:
        params["connect_status"] = connect_status
    return http_json("GET", "/v1/managed-ne", params=params)


def _get_managed_ne(args: dict[str, Any]) -> dict[str, Any]:
    ne_id = str(args.get("ne_id") or "").strip()
    if not ne_id:
        return {"ok": False, "error": "ne_id_required", "error_code": "ne_id_required"}
    return http_json("GET", f"/v1/managed-ne/{ne_id}", params=None)


def _exec_managed_ne(args: dict[str, Any]) -> dict[str, Any]:
    ne_id = str(args.get("ne_id") or "").strip()
    ume_ne_id = str(args.get("ume_ne_id") or "").strip()
    if bool(ne_id) == bool(ume_ne_id):
        return {
            "ok": False,
            "error": "exactly_one_of_ne_id_or_ume_ne_id_required",
            "error_code": "exactly_one_of_ne_id_or_ume_ne_id_required",
        }
    raw_cmds = args.get("commands")
    if not isinstance(raw_cmds, list) or not raw_cmds:
        return {"ok": False, "error": "commands_required", "error_code": "commands_required"}
    commands = [str(c).strip() for c in raw_cmds if str(c).strip()]
    if not commands:
        return {"ok": False, "error": "commands_required", "error_code": "commands_required"}
    if len(commands) > exec_max_commands():
        return {"ok": False, "error": "too_many_commands", "error_code": "too_many_commands"}
    body: dict[str, Any] = {"commands": commands}
    if ne_id:
        body["ne_id"] = ne_id
    if ume_ne_id:
        body["ume_ne_id"] = ume_ne_id
    rts = args.get("read_timeout_sec")
    if rts is not None:
        body["read_timeout_sec"] = int(rts)
    out = http_post_json("/v1/managed-ne/exec", body, timeout=300.0)
    if not out.get("ok"):
        return out
    data = out.get("data") or {}
    if isinstance(data, dict) and data.get("ok") is False:
        return {"ok": False, "data": data, "error": str(data.get("error") or "exec_failed")}
    return {"ok": True, "data": data}


def _list_cli_targets(args: dict[str, Any]) -> dict[str, Any]:
    page = max(1, int(args.get("page") or 1))
    page_size = min(500, max(1, int(args.get("page_size") or 50)))
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if str(args.get("source") or "").strip():
        params["source"] = str(args.get("source")).strip()
    if str(args.get("keyword") or "").strip():
        params["keyword"] = str(args.get("keyword")).strip()
    return http_json("GET", "/v1/cli/targets", params=params)


def _find_topology_paths(args: dict[str, Any]) -> dict[str, Any]:
    from_uid = str(args.get("from_ume_ne_id") or "").strip()
    from_mid = str(args.get("from_managed_ne_id") or "").strip()
    to_uid = str(args.get("to_ume_ne_id") or "").strip()
    to_mid = str(args.get("to_managed_ne_id") or "").strip()
    if bool(from_uid) == bool(from_mid):
        return {"ok": False, "error": "exactly_one_of_from_ume_ne_id_or_from_managed_ne_id_required"}
    if bool(to_uid) == bool(to_mid):
        return {"ok": False, "error": "exactly_one_of_to_ume_ne_id_or_to_managed_ne_id_required"}
    detail = str(args.get("detail") or "summary").strip().lower() or "summary"
    if detail not in {"summary", "full"}:
        detail = "summary"
    body: dict[str, Any] = {
        "max_paths": max(1, min(10, int(args.get("max_paths") or 3))),
        "max_hops": max(1, min(12, int(args.get("max_hops") or 6))),
        "layer": str(args.get("layer") or "physical").strip() or "physical",
        "detail": detail,
    }
    if from_uid:
        body["from_ume_ne_id"] = from_uid
    else:
        body["from_managed_ne_id"] = from_mid
    if to_uid:
        body["to_ume_ne_id"] = to_uid
    else:
        body["to_managed_ne_id"] = to_mid
    return http_post_json("/v1/topology/fabric/paths", body, timeout=30.0)


HTTP_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "queryUmeAlarms",
        "description": (
            "Query UME current alarms (each row includes host_name). "
            "Supports severity/ne_id/host_name/keyword, last_seen time_from/time_to, pagination. "
            "Prefer host_name for display; ne_id is for filters only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string"},
                "ne_id": {"type": "string", "description": "Filter only; do not show UUID to users"},
                "host_name": {"type": "string", "description": "Filter by NE host_name"},
                "ne_name": {"type": "string", "description": "Legacy alias mapped to keyword"},
                "keyword": {"type": "string"},
                "time_from": {"type": "string", "description": "ISO time; filters last_seen_at >="},
                "time_to": {"type": "string", "description": "ISO time; filters last_seen_at <="},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "aggregateUmeAlarms",
        "description": (
            "Aggregate UME current alarms (by_severity + top by_ne). "
            "Optional severity filter (e.g. critical) for risk Top-N. "
            "by_ne is capped by top_ne (default 50) and excludes missing host_name by default "
            "(see by_ne_missing). meta.last_seen_min/max show data freshness. "
            "If group_by is set (e.g. alarm_host_name), automatically routes to the same "
            "behavior as aggregateUmeAlarmsRaw — preferred for custom dimensions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity": {
                    "type": "string",
                    "description": "Optional perceived_severity filter (critical/major/minor/warning).",
                },
                "top_ne": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 500,
                    "default": 50,
                    "description": "Max NE buckets to return (0 = all, capped at API 5000). Ignored when group_by is set.",
                },
                "exclude_missing_host": {
                    "type": "boolean",
                    "default": True,
                    "description": "Omit (host_name missing) from by_ne / host rankings.",
                },
                "time_from": {"type": "string", "description": "ISO time; filters last_seen_at >="},
                "time_to": {"type": "string", "description": "ISO time; filters last_seen_at <="},
                "group_by": {
                    "type": "string",
                    "enum": UME_RAW_GROUP_FIELDS,
                    "description": (
                        "Optional. When set, routes to dynamic raw aggregation "
                        "(same as aggregateUmeAlarmsRaw). Prefer alarm_host_name for NE Top."
                    ),
                },
                "group_by2": {
                    "type": "string",
                    "enum": UME_RAW_GROUP_FIELDS,
                    "description": "Optional second group field when group_by is set.",
                },
                "is_cleared": {"type": "string", "description": "Only used when group_by is set."},
                "ne_id": {"type": "string", "description": "Only used when group_by is set."},
                "event_type": {"type": "string", "description": "Only used when group_by is set."},
                "keyword": {"type": "string", "description": "Only used when group_by is set."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "default": 200,
                    "description": "Bucket limit when group_by is set (default 200).",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "runUmeDiagnostics",
        "description": (
            "UME alarm diagnostics: severity, top_event_types, top_alarm_codes (UME alarmCode), "
            "top_ne (excludes missing host), protocol buckets, and meta.last_seen_min/max freshness."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "queryUmeNeInventory",
        "description": "Paged UME NE inventory synced in netx (keyword matches ne_id/ne_name/user_label/ip/host_name).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "getUmeNe",
        "description": "Get single UME NE detail by ne_id (UUID).",
        "inputSchema": {
            "type": "object",
            "properties": {"ne_id": {"type": "string"}},
            "required": ["ne_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "queryUmeAlarmsRaw",
        "description": "Power query UME current alarms with full alarm_* + ne_* fields; optional field_preset or select_fields.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string"},
                "is_cleared": {"type": "string"},
                "ne_id": {"type": "string"},
                "event_type": {"type": "string"},
                "keyword": {"type": "string"},
                "time_from": {"type": "string"},
                "time_to": {"type": "string"},
                "order_by": {
                    "type": "string",
                    "enum": ["last_seen_at", "time_created", "perceived_severity", "event_type", "ne_id"],
                },
                "order": {"type": "string", "enum": ["asc", "desc"]},
                "select_fields": {"type": "array", "items": {"type": "string"}},
                "field_preset": {"type": "string", "enum": ["brief", "evidence", "ne_debug"]},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "aggregateUmeAlarmsRaw",
        "description": (
            "Dynamic aggregation on UME raw fields (group_by/group_by2); prefer alarm_host_name. "
            "When grouping by host fields, (host_name missing) is omitted by default "
            "(see by_ne_missing)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_by": {"type": "string", "enum": UME_RAW_GROUP_FIELDS},
                "group_by2": {"type": "string", "enum": UME_RAW_GROUP_FIELDS},
                "severity": {"type": "string"},
                "is_cleared": {"type": "string"},
                "ne_id": {"type": "string"},
                "event_type": {"type": "string"},
                "keyword": {"type": "string"},
                "time_from": {"type": "string"},
                "time_to": {"type": "string"},
                "exclude_missing_host": {
                    "type": "boolean",
                    "default": True,
                    "description": "Omit missing host buckets when grouping by host/user_label fields.",
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
            },
            "required": ["group_by"],
            "additionalProperties": False,
        },
    },
    {
        "name": "listUmeAlarmFields",
        "description": "List available fields for UME raw alarm queries.",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "sqlQueryUme",
        "description": "Read-only SELECT on UME tables (ume_alarms_current/ume_inventory_ne); server enforces limits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                "statement_timeout_ms": {"type": "integer", "minimum": 0, "maximum": 30000, "default": 0},
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "name": "listManagedNe",
        "description": "List filtered netx managed NEs (keyword/vendor/connect_status required); use before execManagedNe.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string"},
                "vendor": {"type": "string"},
                "connect_status": {"type": "string", "enum": ["unknown", "testing", "pass", "fail"]},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "getManagedNe",
        "description": "Get single managed NE metadata (connect_status, hop config summary).",
        "inputSchema": {
            "type": "object",
            "properties": {"ne_id": {"type": "string"}},
            "required": ["ne_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "execManagedNe",
        "description": (
            f"Run read-only CLI via netx (show/display/ping/traceroute; "
            f"max {exec_max_commands()} commands per call, NETX_NE_EXEC_MAX_COMMANDS). "
            "Use ne_id (managed NE) OR ume_ne_id (UME inventory). "
            "Batch multiple show commands in one call instead of looping. "
            "On timeout, raise read_timeout_sec (max 120) or shrink commands — do not blind-retry."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ne_id": {"type": "string"},
                "ume_ne_id": {"type": "string"},
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": exec_max_commands(),
                },
                "read_timeout_sec": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 120,
                    "description": "Per-command read timeout; use 60–120 for slow show commands.",
                },
            },
            "required": ["commands"],
            "additionalProperties": False,
        },
    },
    {
        "name": "listCliTargets",
        "description": (
            "List CLI-capable targets (managed NE and/or UME inventory). "
            "Call once per session with keyword/source, cache ume_ne_id/ne_id, "
            "then execManagedNe — do not re-list before every command."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "enum": ["managed", "ume", "all"], "default": "all"},
                "keyword": {"type": "string"},
                "page": {"type": "integer", "minimum": 1, "default": 1},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "findTopologyPaths",
        "description": (
            "Find up to max_paths simple paths between two fabric nodes for troubleshooting. "
            "For each endpoint provide exactly one of ume_ne_id (from UME alarm ne_id) or "
            "managed_ne_id — resolved to fabric node internally. Returns shortest paths "
            "first with compact label + node/edge summary (detail=summary default). "
            "Use after critical alarms to correlate neighboring NEs before CLI login."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_ume_ne_id": {"type": "string", "description": "Source UME ne_id (from alarm ne_id); mutually exclusive with from_managed_ne_id"},
                "from_managed_ne_id": {"type": "string", "description": "Source managed NE id; mutually exclusive with from_ume_ne_id"},
                "to_ume_ne_id": {"type": "string", "description": "Target UME ne_id; mutually exclusive with to_managed_ne_id"},
                "to_managed_ne_id": {"type": "string", "description": "Target managed NE id; mutually exclusive with to_ume_ne_id"},
                "max_paths": {"type": "integer", "minimum": 1, "maximum": 10, "default": 3},
                "max_hops": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
                "layer": {"type": "string", "default": "physical"},
                "detail": {
                    "type": "string",
                    "enum": ["summary", "full"],
                    "default": "summary",
                    "description": "summary=compact ops fields; full=include attrs/coords.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]

_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "queryUmeAlarms": _query_ume_alarms,
    "aggregateUmeAlarms": _aggregate_ume_alarms,
    "runUmeDiagnostics": _run_ume_diagnostics,
    "queryUmeNeInventory": _query_ume_ne_inventory,
    "getUmeNe": _get_ume_ne,
    "queryUmeAlarmsRaw": _query_ume_alarms_raw,
    "aggregateUmeAlarmsRaw": _aggregate_ume_alarms_raw,
    "listUmeAlarmFields": _list_ume_alarm_fields,
    "sqlQueryUme": _sql_query_ume,
    "listManagedNe": _list_managed_ne,
    "getManagedNe": _get_managed_ne,
    "execManagedNe": _exec_managed_ne,
    "listCliTargets": _list_cli_targets,
    "findTopologyPaths": _find_topology_paths,
}

# Minimum scope required to advertise / invoke each tool (matches netx API RBAC).
TOOL_REQUIRED_SCOPE: dict[str, str] = {
    "queryUmeAlarms": "alarms:read",
    "aggregateUmeAlarms": "alarms:read",
    "runUmeDiagnostics": "alarms:read",
    "queryUmeNeInventory": "ne:read",
    "getUmeNe": "ne:read",
    "queryUmeAlarmsRaw": "alarms:read",
    "aggregateUmeAlarmsRaw": "alarms:read",
    "listUmeAlarmFields": "alarms:read",
    "sqlQueryUme": "sql:query",
    "listManagedNe": "ne:read",
    "getManagedNe": "ne:read",
    "execManagedNe": "ne:exec",
    "listCliTargets": "ne:read",
    "findTopologyPaths": "ne:read",
}


def tools_for_scopes(scopes: list[str] | set[str] | frozenset[str] | None) -> list[dict[str, Any]]:
    """Filter MCP tool list by granted scopes. Empty/None => return all (offline / unauthenticated listing)."""
    if scopes is None:
        return list(HTTP_MCP_TOOLS)
    granted = {str(s).strip().lower() for s in scopes if str(s).strip()}
    if not granted:
        return []
    out: list[dict[str, Any]] = []
    for tool in HTTP_MCP_TOOLS:
        name = str(tool.get("name") or "")
        need = TOOL_REQUIRED_SCOPE.get(name)
        if need is None or need in granted:
            out.append(tool)
    return out


def call_http_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = _HANDLERS.get(str(name or "").strip())
    if not fn:
        raise ValueError(f"unknown tool: {name}")
    return mcp_from_handler_result(fn(dict(args or {})))
