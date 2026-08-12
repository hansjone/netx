# netx Topology MCP — 安装与更新

与告警/CLI 的 [`netx-mcp`](./MCP.md) **分开**的 stdio MCP，只提供 **拓扑树、画布（views）、Fabric 边/邻接**。Agent 可只装本包，或与 `netx` 并存。

```
MCP 宿主  →  stdio  netx_topology_mcp  →  HTTP  NETX_API_URL  →  /v1/topology/*
```

| 组件 | 说明 |
|------|------|
| **netx API** | 需已启动，拓扑数据在服务端 |
| **netx-topology-mcp** | 轻量 HTTP 客户端，与宿主同机 |

---

## 1. 安装

```powershell
cd D:\project\chatgpt\netx
pip install -e ./packages/netx-topology-mcp
python -c "import netx_topology_mcp; print('ok')"
python -m netx_topology_mcp
```

从 GitHub：

```powershell
pip install "git+https://github.com/hansjone/netx.git#subdirectory=packages/netx-topology-mcp"
```

环境变量与 [`MCP.md`](./MCP.md) 相同：`NETX_API_URL`、`NETX_API_TOKEN` / `data/auth/mcp_token`、`NETX_LANG`。

---

## 2. Cursor / oclaw 配置

独立服务器 id：`netx-topology`（不要与 `netx` 混在同一个 command 里）。

```json
{
  "mcpServers": {
    "netx-topology": {
      "command": "python",
      "args": ["-m", "netx_topology_mcp"],
      "env": {
        "NETX_API_URL": "http://127.0.0.1:8890",
        "NETX_API_TOKEN": "nxt_your_key_with_ne_write",
        "NETX_LANG": "zh",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

画图必须设 `NETX_API_TOKEN`（网页新建 Key，默认含 `ne:write`）。未设置时回退读 `data/auth/mcp_token`（只读+CLI，**无写工具**）。改 Key 权限后 Sync Tools / 约 45s 内 scopes 会刷新。

样本：[`packages/netx-topology-mcp/mcp.json`](../packages/netx-topology-mcp/mcp.json)。

与告警 MCP 并存时，把两个 server 都放进 `mcpServers` 即可；未勾选/未安装的不会加载工具。

oclaw：Install from JSON → Health → Sync Tools（应看到 **14** 个工具，含 `layoutTopologyView` / `sinkTopologyDualUnits` / `queryTopologyFabricNodes`）→ 专家绑定勾选 `server_id=netx-topology`。

Cursor：改包后若 Sync 工具数不对，请 **禁用/启用** `netx-topology` 或重载窗口（须重启 stdio 进程）。可选在 env 加 `PYTHONPATH=.../packages/netx-topology-mcp/src` 强制最新源码。

配套 Agent Skill（dual_unit 排水 → 扫角/polish → 手拖）：[`.cursor/skills/netx-topology/SKILL.md`](../.cursor/skills/netx-topology/SKILL.md)。Cursor / oclaw 读 skill 后再调 MCP。

---

## 3. 工具一览

### 目录模型

- 顶级为导航「根」；其下唯一「根图」/ `Root map` 为画布（physical view）。
- 子区域即画布（创建时自动 physical view）。
- 文件夹 `ne_count`：子树去重 Fabric 网元数（来源无关）。

### 读

| 工具 | 作用 |
|------|------|
| `getTopologyTree` | 「根 / 根图 / 子区域」树 + views + `ne_count`（找画布用这个，勿再 listViews） |
| `getTopologyView` | 单图（节点+边+坐标）；`detail=summary\|full` |
| `queryTopologyFabricNodes` | Fabric 库存：`mode=summary\|list\|search`（有 `q` 默认 search；list 支持 `region_folder_id`） |
| `classifyTopologyFabricNodes` | **分级打标**：`match`→`tag(level\|role, dry_run)`→`tag`；`level` 为 major.minor（0 外部…3 接入）；`role` 预设仍可用。再 `addTopologyViewNodes` 上区域画布。**勿用切片建图** |
| `queryTopologyNeighborhood` | 邻域：compact nodes + `links[]`（NE 对，非端口） |
| `queryTopologyEdges` | 默认 **adjacency**：`links[{a,b,link_count}]`；画布一对网元一条线。`detail=ports` 才给端口行 |
| `analyzeTopologyViewLayout` | 布图验收（只读）：`verdict` + score；`score_profile=auto\|default\|eye`（大稀疏斜边→eye）；贴边=节点命中∪hits/link；util 甜区下限 0.08；`detail=structure` / `hotspots\|blocks\|both` |

### 写（只动画布，不污染 Fabric）

| 工具 | 作用 |
|------|------|
| `createTopologyFolder` | **唯一建图入口**：顶级→「根」+「根图」；根图下→**子区域**；返回 `view_id` |
| `addTopologyViewNodes` | **优先**传 `keyword`/`role`/`vendor`/`link_status` + `limit`/`offset`，由 API 筛选落点；也可 `fabric_node_ids`。拒绝 managed/UME。返回摘要。 |
| `removeTopologyViewNodes` | 筛选或 id 从画布移除（不删 Fabric），摘要 |
| `sinkTopologyDualUnits` | 根图 dual_units 分批沉入子区域；默认 `layout_batch`→`layout_dual_unit`→块扫挂载；日常禁 `until_empty` |
| `copyTopologyViewNodes` | 一键克隆画布成员+坐标到另一画布（`clear_target` 可选）；源画布不动，测沙箱用 |
| `layoutTopologyView` `move_nodes` | 指定 `fabric_node_ids` 从 `source_view_id`→`view_id`（默认同移出源）；对调两 view 回迁；别名 `sink_nodes`；`park=true` 扫角停靠 |
| `updateTopologyViewPositions` | **优先** `layout=grid\|offset\|stack` + 筛选，API 自己挪点；`positions[]` 仅少量微调 |
| `layoutTopologyView` | **布图/局部修**：公开 `action=layout\|layout_dual_unit\|orbit_sweep\|clear_edge_hits\|pull_far_chains\|compact_bbox\|polish_crossings\|fix_overlaps\|untangle\|straighten_channels\|level_bands\|move_nodes`；recipe 仅 `compact\|corridor\|rings\|unstick`。眼 sink 主路径：`until_limit(crossing→total)` → `clear_edge_hits` → `pull_far_chains` → `compact_bbox` → 手拖；禁对眼跑 polish/fix_overlaps/untangle/round。stall 且 moved≈0 即算法到头。巨图 apply 可能返回 `job_id` → 轮询 `job_status` / `job_cancel`。Job=**子进程+`data/runtime/layout_jobs` 落盘**。`mode=preview\|apply` |
| `projectTopologyNeighbors` | 投影**已有** LLDP 邻居到画布；区域画布务必传 `region_folder_id`，读 `out_of_region_skipped` |

**推荐流水线：** `getTopologyTree` →（可选）`createTopologyFolder` 取 `view_id` → `addTopologyViewNodes` →（可选）邻居投影 / 布局。

**硬顶 2000：目标 ≤2000 画一张图即可**；physical 根图/区域默认 `max_nodes=2000`，custom 仍按角色软顶。Agent 默认 `links[]` 邻接；布图按邻接与 Skill 中的经验摆点。

**刻意不提供：** 手工建链、`populate`、删 Fabric / 删整图。

写操作需要 token 具备 `ne:write`；只读为 `ne:read`。

**权限怎么开：** 网页 **系统 → API Key**（`/api-keys`）。新建默认已含 `ne:write`（可关掉）；已有 Key 在 **操作 → 改权限**。把明文配到 `NETX_API_TOKEN` 后重启 MCP / Sync Tools。仓库自动生成的 `data/auth/mcp_token` 仍是只读+CLI（无写），需要画图请另建 Key 或改权限。

**前端能否看着画：** 在拓扑页左侧树或右侧浏览区点 **「实时同步」**（默认关闭；**不需要先打开某张图**）。开启后树约每 5 秒、已打开的图约每 3 秒拉取，可看到 MCP 新建区域/画布并往上加点。有未保存本地拖动时不会覆盖你的编辑。

---

## 4. 与 netx-mcp 的关系

| 包 | server_id | 职责 |
|----|-----------|------|
| `netx-mcp` | `netx` | 告警、UME、托管网元 CLI（**13** 工具） |
| `netx-topology-mcp` | `netx-topology` | 拓扑画布 / Fabric 只读 + 分类打标 + 安全画图（**15** 工具） |

`queryTopologyEdges` 已从 `netx-mcp` **迁出**到本包，避免重复。

---

## 5. 更新

```powershell
cd <netx 仓库>
git pull
pip install -e ./packages/netx-topology-mcp
```

然后重启 MCP 宿主，并 Sync Tools。
