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

停手：`overlaps=0` 且 `verdict.total≈70`、cpl 中档、`edge_clearance.status≠fail`、`edge_axis.status≠fail` 方可交付。**勿只看 crossing**——orbit_sweep 的 `improving_n=0` 只代表交叉维度卡壳，须逐项检查 edge_clearance/edge_axis/rings，任一 fail 即继续处理。

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

## 分层布局（特殊场景）

当网络需呈现层级结构（外部→接入→核心→汇聚→客户等）时，优化策略不同于通用 mesh：

### 分层通用规则
1. **顶层：外部/对接网络**
2. **次顶层：终端客户接入层**
3. **中间层：核心层**
4. **核心下层：汇聚层**
5. **底层：接入层/孤立层**

### 优化流程
1. **先手动后算法**：用户手动拖拽保证业务结构，再用算法优化
2. **分层约束**：用 `updateTopologyViewPositions` 按层级设定 y 坐标（同层 y 落在区间内可错落，勿跨层）；`orbit_sweep` 传 `y_min/y_max` 约束搜索在本层区间内
3. **分步迭代**：
   - `polish_crossings` 减少交叉（可能破坏分层）
   - 若分层被破坏，**保留 x 坐标，恢复 y 坐标**重新分层
   - `clear_edge_hits` 消除贴边
   - `fix_overlaps` 修复重叠

### 冲突处理原则
| 冲突场景 | 处理方式 |
|---------|---------|
| 交叉 vs 结构 | **结构优先**，接受少量跨层交叉 |
| 算法 vs 手动 | **手动优先**，算法辅助 |
| polish 破坏分层 | 保留 x 坐标，恢复 y 坐标分层 |

### 关键经验
1. **跨层交叉是结构性的**：跨层连线必然穿越中间层，属正常
2. **外层节点必须在外部层**：与 PS/PE 连接的外部网元（RNC、华为设备等）应放在最上方
3. **orbit_sweep 卡壳更早**：分层约束下 `improving_n=0` 来得快；改传 `objective=total` 让 orbit_sweep 按 crossing+edge_clearance 综合排序
4. **验收以层级清晰为先**：`verdict.total` 次于结构可读性；同层 y 在区间内、层间不串层即可交付

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
| `orbit_sweep` | 压交叉；`round` + 大 `max_jump`（约 1800–2800）；`objective=total` 综合 crossing+edge_clearance；`y_min/y_max` 分层约束 |
| `polish_crossings` | 一键：straighten→press→untangle |
| `clear_edge_hits` | 网元贴非关联边时正交弹开 |
| `fix_overlaps` / `resolve_overlaps` | 只拉开重叠 |
| `untangle` | 贪心降交叉；默认可冻门户 |
| `straighten_channels` | 拉直 deg≤2 走廊 |
| `job_status` / `job_cancel` | 后台 job |

阶段2是多目标循环，非单向链：

1. `orbit_sweep` 压交叉 → `polish_crossings`
2. **orbit_sweep `improving_n=0` 时勿停**——立即转看 `edge_clearance`（贴边对视觉可读性影响 ≥ 交叉）→ `clear_edge_hits` 正交弹开
3. 再看 `edge_axis`（斜边过多）→ `straighten_channels` / 手拖归轴
4. 回头复检 crossing 是否因上步变动出现新机会 → 再 `orbit_sweep`

`orbit_round` 只在全局交叉严格下降时落笔；卡顿加大 `max_jump` / 单点 preview→pick。

**关键**：单节点 `updateTopologyViewPositions` 拖动时，某节点移动可能增交叉但解多个贴边——以 `verdict.total` 升降为准，勿只盯 crossing 数。`drag_candidates` 可能为空，此时看 `edge_clearance.top` / `crossing.top_nodes` 自行判断拖谁。

---

## 验收

| 块 | 看什么 |
|----|--------|
| `overlap` | 硬零（权重 0.24，硬门控） |
| `crossing` | crossings/cpl；`top_nodes` / `top_edges`（权重 0.18） |
| `rings` | 最小环被穿（权重 0.10） |
| `edge_clearance` | 贴边 → clear_edge_hits；**权重 0.08 但视觉影响 ≥ crossing，优先处理** |
| `edge_axis` | 斜边过多 → straighten_channels / 手拖归轴（权重 0.06） |
| `verdict.total` | ≈70 可交付（ov=0）；**总分升降为准，勿只盯 crossing** |

### 评分优化快速方法
1. **消除 overlap** → 硬门控项，必须为 0
2. **减少 crossings** → polish_crossings 大幅降低
3. **处理 edge_clearance** → clear_edge_hits 消除贴边
4. **结构检查** → 分层清晰 > 交叉最少

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

## 通用优化策略（实战经验）

### 算法与手动的最佳组合
```
Step 1: 用户手动拖拽 → 保证业务结构和分层
Step 2: polish_crossings → 大幅减少交叉（接受可能破坏分层）
Step 3: 检查分层 → 若被破坏，保留 x 恢复 y 重新分层
Step 4: clear_edge_hits → 处理贴边问题
Step 5: fix_overlaps → 修复节点重叠
Step 6: 最终验收 → 结构清晰 > 交叉最少
```

### 工具使用优先级
| 场景 | 首选工具 | 参数建议 |
|-----|---------|---------|
| 整体减交叉 | `polish_crossings` | `top_n=10, max_moves=50` |
| 单点微调 | `orbit_sweep` | `objective=total, y_min/y_max` |
| 处理贴边 | `clear_edge_hits` | `top_n=15, max_moves=30` |
| 修复重叠 | `fix_overlaps` | 直接调用 |
| 批量调坐标 | `updateTopologyViewPositions` | 保留 x，恢复 y |

### 常见问题解决方案
| 问题 | 原因 | 解决方案 |
|-----|------|---------|
| polish 破坏分层 | 算法优先减少交叉 | 保留 x 坐标，恢复 y 分层 |
| orbit_sweep 无改善 | 位置已优化 | 接受现状或手动调整 |
| 节点重叠 | 坐标调整太近 | fix_overlaps 自动修复 |
| 交叉突然增加 | clear_edge_hits 移动节点 | 重新 polish_crossings |

### 关键原则
1. **业务结构优先于算法优化**：网络拓扑的分层结构比最少交叉更重要
2. **局部微调优于全局重置**：用 `orbit_sweep`/`updateTopologyViewPositions` 单点调整，而非 `layout` 全局布局
3. **预览模式优先于应用模式**：先用 `mode=preview` 查看效果，确认后再 `mode=apply`
4. **分步迭代优于一次性操作**：polish → clear_edge_hits → fix_overlaps 分步骤执行

---

## 代码热更

1. 本仓 MCP 用 `PYTHONPATH=…/src`，改源码后不必为加载而 pip install。
2. **必须重启** stdio 进程；`catalog` 含 `rev`（当前 `NETX_MCP_REV`）。
3. `layoutTopologyView(catalog=true)` 核对 action/recipe 清单。

拓扑页开「实时同步」可看落笔。勿用告警/CLI MCP 写拓扑。
