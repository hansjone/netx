# netx 远程 MCP（Streamable HTTP）

netx API 在同一端口（默认 `8890`）提供 `/mcp` 端点，使用 MCP **Streamable HTTP** 传输。客户端（Claude Code、Codex、Cursor、DSH netxops）只需配置 URL 与 Bearer token，**无需安装 Python MCP 包**。

## 架构

```
MCP 宿主  →  HTTP  http://127.0.0.1:8890/mcp  →  netx_api.mcp_remote（进程内工具）
```

工具分组（查询参数 `groups`）：

| groups | 工具数 | 说明 |
|--------|--------|------|
| `ops` | 15+1 | 告警 / 网元 / 只读 CLI；另有 meta 工具 `getNetxSkill` |
| `topology` | 16+1 | 拓扑树 / 画布 / Fabric / 布局；另有 `getNetxSkill` |
| `bizMonitor` | 8+1 | 割接与业务状态只读；另有 `getNetxSkill` |
| （省略） | 40 | 39 个能力工具 + `getNetxSkill`（有可见 skill 时） |

示例：`/mcp?groups=ops,bizMonitor`

## 鉴权

- Header：`Authorization: Bearer <token>`
- 接受 MCP 令牌（`NETX_AUTH_MCP_TOKEN_FILE`，bootstrap 写入）或用户 JWT
- 无 token / 无效 → HTTP **401**
- `tools/list` 只列出当前 token 有 scope 的工具；`tools/call` 再按 RBAC 校验

## 客户端配置

见根目录 [`mcp.all.json`](../mcp.all.json)。Claude Code 示例：

```bash
claude mcp add --transport http netx http://127.0.0.1:8890/mcp \
  --header "Authorization: Bearer <token>"
```

仅 ops：

```text
http://127.0.0.1:8890/mcp?groups=ops
```

## 语言

- 环境变量 `NETX_LANG=zh|en`，或请求头 `Accept-Language`
- 工具 `description` / `inputSchema` 与历史 Python 包一致；`en` 时 API 查询附加 `lang=en`

## 安全

- `execManagedNe` 等写工具带 `annotations.readOnlyHint=false`
- CLI 允许范围与 REST/Web 一致：按设备 `exec_policy`（`ne_exec_guard`）校验；`readonly` 仅 show/display/ping 等，`linux_shell`/`unrestricted` 允许 Linux/RouterOS 原生命令
- 异步 exec / layout job：先返回 job id，再用轮询工具查询

## 版本

`initialize.serverInfo.version` 使用 netx 包版本（`pyproject.toml` / `netx-ops`）。

无状态模式下不主动推送 `notifications/tools/list_changed`；客户端应在需要时重新 `tools/list`。

## Skills（运维手册资源）

运维 skill 真源在仓库 `skills/`（`ops` / `topology` / `biz-monitor`；不含开发者用的 `biz-state`）。通过 `/mcp` 与工具同一鉴权、同一 `?groups=` 过滤发布：

| 通道 | 说明 |
|------|------|
| `resources/list` · `resources/read` | URI：`netx://skills/<组目录>/<skill名>/<相对路径>`，如 `netx://skills/ops/netx-ops/SKILL.md` |
| 工具 `getNetxSkill` | 参数 `name`（必填）、`file`（可选，默认 `SKILL.md`）；`_meta.netx.group=meta` |
| `prompts/list` · `prompts/get` | 每个可见 skill 一个 prompt（名=skill 名）；Claude Code 显示为 `/mcp__netx__<skill名>` |
| `initialize.instructions` | 在原有说明后追加当前可见 skill 目录，并提示先调 `getNetxSkill` |

可见规则：该组下当前 token 至少有一个可见工具，且遵守 `?groups=`。改 skill 文件后无需重启 netx（每次请求读盘，description 按文件修改时间缓存）。

skill 原文不随 token 改写。当 token 只有该组部分工具的权限时：`getNetxSkill` 和 prompts 会在末尾附上"当前 token 无权使用以下工具"清单；`resources/list` 里 SKILL.md 条目的 `_meta.netx.unavailableTools` 也给出同一份清单（DSH 插件据此在 skill 末尾追加"不可用工具"提示）。

### Cursor / Claude Code 怎么读

1. **Cursor**：在 MCP 资源面板打开 `netx://skills/...`，或让 Agent 调用 `getNetxSkill`。
2. **Claude Code**：`/mcp__netx__netx-ops` 等 prompt，或对 URI 做 resources/read；也可直接 `getNetxSkill`。
3. 仅挂某一组时加查询参数，例如 `http://127.0.0.1:8890/mcp?groups=ops`。

## 相关文档

- [MCP_TOPOLOGY.md](./MCP_TOPOLOGY.md) — topology 组工具说明
- [MCP_BIZMONITOR.md](./MCP_BIZMONITOR.md) — bizMonitor 组工具说明
- [`skills/README.md`](../skills/README.md) — skill 真源说明
