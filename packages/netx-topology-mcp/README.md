# netx-topology-mcp

独立的 **stdio MCP**，只暴露 netx **拓扑画布 / Fabric** 能力，与告警/CLI 的 [`netx-mcp`](../netx-mcp) 分开安装，方便 Agent 按需启用。

```
MCP 宿主  →  stdio  netx_topology_mcp  →  HTTP  NETX_API_URL  →  netx API /v1/topology/*
```

详细说明 → **[docs/MCP_TOPOLOGY.md](../../docs/MCP_TOPOLOGY.md)**

配套 Skill → **[`.cursor/skills/netx-topology`](../../.cursor/skills/netx-topology/SKILL.md)**（画图流水线与硬规则）

## 目录模型

- 顶级「根」为导航；其下「根图」/ `Root map` 为画布。
- **只**用 `createTopologyFolder`：顶级建根+根图；根图下建**子区域**（各带 physical `view_id`）。
- 不提供 `createTopologyView`（禁止同目录另挂 custom 交付图）。

## 安装

```powershell
cd D:\project\chatgpt\netx
pip install -e ./packages/netx-topology-mcp
# optional: igraph for soft-block Leiden / block-center FR (layout still ours)
pip install -e "./packages/netx-topology-mcp[layout]"
python -c "import netx_topology_mcp; print('ok')"
```

GitHub：

```powershell
pip install "git+https://github.com/hansjone/netx.git#subdirectory=packages/netx-topology-mcp"
```

## 配置

复制 [`mcp.json`](./mcp.json) 到 Cursor / oclaw（`server_id=netx-topology`），可与 `netx` 同时存在。

画图请在 env 里设带 `ne:write` 的 `NETX_API_TOKEN`（不要只靠仓库 `mcp_token`）。`getTopologyView` / `projectTopologyNeighbors` 默认 `detail=summary`（坐标抽样 + `links[]` 邻接）；`queryTopologyEdges` 默认 adjacency（一对网元一条线），避免整图/端口边占满上下文。

## 工具（14）

| 类别 | 工具 |
|------|------|
| 树/区域/画布 | `getTopologyTree`, `createTopologyFolder`（唯一建图，返回 view_id）, `getTopologyView` |
| 画图 | `addTopologyViewNodes` / `remove…` / `sinkTopologyDualUnits`（分批沉入+默认可先 layout_dual_unit） / `copyTopologyViewNodes`（一键克隆测沙箱） / `update…Positions`（**优先筛选**，API 自选 id；也可 id 列表）, `projectTopologyNeighbors` |
| Fabric 只读 | `queryTopologyFabricNodes`（summary\|list\|search）, `queryTopologyNeighborhood`, `queryTopologyEdges` |
| 自动布图 | `layoutTopologyView`（小图 `compact`/`corridor`/`rings`；巨图：`sinkTopologyDualUnits`→`orbit_sweep`→`polish_crossings`→`clear_edge_hits`） |
| 布图验收/规划 | `analyzeTopologyViewLayout`（score；`detail=structure` 定重心；`both`=structure+手拖 sight） |

**安全约束：** MCP **不会**创建 Fabric 占位节点、**不会**写手工链路；画布只能引用已存在的 fabric 节点。
