# netx skills（唯一真源）

**一组一个 skill。** 只在这里改正文。

| Group | Skill bundle | 工具（MCP 裸名 / DSH = `netx__`+stem） |
|-------|--------------|----------------------------------------|
| `ops` | `ops/netx-ops/` | NMS 告警/库存/SQL + managed CLI + `findTopologyPaths` |
| `topology` | `topology/netx-topology/` | 画布 / Fabric / dual_unit / 布图 |

## DSH 怎么用

1. 运行时优先：`NETX_SKILLS_ROOT` → 旁路 `../netx/skills` → 包内 `presets/netxops/skills`
2. 发 npm 前：`powershell -File netxops/scripts/sync-skills-from-netx.ps1`
3. Settings → 能力组开关：开哪组就注册哪组的 **tools + 对应 skill**
4. 其它预设可强制挂：`dsh-netxops/tools-ops|topology`（旧别名 `tools-nms` / `tools-common` → ops）

MCP / Cursor：skill 根直接指本目录。MCP 包内不镜像 skills。
