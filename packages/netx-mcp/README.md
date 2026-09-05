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

## 工具（14）

**NMS**（模型面通用名；当前适配器 zte-ume，REST 仍 `/v1/ume/*`）：  
`queryNmsAlarms`, `aggregateNmsAlarms`, `runNmsDiagnostics`, `queryNmsNeInventory`, `getNmsNe`, `queryNmsAlarmsRaw`, `aggregateNmsAlarmsRaw`, `listNmsAlarmFields`, `sqlQueryNms`

**common**：`listManagedNe`, `getManagedNe`, `execManagedNe`, `listCliTargets`, `findTopologyPaths`

参数优先 `nms_ne_id` / `nms_ne_ids`（保留 `ume_*` 别名）。

拓扑画布 / Fabric → 请单独安装 [`netx-topology-mcp`](../netx-topology-mcp)（见 [docs/MCP_TOPOLOGY.md](../../docs/MCP_TOPOLOGY.md)）。

## Breaking (0.3.0)

模型工具名从 `*Ume*` 改为 `*Nms*`（与 dsh-netxops 对齐）。请更新 skills / 提示词并重启 MCP 宿主。

## 兼容

全量 `netx-ops` 开发安装下 `python -m netx_api.mcp` 仍会转到本包；新环境请只装 **netx-mcp**。
