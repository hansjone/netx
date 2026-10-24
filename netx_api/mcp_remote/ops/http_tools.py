"""MCP tool schemas and HTTP-backed handlers (NMS adapter + managed NE).

Model-facing tool names use generic Nms*; REST paths remain /v1/ume/* for the
current zte-ume provider. Model-facing params use nms_ne_id exclusively.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from netx_api.mcp_remote.asgi_client import http_json, http_post_json, mcp_from_handler_result, quote_ne_id

_EXEC_MAX_COMMANDS_CAP = 50
_EXEC_MAX_COMMANDS_DEFAULT = 5


def exec_max_commands() -> int:
    """Mirror netx API NETX_NE_EXEC_MAX_COMMANDS (default 5, hard cap 50)."""
    try:
        raw = int(os.getenv("NETX_NE_EXEC_MAX_COMMANDS") or _EXEC_MAX_COMMANDS_DEFAULT)
    except ValueError:
        raw = _EXEC_MAX_COMMANDS_DEFAULT
    return max(1, min(_EXEC_MAX_COMMANDS_CAP, raw))


def _async_min_nes() -> int:
    try:
        raw = int(os.getenv("NETX_NE_EXEC_ASYNC_MIN_NES") or 4)
    except ValueError:
        raw = 4
    return max(0, min(50, raw))


def _truthy_async_flag(raw: Any) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def _count_exec_ne_targets(args: dict[str, Any]) -> int:
    n = 0
    for key in ("ne_ids", "nms_ne_ids"):
        val = args.get(key)
        if isinstance(val, list):
            n += len([x for x in val if str(x or "").strip()])
    targets = args.get("targets")
    if isinstance(targets, list):
        n += len([t for t in targets if isinstance(t, dict)])
    if n == 0 and (
        str(args.get("ne_id") or "").strip()
        or str(args.get("nms_ne_id") or "").strip()
    ):
        return 1
    return int(n)


def _should_run_exec_async(args: dict[str, Any]) -> bool:
    flag = _truthy_async_flag(args.get("async"))
    if flag is False:
        return False
    if flag is True:
        return True
    min_n = _async_min_nes()
    if min_n <= 0:
        return False
    return _count_exec_ne_targets(args) >= min_n


class ToolArgError(ValueError):
    """Bad tool argument; surfaced to the model as a tool error (not a JSON-RPC crash)."""


def _int_arg(args: dict[str, Any], key: str, default: int, lo: int, hi: int) -> int:
    raw = args.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise ToolArgError(f"{key} must be an integer, got {raw!r}") from None
    return max(lo, min(hi, val))


def _severity(args: dict[str, Any]) -> str:
    # Stored values are lowercase (critical/major/minor/warning); exact match server-side.
    return str(args.get("severity") or "").strip().lower()


def _is_me_placeholder(ne_name: Any, ne_id: Any) -> bool:
    return bool(ne_id) and str(ne_name or "") == f"ME{{{ne_id}}}"


def _compact_rows(out: dict[str, Any], *, name_key: str, id_key: str) -> dict[str, Any]:
    """Drop ZTE ``ME{<uuid>}`` ne_name placeholders: they repeat ne_id and carry no meaning."""
    data = out.get("data") if isinstance(out, dict) else None
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, list):
        for row in items:
            if isinstance(row, dict) and _is_me_placeholder(row.get(name_key), row.get(id_key)):
                row.pop(name_key, None)
    return out


def _strip_meta_field_lists(out: dict[str, Any]) -> dict[str, Any]:
    """Raw endpoints echo ~40 field names per call; listNmsAlarmFields already serves them."""
    data = out.get("data") if isinstance(out, dict) else None
    meta = data.get("meta") if isinstance(data, dict) else None
    if isinstance(meta, dict):
        for k in ("available_fields", "group_by_allowed", "order_by_allowed"):
            meta.pop(k, None)
    return out


def _nms_naming(row: dict[str, Any]) -> None:
    """REST still speaks ume_*; model-facing params are nms_* -- keep output consistent."""
    if "ume_ne_id" in row:
        row["nms_ne_id"] = row.pop("ume_ne_id")
    if row.get("source") == "ume":
        row["source"] = "nms"


_HOP_KEYS = (
    "hop_vendor",
    "hop_host",
    "hop_port",
    "hop_protocol",
    "hop_username",
    "hop_command_template",
    "hop_vrf",
    "hop_target_auth_mode",
    "hop_enter_system_view",
)


def _compact_managed_ne(row: dict[str, Any], *, keep_detail: bool) -> None:
    if row.get("hop_enabled") is False:
        for k in _HOP_KEYS:
            row.pop(k, None)
    if not keep_detail:
        row.pop("connect_detail", None)


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
    params: dict[str, Any] = {
        "page": _int_arg(args, "page", 1, 1, 100000),
        "page_size": _int_arg(args, "page_size", 50, 1, 500),
    }
    if _severity(args):
        params["severity"] = _severity(args)
    if str(args.get("is_cleared") or "").strip():
        params["is_cleared"] = str(args.get("is_cleared")).strip()
    keyword = str(args.get("keyword") or "").strip()
    if keyword:
        params["keyword"] = keyword
    if str(args.get("ne_id") or "").strip():
        params["ne_id"] = str(args.get("ne_id")).strip()
    if str(args.get("host_name") or "").strip():
        params["host_name"] = str(args.get("host_name")).strip()
    for k in ("time_from", "time_to"):
        if str(args.get(k) or "").strip():
            params[k] = str(args.get(k)).strip()
    return _compact_rows(http_json("GET", "/v1/ume/alarms", params=params), name_key="ne_name", id_key="ne_id")


def _aggregate_ume_alarms(args: dict[str, Any]) -> dict[str, Any]:
    # Agents often pass group_by here (docs historically mixed tools). Route to raw aggregate.
    group_by = str(args.get("group_by") or "").strip()
    if group_by:
        return _aggregate_ume_alarms_raw(args)
    # Default top 50 named NEs — missing host buckets reported separately.
    params: dict[str, Any] = {"top_ne": _int_arg(args, "top_ne", 50, 0, 5000)}
    if "exclude_missing_host" in args:
        params["exclude_missing_host"] = bool(args.get("exclude_missing_host"))
    if _severity(args):
        params["severity"] = _severity(args)
    for k in ("time_from", "time_to"):
        if str(args.get(k) or "").strip():
            params[k] = str(args.get(k)).strip()
    return http_json("GET", "/v1/ume/alarms/aggregate", params=params)


def _run_ume_diagnostics(args: dict[str, Any]) -> dict[str, Any]:
    _ = args
    return http_json("GET", "/v1/ume/diagnostics", params=None)


def _query_ume_ne_inventory(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "page": _int_arg(args, "page", 1, 1, 100000),
        "page_size": _int_arg(args, "page_size", 50, 1, 500),
    }
    if str(args.get("keyword") or "").strip():
        params["keyword"] = str(args.get("keyword")).strip()
    return _compact_rows(http_json("GET", "/v1/ume/inventory/ne", params=params), name_key="ne_name", id_key="ne_id")


def _looks_like_uuid(text: str) -> bool:
    parts = text.split("-")
    return len(parts) == 5 and all(p and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts)


def _get_ume_ne(args: dict[str, Any]) -> dict[str, Any]:
    ne_id = str(args.get("ne_id") or "").strip()
    if not ne_id:
        return {"ok": False, "error": "ne_id_required", "error_code": "ne_id_required"}
    out = http_json("GET", f"/v1/ume/inventory/ne/{quote_ne_id(ne_id)}", params=None)
    if out.get("ok") or out.get("error") != "netx_http_404" or _looks_like_uuid(ne_id):
        return out
    # Agents usually know the host_name (alarms show it), not the UUID: resolve it.
    found = http_json("GET", "/v1/ume/inventory/ne", params={"keyword": ne_id, "page": 1, "page_size": 20})
    items = ((found.get("data") or {}).get("items") or []) if found.get("ok") else []
    needle = ne_id.lower()
    exact = [
        r
        for r in items
        if isinstance(r, dict)
        and needle in {str(r.get(k) or "").lower() for k in ("host_name", "user_label", "ip_address", "ne_name")}
    ]
    if len(exact) == 1 and exact[0].get("ne_id"):
        return http_json("GET", f"/v1/ume/inventory/ne/{quote_ne_id(str(exact[0]['ne_id']))}", params=None)
    out = dict(out)
    out["hint"] = "ne_id is the NMS UUID. Resolve host_name/IP via queryNmsNeInventory(keyword=...)."
    if items:
        out["candidates"] = [
            {"ne_id": r.get("ne_id"), "host_name": r.get("host_name"), "ip_address": r.get("ip_address")}
            for r in items[:10]
            if isinstance(r, dict)
        ]
    return out


def _query_ume_alarms_raw(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "page": _int_arg(args, "page", 1, 1, 100000),
        "page_size": _int_arg(args, "page_size", 50, 1, 500),
    }
    if _severity(args):
        params["severity"] = _severity(args)
    for k in ("is_cleared", "ne_id", "event_type", "keyword", "time_from", "time_to", "order_by", "order"):
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
    out = http_json("GET", "/v1/ume/alarms/raw", params=params)
    data = out.get("data") if isinstance(out.get("data"), dict) else None
    if data and isinstance(data.get("items"), list):
        for row in data["items"]:
            if isinstance(row, dict) and _is_me_placeholder(
                row.get("ne_ne_name"), row.get("ne_ne_id") or row.get("alarm_ne_id")
            ):
                row.pop("ne_ne_name", None)
    return _strip_meta_field_lists(out)


def _aggregate_ume_alarms_raw(args: dict[str, Any]) -> dict[str, Any]:
    if not str(args.get("group_by") or "").strip():
        raise ToolArgError("group_by is required; use listNmsAlarmFields to choose a field")
    params: dict[str, Any] = {}
    for k in (
        "group_by",
        "group_by2",
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
    if _severity(args):
        params["severity"] = _severity(args)
    if "limit" in params:
        params["limit"] = _int_arg(args, "limit", 200, 1, 2000)
    if "exclude_missing_host" in args:
        params["exclude_missing_host"] = bool(args.get("exclude_missing_host"))
    return _strip_meta_field_lists(http_json("GET", "/v1/ume/alarms/aggregate/raw", params=params))


def _list_ume_alarm_fields(args: dict[str, Any]) -> dict[str, Any]:
    _ = args
    return http_json("GET", "/v1/ume/alarms/fields", params=None)


def _sql_query_ume(args: dict[str, Any]) -> dict[str, Any]:
    sql = str(args.get("sql") or "").strip()
    limit = _int_arg(args, "limit", 200, 1, 2000)
    statement_timeout_ms = _int_arg(args, "statement_timeout_ms", 0, 0, 30000)
    if not sql:
        return {"ok": False, "error": "sql_required"}
    return http_post_json(
        "/v1/sql/ume_query",
        {"sql": sql, "limit": limit, "statement_timeout_ms": statement_timeout_ms},
        timeout=60.0,
    )


def _list_managed_ne(args: dict[str, Any]) -> dict[str, Any]:
    page = _int_arg(args, "page", 1, 1, 100000)
    page_size = _int_arg(args, "page_size", 20, 1, 100)
    keyword = str(args.get("keyword") or "").strip()
    vendor = str(args.get("vendor") or "").strip()
    connect_status = str(args.get("connect_status") or "").strip()
    if not (keyword or vendor or connect_status):
        return {
            "ok": False,
            "error": "managed_ne_filter_required",
            "error_code": "managed_ne_filter_required",
            "hint": "Pass keyword (name/IP, >=2 chars), vendor, or connect_status (e.g. pass).",
        }
    if keyword and len(keyword) < 2:
        return {"ok": False, "error": "managed_ne_keyword_too_short", "error_code": "managed_ne_keyword_too_short"}
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if vendor:
        params["vendor"] = vendor
    if connect_status:
        params["connect_status"] = connect_status
    out = http_json("GET", "/v1/managed-ne", params=params)
    items = (out.get("data") or {}).get("items") if out.get("ok") else None
    if isinstance(items, list):
        for row in items:
            if isinstance(row, dict):
                _compact_managed_ne(row, keep_detail=False)
    return out


def _first_str(*vals: Any) -> str:
    for v in vals:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def _get_managed_ne(args: dict[str, Any]) -> dict[str, Any]:
    ne_id = str(args.get("ne_id") or "").strip()
    if not ne_id:
        return {
            "ok": False,
            "error": "ne_id_required",
            "error_code": "ne_id_required",
            "hint": (
                "Pass managed NE id from listManagedNe/listCliTargets (source=managed). "
                "For NMS inventory UUIDs use execManagedNe(nms_ne_id=...) or getNmsNe, not getManagedNe."
            ),
            "example": {"ne_id": "<managed-ne-uuid-from-listManagedNe>"},
        }
    # NMS ne_id is typically a UUID; managed NE may differ. Soft-guide when callers mix them.
    out = http_json("GET", f"/v1/managed-ne/{quote_ne_id(ne_id)}", params=None)
    if out.get("ok") and isinstance(out.get("data"), dict):
        _compact_managed_ne(out["data"], keep_detail=True)
    if isinstance(out, dict) and out.get("ok") is False:
        detail = str(out.get("detail") or out.get("error") or "")
        low = detail.lower()
        if "404" in low or "not_found" in low or "not found" in low or out.get("error") == "netx_http_404":
            out = dict(out)
            out["hint"] = (
                "Managed NE not found for this ne_id. Call listManagedNe(keyword=...) or "
                "listCliTargets(source=managed) first. If this is an NMS ne_id, use "
                "execManagedNe(nms_ne_id=...) / getNmsNe instead of getManagedNe."
            )
    return out


def _exec_ack(out: dict[str, Any]) -> dict[str, Any]:
    if not out.get("ok"):
        return out
    data = out.get("data") if isinstance(out.get("data"), dict) else out
    if isinstance(data, dict) and data.get("job_id"):
        data = dict(data)
        data["poll_after_ms"] = 5000
        data["hint"] = (
            "Execution started; it has not succeeded yet. Continue independent work, then poll "
            "getNeExecJob with this job_id after about 5 seconds. Do not resubmit commands while "
            "the job is running. Inspect terminal, result.ok, summary.failed and every target result before reporting success."
        )
    return data if isinstance(data, dict) else {"ok": True, "data": data}


def _exec_result_failed(data: Any) -> bool:
    """Batch ok means the request ran; individual targets may still all fail."""
    if not isinstance(data, dict):
        return False
    if data.get("ok") is False:
        return True
    summary = data.get("summary")
    if isinstance(summary, dict) and isinstance(summary.get("failed"), int) and summary["failed"] > 0:
        return True
    results = data.get("results")
    return isinstance(results, list) and any(isinstance(row, dict) and row.get("ok") is False for row in results)


def _exec_managed_ne(args: dict[str, Any]) -> dict[str, Any]:
    # Command allow/deny is enforced by REST ne_exec_guard per device exec_policy
    # (readonly vs linux_shell/unrestricted). MCP does not apply a stricter gate.
    targets_raw = args.get("targets")
    ne_ids_raw = args.get("ne_ids")
    nms_ne_ids_raw = args.get("nms_ne_ids")
    shared_cmds_raw = args.get("commands")
    shared_commands = (
        [str(c).strip() for c in shared_cmds_raw if str(c).strip()]
        if isinstance(shared_cmds_raw, list)
        else []
    )
    multi = bool(
        (isinstance(targets_raw, list) and targets_raw)
        or (isinstance(ne_ids_raw, list) and ne_ids_raw)
        or (isinstance(nms_ne_ids_raw, list) and nms_ne_ids_raw)
    )
    want_async = _should_run_exec_async(args)

    if multi and (args.get("ne_id") or args.get("nms_ne_id")):
        raise ToolArgError("Do not mix single NE identifiers with batch targets")
    if multi and _count_exec_ne_targets(args) == 0:
        raise ToolArgError("targets_required")
    if _count_exec_ne_targets(args) > 20:
        raise ToolArgError("too_many_targets (max 20 across targets/ne_ids/nms_ne_ids)")

    if multi:
        body: dict[str, Any] = {}
        if isinstance(targets_raw, list) and targets_raw:
            cleaned_targets: list[dict[str, Any]] = []
            for t in targets_raw:
                if not isinstance(t, dict):
                    continue
                row: dict[str, Any] = {}
                if str(t.get("ne_id") or "").strip():
                    row["ne_id"] = str(t.get("ne_id")).strip()
                # Wire still uses ume_ne_id on REST.
                nms_id = _first_str(t.get("nms_ne_id"))
                if bool(row.get("ne_id")) == bool(nms_id):
                    raise ToolArgError("Each target requires exactly one of ne_id or nms_ne_id")
                if nms_id:
                    row["ume_ne_id"] = nms_id
                cmds = t.get("commands")
                if isinstance(cmds, list) and cmds:
                    row["commands"] = [str(c).strip() for c in cmds if str(c).strip()]
                effective_commands = row.get("commands", shared_commands)
                if not effective_commands:
                    raise ToolArgError("commands_required for every target")
                if len(effective_commands) > exec_max_commands():
                    raise ToolArgError("too_many_commands for target")
                if row:
                    cleaned_targets.append(row)
            body["targets"] = cleaned_targets
        if isinstance(ne_ids_raw, list) and ne_ids_raw:
            body["ne_ids"] = [str(x).strip() for x in ne_ids_raw if str(x).strip()]
        if isinstance(nms_ne_ids_raw, list) and nms_ne_ids_raw:
            body["ume_ne_ids"] = [str(x).strip() for x in nms_ne_ids_raw if str(x).strip()]
        if (body.get("ne_ids") or body.get("ume_ne_ids")) and not shared_commands:
            raise ToolArgError("commands_required for ne_ids/nms_ne_ids")
        if shared_commands:
            if len(shared_commands) > exec_max_commands():
                return {"ok": False, "error": "too_many_commands", "error_code": "too_many_commands"}
            body["commands"] = shared_commands
        body["read_timeout_sec"] = _int_arg(args, "read_timeout_sec", 60, 10, 120)
        if args.get("concurrency") is not None:
            body["concurrency"] = _int_arg(args, "concurrency", 4, 1, 8)
        if want_async:
            out = http_post_json("/v1/managed-ne/exec-jobs", body, timeout=60.0)
            return _exec_ack(out)
        # Wall clock: many NEs × per-cmd timeout; keep below oclaw MCP override.
        out = http_post_json("/v1/managed-ne/exec-batch", body, timeout=600.0)
        if not out.get("ok"):
            return out
        data = out.get("data") or {}
        if _exec_result_failed(data):
            return {"ok": False, "data": data, "error": str(data.get("error") or "exec_batch_failed")}
        return {"ok": True, "data": data}

    ne_id = str(args.get("ne_id") or "").strip()
    nms_ne_id = _first_str(args.get("nms_ne_id"))
    if bool(ne_id) == bool(nms_ne_id):
        return {
            "ok": False,
            "error": "exactly_one_of_ne_id_or_nms_ne_id_required",
            "error_code": "exactly_one_of_ne_id_or_nms_ne_id_required",
            "hint": (
                "For one NE pass ne_id OR nms_ne_id. For many NEs pass "
                "ne_ids / nms_ne_ids with shared commands, or targets[] with per-NE commands — "
                "one call, concurrent on server."
            ),
        }
    if not shared_commands:
        return {"ok": False, "error": "commands_required", "error_code": "commands_required"}
    if len(shared_commands) > exec_max_commands():
        return {"ok": False, "error": "too_many_commands", "error_code": "too_many_commands"}
    body = {"commands": shared_commands}
    if ne_id:
        body["ne_id"] = ne_id
    if nms_ne_id:
        body["ume_ne_id"] = nms_ne_id
    # Default 60s matches netx API default; slow show commands often exceed 30s.
    body["read_timeout_sec"] = _int_arg(args, "read_timeout_sec", 60, 10, 120)
    if want_async:
        out = http_post_json("/v1/managed-ne/exec-jobs", body, timeout=60.0)
        return _exec_ack(out)
    out = http_post_json("/v1/managed-ne/exec", body, timeout=300.0)
    if not out.get("ok"):
        return out
    data = out.get("data") or {}
    if isinstance(data, dict) and data.get("ok") is False:
        return {"ok": False, "data": data, "error": str(data.get("error") or "exec_failed")}
    return {"ok": True, "data": data}


def _get_ne_exec_job(args: dict[str, Any]) -> dict[str, Any]:
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        return {
            "ok": False,
            "error": "job_id_required",
            "error_code": "job_id_required",
            "hint": "Pass job_id from execManagedNe async ack.",
        }
    out = http_json("GET", f"/v1/managed-ne/exec-jobs/{quote_ne_id(job_id)}", params=None)
    data = out.get("data")
    if out.get("ok") and isinstance(data, dict):
        if data.get("terminal") and (data.get("status") in {"failed", "timeout"}
                                   or _exec_result_failed(data.get("result"))):
            return {"ok": False, "error": "ne_exec_job_failed", "data": data}
        if not data.get("terminal") and data.get("status") == "running":
            data["poll_after_ms"] = 5000
            data["hint"] = "Still running. Continue independent work; poll this job_id after about 5 seconds. Do not resubmit."
    return out


def _list_cli_targets(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {
        "page": _int_arg(args, "page", 1, 1, 100000),
        "page_size": _int_arg(args, "page_size", 50, 1, 500),
    }
    source = str(args.get("source") or "").strip()
    if source:
        # API still uses source=ume; accept model-facing "nms".
        params["source"] = "ume" if source.lower() == "nms" else source
    if str(args.get("keyword") or "").strip():
        params["keyword"] = str(args.get("keyword")).strip()
    out = http_json("GET", "/v1/cli/targets", params=params)
    items = (out.get("data") or {}).get("items") if out.get("ok") else None
    if isinstance(items, list):
        for row in items:
            if isinstance(row, dict):
                _nms_naming(row)
    return out


def _find_topology_paths(args: dict[str, Any]) -> dict[str, Any]:
    from_uid = _first_str(args.get("from_nms_ne_id"))
    from_mid = str(args.get("from_managed_ne_id") or "").strip()
    to_uid = _first_str(args.get("to_nms_ne_id"))
    to_mid = str(args.get("to_managed_ne_id") or "").strip()
    if bool(from_uid) == bool(from_mid):
        return {"ok": False, "error": "exactly_one_of_from_nms_ne_id_or_from_managed_ne_id_required"}
    if bool(to_uid) == bool(to_mid):
        return {"ok": False, "error": "exactly_one_of_to_nms_ne_id_or_to_managed_ne_id_required"}
    detail = str(args.get("detail") or "summary").strip().lower() or "summary"
    if detail not in {"summary", "full"}:
        detail = "summary"
    body: dict[str, Any] = {
        "max_paths": _int_arg(args, "max_paths", 3, 1, 10),
        "max_hops": _int_arg(args, "max_hops", 6, 1, 12),
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
    out = http_post_json("/v1/topology/fabric/paths", body, timeout=30.0)
    paths = (out.get("data") or {}).get("paths") if out.get("ok") else None
    if isinstance(paths, list):
        for p in paths:
            for node in (p.get("nodes") or []) if isinstance(p, dict) else []:
                if isinstance(node, dict):
                    _nms_naming(node)
    return out


HTTP_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "queryNmsAlarms",
        "description": (
            "Query NMS current alarms (each row includes host_name). "
            "Supports severity/ne_id/host_name/keyword, last_seen time_from/time_to, pagination. "
            "Prefer host_name for display; ne_id is for filters only. "
            "Field keyword examples (native_probable_cause): LOS, Fiber Break, bandwidth, CRC, "
            "BN EMS, dying gasp, License, BGP Neighbour, Port down, optical power, NTP. "
            "Area = hostname prefix (MDN-/MKS-/PLG-/ACH-/PAD-/BJM-/…). "
            "Optical-power threshold ≠ fiber cut; capacity A<>B is CLI optics, not this tally alone."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "description": "critical/major/minor/warning"},
                "is_cleared": {"type": "string", "description": "\"False\" = active only; \"True\" = cleared."},
                "ne_id": {"type": "string", "description": "Filter only; do not show UUID to users"},
                "host_name": {"type": "string", "description": "Filter by NE host_name"},
                "keyword": {
                    "type": "string",
                    "description": (
                        "Substring on cause/object/event. Examples: LOS, Fiber Break, bandwidth, "
                        "CRC, BN EMS, dying gasp, License, optical power, BGP Neighbour."
                    ),
                },
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
        "name": "aggregateNmsAlarms",
        "description": (
            "Aggregate NMS current alarms (by_severity + top by_ne). "
            "Optional severity filter (e.g. critical) for risk Top-N. "
            "by_ne is capped by top_ne (default 50) and excludes missing host_name by default "
            "(see by_ne_missing). meta.last_seen_min/max show data freshness. "
            "If group_by is set (e.g. alarm_host_name), automatically routes to the same "
            "behavior as aggregateNmsAlarmsRaw — preferred for custom dimensions. "
            "Snapshot volumes are large (tens of thousands); always filter severity/keyword/time "
            "before paging — do not dump unfiltered lists."
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
                    "maximum": 5000,
                    "default": 50,
                    "description": "Max NE buckets to return (0 = all; positive limit up to 5000). Ignored when group_by is set.",
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
                        "(same as aggregateNmsAlarmsRaw). Prefer alarm_host_name for NE Top."
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
        "name": "runNmsDiagnostics",
        "description": (
            "NMS alarm diagnostics: severity, top_event_types, top_alarm_codes (vendor alarmCode), "
            "top_ne (excludes missing host), protocol buckets, and meta.last_seen_min/max freshness."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "queryNmsNeInventory",
        "description": "Paged NMS NE inventory synced in netx (keyword matches ne_id/ne_name/user_label/ip/host_name).",
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
        "name": "getNmsNe",
        "description": "Get single NMS NE detail by ne_id (UUID); exact host_name / user_label / IP also resolve.",
        "inputSchema": {
            "type": "object",
            "properties": {"ne_id": {"type": "string", "description": "NMS UUID, or exact host_name / IP."}},
            "required": ["ne_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "queryNmsAlarmsRaw",
        "description": (
            "Power query NMS current alarms with full alarm_* + ne_* fields; optional field_preset or select_fields. "
            "Use field_preset=evidence for WA citations. Same keyword vocabulary as queryNmsAlarms "
            "(LOS / Fiber Break / bandwidth / CRC / BN EMS / dying gasp / License / optical power). "
            "For area asks: keyword then keep hosts starting with AREA- (e.g. PAD-, ACH-, BPP-)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string"},
                "is_cleared": {"type": "string"},
                "ne_id": {"type": "string"},
                "event_type": {"type": "string"},
                "keyword": {
                    "type": "string",
                    "description": "Substring filter; see queryNmsAlarms keyword examples.",
                },
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
        "name": "aggregateNmsAlarmsRaw",
        "description": (
            "Dynamic aggregation on NMS raw fields (group_by/group_by2); prefer alarm_host_name. "
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
        "name": "listNmsAlarmFields",
        "description": "List available fields for NMS raw alarm queries.",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "sqlQueryNms",
        "description": (
            "Read-only SELECT on NMS tables (ume_alarms_current/ume_inventory_ne; zte-ume adapter); "
            "server enforces limits. Requires netx scope sql:query. If insufficient_scope, use "
            "aggregateNmsAlarms / queryNmsAlarmsRaw instead."
        ),
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
        "description": (
            "Get one **managed** NE by managed ne_id (from listManagedNe / listCliTargets source=managed). "
            "Response includes BACKEND capability (device_family, exec_policy_effective, recommended_mode, hints). "
            "execManagedNe follows the same per-device exec_policy as REST/Web "
            "(readonly → show/display/ping; linux_shell/unrestricted → native shell/RouterOS). "
            "Do NOT pass NMS inventory UUID here — use getNmsNe or execManagedNe(nms_ne_id=...) instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ne_id": {
                    "type": "string",
                    "description": "Managed NE id (not NMS host_name / not NMS ne_id unless they coincide).",
                },
            },
            "required": ["ne_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "execManagedNe",
        "description": (
            f"Run CLI on managed NEs via netx (max {exec_max_commands()} commands per NE, "
            "NETX_NE_EXEC_MAX_COMMANDS). Allow/deny matches REST/Web: each device's "
            "exec_policy (readonly → show/display/ping/traceroute + output-filter pipes; "
            "linux_shell/unrestricted on Linux/MikroTik → native shell or RouterOS, including "
            "multiline scripts within platform limits). Call getManagedNe.capability first when "
            "unsure which command style the device accepts. "
            "Single NE: ne_id OR nms_ne_id + commands. "
            "Many NEs (batch-first, max 20 targets, concurrency default 4, max 8): "
            "(1) same CLI on all → ne_ids[]/nms_ne_ids[] + shared commands; "
            "(2) different CLI per NE (vendor/role) → ONE targets=["
            "{nms_ne_id|ne_id, commands:[…]}, …] — do NOT fall back to one-NE loops. "
            "Do NOT loop one-NE execManagedNe for multi-NE work. "
            "Default read_timeout_sec=60; on timeout raise to 90–120 — do not blind-retry. "
            "Long or multi-NE work: async=true (or auto when ≥4 NEs) returns job_id immediately; "
            "poll getNeExecJob. Pass async=false to force sync. "
            "For batches inspect summary.failed and every result: job succeeded/result.ok alone "
            "does not mean all targets passed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ne_id": {"type": "string"},
                "nms_ne_id": {"type": "string", "description": "NMS inventory id (preferred)."},
                "ne_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                    "description": "Managed NE ids for concurrent batch (shared commands).",
                },
                "nms_ne_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 20,
                    "description": "NMS inventory ne_ids for concurrent batch (shared commands).",
                },
                "targets": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "properties": {
                            "ne_id": {"type": "string"},
                            "nms_ne_id": {"type": "string"},
                            "commands": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 1,
                                "maxItems": exec_max_commands(),
                            },
                        },
                        "additionalProperties": False,
                    },
                    "description": (
                        "Preferred for mixed-vendor / per-NE command sets: "
                        "each item is one NE (ne_id OR nms_ne_id) with its own commands[]. "
                        "Per-target commands override top-level shared commands. "
                        "Still one concurrent batch — not N single-NE calls."
                    ),
                },
                "commands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": exec_max_commands(),
                    "description": (
                        "Commands for single NE, or shared commands for ne_ids/nms_ne_ids. "
                        "Optional fallback for targets that omit per-target commands."
                    ),
                },
                "read_timeout_sec": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 120,
                    "default": 60,
                    "description": "Per-command read timeout (default 60; use 90–120 for slow show).",
                },
                "concurrency": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8,
                    "default": 4,
                    "description": "Parallel NEs for batch mode (ignored for single-NE).",
                },
                "async": {
                    "type": "boolean",
                    "description": (
                        "true=background job_id + getNeExecJob; false=force sync; "
                        "omit=auto for large batches (~4+ NEs, NETX_NE_EXEC_ASYNC_MIN_NES)."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "getNeExecJob",
        "description": (
            "Poll a background execManagedNe job (job_id from async ack). "
            "When terminal=true, result holds the exec/exec-batch payload. "
            "Use poll_after_ms (about 5 seconds); continue independent work while waiting. "
            "Terminal failed/timeout is a tool error and retains the original result for diagnosis."
            " Batch jobs can finish succeeded with individual failures: inspect result.summary.failed "
            "and each result.results[].ok before reporting success."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job id from execManagedNe async response."},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "listCliTargets",
        "description": (
            "List CLI-capable targets (managed NE and/or NMS inventory). "
            "Call once per session with keyword/source, cache nms_ne_id/ne_id, "
            "then execManagedNe — do not re-list before every command."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "enum": ["managed", "nms", "all"],
                    "default": "all",
                    "description": "nms selects the configured NMS inventory; managed selects managed NEs.",
                },
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
            "Find up to max_paths simple paths between two fabric nodes for troubleshooting "
            "(parallel links between the same NEs count as one hop; see edges[].parallel_links). "
            "For each endpoint provide exactly one of nms_ne_id (from NMS alarm ne_id) or "
            "managed_ne_id — resolved to fabric node internally. Returns "
            "shortest paths first with compact label + node/edge summary (detail=summary default). "
            "Use after critical alarms to correlate neighboring NEs before CLI login."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_nms_ne_id": {
                    "type": "string",
                    "description": "Source NMS ne_id (from alarm ne_id); mutually exclusive with from_managed_ne_id",
                },
                "from_managed_ne_id": {
                    "type": "string",
                    "description": "Source managed NE id; mutually exclusive with from_nms_ne_id",
                },
                "to_nms_ne_id": {
                    "type": "string",
                    "description": "Target NMS ne_id; mutually exclusive with to_managed_ne_id",
                },
                "to_managed_ne_id": {
                    "type": "string",
                    "description": "Target managed NE id; mutually exclusive with to_nms_ne_id",
                },
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
    "queryNmsAlarms": _query_ume_alarms,
    "aggregateNmsAlarms": _aggregate_ume_alarms,
    "runNmsDiagnostics": _run_ume_diagnostics,
    "queryNmsNeInventory": _query_ume_ne_inventory,
    "getNmsNe": _get_ume_ne,
    "queryNmsAlarmsRaw": _query_ume_alarms_raw,
    "aggregateNmsAlarmsRaw": _aggregate_ume_alarms_raw,
    "listNmsAlarmFields": _list_ume_alarm_fields,
    "sqlQueryNms": _sql_query_ume,
    "listManagedNe": _list_managed_ne,
    "getManagedNe": _get_managed_ne,
    "execManagedNe": _exec_managed_ne,
    "getNeExecJob": _get_ne_exec_job,
    "listCliTargets": _list_cli_targets,
    "findTopologyPaths": _find_topology_paths,
}

# Minimum scope required to advertise / invoke each tool (matches netx API RBAC).
TOOL_REQUIRED_SCOPE: dict[str, str] = {
    "queryNmsAlarms": "alarms:read",
    "aggregateNmsAlarms": "alarms:read",
    "runNmsDiagnostics": "alarms:read",
    "queryNmsNeInventory": "alarms:read",
    "getNmsNe": "alarms:read",
    "queryNmsAlarmsRaw": "alarms:read",
    "aggregateNmsAlarmsRaw": "alarms:read",
    "listNmsAlarmFields": "alarms:read",
    "sqlQueryNms": "sql:query",
    "listManagedNe": "ne:read",
    "getManagedNe": "ne:read",
    "execManagedNe": "ne:exec",
    "getNeExecJob": "ne:exec",
    "listCliTargets": "ne:read",
    "findTopologyPaths": "ne:read",
}


def tools_for_scopes(scopes: list[str] | set[str] | frozenset[str] | None) -> list[dict[str, Any]]:
    """Filter by granted scopes. None advertises all offline; an empty grant advertises none."""
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


def _validate_arguments(args: Any, schema: dict[str, Any], path: str = "arguments") -> None:
    """Enforce advertised shapes even when a host skips JSON Schema validation."""
    kind = schema.get("type")
    expected = {"object": dict, "array": list, "string": str, "boolean": bool, "integer": int}
    if kind in expected and (not isinstance(args, expected[kind]) or (kind == "integer" and isinstance(args, bool))):
        raise ToolArgError(f"{path} must be {kind}")
    if "enum" in schema and args not in schema["enum"]:
        raise ToolArgError(f"{path} must be one of {schema['enum']}")
    if kind == "object":
        props = schema.get("properties", {})
        unknown = set(args) - set(props)
        if schema.get("additionalProperties") is False and unknown:
            raise ToolArgError(f"{path}: unsupported parameter(s): {', '.join(sorted(unknown))}")
        for key, value in args.items():
            if key in props:
                _validate_arguments(value, props[key], f"{path}.{key}")
    elif kind == "array":
        # Command limits are read at invocation time by the handler (env may change).
        if not path.endswith(".commands") and len(args) > schema.get("maxItems", float("inf")):
            raise ToolArgError(f"{path} has too many items (max {schema['maxItems']})")
        if len(args) < schema.get("minItems", 0):
            raise ToolArgError(f"{path} requires at least {schema['minItems']} item(s)")
        for i, value in enumerate(args):
            _validate_arguments(value, schema.get("items", {}), f"{path}[{i}]")


_SECRET_KEYS = frozenset({
    "password", "hop_password", "password_enc", "hop_password_enc", "password_ciphertext",
    "hop_password_ciphertext", "credential_secret", "secret_key", "token", "access_token",
    "refresh_token", "authorization", "private_key",
})


def _redact_secrets(value: Any) -> Any:
    """Keep capability/has_password flags, but never expose structured credentials to a model."""
    if isinstance(value, dict):
        return {key: _redact_secrets(item) for key, item in value.items() if str(key).lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    return value


def call_http_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = _HANDLERS.get(str(name or "").strip())
    if not fn:
        raise ValueError(f"unknown tool: {name}")
    try:
        schema = next(tool["inputSchema"] for tool in HTTP_MCP_TOOLS if tool["name"] == name)
        _validate_arguments(args, schema)
        result = fn(dict(args or {}))
    except ToolArgError as exc:
        result = {"ok": False, "error": "invalid_argument", "error_code": "invalid_argument", "detail": str(exc)}
    return mcp_from_handler_result(_redact_secrets(result))
