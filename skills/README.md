# netx skills（唯一真源）

**一组一个 skill。** 只在这里改正文。

| Group | Skill bundle | 工具（MCP 裸名 / DSH = `netx__`+stem） |
|-------|--------------|----------------------------------------|
| `ops` | `ops/netx-ops/` | NMS 告警/库存/SQL + managed CLI + `findTopologyPaths` |
| `topology` | `topology/netx-topology/` | 画布 / Fabric / dual_unit / 布图 |
| `bizMonitor` | `biz-monitor/netx-biz-monitor/` | 割接/业务监控只读分析（远程 `/mcp?groups=bizMonitor` 或 netxops） |
| `biz-state` | `biz-state/netx-biz-state/` | 状态监控：TextFSM / parser / Profile（Cursor Agent；无 DSH 工具） |

## DSH 怎么用

1. 运行时优先：`NETX_SKILLS_ROOT` → 旁路 `../netx/skills` → 包内 `presets/netxops/skills`
2. 发 npm 前：`powershell -File netxops/scripts/sync-skills-from-netx.ps1`
3. Settings → 能力组开关：开哪组就注册哪组的 **tools + 对应 skill**（含 **bizMonitor**）
4. 其它预设可强制挂：`dsh-netxops/tools-ops|topology|biz-monitor`

MCP / Cursor：连 netx API 的 `/mcp`（可用 `?groups=` 过滤）。配置见根 `mcp.all.json`。工具在服务端进程内执行，不另起 Python MCP 包。

**Cursor Agent**：加 biz_state 监控命令时启用或点名 **netx-biz-state**（`skills/biz-state/netx-biz-state/`）。
