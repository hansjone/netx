"""The eight bizMonitor tools, aligned with netxops and backend HTTP contracts."""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

from netx_api.mcp_remote.asgi_client import http_json, mcp_from_handler_result


class _InvalidArgument(ValueError):
    """Only messages constructed from known schema fields; never include values."""


def _str(description: str = "") -> dict[str, Any]:
    return {"type": "string", "description": description}


def _int(default: int, maximum: int, minimum: int = 1) -> dict[str, Any]:
    return {"type": "integer", "default": default, "minimum": minimum, "maximum": maximum}


def _tool(name: str, description: str, properties: dict[str, Any],
          required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False,
    }, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}}


HTTP_MCP_TOOLS = [
    _tool("listBizMonitors", "入口：列出割接项目和业务状态任务，无需 ID。取得 project_id/task_id 后查上下文或批次。", {
        "kind": {"type": "string", "enum": ["all", "projects", "tasks"], "default": "all"},
        "purpose": _str("任务用途：portrait / cutover_hf；空为全部"),
        "status": _str("本地过滤状态，如 active/paused"), "q": _str("名称、设备、备注或 ID 关键词"),
        "limit": _int(100, 500),
    }),
    _tool("listBizMonitorBatches", "列批次/评估运行 ID：project_id→割接批次，task_id→采集批次，batch_id+kind=runs→评估运行。include_runs 最多展开前 5 个割接批次。", {
        "kind": {"type": "string", "enum": ["auto", "all", "cutover", "collect", "runs"], "default": "auto"},
        "project_id": _str(), "task_id": _str(), "batch_id": _str("割接批次 ID，非采集批次 ID"),
        "include_runs": {"type": "boolean", "default": False}, "status": _str(),
        "limit": _int(50, 200), "runs_limit": _int(10, 50),
    }),
    _tool("getBizMonitorContext", "读取项目、监控/对比模板、端口映射及关联任务命令。project_id/task_id 至少一个；原始 A/B 键保留。", {
        "project_id": _str(), "task_id": _str(),
    }),
    _tool("getBizMonitorBoard", "读取割接批次看板、最新或指定评估运行摘要、sheet 卡片及 verdict 统计。", {
        "batch_id": _str("割接批次 ID"), "run_id": _str("可选评估运行 ID"),
    }, ("batch_id",)),
    _tool("listBizMonitorReds", "读取项目红票及证据（设备、采集时间、show 命令、接口原始/归一/映射键）；status 空为全部。", {
        "project_id": _str(), "status": _str("open / carried / resolved；空为全部"), "limit": _int(200, 500),
    }, ("project_id",)),
    _tool("getBizMonitorDiffs", "分页读取评估差异，保留原始 A/B、match_* 键与 show 命令证据。q 转发后端 kw；用 offset/limit 翻页。", {
        "run_id": _str(), "metric_id": _str(), "sheet_id": _str(), "verdict": _str(),
        "color": _str("如 red"), "q": _str("差异关键词，映射 HTTP kw"),
        "limit": _int(100, 500), "offset": _int(0, 9007199254740991, 0),
    }, ("run_id",)),
    _tool("getBizCollectBatch", "读取采集批次工作簿摘要、commands 和 sheet 目录。后端不返回全量指标行及 CLI 原文；以命令 ID 查原文。", {
        "batch_id": _str("业务状态采集批次 ID"),
    }, ("batch_id",)),
    _tool("getBizCollectCommandRaw", "深挖一个采集命令：返回完整 raw_text、raw_command、设备和采集时间；仅凭据脱敏，不截断原文。", {
        "batch_id": _str("采集批次 ID"), "command_id": _str("getBizCollectBatch 返回的命令 ID"),
    }, ("batch_id", "command_id")),
]
TOOL_REQUIRED_SCOPE = {t["name"]: "biz-monitor:read" for t in HTTP_MCP_TOOLS}
_SCHEMAS = {t["name"]: t["inputSchema"] for t in HTTP_MCP_TOOLS}


def tools_for_scopes(scopes: list[str] | None) -> list[dict[str, Any]]:
    # If /auth/me is unavailable, let the backend enforce authorization per GET.
    return list(HTTP_MCP_TOOLS) if scopes is None or "biz-monitor:read" in scopes else []


def _s(args: dict[str, Any], key: str, default: str = "") -> str:
    return str(args.get(key, default)).strip()


def _n(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _InvalidArgument(f"{key} 必须为有限数值")
    return max(low, min(high, math.trunc(value)))


def _pick(row: dict[str, Any], keys: str) -> dict[str, Any]:
    return {k: row[k] for k in keys.split() if k in row}


def _items(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = result.get("data", {}).get("items", [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _failed(result: dict[str, Any]) -> bool:
    return result.get("ok") is False or (isinstance(result.get("data"), dict)
                                         and result["data"].get("ok") is False)


def _error(code: str, detail: str = "") -> dict[str, Any]:
    return {"ok": False, "error": code, **({"detail": detail} if detail else {})}


def _project(row: dict[str, Any]) -> dict[str, Any]:
    out = _pick(row, "id name status note monitor_template_id old_task_id new_task_id old_hf_task_id new_hf_task_id hf_interval_sec hf_start_at hf_end_at updated_at")
    out["monitor_template_name"] = (row.get("monitor_template") or {}).get("name", "")
    for side in ("old", "new"):
        task = row.get(f"{side}_task") or {}
        name, ip = str(task.get("ne_name") or "").strip(), str(task.get("ne_ip") or "").strip()
        out[f"{side}_ne"] = f"{name} ({ip})" if name and ip else name or ip
    return out


def _run(row: dict[str, Any]) -> dict[str, Any]:
    out = _pick(row, "id batch_id purpose old_batch_id new_batch_id created_at")
    summary = row.get("summary") or {}
    progress = summary.get("progress") or {}
    if "anomaly" in summary:
        out["anomaly"] = summary["anomaly"]
    for key in ("ok", "total"):
        value = progress.get(key, summary.get(f"progress_{key}"))
        if value is not None:
            out[f"progress_{key}"] = value
    return out


def _catalog(args: dict[str, Any]) -> dict[str, Any]:
    kind = _s(args, "kind", "all").lower() or "all"
    if kind not in {"all", "projects", "tasks"}:
        return _error("kind_invalid", "kind must be all|projects|tasks")
    limit = _n(args, "limit", 100, 1, 500)
    status, query = _s(args, "status").lower(), _s(args, "q").lower()
    data: dict[str, Any] = {"kind": kind}
    counts = {"projects": 0, "tasks": 0, "projects_returned": 0, "tasks_returned": 0}
    for group, path in (("projects", "/v1/biz-migration/projects"), ("tasks", "/v1/biz-state/tasks")):
        if kind not in {"all", group}:
            continue
        params = {"purpose": _s(args, "purpose")} if group == "tasks" and _s(args, "purpose") else {}
        result = http_json("GET", path, params=params)
        if _failed(result):
            return result
        rows = [_project(row) for row in _items(result)] if group == "projects" else _items(result)
        rows = [row for row in rows if (not status or str(row.get("status", "")).lower() == status)
                and (not query or query in " ".join(str(row.get(k) or "").lower() for k in
                     "id name note status ne_name ne_ip ne_id old_ne new_ne monitor_template_name purpose vendor".split()))]
        counts[group] = len(rows)
        data[group] = rows[:limit]
        counts[f"{group}_returned"] = len(data[group])
    data.update(counts=counts, next="将 project_id/task_id 传给 getBizMonitorContext；用 listBizMonitorBatches 获取 batch_id/run_id。")
    return {"ok": True, "data": data}


def _batches(args: dict[str, Any]) -> dict[str, Any]:
    pid, tid, bid = (_s(args, key) for key in ("project_id", "task_id", "batch_id"))
    kind = _s(args, "kind", "auto").lower() or "auto"
    include = args.get("include_runs") is True
    limit, run_limit = _n(args, "limit", 50, 1, 200), _n(args, "runs_limit", 10, 1, 50)
    status = _s(args, "status").lower()
    if kind == "auto":
        kind = "all" if pid and tid else "cutover" if pid else "collect" if tid else "runs" if bid else ""
        if not kind:
            return _error("project_id_or_task_id_or_batch_id_required")
    if kind not in {"all", "cutover", "collect", "runs"}:
        return _error("kind_invalid", "kind must be auto|all|cutover|collect|runs")
    cutover, collect = kind in {"all", "cutover"}, kind in {"all", "collect"}
    for needed, value, key in ((cutover, pid, "project_id"), (collect, tid, "task_id"), (kind == "runs", bid, "batch_id")):
        if needed and not value:
            return _error(f"{key}_required")
    cb: list[dict[str, Any]] = []
    cl: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    if cutover:
        result = http_json("GET", f"/v1/biz-migration/projects/{quote(pid, safe='')}/batches", params={})
        if _failed(result):
            return result
        cb = [_pick(row, "id project_id batch_label status accept_status accept_run_id started_at ended_at note created_at updated_at")
              for row in _items(result) if (not bid or str(row.get("id") or "") == bid)
              and (not status or str(row.get("status") or "").lower() == status)][:limit]
    if collect:
        result = http_json("GET", f"/v1/biz-state/tasks/{quote(tid, safe='')}/batches", params={"limit": limit})
        if _failed(result):
            return result
        cl = [_pick(row, "id task_id status ne_name command_count row_count is_baseline protected message started_at ended_at")
              for row in _items(result) if not status or str(row.get("status") or "").lower() == status][:limit]
        for row in cl:
            row["task_id"] = row.get("task_id") or tid
    run_bids = [bid] if kind == "runs" else [str(row.get("id") or "").strip() for row in cb if row.get("id")][:5] if include else []
    for run_bid in run_bids:
        result = http_json("GET", f"/v1/biz-migration/batches/{quote(run_bid, safe='')}/runs", params={"limit": run_limit})
        target = next((row for row in cb if row.get("id") == run_bid), None)
        if _failed(result):
            if kind == "runs":
                return result
            if target is not None:
                target["runs_error"] = result.get("error") or "runs_fetch_failed"
            continue
        items = [_run(row) for row in _items(result)][:run_limit]
        runs.extend(items)
        if target is not None:
            target["runs"] = items
    data: dict[str, Any] = {"kind": kind, "counts": {"cutover_batches": len(cb), "collect_batches": len(cl), "runs": len(runs)}}
    data.update({key: val for key, val in (("project_id", pid), ("task_id", tid), ("batch_id", bid)) if val})
    if cutover:
        data["cutover_batches"] = cb
    if collect:
        data["collect_batches"] = cl
    if kind == "runs":
        data["runs"] = runs
    data["next"] = "割接 batch_id→getBizMonitorBoard/listBizMonitorBatches(kind=runs)；采集 batch_id→getBizCollectBatch；run_id→getBizMonitorDiffs。"
    return {"ok": True, "data": data}


def call_http_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        schema = _SCHEMAS.get(name)
        if schema is None:
            return mcp_from_handler_result(_error("unknown_tool"))
        for key, value in args.items():
            prop = schema["properties"].get(key)
            if prop is None:
                raise _InvalidArgument("存在未知字段；允许字段：" + ", ".join(schema["properties"]))
            if prop["type"] == "string" and not isinstance(value, str):
                raise _InvalidArgument(f"{key} 必须为字符串")
            if prop["type"] == "boolean" and not isinstance(value, bool):
                raise _InvalidArgument(f"{key} 必须为布尔值")
        if name == "listBizMonitors":
            result = _catalog(args)
        elif name == "listBizMonitorBatches":
            result = _batches(args)
        else:
            pid, tid, bid, rid, cid = (_s(args, key) for key in ("project_id", "task_id", "batch_id", "run_id", "command_id"))
            params: dict[str, Any] = {}
            if name == "getBizMonitorContext":
                if not pid and not tid:
                    return mcp_from_handler_result(_error("project_id_or_task_id_required"))
                path = "/v1/biz-migration/monitor-context"
                params = {k: v for k, v in (("project_id", pid), ("task_id", tid)) if v}
            else:
                missing = [key for key in schema["required"] if not _s(args, key)]
                if missing:
                    code = "batch_id_and_command_id_required" if name == "getBizCollectCommandRaw" else f"{missing[0]}_required"
                    return mcp_from_handler_result(_error(code))
                if name == "getBizMonitorBoard":
                    path = f"/v1/biz-migration/batches/{quote(bid, safe='')}/board"
                    if rid:
                        params["run_id"] = rid
                elif name == "listBizMonitorReds":
                    path = f"/v1/biz-migration/projects/{quote(pid, safe='')}/red-tickets"
                    if _s(args, "status"):
                        params["status"] = _s(args, "status")
                elif name == "getBizMonitorDiffs":
                    path = f"/v1/biz-migration/runs/{quote(rid, safe='')}/diffs"
                    params = {("kw" if k == "q" else k): _s(args, k) for k in ("metric_id", "sheet_id", "verdict", "color", "q") if _s(args, k)}
                    if "offset" in args:
                        params["offset"] = _n(args, "offset", 0, 0, 9007199254740991)
                elif name == "getBizCollectBatch":
                    path = f"/v1/biz-state/batches/{quote(bid, safe='')}"
                else:
                    path = f"/v1/biz-state/batches/{quote(bid, safe='')}/commands/{quote(cid, safe='')}"
                if "limit" in args:
                    params["limit"] = _n(args, "limit", 200, 1, 500)
            result = http_json("GET", path, params=params)
        return mcp_from_handler_result(result)
    except _InvalidArgument as exc:
        return mcp_from_handler_result(_error("invalid_argument", str(exc)))
    except (ValueError, TypeError, OverflowError):
        # Avoid echoing user-provided values (which might contain credentials).
        return mcp_from_handler_result(_error("invalid_argument", "参数类型或字段不合法；请参照 tools/list 的 inputSchema。"))
