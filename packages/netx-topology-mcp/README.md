# netx-topology-mcp

独立的 **stdio MCP**，只暴露 netx **拓扑画布 / Fabric** 能力，与告警/CLI 的 [`netx-mcp`](../netx-mcp) 分开安装，方便 Agent 按需启用。

```
MCP 宿主  →  stdio  netx_topology_mcp  →  HTTP  NETX_API_URL  →  netx API /v1/topology/*
```

详细说明 → **[docs/MCP_TOPOLOGY.md](../../docs/MCP_TOPOLOGY.md)**

配套 Skill → **[`skills/topology/netx-topology`](../../skills/topology/netx-topology/SKILL.md)**（画图流水线与硬规则）

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

## 工具（16）

覆盖 netxops `topology` 清单全部 15 项，另提供 `sinkTopologyDualUnits`。netxops 工具名前缀为 `netx__`，独立 MCP 不带此前缀。

| 类别 | 工具 |
|------|------|
| 树/区域/画布 | `getTopologyTree`, `createTopologyFolder`（唯一建图，返回 view_id）, `getTopologyView` |
| 画图 | `addTopologyViewNodes` / `remove…` / `sinkTopologyDualUnits`（分批沉入+默认可先 layout_dual_unit） / `copyTopologyViewNodes`（一键克隆测沙箱） / `update…Positions`（**优先筛选**，API 自选 id；也可 id 列表）, `projectTopologyNeighbors` |
| Fabric 只读 | `queryTopologyFabricNodes`（summary\|list\|search）, `queryTopologyNeighborhood`, `queryTopologyEdges` |
| Fabric 分类 | `classifyTopologyFabricNodes`（match/tag/patch/unmatched/preview_rules/apply_rules/list_rules） |
| 自动布图 | `layoutTopologyView`（小图 `compact`/`corridor`/`rings`；巨图：`sinkTopologyDualUnits`→`orbit_sweep`→`polish_crossings`→`clear_edge_hits`） |
| 布图验收/规划 | `analyzeTopologyViewLayout`（score；`detail=structure` 定重心；`both`=structure+手拖 sight） |

**安全约束：** MCP **不会**创建 Fabric 占位节点、**不会**写手工链路；画布只能引用已存在的 fabric 节点。

画布节点增删和位置布局只接受 `keyword/role/vendor/link_status` 筛选。按 `region_folder_id/level/level_major` 画图时，先用 `queryTopologyFabricNodes(mode=list)` 分页查询，再将返回的 `id` 作为 `fabric_node_ids` 传入画布工具。`projectTopologyNeighbors(dry_run=true)` 可预览邻居投影而不保存。

## 自动布局与异步任务

1. `layoutTopologyView({view_id, action:"layout", recipe:"compact", mode:"preview"})` 预览；`mode:"apply"` 才保存坐标。
2. 需要后台计算时，调用 `layoutTopologyView({view_id, action:"layout", recipe:"compact", mode:"apply", params:{background:true}})`，记录返回的 `job_id`。布局/局部优化计算支持显式后台；`move_nodes` 和 `align_reference` 按各自流程同步执行。
3. 调用 `layoutTopologyView({action:"job_status", params:{job_id}})`，直到 `status` 为 `done/error/cancelled`。`running/cancelling` 仍需轮询；`stale=true` 仅表示心跳过期，不能视为完成。成功必须检查 `ok=true`、`status=done`、`result.applied=true`。
4. `getTopologyView({view_id, detail:"full"})` 回读 HTTP API 持久化后的坐标。`job_status` 的结果会裁剪大字段，不替代完整画布回读。
5. 需要取消时调用 `layoutTopologyView({action:"job_cancel", params:{job_id}})`；取消为协作式，仍需轮询终态。已经写入的坐标不会因取消自动回滚。

部分大图优化会自动转后台：`polish_crossings/orbit_sweep/clear_edge_hits/compact_bbox/pull_far_chains/layout_dual_unit` 在节点数达到 600 时；`orbit_sweep(until_limit=true)` 的 apply 总是后台。`params.sync=true` 可强制同步。

任务 JSON 默认位于仓库 `data/runtime/layout_jobs`；用绝对路径 `NETX_LAYOUT_JOB_DIR` 可指定共享目录。后台子进程和任务文件能跨 MCP 主进程重启，轮询方必须访问同一目录；这不是 HTTP 后端任务接口，也不支持跨机器自动共享。历史回收仅清理终态，保留运行中的任务。

HTTP 200 内的 `ok=false` 会作为 MCP `isError=true` 返回。分批写入遇错即停；复制/移动/下沉若保存目标坐标失败，会保留源成员并报告已增加的目标成员。最终回读失败返回 `view_readback_failed` 和 `write_completed=true`，此时应先检查画布再重试。多次 HTTP 写入不构成事务，已完成的部分不会自动撤销。

只使用工具 schema 中公开的动作。旧动作别名 `resolve_overlaps/sink_nodes/align_to_reference`、查询模式 `stats/count/find`、分类动作 `find/bulk_tag/assign/preview/apply/rules`、详情别名 `raw/graph/full`（边查询的 full）已移除；规范动作分别见 `layoutTopologyView(catalog=true)` 与各工具 schema。
