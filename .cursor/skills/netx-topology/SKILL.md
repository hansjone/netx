---
name: netx-topology
description: >-
  用 netx-topology MCP 查询 Fabric 链路、建拓扑画布并安全摆点（不污染 Fabric）。
  触发：画拓扑/拓扑图/拓扑画布、LLDP 邻居/链路、Fabric 网元搜索、createTopologyView /
  addTopologyViewNodes、netx-topology、看着 MCP 画图。须先读本 skill 再调 MCP。
user-invocable: true
disable-model-invocation: false
---

# netx 拓扑 MCP

通过 **`netx-topology`** MCP（包 `netx-topology-mcp`）操作 netx 拓扑。与告警/CLI 的 **`netx`** MCP **分开**；画图只用本包工具。

安装与 scopes 真源：仓库 [`docs/MCP_TOPOLOGY.md`](../../../docs/MCP_TOPOLOGY.md)。

## 硬规则

1. **先读后写**：任何建图/摆点前先 `getTopologyTree`；改已有图前先 `getTopologyView`。
2. **只放已有 Fabric 节点**：`addTopologyViewNodes` **仅** `fabric_node_ids`。禁止臆造 id；禁止试图传 `managed_ne_ids` / `ume_ne_ids`（会被拒）。
3. **不污染 Fabric**：本 MCP **不能**手工建链、不能 `populate`、不能删 Fabric / 整图。链路来自已有 LLDP/手工边；邻居用 `projectTopologyNeighbors`。
4. **写权限**：画图工具要 token 含 `ne:write`。若 `tools/list` 没有写工具 → 停下来告诉用户去 **系统 → API Key** 用「MCP + 拓扑写」签发，并配置 `NETX_API_TOKEN` 后 Sync Tools。勿假装已画成功。
5. **无文件夹则停**：`createTopologyView` 需要已有 `folder_id`（region）。树里没有可用 folder 时，请用户先在网页建区域，或改挂到已有 region；**MCP 不能新建 region/folder**。
6. **批量克制**：单次 `addTopologyViewNodes` 控制在合理数量（优先先搜再加）；大图用多次调用 + `projectTopologyNeighbors` 扩展。

## 推荐流水线（从零画一张图）

```
1 getTopologyTree          → 选 folder_id（region）
2 searchTopologyFabricNodes / listTopologyFabricNodes → 拿到 fabric_node_ids
3 createTopologyView       → name + folder_id → 得到 view_id
4 addTopologyViewNodes     → view_id + fabric_node_ids（layout=grid）
5 projectTopologyNeighbors → 把已有 LLDP 邻居投影上画布（可重复）
6 （可选）updateTopologyViewPositions → 微调坐标
7 getTopologyView          → 向用户确认节点/边数量
```

查链路不画图时：`queryTopologyEdges`（带 `node_id` 看 `peer_count`）或 `queryTopologyNeighborhood`。

## 工具速查

| 目的 | 工具 |
|------|------|
| 树 / region / 已有画布 | `getTopologyTree`, `listTopologyViews` |
| 读一图画布 | `getTopologyView` |
| 新建画布 | `createTopologyView` |
| 摆点 / 移除（仅画布） | `addTopologyViewNodes`, `removeTopologyViewNodes` |
| 摆坐标 | `updateTopologyViewPositions` |
| 投影 LLDP 邻居 | `projectTopologyNeighbors` |
| 搜 Fabric | `searchTopologyFabricNodes`, `listTopologyFabricNodes` |
| 汇总 / 邻接 / 边 | `getTopologyFabricSummary`, `queryTopologyNeighborhood`, `queryTopologyEdges` |

## 对人说清楚

- 网页观看：拓扑页左侧或浏览区开 **「实时同步」**（默认关）；**不必先打开某张图**也能看到新建画布。
- 回报时给出：`view_id`、画布名、folder、节点数；写失败则原样报 scope/API 错误。

## 不要做

- 不要用 `netx`（告警 MCP）冒充拓扑写接口。
- 不要为「画上设备」去改 managed-NE / 造假 Fabric。
- 不要在未确认 folder/view 时连环盲写。
