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
        "NETX_LANG": "zh",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

样本：[`packages/netx-topology-mcp/mcp.json`](../packages/netx-topology-mcp/mcp.json)。

与告警 MCP 并存时，把两个 server 都放进 `mcpServers` 即可；未勾选/未安装的不会加载工具。

oclaw：Install from JSON → Health → Sync Tools（应看到 **13** 个工具）→ 专家绑定勾选 `server_id=netx-topology`。

配套 Agent Skill（画图流水线 / 安全约束）：[`.cursor/skills/netx-topology/SKILL.md`](../.cursor/skills/netx-topology/SKILL.md)。Cursor / oclaw 读 skill 后再调 MCP。

---

## 3. 工具一览

### 读

| 工具 | 作用 |
|------|------|
| `getTopologyTree` | 站点/区域文件夹树 + 下属画布 |
| `listTopologyViews` / `getTopologyView` | 画布列表 / 单图（节点+边+坐标） |
| `getTopologyFabricSummary` | Fabric 汇总 |
| `listTopologyFabricNodes` / `searchTopologyFabricNodes` | 网元搜索 |
| `queryTopologyNeighborhood` | 指定节点邻接 |
| `queryTopologyEdges` | LLDP/手工链路（含 `peer_count`） |

### 写（只动画布，不污染 Fabric）

| 工具 | 作用 |
|------|------|
| `createTopologyView` | 在 folder 下新建画布 |
| `addTopologyViewNodes` | **优先**传 `keyword`/`role`/`vendor`/`link_status` + `limit`/`offset`，由 API 筛选落点；也可 `fabric_node_ids`。拒绝 managed/UME。返回摘要。 |
| `removeTopologyViewNodes` | 筛选或 id 从画布移除（不删 Fabric），摘要 |
| `updateTopologyViewPositions` | **优先** `layout=grid\|offset\|stack` + 筛选，API 自己挪点；`positions[]` 仅少量微调 |
| `projectTopologyNeighbors` | 投影**已有** LLDP 邻居到画布 |

**单画布硬顶 2000**；库存更大时多画布 + `offset` 翻页加满。前端对可见节点做 `onlyRenderVisibleElements`。

**刻意不提供：** 手工建链、`populate`、删 Fabric / 删整图。

写操作需要 token 具备 `ne:write`；只读为 `ne:read`。

**权限怎么开：** 网页 **系统 → API Key**（`/api-keys`）。新建默认已含 `ne:write`（可关掉）；已有 Key 在 **操作 → 改权限**。把明文配到 `NETX_API_TOKEN` 后重启 MCP / Sync Tools。仓库自动生成的 `data/auth/mcp_token` 仍是只读+CLI（无写），需要画图请另建 Key 或改权限。

**前端能否看着画：** 在拓扑页左侧树或右侧浏览区点 **「实时同步」**（默认关闭；**不需要先打开某张图**）。开启后树约每 5 秒、已打开的图约每 3 秒拉取，可看到 MCP 新建区域/画布并往上加点。有未保存本地拖动时不会覆盖你的编辑。

---

## 4. 与 netx-mcp 的关系

| 包 | server_id | 职责 |
|----|-----------|------|
| `netx-mcp` | `netx` | 告警、UME、托管网元 CLI（**13** 工具） |
| `netx-topology-mcp` | `netx-topology` | 拓扑画布 / Fabric 只读 + 安全画图（**13** 工具） |

`queryTopologyEdges` 已从 `netx-mcp` **迁出**到本包，避免重复。

---

## 5. 更新

```powershell
cd <netx 仓库>
git pull
pip install -e ./packages/netx-topology-mcp
```

然后重启 MCP 宿主，并 Sync Tools。
