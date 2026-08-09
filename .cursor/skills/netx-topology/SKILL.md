---
name: netx-topology
description: >-
  用 netx-topology MCP 查邻接、dual_unit 分批沉入、扫角压交叉（不污染 Fabric）。
  触发：画拓扑、布图、拖图、LLDP、Fabric、netx-topology。先读本 skill 再调工具。
---

# netx 拓扑（通用）

只用 **`netx-topology`** MCP（包 `netx-topology-mcp`）。安装与 scopes：仓库 [`docs/MCP_TOPOLOGY.md`](../../../docs/MCP_TOPOLOGY.md)。

**原则**：复杂图先拆 **dual_units** 再拼；交叉少、边短、近轴优先。  
**禁止**写临时 py 穷举坐标或直接调 HTTP；验证与压交叉**只调 MCP**。不造 Fabric 边。  
**勿**把客户网元名、区域名、具体交叉数写进本 skill。

---

## 主路径（必循）

```
analyze(structure) → 认 dual_units / shape
→ 核心层：手拖或小图 layout(compact|corridor|rings)
→ sinkTopologyDualUnits（一批，layout_batch=true）
   或 move_nodes(park=true) 指定 ids
→ orbit_sweep(round) → polish_crossings → clear_edge_hits
→ 手拖微调 updateTopologyViewPositions
→ 下一批 sink…（禁止 until_empty 日常连抽）
```

停手：`overlaps=0` 且 `verdict.total≈70`、cpl 中档内即可交付。

---

## 阶段 0 — 画布与成员

1. `getTopologyTree` → **`view_id`**（文件夹 physical 画布）。
2. 建根/子区域只用 `createTopologyFolder`。
3. `analyzeTopologyViewLayout({ view_id, detail: "structure" })` 读 `dual_units` / `shape`。
4. `addTopologyViewNodes` / `projectTopologyNeighbors`（区域画布务必 `region_folder_id`）。
5. 多余点用 `removeTopologyViewNodes` **移出画布**（不删 Fabric）。`region:…` 幽灵点勿当网元拖。

---

## 形状与 dual_units

```
analyzeTopologyViewLayout({ view_id, detail: "structure" })
```

| 字段 | 用途 |
|------|------|
| `shape.primary` | `chains` / `star` / `mesh` / `mixed_blocks` |
| `dual_units` | 两端门户 + ≥2 条内部不交走廊；成员可重叠 |
| `advice.block_plan` | 每块怎么拖 |
| `gravity.type` | 链图勿当 hub 花瓣 |

- **链图**：脊柱水平 + stub；小图 preview `corridor`/`compact`（跳过 rings）。
- **巨图 / 多门户**：勿全图一把揉；走 **sink 分批 dual_unit**。
- **禁止**再用互斥 soft_block 把通路切开。

---

## 根图 → 子区域排水

```
sinkTopologyDualUnits({
  source_view_id: <根图>,
  sink_view_id: <子区域>,
  max_units: 3,
  max_batch_nodes: 120,
  layout_batch: true,   // 每单元 layout_dual_unit → 块扫挂 sink
})
→ orbit_sweep / polish_crossings / clear_edge_hits
→ 再调下一批（source_remaining>0）
```

- **一次只沉一批**；勿日常 `until_empty`。
- 落点：**块扫**（交叉/重叠/桥长择优），禁止固定往右排。
- 指定迁移：
  ```
  layoutTopologyView({
    action: "move_nodes",
    source_view_id: <FROM>, view_id: <TO>,
    mode: "apply",
    params: { fabric_node_ids: [...], park: true, remove_from_source: true },
  })
  ```
  `park=true` = 扫角停靠；回迁对调两 view_id。

---

## layoutTopologyView（精简）

| action | 用途 |
|--------|------|
| `layout` | 小图配方：`compact` / `corridor` / `rings` / `unstick` |
| `layout_dual_unit` | 双门户眼形；单元内交叉≠0 拒绝 |
| `move_nodes` / `sink_nodes` | 指定 ids 双向迁移；`park` 块扫 |
| `orbit_sweep` | 压交叉；`round` + 大 `max_jump`（约 1800–2800） |
| `polish_crossings` | 一键：straighten→press→untangle |
| `clear_edge_hits` | 网元贴非关联边时正交弹开 |
| `fix_overlaps` / `resolve_overlaps` | 只拉开重叠 |
| `untangle` | 贪心降交叉；默认可冻门户 |
| `straighten_channels` | 拉直 deg≤2 走廊 |
| `job_status` / `job_cancel` | 后台 job |

阶段2顺序：先 `orbit_sweep` 压交叉 → `polish_crossings` → 看 `edge_clearance` 再 `clear_edge_hits`。  
`orbit_round` 只在全局交叉严格下降时落笔；卡顿加大 `max_jump` / 单点 preview→pick。

---

## 验收

| 块 | 看什么 |
|----|--------|
| `overlap` | 硬零 |
| `crossing` | crossings/cpl；`top_nodes` / `top_edges` |
| `edge_clearance` | 贴边 → clear_edge_hits |
| `verdict.total` | ≈70 可交付（ov=0） |

图标 25px；推荐中心距 Δx≥200、Δy≥170。交叉 = 无向 NE↔NE 真交叉（共端点不算）。

---

## 工具速查

| 工具 | 作用 |
|------|------|
| `getTopologyTree` / `getTopologyView` | 树与画布 |
| `createTopologyFolder` | 新建根/区域（返回 view_id） |
| `add` / `remove` / `updateTopologyViewPositions` | 成员与手拖 |
| `sinkTopologyDualUnits` | dual_units 分批沉入 |
| `copyTopologyViewNodes` | 克隆沙箱 |
| `projectTopologyNeighbors` | 投影邻居 |
| `queryTopologyFabricNodes` | 库存（summary\|list\|search） |
| neighborhood / edges | 邻接 |
| `layoutTopologyView` | 上表 action |
| `analyzeTopologyViewLayout` | structure + 验收 |

---

## 代码热更

1. 本仓 MCP 用 `PYTHONPATH=…/src`，改源码后不必为加载而 pip install。
2. **必须重启** stdio 进程；`catalog` 含 `rev`（当前 `NETX_MCP_REV`）。
3. `layoutTopologyView(catalog=true)` 核对 action/recipe 清单。

拓扑页开「实时同步」可看落笔。勿用告警/CLI MCP 写拓扑。
