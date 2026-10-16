# netx 远程 MCP（Streamable HTTP）

netx API 在同一端口（默认 `8890`）提供 `/mcp` 端点，使用 MCP **Streamable HTTP** 传输。客户端（Claude Code、Codex、Cursor、DSH netxops）只需配置 URL 与 Bearer token，**无需安装 Python MCP 包**。

## 架构

```
MCP 宿主  →  HTTP  http://127.0.0.1:8890/mcp  →  netx_api.mcp_remote（进程内工具）
```

工具分组（查询参数 `groups`）：

| groups | 工具数 | 说明 |
|--------|--------|------|
| `ops` | 15 | 告警 / 网元 / 只读 CLI |
| `topology` | 16 | 拓扑树 / 画布 / Fabric / 布局 |
| `bizMonitor` | 8 | 割接与业务状态只读 |
| （省略） | 39 | 当前 token 有权限的全部工具 |

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
- CLI 硬门（`cli_guard`）：仅单行 `show`/`display`，拒绝 shell 与破坏性命令
- 异步 exec / layout job：先返回 job id，再用轮询工具查询

## 版本

`initialize.serverInfo.version` 使用 netx 包版本（`pyproject.toml` / `netx-ops`）。

无状态模式下不主动推送 `notifications/tools/list_changed`；客户端应在需要时重新 `tools/list`。

## 相关文档

- [MCP_TOPOLOGY.md](./MCP_TOPOLOGY.md) — topology 组工具说明
- [MCP_BIZMONITOR.md](./MCP_BIZMONITOR.md) — bizMonitor 组工具说明
