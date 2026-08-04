---
name: netx-topology
description: >-
  用 netx-topology MCP 查链路、画拓扑（不污染 Fabric）。触发：画拓扑、LLDP、Fabric、netx-topology。先读再调。
user-invocable: true
disable-model-invocation: false
---

# netx 拓扑 MCP

通过 **`netx-topology`** MCP（包 `netx-topology-mcp`）操作 netx 拓扑。与告警/CLI 的 **`netx`** MCP **分开**；画图只用本包工具。

安装与 scopes 真源：仓库 [`docs/MCP_TOPOLOGY.md`](../../../docs/MCP_TOPOLOGY.md)。

## 硬规则

1. **先读后写**：任何建图/摆点前先 `getTopologyTree`；改已有图前可 `getTopologyView`（大图慎拉整图）。
2. **筛选交给 API**：加/挪/删优先传 `keyword` / `role` / `vendor` / `link_status`（加节点再用 `limit`/`offset`）。**不要**先 list 全量再回传成千上万 id。
3. **只动已有 Fabric**：禁止 `managed_ne_ids` / `ume_ne_ids`；禁止臆造 fabric id。
4. **不污染 Fabric**：不能手工建链、不能 populate、不能删 Fabric / 整图。邻居用 `projectTopologyNeighbors`。
5. **写权限**：需要 `ne:write`。tools/list 没有写工具 → 停，让用户用「MCP + 拓扑写」签发 Token。
6. **无区域则先建**：`createTopologyView` 需要 `folder_id`；没有合适区域时用 `createTopologyFolder`，再 `createTopologyView` 建画布。
7. **单画布硬顶 2000**：满了 `truncated` / 触顶 → 新建另一张画布继续；全网五万设备靠多画布切片。

## 推荐流水线（从零画一张图）

```
1 getTopologyTree                         → 看有无区域；没有则 createTopologyFolder → folder_id
2 createTopologyView                      → view_id
3 addTopologyViewNodes(keyword=…, limit)  → 看 added / next_offset，循环 offset 直到无更多或满 cap
4 projectTopologyNeighbors                → 可选
5 updateTopologyViewPositions(layout=grid|offset|stack, keyword=…)  → API 自己筛并摆
6 （少量微调才用 positions[]）
```

查链路不画图：`queryTopologyEdges` / `queryTopologyNeighborhood`。

## 工具速查

| 目的 | 工具 |
|------|------|
| 树 / 区域 / 画布 | `getTopologyTree`, `createTopologyFolder`, `listTopologyViews`, `getTopologyView`, `createTopologyView` |
| 筛选批量加 | `addTopologyViewNodes`（filters + limit/offset） |
| 筛选批量挪 | `updateTopologyViewPositions`（layout + filters） |
| 筛选批量删 | `removeTopologyViewNodes`（filters） |
| 投影邻居 | `projectTopologyNeighbors` |
| Fabric 读 | `search…` / `list…` / `queryTopologyEdges` / `…Neighborhood` / summary |

## 对人说清楚

- 网页观看：拓扑页开 **「实时同步」**（默认关）。
- 回报：`folder_id` / `view_id`、画布名、`added`/`updated`/`removed`、是否 `truncated`/`next_offset`。

## 不要做

- 不要用 `netx` 告警 MCP 冒充拓扑写。
- 不要为画图去造 Fabric / 改 managed-NE。
- 不要在未确认 folder/view 时连环盲写。
