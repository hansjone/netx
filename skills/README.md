# netx skills（唯一真源）

**一组一个 skill。** 只在这里改正文。

| Group | Skill bundle | 工具（MCP 裸名 / DSH = `netx__`+stem） |
|-------|--------------|----------------------------------------|
| `ops` | `ops/netx-ops/` | NMS 告警/库存/SQL + managed CLI + `findTopologyPaths` |
| `topology` | `topology/netx-topology/` | 画布 / Fabric / dual_unit / 布图 |
| `bizMonitor` | `biz-monitor/netx-biz-monitor/` | 割接/业务监控只读分析（独立 `netx-bizmonitor-mcp` 或 netxops 宿主工具） |
| `biz-state` | `biz-state/netx-biz-state/` | 状态监控：TextFSM / parser / Profile（Cursor Agent；无 DSH 工具） |

## DSH 怎么用

1. 运行时优先：`NETX_SKILLS_ROOT` → 旁路 `../netx/skills` → 包内 `presets/netxops/skills`
2. 发 npm 前：`powershell -File netxops/scripts/sync-skills-from-netx.ps1`
3. Settings → 能力组开关：开哪组就注册哪组的 **tools + 对应 skill**（含 **bizMonitor**）
4. 其它预设可强制挂：`dsh-netxops/tools-ops|topology|biz-monitor`

MCP / Cursor：skill 根直接指本目录，包内不镜像 skills。不装 netxops 时，分别使用 `netx-mcp`（15 工具）、`netx-topology-mcp`（16 工具）和 `netx-bizmonitor-mcp`（8 工具）；配置见根 `mcp.all.json`。独立包均通过 HTTP 访问 netx，不直连数据库。

**Cursor Agent**：加 biz_state 监控命令时启用或点名 **netx-biz-state**（`skills/biz-state/netx-biz-state/`）。
