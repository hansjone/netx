# 割接与业务监控 MCP

`netx-bizmonitor-mcp` 独立提供 bizMonitor 的 8 个只读工具。不安装 netxops 也能读取已有项目、批次、看板、红票、差异和采集原文。它只依赖 Python 3.11+ 与 httpx，通过 HTTP 使用正在运行的 netx。

## 三组一起配置

在运行 MCP 客户端的 Python 环境分别安装：

```powershell
python -m pip install -e ./packages/netx-mcp
python -m pip install -e ./packages/netx-topology-mcp
python -m pip install -e ./packages/netx-bizmonitor-mcp
```

将根 [mcp.all.json](../mcp.all.json) 的三个 server 放入客户端 MCP 配置。也可以不安装包，分别将该包的 `src` 绝对路径设为对应 server 的 `PYTHONPATH`。三个模块分别为 `netx_mcp`、`netx_topology_mcp`、`netx_bizmonitor_mcp`；console scripts 属于各独立包，安装 API 本体不会提供一个缺少依赖的 MCP 假入口。

统一设置 `NETX_API_URL`。凭据优先用 `NETX_API_TOKEN`，否则读取 `NETX_MCP_TOKEN_FILE`，再尝试工作目录 `data/auth/mcp_token`。客户端工作目录不确定时，给 `NETX_MCP_TOKEN_FILE` 配置绝对路径。不要把真实令牌提交到仓库。

业务组需 `biz-monitor:read`；ops 需 `alarms:read`、`ne:read`、`ne:exec`，SQL 另需 `sql:query`；topology 读取需 `ne:read`，建图/布局需 `ne:write`。默认 bootstrap token 不含 SQL 和拓扑写权限，因此工具数量会按 scope 缩减；有完整权限时分别列出 **15、16、8** 个工具。

## 入口与证据链

MCP 使用裸名；netxops 使用 `netx__` 加相同名称。

1. `listBizMonitors`：无 ID 的入口，取得 project_id/task_id。
2. `getBizMonitorContext`：读取模板、归一化、端口映射、两侧任务和基线。
3. `listBizMonitorBatches(project_id,include_runs=true)`：取得割接批次与 run_id；也可 `kind=runs,batch_id=...`。
4. `getBizMonitorBoard(batch_id)`：检查完成状态、缺失数据和统计。
5. `listBizMonitorReds(project_id)` / `getBizMonitorDiffs(run_id,color=red)`：逐页取得红项和证据。`q` 自动映射后端 `kw`。
6. `getBizCollectBatch`：采集批次的命令目录；需要时由 `listBizMonitorBatches(task_id)` 找采集批次。
7. `getBizCollectCommandRaw(batch_id,command_id)`：两侧原文取证。这里使用采集 batch_id，不能传割接 batch_id。

`old_key/new_key` 保留设备原始 A/B；`match_*` 和归一化映射用于内部配对。原文按需获取，可能很大；凭据会脱敏，不把内部匹配键冒充设备原始端口。

本组不会创建任务/模板、启动采集、执行 evaluate、关闭红票。没有批次时不能得出“割接通过”。完整行为和参数见 [包 README](../packages/netx-bizmonitor-mcp/README.md) 与 [skill](../skills/biz-monitor/netx-biz-monitor/SKILL.md)。

## 可复现验收

`scripts/mcp_acceptance.py` 启动当前 worktree 的真实 stdio MCP，记录去敏 JSONL；仅允许本机 8890 或隔离样例 8891。工具源目录可用 `--source-root` 显式指定，不会静默导入主仓库的 editable 包。

没有现场割接数据时，可用 `scripts/seed_mcp_acceptance.py` 在 `agent-wt/netx-mcp-lead/artifacts/` 的新 acceptance SQLite 文件生成明确标注的离线样例，真实运行业务评价，再通过本地 API/MCP 验证读取链路。样例不代表现场故障；不要将样例库替换应用库。脚本拒绝覆盖、外部路径、网络连接和监听端口。
