# netx-mcp

轻量 **stdio MCP** 客户端，通过 HTTP 调用 [netx](../../README.md) REST API。装在 Cursor、oclaw、Claude Desktop 等 **MCP 宿主所在机器**；`NETX_API_URL` 可指向本机或远端 API。

**安装、更新、各宿主配置、排错** → 仓库主文档 **[docs/MCP.md](../../docs/MCP.md)**（请优先阅读）。

**Skills（唯一真源在仓库根 [`skills/`](../../skills/README.md)）** — 本包不镜像 skills。

## 速查：安装

```powershell
# 在 netx 仓库根目录
pip install -e ./packages/netx-mcp
python -c "import netx_mcp; print('ok')"
```

要求 **Python 3.11+**，且 netx API 已运行（`GET /health`）。

## 速查：更新

```powershell
cd <netx 仓库>
git pull
pip install -e ./packages/netx-mcp
```

然后 **重启 MCP 宿主**（Cursor 重开；oclaw 重启后在 Admin 对 `netx` 执行 Health → Sync Tools）。

## 运行

```powershell
$env:NETX_API_URL = "http://127.0.0.1:8890"
python -m netx_mcp
# 或: netx-mcp
```

## 配置

[`mcp.json`](./mcp.json) — `command: python`，`args: ["-m", "netx_mcp"]`，`env` 见文件。

## 工具（15）— skill 组 **ops**（`netx-ops`）

**NMS**（模型面通用名；当前适配器 zte-ume，REST 仍 `/v1/ume/*`）：  
`queryNmsAlarms`, `aggregateNmsAlarms`, `runNmsDiagnostics`, `queryNmsNeInventory`, `getNmsNe`, `queryNmsAlarmsRaw`, `aggregateNmsAlarmsRaw`, `listNmsAlarmFields`, `sqlQueryNms`

**Managed CLI + paths**（问「能否登录」必须走这里，不要只查 inventory）：  
`listManagedNe`, `getManagedNe`, `execManagedNe`, `getNeExecJob`, `listCliTargets`, `findTopologyPaths`

0.3.1：`getNmsNe` 可直接传 host_name/IP；输出统一 `nms_ne_id` / `source=nms`；参数类型错误返回工具错误而非协议崩溃。

参数使用 `nms_ne_id` / `nms_ne_ids`，路径端点使用 `from_nms_ne_id` / `to_nms_ne_id`；`getManagedNe` 只接受 `ne_id`。旧 `ume_*`、`managed_ne_id`、`id`、告警 `ne_name` 别名已删除，未知参数会返回工具错误。REST 适配器内部的 `ume_*` 字段不变。

NMS 查询（含 inventory）按当前 HTTP 权限规则需要 `alarms:read`；托管资源、CLI 目标和路径查询需要 `ne:read`；执行与轮询需要 `ne:exec`；SQL 需要 `sql:query`。所有工具返回递归裁剪结构化口令、令牌等凭据，但 CLI 原文输出仍需调用者判断是否敏感。

拓扑画布 / Fabric → skill **topology** / [`netx-topology-mcp`](../netx-topology-mcp)（见 [docs/MCP_TOPOLOGY.md](../../docs/MCP_TOPOLOGY.md)）。

业务监控 / 割接 → [`netx-bizmonitor-mcp`](../netx-bizmonitor-mcp)。三组统一配置见根 `mcp.all.json`，均不依赖 netxops。

本包的 `execManagedNe` 独立强制只读 show/display；即使后端设备开放 shell，也拒绝配置、命令拼接、重定向和多行脚本。getManagedNe.capability 描述后端能力，不能作为 MCP 脚本许可；Linux/RouterOS 原生 CLI 当前未暴露。异步 ack 返回 `poll_after_ms=5000`；单台核对 terminal/result.ok，批量还须核对 summary.failed=0 和每个 results[].ok。作业 succeeded 仍可能有单台失败，须分别报告，勿整批重跑已成功设备。失败 job 作为工具错误返回，保留原始失败证据。

同 IP 多条纳管记录须按厂商/驱动选 ID。connect_status 为历史连接测试状态，不替代当前执行结果；有正常业务正文才算查询通过，不能把空正文或仅命令头当作成功。后端的 Cisco IOS SSH 驱动使用 CR 换行并等待命令回显和完整提示符；不影响其它厂商或 Telnet 的默认换行。

## Breaking (0.3.0)

模型工具名从 `*Ume*` 改为 `*Nms*`（与 dsh-netxops 对齐）。请更新 skills / 提示词并重启 MCP 宿主。

## 入口

只保留独立 **netx-mcp** 包入口：`python -m netx_mcp` / `netx-mcp`。旧 `netx_api.mcp`、`netx_api/mcp_server.py` 和直连数据库模式已删除；配置需迁移到本包。

旧 DB 模式的导入告警查询/聚合/批次/诊断、同步触发和历史告警查询未纳入本包 15 工具，相关 HTTP API 仍保留。
