"""离线生成验收样例/非现场故障；评价结论必须来自真实 netx 业务逻辑。

用法（在 worktree 根目录，使用已有 netx Python 环境）：
  python scripts/seed_mcp_acceptance.py --database <绝对路径>/artifacts/acceptance.sqlite \
      --metadata <绝对路径>/artifacts/acceptance.json

只接受本工人/组长 worktree artifacts 下带 acceptance 的新文件。
不启动服务、不导入应用入口、不使用网络、不连接应用数据库。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_WORKTREES = frozenset({"netx-mcp-bizmonitor-codex", "netx-mcp-lead"})
DECLARATION = "验收样例/非现场故障：离线合成接口采集输入，真实执行 netx 业务评价；无真实设备或凭据。"
PREFIX = "acceptance-"


def artifact_path(raw: str, *, metadata: bool = False) -> Path:
    if ROOT.parent.name != "agent-wt" or ROOT.name not in ALLOWED_WORKTREES:
        raise ValueError("脚本必须位于允许的 agent-wt worktree 内")
    path = Path(raw)
    if not path.is_absolute() or "://" in raw:
        raise ValueError("必须指定 SQLite 文件绝对路径，不能使用数据库 URL")
    path = path.resolve()
    allowed = []
    for name in ALLOWED_WORKTREES:
        worktree = ROOT.parent / name
        base = worktree / "artifacts"
        # Reparse points/symlinks must not turn the allowlist into an external directory.
        if worktree.resolve() != worktree or base.resolve() != base:
            continue
        allowed.append(base)
    if not any(path.is_relative_to(base) for base in allowed):
        raise ValueError("仅允许本工人/组长 worktree 的 artifacts 目录")
    suffixes = {".json"} if metadata else {".sqlite", ".sqlite3", ".db"}
    if "acceptance" not in path.name.lower() or path.suffix.lower() not in suffixes:
        raise ValueError("文件名必须含 acceptance，扩展名须为 SQLite 文件或元数据 JSON")
    if path.exists():
        raise ValueError("目标已存在，拒绝覆盖；请使用新的验收文件名")
    return path


def _offline_audit(event: str, _args: tuple[Any, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo", "socket.bind"}:
        raise RuntimeError("离线样例脚本禁止网络/监听端口")


def seed(database: Path) -> dict[str, Any]:
    # Environment must precede imports; explicit sys.path wins over editable main repo.
    if any(name == "netx_api" or name.startswith("netx_api.") for name in sys.modules):
        raise RuntimeError("必须通过独立 Python 进程运行，不能复用已导入 netx_api 的进程")
    url = "sqlite:///" + database.as_posix()
    os.environ["NETX_DATABASE_URL"] = url
    os.environ["NETX_CREDENTIAL_SECRET_FILE"] = str(database.with_suffix(".unused-secret"))
    for setting in ("NE_COLLECT_SCHEDULER_ENABLED", "CONFIG_SYNC_SCHEDULER_ENABLED",
                    "LLDP_COLLECT_SCHEDULER_ENABLED", "FABRIC_RECONCILE_SCHEDULER_ENABLED",
                    "PORT_TRAFFIC_SCHEDULER_ENABLED", "BIZ_STATE_SCHEDULER_ENABLED",
                    "UME_KEEPALIVE_ENABLED", "UME_ALARM_WS_ENABLED",
                    "UME_SYNC_ALARMS_CURRENT_ENABLED", "UME_SYNC_INVENTORY_AUTO_ENABLED",
                    "UME_SYNC_TOPOLOGY_AUTO_ENABLED"):
        os.environ["NETX_" + setting] = "false"
    sys.path.insert(0, str(ROOT))
    sys.addaudithook(_offline_audit)

    import netx_api
    from sqlalchemy.engine import make_url
    from netx_api.db import Base, SessionLocal, engine
    from netx_api.biz_migration import service as mig
    from netx_api.biz_state import service as biz
    from netx_api.models import (
        BizCompareTemplate, BizMigrationProject, BizMonitorTemplate,
        BizPortMapping, BizPortMappingRow, BizStateBatch, BizStateBatchCommand,
        BizStateMetricRow, BizStateTask, BizStateTaskItem,
    )

    if Path(netx_api.__file__).resolve().parent != ROOT / "netx_api":
        raise RuntimeError("netx_api 未从当前 worktree 导入，拒绝运行")
    if engine.url != make_url(url):
        raise RuntimeError("数据库配置未指向隔离 SQLite 文件，拒绝运行")
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ids = {
        "project_id": PREFIX + "project",
        "old_portrait_task_id": PREFIX + "old-portrait",
        "new_portrait_task_id": PREFIX + "new-portrait",
        "old_current_task_id": PREFIX + "old-current",
        "new_current_task_id": PREFIX + "new-current",
    }
    batch_ids: dict[str, str] = {}
    command_ids: dict[str, str] = {}
    try:
        with SessionLocal() as db:
            sheet = {"sheet_id": "interface_brief", "title": "验收样例接口",
                     "metric_id": "interface_brief", "key_fields": ["interface"],
                     "iface_fields": ["interface"], "compare_fields": ["admin", "phy", "prot"],
                     "row_filters": [], "field_rules": []}
            db.add(BizCompareTemplate(id=PREFIX + "compare", name="验收样例/非现场故障-接口对比",
                                     metric_id="interface_brief", key_fields=["interface"],
                                     iface_fields=["interface"], compare_fields=["admin", "phy", "prot"],
                                     metrics_json=[sheet], iface_normalize_json=[{"from": "GE", "to": "gei"}]))
            db.add(BizMonitorTemplate(id=PREFIX + "monitor", name="验收样例/非现场故障-接口监控",
                                     compare_template_id=PREFIX + "compare",
                                     collect_metric_ids_json=["interface_brief"],
                                     defaults_json={"out_of_expect": "strict"}, sheet_overrides_json=[{
                                         "metric_id": "interface_brief", "sheet_id": "interface_brief",
                                         "status_fields": ["admin", "phy", "prot"],
                                         "down_values": ["down"], "up_values": ["up"],
                                         "success": [{"old": ["removed", "down"], "new": ["added", "up", "unchanged"]}],
                                         "anomaly": [],
                                     }]))
            db.add(BizPortMapping(id=PREFIX + "mapping", name="验收样例端口映射", note=DECLARATION))
            db.add(BizPortMappingRow(id=PREFIX + "mapping-row", mapping_id=PREFIX + "mapping",
                                     before_if="gei-old", after_if="xgei-new"))
            for side in ("old", "new"):
                for mode in ("portrait", "current"):
                    tid = ids[f"{side}_{mode}_task_id"]
                    task = BizStateTask(
                        id=tid, source="import", ne_id=PREFIX + side + "-offline",
                        ne_name=f"验收样例-{'旧' if side == 'old' else '新'}侧（非现场）", ne_ip="",
                        vendor="zte", device_type="router", note=DECLARATION,
                        purpose="portrait" if mode == "portrait" else "cutover_hf",
                        status="draft", collect_manual_only=True, collect_running=False,
                        interval_sec=3600, retention_days=30,
                    )
                    db.add(task)
                    db.add(BizStateTaskItem(id=PREFIX + side + "-" + mode + "-item", task_id=tid,
                                           source_profile_id="zte.interface_brief", kind="catalog",
                                           enabled=False, title="验收样例离线输入；禁用采集"))
                    bid = PREFIX + side + "-" + mode + "-collect"
                    cid = PREFIX + side + "-" + mode + "-command"
                    batch_ids[f"{side}_{mode}"] = bid
                    command_ids[f"{side}_{mode}"] = cid
                    at = now - timedelta(minutes=10) if mode == "portrait" else now - timedelta(seconds=2)
                    interface = "GE-old" if side == "old" else "xgei-new"
                    state = "up" if side == "old" else "down"
                    raw = ("# 验收样例/非现场故障；离线合成输出\n"
                           "show interface brief\nInterface Admin Phy Prot\n"
                           f"{interface} {state} {state} {state}\n")
                    db.add(BizStateBatch(id=bid, task_id=tid, source="import", ne_id=task.ne_id,
                                         ne_name=task.ne_name, vendor="zte", status="success",
                                         started_at=at, ended_at=at + timedelta(seconds=1),
                                         row_count=1, command_count=1, message=DECLARATION,
                                         is_baseline=mode == "portrait"))
                    db.add(BizStateBatchCommand(id=cid, batch_id=bid,
                                                profile_id="zte.interface_brief", parser_id="zte.interface_brief",
                                                metric_id="interface_brief", raw_command="show interface brief",
                                                parse_status="ok", row_count=1, raw_line_count=len(raw.splitlines()),
                                                raw_text=raw, message=DECLARATION, created_at=at))
                    db.add(BizStateMetricRow(id=PREFIX + side + "-" + mode + "-row", batch_id=bid,
                                             batch_command_id=cid, task_id=tid, ne_id=task.ne_id,
                                             metric_id="interface_brief", seq=0, collected_at=at,
                                             data_json={"interface": interface, "admin": state, "phy": state, "prot": state}))
            db.add(BizMigrationProject(
                id=ids["project_id"], name="验收样例/非现场故障-旧口仍UP新口DOWN", note=DECLARATION,
                old_task_id=ids["old_portrait_task_id"], new_task_id=ids["new_portrait_task_id"],
                old_hf_task_id=ids["old_current_task_id"], new_hf_task_id=ids["new_current_task_id"],
                old_baseline_batch_id=batch_ids["old_portrait"], new_baseline_batch_id=batch_ids["new_portrait"],
                mapping_id=PREFIX + "mapping", monitor_template_id=PREFIX + "monitor",
                collect_metric_ids_json=["interface_brief"], status="draft", hf_interval_sec=3600,
            ))
            db.commit()
            cutover = mig.create_batch(db, ids["project_id"], {
                "batch_label": "验收样例/非现场故障-第一批", "status": "active",
                "expect_set": {"ports": ["gei-old"]}, "note": DECLARATION,
            })
            # This executes real run_evaluate and real red-ticket persistence.
            finished = mig.finish_batch(db, cutover["id"], mark_done=False)
            run_id = finished["run"]["id"]
            diffs = mig.list_run_diffs(db, run_id, color="red", limit=50)
            reds = mig.list_red_tickets(db, ids["project_id"])
            if diffs["total"] < 1 or reds["open_count"] < 1 or finished["accept_summary"]["passed"]:
                raise RuntimeError("真实评价未生成预期红项，样例验证失败")
            item = diffs["items"][0]
            if item["old_key"] != "GE-old" or item["new_key"] != "xgei-new":
                raise RuntimeError("缺少原始 A/B 键，样例验证失败")
            evidence = item.get("evidence") or {}
            raw_checks: dict[str, Any] = {}
            for side in ("old", "new"):
                ev = evidence.get(side) or {}
                command = ev.get("command") or {}
                collect = ev.get("collect") or {}
                if command.get("raw_command") != "show interface brief" or not collect.get("collected_at"):
                    # Some evidence versions name the timestamp at batch granularity.
                    if command.get("raw_command") != "show interface brief" or not any(collect.get(k) for k in ("started_at", "ended_at", "batch_started_at")):
                        raise RuntimeError("双侧命令/采集时间证据缺失")
                bid = collect["batch_id"]
                cid = command.get("id") or command.get("command_id")
                if not cid:
                    raise RuntimeError("证据没有可请求的命令 ID")
                raw = biz.get_batch_command(db, bid, cid)
                if "验收样例/非现场故障" not in raw["raw_text"]:
                    raise RuntimeError("命令原文回读失败")
                raw_checks[side] = {"batch_id": bid, "command_id": cid, "raw_text": raw["raw_text"],
                                    "collected_at": raw["collected_at"]}
            # Read paths behind all eight MCP tools; HTTP/stdio is the lead's next step.
            assert mig.list_projects(db) and biz.list_tasks(db)
            assert mig.list_batches(db, ids["project_id"]) and biz.list_batches(db, ids["old_current_task_id"])
            assert mig.list_runs(db, cutover["id"])
            assert mig.get_monitor_context(db, project_id=ids["project_id"])
            assert mig.board(db, cutover["id"], run_id=run_id)
            assert biz.get_batch(db, batch_ids["new_current"])["commands"]
            tasks = db.query(BizStateTask).all()
            if not all(t.status == "draft" and t.collect_manual_only and not t.collect_running for t in tasks):
                raise RuntimeError("样例任务安全状态检查失败")
            db.commit()
            return {
                "declaration": DECLARATION, "database": str(database), "database_url": url,
                "source_root": str(ROOT), "created_at": now.isoformat() + "Z", **ids,
                "cutover_batch_id": cutover["id"], "run_id": run_id,
                "collect_batch_ids": batch_ids, "command_ids": command_ids,
                "expected": {"scenario": "旧侧接口仍UP，新侧映射接口DOWN，割接验收未完成，应产生红项",
                             "old_key": "GE-old", "new_key": "xgei-new", "minimum_red_count": 1},
                "observed": {"red_diff_count": diffs["total"], "open_red_count": reds["open_count"],
                             "verdict": item["verdict"], "color": item["color"],
                             "accept_status": finished["batch"]["accept_status"],
                             "accept_summary": finished["accept_summary"], "raw_evidence": raw_checks},
                "safety": {"network_blocked": True, "task_status": "draft", "manual_only": True,
                           "task_items_enabled": False, "device_credentials": False, "real_device_ip": False},
            }
    finally:
        engine.dispose()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="artifacts 内新 SQLite 文件的绝对路径，文件名含 acceptance")
    parser.add_argument("--metadata", help="artifacts 内新 JSON 文件的绝对路径，文件名含 acceptance")
    args = parser.parse_args()
    try:
        database = artifact_path(args.database)
        metadata = artifact_path(args.metadata, metadata=True) if args.metadata else None
        database.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents overwrite, including races after validation.
        with database.open("xb"):
            pass
        result = seed(database)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if metadata:
            metadata.parent.mkdir(parents=True, exist_ok=True)
            with metadata.open("x", encoding="utf-8") as output:
                output.write(text + "\n")
        print(text)
        return 0
    except Exception as exc:
        # Known local validation messages are safe; other failures omit internals.
        message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        print(json.dumps({"ok": False, "error": message, "declaration": DECLARATION}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
