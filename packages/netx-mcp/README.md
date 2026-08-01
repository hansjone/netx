# netx-mcp

轻量 **stdio MCP** 客户端，通过 HTTP 调用 [netx](../../README.md) REST API。装在 Cursor、oclaw、Claude Desktop 等 **MCP 宿主所在机器**；`NETX_API_URL` 可指向本机或远端 API。

**安装、更新、各宿主配置、排错** → 仓库主文档 **[docs/MCP.md](../../docs/MCP.md)**（请优先阅读）。

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

## 工具（14）

UME：`queryUmeAlarms`, `aggregateUmeAlarms`, `runUmeDiagnostics`, `queryUmeNeInventory`, `getUmeNe`, `queryUmeAlarmsRaw`, `aggregateUmeAlarmsRaw`, `listUmeAlarmFields`, `sqlQueryUme`

托管网元：`listManagedNe`, `getManagedNe`, `execManagedNe`, `listCliTargets`

拓扑 Fabric：`queryTopologyEdges`（可按 `node_id` 查 A 与多少网元互联，返回 `peer_count`）

## 兼容

全量 `netx-ops` 开发安装下 `python -m netx_api.mcp` 仍会转到本包；新环境请只装 **netx-mcp**。
