# netx-topology-mcp

独立的 **stdio MCP**，只暴露 netx **拓扑画布 / Fabric** 能力，与告警/CLI 的 [`netx-mcp`](../netx-mcp) 分开安装，方便 Agent 按需启用。

```
MCP 宿主  →  stdio  netx_topology_mcp  →  HTTP  NETX_API_URL  →  netx API /v1/topology/*
```

详细说明 → **[docs/MCP_TOPOLOGY.md](../../docs/MCP_TOPOLOGY.md)**

配套 Skill → **[`.cursor/skills/netx-topology`](../../.cursor/skills/netx-topology/SKILL.md)**（画图流水线与硬规则）

## 安装

```powershell
cd D:\project\chatgpt\netx
pip install -e ./packages/netx-topology-mcp
python -c "import netx_topology_mcp; print('ok')"
```

GitHub：

```powershell
pip install "git+https://github.com/hansjone/netx.git#subdirectory=packages/netx-topology-mcp"
```

## 配置

复制 [`mcp.json`](./mcp.json) 到 Cursor / oclaw（`server_id=netx-topology`），可与 `netx` 同时存在。

## 工具（13）

| 类别 | 工具 |
|------|------|
| 树/画布 | `getTopologyTree`, `listTopologyViews`, `getTopologyView`, `createTopologyView` |
| 画图 | `addTopologyViewNodes`（仅已有 `fabric_node_ids`）, `removeTopologyViewNodes`, `updateTopologyViewPositions`, `projectTopologyNeighbors` |
| Fabric 只读 | `getTopologyFabricSummary`, `listTopologyFabricNodes`, `searchTopologyFabricNodes`, `queryTopologyNeighborhood`, `queryTopologyEdges` |

**安全约束：** MCP **不会**创建 Fabric 占位节点、**不会**写手工链路；画布只能引用已存在的 fabric 节点。
