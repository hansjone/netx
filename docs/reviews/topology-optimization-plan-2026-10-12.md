# 拓扑管理问题分析与优化计划

日期：2026-10-12。范围：拓扑管理画布（`web/src/features/topology/*`）及其视图接口（`netx_api/topology_views_graph.py`、`topology_router.py`、`topology_fabric_links.py`）。不含：UME World 平铺大图 LOD、LLDP 采集策略、分类规则、MCP 布局算法。
用途：交给 Cursor 按任务逐项实施；Claude 负责验收。

本文结论来自代码阅读和本地脚本验证，没有在真实 PostgreSQL 或真实设备上执行。标了 **[已验证]** 的问题已经用脚本复现。

**基线（修改前）：**
- `pytest tests/test_topology.py tests/test_ume_topology_apply.py`：**69 项通过，4 项失败**（见 T13，修改前就失败，与本计划无关）
- `web`：`npx tsc -b` 通过
- 拓扑前端目前没有任何单元测试

**实施进度（2026-10-12）：**
| 任务 | 状态 |
|---|---|
| T11 基线失败测试 | ✅ 完成 |
| T1 保存保留 lock | ✅ 完成 |
| T2 不固化设备名为 label | ✅ 完成 |
| T6 原子 commit + useTopologySave | ✅ 完成 |
| T3 脏状态添加确认 | ✅ 完成 |
| T4 手工边端口持久化 | ✅ 完成 |
| T5 边样式走保存模型 | ✅ 完成 |
| T7 N+1 / 截断确定性 | ✅ 完成 |
| T8 positions 跳过缺失节点 | ✅ 完成（MCP 不依赖 positions 顺便加点） |
| T9 点击不标脏 | ✅ 完成 |
| T10 拆分 TopologyPage | ✅ 完成（`TopologyPage.tsx` ≤1200；抽出 palette/edge/keyboard/find/export/nodeOps/moduleLinks + `TopologyPageView`） |

回归：`pytest` 87 passed；`npx tsc -b` 通过；`topologySaveDiff.test.mjs` 4 passed。

---

## 0. 结论摘要

整体架构没有大问题（服务端图 + 本地编辑 + 一次保存）。主要问题集中在**“本地编辑状态”和“服务端状态”的同步边界**，有几处会静默丢数据或误删数据：

1. **UI 保存会清掉 MCP 设置的节点锁定，并把设备名固化成标签。** **[已验证]** 保存后 `locked` 从 True 变为 False，节点被移走；之后设备改名，画布上仍显示旧名称。
2. **有未保存修改时“添加网元/投影邻居”会静默丢弃这些修改，并留下隐形删除。** 被删除的连线会重新出现在画布上，但仍留在待删除队列里，下次保存时会被真正删掉。
3. **修改已有连线的端口不会被保存。** 界面上可以改，也会显示新值，但不会标记为未保存，保存时也不会提交，刷新后就恢复原值。
4. **连线样式绕过了“编辑→保存”模型。** 样式修改会立即写库，但撤销只回退本地。撤销之后，画布显示和数据库就不一致了。
5. **保存不是原子的。** 一次保存会依次调用 N 次建边、1 次删边、1 次加点、1 次删点和 1 次写坐标，其中 3 次会返回完整的图数据。中途失败会留下“部分已保存”的状态。另外，同一套保存逻辑在 `purgePlaceholderById` 里重复实现了一遍。
6. **后端有 N+1 查询和不确定的截断。** 写坐标时每个点各查一次；外部邻居查询的 `limit(5000)` 把视图内部的边也算进去了，边密集的视图可能找不到外部邻居；节点超过上限时截断没有排序。
7. **`TopologyPage.tsx` 有 2509 行。** 保存、面板添加、连线编辑、快捷键等逻辑都堆在这一个组件里，所以上面这些问题很难被发现，也很难测试。

---

## 1. 现状：画布编辑模型（给 Cursor 的背景）

```
GET /views/{id} ──> graphQuery.data ──(graphToFlow)──> nodes / edges (本地 React state)
                                                          │ 拖动/改名/连线/删除
                                                          ▼
                     dirtyRef + historyRef(撤销栈, UNDO_MAX=40)
                     pendingEdgeCreatesRef / pendingEdgeDeletesRef
                                                          │ Ctrl+S / 保存
                                                          ▼
  saveMut (TopologyPage.tsx:516)
    1. 每条待建边 POST /fabric/edges          (逐条串行)
    2. POST /fabric/edges/delete               (待删边)
    3. POST /views/{id}/nodes                  (服务端没有的本地点；返回整图)
    4. POST /views/{id}/nodes/remove           (本地没有的服务端点；返回整图)
    5. PATCH /views/{id}/positions             (全部点的 x/y/label；返回整图)
```

旁路（不走保存，直接写库）：
- 添加网元/拖入面板 `addPaletteItems` (`TopologyPage.tsx:621`)、投影外部邻居 `addOutsidePeers` (`:1047`)：写库后**用服务端图整体覆盖本地并 `clearDirty()`**
- 连线样式 `patchSelectedEdgeStyle` (`:765`)：立即 `PATCH /edge-style`
- 删除占位网元 `purgePlaceholderById` (`:1238`)：先把保存逻辑复制执行一遍，再清理

脏状态下的轮询 (`useTopologyCanvas.ts:306-318`)：只合并在线状态、层级等叠加字段，不动几何信息，这部分是对的。

---

## 2. 问题清单与任务

优先级：**P0** = 静默丢数据/误删，**P1** = 可靠性/性能，**P2** = 体验/可维护性。

### T1 [P0] UI 保存会清除节点锁定 **[已验证]**

- `flowToPositions` (`web/src/features/topology/lib/graphFlow.ts:90`) 不发送 `locked` 字段。
- `ViewNodeIn.locked` 的默认值是 `False` (`netx_api/topology_schemas.py:277`)。
- `patch_view_positions` 只在“行已锁定且请求也带 locked=True”时才跳过 (`topology_views_graph.py:738`)，否则会覆盖坐标并写回 `locked=False`。
- MCP 会用 `locked=true` 固定布局（`packages/netx-topology-mcp/.../http_tools.py:1985`）。用户在 UI 里随便拖一下再保存，这个锁就没了。

复现：MCP 把节点锁定在 (500,500)，UI 保存 (10,10)，结果节点变成 `locked=False x=10`。

**修改：**
1. 后端：把 `ViewNodeIn.locked` 改为 `bool | None = None`，`None` 表示保持原值。对已锁定的行，只有请求里明确带 `locked=False` 才能移动（解锁）；否则跳过并计入 `skipped_locked`。MCP 显式传 `locked` 的行为保持不变。
2. 前端：在 `NeNodeData` 里加 `locked`（由 `graphToFlow` 从 `n.locked` 填入）。锁定的节点设 `draggable: false`，并显示一个锁形角标；`flowToPositions` 不再发送 `locked`。
3. 保存结果里如果 `skipped_locked > 0`，用 toast 提示。

**验收：**
- 新增 `tests/test_topology.py::test_positions_preserve_lock_when_omitted`：锁定后用不带 locked 的 positions 请求，坐标不变，`locked` 仍为 True。
- 新增 `test_positions_explicit_unlock_moves`：`locked=False` 可以移动并解锁。
- 现有 MCP 相关测试全部通过。

### T2 [P0] 保存会把设备名固化成节点标签 **[已验证]**

- 接口返回的 `label` 已经做过回退：`vn.label` 为空时用 `fn.name / ip` (`topology_views_graph.py:195-197`)。
- 前端把它放进 `data.label`，保存时 `flowToPositions` 又原样写回 `vn.label` (`graphFlow.ts:100`)。
- 所以保存过一次之后，所有节点的 `vn.label` 都是当时的设备名。以后设备改名（例如 UME/网管同步），画布不会跟着变。

复现：保存后把 fabric 节点改名为 `NEW-NAME`，画布仍显示 `OLD-NAME`。

**修改：**
1. 后端 `ViewNodeOut` 增加 `label_override: str`（原始 `vn.label`）；`label` 仍是回退后的显示值，保持兼容。`ViewNodeIn.label` 改为 `str | None = None`，`None` 表示不修改。
2. 前端 `NeNodeData` 增加 `label_override`，只有用户“重命名节点”（`renameSelectedNode`，`TopologyPage.tsx:820`）时才写入。`flowToPositions` 只对 `label_override` 有变化的节点发送 `label`；重命名为空字符串表示清除覆盖，恢复跟随设备名。
3. 数据修复脚本 `scripts/topology_clear_frozen_labels.py`，默认 dry-run：`vn.label` 等于当前 `fn.name` 或 `fn.ip` 的行，把 label 置空。输出受影响的行数，加 `--apply` 才真正写库。

**验收：**
- 新增 `test_positions_without_label_keeps_following_name`：保存不带 label，改名后画布 label 是新名称。
- 新增 `test_label_override_survives_rename`：显式覆盖后改名，仍显示覆盖值。
- 脚本 dry-run 在测试库上输出正确的计数，`--apply` 之后再跑一次计数为 0。

### T3 [P0] 有未保存修改时“添加网元 / 投影邻居”会丢弃修改并留下隐形删除

- `addPaletteItems` (`TopologyPage.tsx:681-686`) 和 `addOutsidePeers` (`:1096-1101`) 都是直接写库，然后 `applyViewGraph(refreshed)` 再 `clearDirty()`：
  - 本地未保存的拖动、改名、删点**直接丢失**，也没有提示；
  - `pendingEdgeDeletesRef` **没有清空**。被删除的边按服务端数据重新画回画布，但它的 id 还在待删队列里。用户以后再改任何东西并保存时，这条**看得见的边会被删掉**；
  - 撤销栈也没有清空，撤销会回到和服务端不一致的快照。
- 同类问题：`useTopologyCanvas.ts:320-355` 在非脏状态下整体刷新时，也不会清理撤销栈。

**修改（推荐“先保存再添加”，简单而且语义清楚）：**
1. 抽出 `commitCanvas()`（见 T6）。`addPaletteItems` / `addOutsidePeers` 开始时如果 `dirtyRef.current` 为 true，就弹窗“当前有未保存修改，保存后继续添加？”：确认则先 `commitCanvas()` 再添加，取消则中止。
2. 新增一个统一的辅助函数 `resetLocalFromServer(graph)`，用于所有“用服务端图整体覆盖本地”的地方：同时清空两个 pending 集合、撤销栈和重做栈，然后 `clearDirty()`。目前 `purgePlaceholderById` (`:1289-1297`) 是手写的这套逻辑，其他地方都漏了。

**验收（手工，Cursor 需要在 PR 描述里写明步骤和结果）：**
1. 删除一条已有边 → 不保存 → 从面板拖入一个网元 → 应弹出确认框；选择保存后，该边被删除，新网元出现。
2. 同上，但选择取消 → 不添加网元，删除操作仍处于未保存状态。
3. 删除边 → 投影邻居（确认保存）→ 刷新页面，边确实被删除，没有“复活后又消失”。
4. 用 grep 检查：所有 `applyViewGraph(` 调用点要么使用 `localPositions` 合并，要么经过 `resetLocalFromServer`。

### T4 [P0] 修改已有连线的端口不会被保存

- 右键菜单对任意单条边都显示端口输入框 (`TopologyCtxMenu.tsx:400-429`)。
- `patchSelectedEdgeStyle` 修改本地端口后，只把样式字段发给 `PATCH /edge-style`（`ViewEdgeStylePatch` 本来就没有端口字段，`topology_schemas.py:450`），也没有 `markDirty()`。
- 保存只处理 `pendingEdgeCreatesRef` 里的新边，已有边的端口修改永远不会提交。

**修改：**
1. 只有 `source === "manual"` 的边可以编辑端口；LLDP/UME 边的端口改为只读（这两类以采集结果为准，手工修改会在下次发现时被覆盖）。
2. 未保存的新手工边：端口修改继续只改本地，保存时随建边一起提交（现有逻辑已支持）。
3. 已保存的手工边：新增 `PATCH /v1/topology/fabric/edges/{id}`，body `{a_port, b_port}`，只允许 `source=manual`，否则 400 `edge_not_manual`；端口要做归一化，与已有边冲突（违反唯一约束）时返回 409。前端把修改记入 `pendingEdgePortEditsRef: Map<id,{a,b}>` 并 `markDirty()`，在 `commitCanvas()` 里提交。

**验收：**
- 新增 `test_patch_manual_edge_ports`、`test_patch_non_manual_edge_ports_rejected`、`test_patch_manual_edge_ports_conflict_409`。
- 手工测试：修改已保存手工边的端口 → 出现未保存标记 → 保存 → 刷新后仍是新端口；LLDP 边的端口输入框为只读。

### T5 [P1] 连线样式与撤销/保存模型不一致

- `patchSelectedEdgeStyle` (`TopologyPage.tsx:807-815`) 每次修改都立即发请求（颜色选择器拖动时会连续发很多次）。同时调用了 `pushHistory()`，但撤销只回退本地状态，所以撤销后画布和数据库不一致。

**修改：** 样式也改为走保存：记入 `pendingEdgeStyleRef: Map<edgeId, style>`，`markDirty()`，在 `commitCanvas()` 里批量提交（后端 `PATCH /edge-style` 支持 `items: []` 批量，旧的单条 body 保留兼容）。撤销快照要包含这个 Map。

**验收：** 改颜色 → 撤销 → 保存 → 刷新，颜色是修改前的值；改颜色 → 保存 → 刷新，颜色是修改后的值；拖动颜色选择器期间，网络面板里没有 `/edge-style` 请求。

### T6 [P1] 保存原子化，并去掉重复的保存逻辑

现状见第 1 节。中途失败时，前面已执行的步骤（例如已建的边、已加的点）不会回滚，`pending*` 集合也只清了一部分。建边是幂等 upsert（`topology_fabric_links.py:65`），重试不会建出重复边，但删点和写坐标的顺序问题依然存在。

**修改：**
1. 后端新增 `POST /v1/topology/views/{id}/commit`，在一个事务里依次处理：
   ```json
   {
     "edge_creates": [{"client_id":"local-..","a_node_id":"","b_node_id":"","a_port":"","b_port":""}],
     "edge_deletes": ["edge-id"],
     "edge_port_edits": [{"id":"","a_port":"","b_port":""}],
     "edge_styles": [{"fabric_edge_id":"","stroke_color":"","stroke_width":0,"line_style":""}],
     "node_adds": ["fabric-id"],
     "node_removes": ["fabric-id"],
     "positions": [{"fabric_node_id":"","x":0,"y":0,"label":null}]
   }
   ```
   任何一步失败都整体回滚，返回 4xx 和 `{"failed_step": "...", "detail": "..."}`。成功时只返回**一次**完整图数据，外加 `edge_id_map: {client_id: server_id}`。节点上限和冻结规则与 `add_nodes_to_view` 一致。
2. 前端新增 `hooks/useTopologySave.ts`，导出 `commitCanvas()`：从本地状态计算差异（纯函数 `lib/saveDiff.ts`，可以单独测试），调用 `/commit`，成功后调用 `resetLocalFromServer`。`saveMut`、`purgePlaceholderById` (`:1248-1281`)、T3 的“先保存”都改用它。
3. 旧的分步接口保留（MCP 在用）。

**验收：**
- 新增 `test_view_commit_atomic_rollback`：构造一个 `edge_creates` 自环（第一步就失败），断言 `node_adds` 和 `positions` 都没有落库。
- 新增 `test_view_commit_happy_path`：一次提交包含建边、删边、加点、删点、写坐标，结果图正确，`edge_id_map` 正确。
- `web/tests/topologySaveDiff.test.mjs`：覆盖“本地新增/删除节点”“region 节点不进入增删列表”“只发送有变化的 label”“删除后又撤销的边不出现在 edge_deletes 中”等情况。
- 手工测试：一次保存只产生 **1 个** 写请求（网络面板截图附在 PR 描述里）。

### T7 [P1] 后端视图接口的 N+1 查询与不确定截断

| 位置 | 问题 | 修改 |
|---|---|---|
| `topology_views_graph.py:719` | 写坐标时每个点各执行一次 `db.get(TopoFabricNode)`，最多 2000 次 | 先用一次 `IN` 查询拿到存在的 id 集合 |
| `:160-166` | 节点超过 `VIEW_GRAPH_NODE_HARD_CAP` 时截断没有 `ORDER BY`，每次显示哪些节点不确定 | `order_by(TopoViewNode.created_at, TopoViewNode.id)` |
| `:354-363` | 外部邻居的 `limit(5000)` 把**两端都在视图内**的边也算进去了，边密集的视图会漏掉外部邻居 | SQL 里排除两端都在视图内的边（`~and_(a.in_, b.in_)`），再加 `limit` |
| `:375` | 每个外部邻居执行一次 `db.get` | 收集 id 后用一次 `IN` 查询 |
| `:178-179` | region 节点每个执行一次 `db.get(TopoFolder)` | 一次 `IN` 查询 |

**验收：**
- 新增 `test_outside_peers_found_when_many_internal_edges`：视图内有 >5000 条内部边（测试中可以把上限改成参数，例如用 10 条模拟），外部邻居仍然能返回。
- 新增 `test_view_truncation_is_deterministic`：两次 GET 返回同一批节点。
- 用 SQLAlchemy `before_cursor_execute` 统计 2000 个点的 positions 请求，语句数 ≤ 10（写成测试）。

### T8 [P1] positions 接口可以绕过节点上限加点

`patch_view_positions` 遇到视图中没有的 `fabric_node_id` 会直接插入 `TopoViewNode`（`:720-733`），不检查 `max_nodes` 和冻结状态，而 `add_nodes_to_view` 会检查（`:778`）。

**修改：** 先确认 MCP 是否依赖“positions 顺便加点”（用 grep 查 `packages/netx-topology-mcp` 里的 `positions` 调用）。如果不依赖，就改为跳过不存在的节点，并在返回里计入 `skipped_missing`；如果依赖，就加上与 `add_nodes_to_view` 相同的上限和冻结检查。**这一项的结论需要写进 PR 描述。**

**验收：** 新增 `test_positions_respects_max_nodes`。

### T9 [P2] 点击节点就会产生“未保存”和无效撤销记录

`onNodeDragStart` 会无条件 `pushHistory()`；`onNodesChange` 收到任何 `position` 变更都会 `markDirty()`，包括没有实际移动的点击 (`TopologyReactFlowView.tsx:442-449`)。后果：单击一个节点就出现“未保存”，并且触发脏锁，live sync 不再整体刷新。

**修改：** 在 `onNodeDragStart` 记录起点坐标；`onNodeDragStop` 时只有位移超过 1px 才把起始快照压入撤销栈并 `markDirty()`。`onNodesChange` 不再对 `position` 变更调用 `markDirty`（`remove`/`add` 保持不变）。

**验收：** 单击节点后不出现未保存标记，撤销按钮保持禁用；拖动 10px 后出现未保存标记，并且可以撤销。

### T10 [P2] 拆分 `TopologyPage.tsx`（在 T1–T6 完成后进行）

目标是把主组件压到 ≤ 1200 行，**只搬代码、不改行为**：
- `hooks/useTopologySave.ts`（T6）
- `hooks/useTopologyPalette.ts`：`addPaletteItems`、`addOutsidePeers`、拖放、`neQuery`、`umeQuery`
- `hooks/useTopologyEdgeEditing.ts`：`patchSelectedEdgeStyle`、`queueDeleteEdges`、`removeSelected`、`onConnect`、`isValidConnection`
- `hooks/useTopologyKeyboard.ts`：全局快捷键（`:1130-1182`）。依赖改为通过 ref 读取，避免每次 `nodes`/`edges` 变化都重新绑定监听
- `lib/moduleLinks.ts`：`openNeInventory`、`openWebcrtFor`、`openAlarmsFor`、`openPortTrafficForEdge`

**验收：** `tsc -b` 通过；第 4 节的手工回归清单全部通过；`git diff --stat` 显示 TopologyPage.tsx 净减少 ≥ 1000 行。

### T11 [P2] 修复修改前就存在的失败测试

| 测试 | 原因 | 处理 |
|---|---|---|
| `test_ume_topology_apply.py::test_lldp_upgrades_bare_ume_ports_and_keeps_ume_primary` | **测试本身不稳定**：`rich_a/rich_b` 取自 `edge.a_port/b_port`（按节点 id 排序后的 A/B），但调用 upsert 时传的是 `a.id/b.id`（按 UME 名称取的节点）。uuid 排序不同时端口就配反了，结果约有一半概率失败。已用调试脚本确认这不是产品 bug。 | 改为传 `a_node_id=edge.a_node_id, b_node_id=edge.b_node_id`（`:358-365`）。`:297`、`:393` 有同样的写法，一起检查 |
| `test_topology.py::test_lldp_discover_writes_fabric_edge`、`test_lldp_unmatched_creates_ssh_placeholder_ne` | 发现任务一直是 `pending`，没有变成 `done`（`:1285`、`:1458`） | 排查后台 worker 在 SQLite 内存库下是否被启动或被跳过，修好测试的启动方式，或者改为同步执行 job |
| `test_topology.py::test_fabric_reconcile_scheduler_once_sweeps_orphans` | `0 not >= 1`（`:2287`） | 排查，可能和上一条同源 |

**验收：** 两个测试文件 **0 失败**，并且连续跑 5 次都通过（用于检验不稳定性）。

### 产品决定（2026-10-12 已确认）

- **Q1**：`isValidConnection` (`TopologyPage.tsx:568`) 不允许在同一对网元之间画第二条手工连线，但 LLDP/UME 支持并行链路，画布也支持聚合显示。是否应该允许手工建并行链路（端口不同即可）？
- **Q2**：T3 选择了“先保存再添加”。如果希望“添加后保留本地未保存修改”，实现会复杂很多（需要把服务端新增部分合并进本地），请确认。

- **结论**：Q1 不允许手工画并行链路，保持 `isValidConnection`（现位于 `hooks/useTopologyEdgeEditing.ts`）现状；Q2 沿用“先保存再添加”。

---

## 3. 建议实施顺序

1. **T11**（先把基线修到全部通过，后面每一步的回归才有意义）
2. **T1 + T2**（后端 schema 改动，两项一起做，再加数据修复脚本）
3. **T6**（commit 接口 + `useTopologySave`），然后在它之上做 **T3、T4、T5**
4. **T7、T8**
5. **T9**，最后做 **T10**（纯重构，单独一个 PR）

每一步单独提交；T10 必须单独成 PR。

## 4. 回归命令与手工清单

```bash
python -m pytest tests/test_topology.py tests/test_ume_topology_apply.py -q
```

```bash
cd web && npx tsc -b
```

```bash
cd web && node --experimental-strip-types --test tests/topologySaveDiff.test.mjs
```

> `lib/saveDiff.ts` 只放纯函数，除 `import type` 外不要有运行时 import（Node strip-types 不会解析不带扩展名的相对路径）。写法参考现有的 `web/tests/cutoverDataCache.test.mjs`。

手工回归（每个 PR 都要过一遍，结果写进 PR 描述）：
1. 拖动节点 → 保存 → 刷新，位置保持。
2. 新建手工连线（带端口）→ 保存 → 刷新，连线和端口都在。
3. 删除连线 → 撤销 → 保存，连线仍然存在。
4. 删除节点 → 保存，节点从视图中移除，但 fabric 中仍存在（可以通过搜索找到）。
5. 重命名节点 → 保存 → 刷新，显示新名称；再把名称清空 → 保存，恢复为设备名。
6. 打开 live sync，另一个窗口用 MCP 移动节点，3 秒内本窗口同步；本窗口有未保存修改时不会被覆盖，只刷新在线状态。
7. 只读用户（没有 `ne:write`）：不能拖动、连线、保存，右键菜单里没有写操作。
8. 删除占位网元（有未保存修改时），修改先被保存，占位网元被清理。
9. 导出 SVG/XML 正常。

---

## 5. 验收记录（2026-10-12）

Cursor 完成 T1–T11 后由 Claude 验收并接手修复，结论：主体实现正确，另修 6 个问题（均补了回归测试）：

1. `/commit` 同次 `node_adds` 的节点坐标被忽略（`autoflush=False` 下重查看不到新行）→ 先 `flush`；`_apply_positions` 的逐点 `db.get` 改为批量。
2. T8 收紧后，无放置行的旧子区域图标拖动无法保存 → `add_missing_region_placement`（仅限本画布的手工子区域）。
3. MCP `updateTopologyViewPositions` 总是发送 `label:""`、`locked:false`，会清掉 UI 重命名并解锁 → 未提供则不发送。
4. 已改样式/端口的边被删除后保存 404 → 前端 `saveDiff` 与后端都跳过已删边；新建本地边的样式经 `edge_id_map` 映射。
5. 框选拖动、方向键移动不标记未保存 → 补 `onSelectionDrag*` 和键盘移动判定。
6. （改动前已存在）大视图打开空白、远处节点定位失效：xyflow `fitView` 等待节点测量，而 `onlyRenderVisibleElements` 下视口外节点永不测量 → `lib/fitNodes.ts` 按已知几何计算 `fitBounds`。

开发库数据修复：`topology_clear_frozen_labels.py --apply` 清理 516 条固化标签（执行前已备份原值）。

回归：拓扑后端 91 通过；Web Node 测试 30 通过；`tsc -b` 通过；MCP 包 238 通过 / 5 失败（与改动前同一批，既有问题）。
