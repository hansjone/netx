---
name: netx-topology
description: >-
  用 netx-topology MCP / DSH topology 组查邻接、分类打标；先 dual_unit 一次抽最大核心眼，
  再换其它算法下沉/布图（不污染 Fabric）。触发：画拓扑、布图、拖图、分类、LLDP、Fabric、netx-topology。
---

# netx 拓扑（通用）

## Hosts（MCP + DSH）

| Host | Tools |
|------|--------|
| **MCP** `netx-topology-mcp` | 裸名（`sinkTopologyDualUnits` …）；安装见 [`docs/MCP_TOPOLOGY.md`](../../../docs/MCP_TOPOLOGY.md) |
| **DSH** `dsh-netxops` | `netx__` + 同 stem。整组工具 + skill **`netx-topology`**（能力组 **topology**，默认关）。缺布局引擎能力时用 MCP。 |

Canonical: `netx/skills/topology/netx-topology/`（唯一正文）。

**原则**：
1. **第一步用 dual_units**：从**核心**出发，选**覆盖网元最多**的那一只眼（`prefer_top_eye` + max cover）。
2. **`sinkTopologyDualUnits` / `layout_dual_unit` 整图流程只用一次**——只沉这一只最大眼；**禁止**再连抽下一批 dual_unit。
3. **后续下沉换算法**：`suggestSinkHubs` → `move_nodes(park)` / `projectTopologyNeighbors`；**不要**再 `sinkTopologyDualUnits`。
4. **眼图已定型 → 门控精修**：对该 sink 只用下表「允许」动作；禁止全局拆眼工具。
5. 布眼目标：少交叉、眼心空旷、少重叠（椭圆弧带；长链在眼外）。对照人工金标时还要看：**紧凑度、正交边、贴边清开**（见「算法天花板」）。

**禁止**写临时 py 穷举坐标或直接调 HTTP；验证与压交叉**只调 MCP**（或 DSH 已实现的同名工具）。不造 Fabric 边。  
**脱敏（硬）**：勿把客户网元名、站点/区域名、具体交叉数、具体 view_id 写进本 skill。角色只用通用词：门户 / 枢纽 / 汇聚 / 接入 / 末梢。**禁止**在 skill 正文写站点缩写或设备角色缩写当专名举例。

---

## 完整处理思路（一次大区接入眼）

按阶段执行；每阶段只调 MCP，读返回再决定下一步。

```
① 认眼
   analyze(structure) → dual_units 里取覆盖最大的核心门户对
② 一次 sink
   sinkTopologyDualUnits(max_units=1, prefer_top_eye, layout_batch)
   → 同一 sink 贯穿全程；眼形不对只 layout_dual_unit 重布同一对门户
③ 换算法下沉（禁 dual）
   循环：suggestSinkHubs(pick:1) → move_nodes(park) → until_limit(crossing)
   空批次后：orphan_batch / 对根−sink−门户 分批 park；仍禁 dual
④ 眼定型精修（门控序列，见下）
   until_limit(crossing) → until_limit(total)
   → clear_edge_hits(portal_ids) → pull_far_chains(portal_ids)
   → compact_bbox(portal_ids, outlier_only) → 可选再 clear / total
⑤ 停手（算法天花板，**默认无范本**）
   overlaps=0；until_limit stall；pull/compact/clear 均 moved≈0
   → 交付：眼可读 + ov=0 + 交叉可接受；util/正交/贴边缺口 → 手拖或改初布
   → 勿再发明无门控全局工具硬挤
```

**范本现实（硬）**：
- **日常默认没有范本**；有人工图也几乎不是同一张网 → **禁止**把 `align_reference` / 抄坐标当主路径。
- `align_reference` **仅**用于：同网同成员画布的 A/B 对照、调试、回放（共享 `fabric_node_id`）。跨网 ID 对不上，抄了也无意义。
- 生产要缩短与人工观感差距 → **改初布配方**（正交/紧凑从 dual 一次就偏地铁风），不是现场找金标对齐。

**从零重布（无范本算力路径）**：
- 眼已在 sink、只需甩掉坏坐标 / 金标残留：对本 sink **`layout_dual_unit`（同一门户对）→ 立刻门控精修**。这**不算**再 sink 一批 dual。
- 初布后交叉会先飙高、贴边变差属正常；**不要**因此改走 `polish_crossings` / `fix_overlaps`。
- 纯算力压交叉后 util/bbox 常崩（枢纽被 `until_limit` 甩远）→ **必须**接 `pull_far_chains` → `compact_bbox`；缺这两个 action 就先重启 MCP，勿半截交付。
- 无范本时：交叉可压到可读，但仍常高于「抄同网坐标」路径；util/正交缺口靠 pull + 初布，**不是**再堆 until_limit。

**开局核对 catalog（硬）**：
- 精修前先 `layoutTopologyView(catalog=true)`：须见 `pull_far_chains`、`compact_bbox`，且 `version`/`rev` 为当前包。
- 缺 action 或 `rev` 过旧 → **重启** netx-topology MCP 再干；禁止用旧进程硬跑、禁止改走 HTTP/临时 py。

**精修硬门控（已写进算法，skill 须遵守）**：
- 任何落笔：**全局交叉不得升高**；升高 → 弃该候选。
- **最小化展开**（bundle）：从小压缩步长试探；步长须清开 footprint（成员互叠也算失败）。
- **侵入** = 展开后踏入外人框 / 留下任意 footprint 重叠 → 换 tip，勿硬塞。
- `until_limit` 只接受 `overlaps=0`；apply 遇 `overlaps_remain` → 修算法，**勿**对眼 sink 跑 `fix_overlaps`。
- `portal_ids` **只钉眼门户**；`freeze_layers=["core"]` 会冻住 sink 上所有核心（含旁路枢纽）——慎用。
- 旁路枢纽也要动时：`protect_rigid=off`（仍受 `portal_ids` 约束）。
- `clear_edge_hits`：**禁止**放宽交叉（含 `preserve_axis`）；交叉升高一律拒。
- `pull_far_chains`：相对**门户中点**缩放远走廊/孤立点/远叶（不是朝外圈枢纽）；枢纽本身在外围时朝枢纽缩**收不了 bbox**。
- `compact_bbox`：默认 **farthest-K**（`outlier_only`）；全图/分位均匀缩易叠点 → 拒。
- `until_limit`：`stop_reason=max_moves` **≠** stall → **再调**直到 `stall`（可提高 `max_moves`，但仍以 stall 为准）。
- 大眼 `max_jump` 用 4k–8k；勿收到 &lt;2k。大图会 background → `job_status`（可数分钟）；勿中途改走拆眼工具。

---

## 算法天花板（经验固化）

大区接入眼跑完门控序列后，常见结果：

| 维度 | 算法能做到 | 再挤会怎样 | 交付策略 |
|------|------------|------------|----------|
| 交叉 | 从零 dual 后 `until_limit` 可大幅压下；无范本时仍常高于抄坐标路径 | 再挪极值尖端易抬交叉；`max_moves` 停了要续跑到 stall | **停**于 stall；残余当弦交叉可接受 |
| 重叠 | 硬零 | — | 必须保持 |
| 紧凑 util / bbox | 压交叉常先**炸开** bbox；`pull_far_chains` 多轮收远走廊 | 缺 pull 就半截交付；强缩常 `Δg>0` 拒 | **必跑** pull→compact 到 `moved=0`；其余手拖 |
| 贴边 clearance | `clear` + `objective=total` 能降一批 | 与交叉/紧凑拉扯，清到个位数很难 | stall 后手拖擦边点 |
| 正交 axis | `total` 略抬；远低于人工地铁风 | `preserve_axis` 清边若放宽交叉会毁掉前面成果（已禁 slack） | 手拖走廊；勿为轴牺牲交叉 |
| 直链 / 环穿 | bundle 后仍有折角与穿环 | 全局 straighten 拆眼 | 勿清零；结构优先 |

**判定「算法到头」**（门控精修，无范本）：
1. `overlaps=0`
2. `until_limit`（crossing 与 total）均 stall
3. pull/compact/clear `moved≈0`
4. 目视：眼心空、双门户可辨

**可选对照**（仅评测）：对**同网**人工画布与算法 sink 各 `analyze`，只比 `summary`；**勿**因此去 `align_reference` 当交付手段。

**明确无效 / 禁止再试**（眼 sink）：
- 再次 `sinkTopologyDualUnits` / `until_empty` / 新块画布抽 dual
- `polish_crossings` / `straighten_channels` / `untangle` / `fix_overlaps` / `orbit_sweep(round=true)`
- 临时 py 穷举坐标、无门控的整图缩放/平移极值点
- 为追 util 关掉交叉门控或给 `clear` 开交叉 slack
- **把跨网/他网人工图当范本抄坐标**（含滥用 `align_reference`）

---

## 主路径（必循）

```
analyze(structure) → 读 dual_units（认核心最大眼）
→ 有 level：先打标；巨图勿先全量 level_bands
→ 【仅一次】sinkTopologyDualUnits(
     max_units=1, prefer_top_eye=true, prefer_pure=false,
     sink_layers=[access], keep_layers=[core,agg], layout_batch=true,
     max_nodes/max_batch_nodes 放行大眼
   )
→ 眼形不对：可对本 sink 重跑 layout_dual_unit（仍算同一只眼；勿再 sink 下一批 dual）
→ 【换算法】suggestSinkHubs → move_nodes(park) 分批迁入同一 sink；每批后 until_limit；禁止再 dual
→ 【眼定型】门控精修序列（下节）；禁止全局拆眼
→ 根图可 level_bands（只动根上残留，不动已定型的眼 sink）
```

停手：最大眼可读、眼心空、`overlaps≈0`、门户清晰；其余节点用非 dual 手段靠上去即可。交叉允许来自跨走廊弦边。

**dual 一次用尽**：`until_empty`、再次 `sinkTopologyDualUnits`、为下一批 dual 新建块画布 —— **一律禁止**（日常）。

**布眼几何**（仅那一次）：嵌套半椭圆；中轴只有双门户；长链/尾巴放眼外禁穿环。眼坏了重跑 `layout_dual_unit`，勿全局拆眼。

---

## 眼图定型后的精修（门控序列）

眼 sink 一经验收，**禁止**再对该画布跑会整图拆眼的工具。

| 允许 | 禁止（眼 sink 上） |
|------|-------------------|
| `analyze(detail=hotspots\|both)` 只读 | `polish_crossings` / `straighten_channels` |
| `orbit_sweep`：**`until_limit=true`** 或 `node_id` preview→pick | `orbit_sweep(round=true)` 全图轮扫 |
| `clear_edge_hits`（**须** `portal_ids`；交叉不升） | `fix_overlaps` / `untangle` 批处理整图 |
| `pull_far_chains`（朝**门户中点**收远走廊/孤立点；同上门控） | `layout` / 再次 `sinkTopologyDualUnits` |
| `compact_bbox`（farthest-K；交叉不升、ov=0） | 临时 py 穷举坐标 |
| `updateTopologyViewPositions` 手拖 1～少数点 | `align_reference` 当无同网范本时的交付手段 |
| 眼形整体崩了才 `layout_dual_unit` 重布同一门户对 | |

**推荐节奏**（一次序列，读返回再决定是否重复某一环）：

```
0) catalog=true 核对 pull_far_chains / compact_bbox（缺则重启 MCP）
0b) 从零重布时：layout_dual_unit(同一门户对, apply)  // 交叉飙高正常
1) until_limit + objective=crossing + portal_ids + bundle
   → stop_reason=max_moves 则续跑，直到 stall
2) until_limit + objective=total     // 交叉不升；抬贴边/正交；亦可 max_moves→续跑
3) clear_edge_hits(portal_ids, top_n/max_moves 可加大；勿 preserve_axis 除非确认不抬交叉)
4) pull_far_chains(portal_ids)       // 可 2～3 轮直到 moved=0；压交叉后必做
5) compact_bbox(portal_ids, outlier_only)  // 常 accepted=false，正常
6) 可选：再 clear 或 total；再 stall → 停手
```

`until_limit` 调用模板：

```
layoutTopologyView({
  action: "orbit_sweep", mode: "apply",
  params: {
    until_limit: true,
    objective: "crossing",   // 再一轮改 total
    portal_ids: [<门户A>, <门户B>],
    protect_rigid: "off",
    max_degree: 14,
    max_jump: 8000,          // 大眼用 4k–8k；勿 <2k
    max_stretch: 32,
    max_moves: 40,           // 触顶只说明配额满，不是算法到头
    bundle: true
  }
})
→ 读 local.op：start/end_crossings、moves[]、stop_reason
→ max_moves → 再调；stall → 进入下一步
```

`round=true` ≠ `until_limit`：前者一批 top_n 死拿 pick#1；后者循环到 stall。  
勿为清零交叉拆弧带；以 `verdict.total` + 目视眼形 +「算法到头」四条为准。

---

## 阶段 0 — 画布与成员

1. `getTopologyTree` → **`view_id`**（文件夹 physical 画布）。
2. 建根/子区域只用 `createTopologyFolder`。
3. 缺层级/区域时：`classifyTopologyFabricNodes`（`match` → `tag(level, dry_run)` → `tag`；或 `unmatched` / `apply_rules`）。**level** 越小越靠外（0 外部 / 1 核心 / 2 汇聚 / 3 接入；可用 1.1、2.1 子层）。**勿**切片建图。
4. `analyzeTopologyViewLayout({ view_id, detail: "structure" })` 读 `dual_units` / `shape` / `layers` —— **先认核心最大眼**（`unit_count` / 各 unit `node_count`）。
5. `addTopologyViewNodes`（可按 `role` 大档或后续 level）/ `projectTopologyNeighbors`（区域画布务必 `region_folder_id`）。
6. 多余点用 `removeTopologyViewNodes` **移出画布**（不删 Fabric）。`region:…` 幽灵点勿当网元拖。

---

## 形状与 dual_units（只服务「第一只眼」）

```
analyzeTopologyViewLayout({ view_id, detail: "structure" })
```

| 字段 | 用途 |
|------|------|
| `shape.primary` | `chains` / `star` / `mesh` / `mixed_blocks` |
| `dual_units` | **从核心出发**：core–core → core–agg → …，再取 **node_count 最大**的一只做唯一 sink |
| `advice.block_plan` | 其余块怎么靠（非 dual 连抽） |
| `gravity.type` | 链图勿当 hub 花瓣 |

- **第一步必选最大核心眼**：`prefer_top_eye=true`，`prefer_pure=false`，`max_units=1`；目标是**一块吃进尽量多网元**，不要求交叉=0。
- **禁止**把 dual_units 当成「抽干源图」的循环泵；剩余网元见下方「后续下沉」。
- **禁止**再用互斥 soft_block 把通路切开。
- **链图 / 小图**：可不走 dual；preview `corridor`/`compact`。

---

## 根图 → 子区域：第一次（dual，仅一次）

```
sinkTopologyDualUnits({
  source_view_id: <根图>,
  sink_view_id: <子区域>,   // 同一张接入画布贯穿全程
  max_units: 1,
  max_nodes: 300,
  max_batch_nodes: 400,
  layout_batch: true,
  sink_layers: ["access"],
  keep_layers: ["core", "agg"],
  prefer_top_eye: true,
  prefer_pure: false,
  // 勿 until_empty；勿随后再调本工具
})
→ 眼形验收 / 手拖；必要时 layout_dual_unit 重布同一眼
```

- **同一 sink**：后续非 dual 迁入也进**同一个** `sink_view_id`；禁止块1/块2/块3。
- **眼定型后**：对该 sink **只门控精修**（见上节）。

---

## 后续下沉（换算法，禁用 dual）

源图仍有接入 / 旁路网元时，**不要**再 `sinkTopologyDualUnits`。

0. **先问算法要批次**（推荐）
   ```
   suggestSinkHubs({
     source_view_id: <根图>,
     sink_view_id: <眼 sink>,
     pick: 1,
   })
   → batch.fabric_node_ids / move_nodes 可直接套用
   ```
   排序：剩余领地大优先、agg 优于 core；眼门户不当 batch 头。

   **每批迁完立刻** `until_limit(crossing, portal_ids)`，再取下一批；勿攒多批再一起 polish。

   `suggestSinkHubs` 空了但根上还有孤立末梢/旁路：清掉已在 sink 的重复点，再对 `root−sink−眼门户` 分批 `move_nodes(park)`（每批后 `until_limit`）。也会直接吐 `orphan_batch_*`。勿再 dual。

1. **指定迁移 + 扫角停靠**
   ```
   layoutTopologyView({
     action: "move_nodes",
     source_view_id: <FROM>, view_id: <TO>,
     mode: "apply",
     params: {
       fabric_node_ids: [...],
       park: true,
       remove_from_source: true,
     },
   })
   ```

2. **投影邻居**：`projectTopologyNeighbors`（区域画布带 `region_folder_id`），再手拖或 `orbit_sweep`。

3. **根图收尾**：`level_bands` 只钉**根图**上残留的 core/agg；**勿**对已定型眼 sink 跑全局带。

4. **小连通块**：源上剩余短链/星，可对**源图**或迁入后**单点**处理；勿对眼 sink `layout(recipe=…)`。

---

## 分层布局（特殊场景）

当网络需呈现层级结构（外部→接入→核心→汇聚→客户等）时：

### 分层通用规则
1. **顶层：外部/对接网络**
2. **次顶层：终端客户接入层**
3. **中间层：核心层**
4. **核心下层：汇聚层**
5. **底层：接入层/孤立层**

### 优化流程
1. **先手动后算法**：用户手动拖拽保证业务结构，再用算法优化
2. **分层约束**：`updateTopologyViewPositions` 按层钉 y；`orbit_sweep` 可传 `y_min/y_max`
3. **分步（非眼画布 / 根图）**：可贴边、分层钉 y；**已定型眼 sink 除外**——只允许门控精修 / 手拖

### 冲突处理
| 冲突场景 | 处理方式 |
|---------|---------|
| 交叉 vs 结构 | **结构优先**（最大眼 / 分层） |
| 算法 vs 手动 | **手动优先** |
| polish 拆眼 | **禁止**对 dual 眼走 straighten 主路径 |
| 算法 vs 金标观感 | 交叉可赢；紧凑/正交/贴边交给手拖 |

---

## layoutTopologyView（精简）

| action | 用途 |
|--------|------|
| `layout` | 小图 / 剩余块：`compact` / `corridor` / `rings` / `unstick` |
| `layout_dual_unit` | **仅**重布已沉的那一只眼；椭圆弧带 + 空心眼 |
| `move_nodes` / `sink_nodes` | **后续下沉主路径**；`park` 块扫 |
| `orbit_sweep` | **眼后精修主工具**：`until_limit` 或 `node_id`；`objective=crossing\|total` |
| `clear_edge_hits` | 眼 sink **可**（须 `portal_ids`；交叉不升） |
| `pull_far_chains` | 眼 sink **可**：朝门户中点收远走廊/孤立点 |
| `align_reference` | **非主路径**：仅同网同成员 A/B 调试；跨网禁用 |
| `compact_bbox` | 眼 sink **可**：farthest-K 收 bbox |
| `level_bands` | 仅根图/未定型画布；巨图在 dual 一次之后钉根 |
| `polish_crossings` | **眼 sink 禁用** |
| `fix_overlaps` / `untangle` / `straighten_channels` | **眼 sink 禁用** |
| `job_status` / `job_cancel` | 后台 job |

眼 sink 精修 = 门控序列 + 手拖补观感；不要排全局流水线，也不要在 stall 后继续发明新全局动作。

---

## 验收

| 块 | 看什么 |
|----|--------|
| `overlap` | 硬零（权重 0.24） |
| `crossing` | crossings/cpl；`top_nodes` / `top_edges` |
| `sparsity` / `edge_axis` / `edge_clearance` | 对照人工：紧凑、正交、贴边（算法常弱于此） |
| `rings` / `chains` | 环被穿、走廊折角 |
| `verdict.total` | ≈70 可交付（ov=0）；结构可读优先于刷分 |
| `score_profile` | `auto`（默认）/ `default` / `eye`；大稀疏斜边画布自动走 eye |

**评分修订（相对旧权）**：util 甜区下限 0.08；贴边分=节点命中∪hits/link；rings 降权；eye 再降 axis、抬 util/clearance；`compactness` 仅诊断不加权。

图标 25px；推荐中心距 Δx≥200、Δy≥170。

---

## 工具速查

| 工具 | 作用 |
|------|------|
| `getTopologyTree` / `getTopologyView` | 树与画布 |
| `createTopologyFolder` | 新建根/区域 |
| `add` / `remove` / `updateTopologyViewPositions` | 成员与手拖 |
| `sinkTopologyDualUnits` | **仅一次**：最大核心眼 → sink |
| `suggestSinkHubs` | **后续下沉批次**（含 orphan_batch） |
| `move_nodes`（经 layoutTopologyView） | **后续下沉** |
| `projectTopologyNeighbors` | 投影邻居 |
| `copyTopologyViewNodes` | 克隆沙箱 |
| `queryTopologyFabricNodes` | 库存 |
| neighborhood / edges | 邻接 |
| `layoutTopologyView` | 上表 action |
| `analyzeTopologyViewLayout` | structure + 验收 |

---

## 代码热更

1. 本仓 MCP 用 `PYTHONPATH=…/src`（见用户 `~/.cursor/mcp.json`），改源码后不必为加载而 pip install。
2. **必须重启** stdio 进程（杀旧 `python -m netx_topology_mcp` 后由 Cursor 拉起；或 MCP 面板禁用/启用）。
3. `layoutTopologyView(catalog=true)` 核对：`version` / `rev`（`NETX_MCP_REV`）+ action 含 `pull_far_chains` / `compact_bbox`。
4. 机器上常残留多份旧 MCP 进程 → 精修前 catalog 若仍缺 action，**杀光再启**；勿用旧进程半截交付，勿改走 HTTP/临时 py。

拓扑页开「实时同步」可看落笔。勿用告警/CLI MCP 写拓扑。
