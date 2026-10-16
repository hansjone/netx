# netx-bizmonitor-mcp

独立 Python stdio MCP，只读访问 netx 的割接对比与业务状态 HTTP API。运行依赖仅 `httpx`，不依赖 `netx_api`、数据库驱动或 netxops。采用独立包与现有 topology 包、一组一 skill 的组织方式一致，客户端可按组挂载最小权限，避免扩大 ops 工具域及引入重依赖。

## 配置与启动

安装本包后使用 `python -m netx_bizmonitor_mcp`，或命令 `netx-bizmonitor-mcp`。无需安装时，将本包 `src` 的绝对路径设置为 `PYTHONPATH`。`mcp.json` 是客户端配置示例，需改为实际 Python/src 路径与令牌。

使用凭据文件时，应删除样例的 NETX_API_TOKEN 占位值或设为空；同时为客户端设定正确工作目录，或用 NETX_MCP_TOKEN_FILE 指定绝对路径。

- `NETX_API_URL`：默认 `http://127.0.0.1:8890`，支持 HTTP(S) 和路径前缀；禁止内嵌用户名/密码、查询参数、片段。
- `NETX_API_TOKEN`：最高优先级 Bearer API 令牌；为空时依次读 `NETX_MCP_TOKEN_FILE` 指定的 UTF-8 文件、当前工作目录的 `data/auth/mcp_token`。文件为空、缺失、不可读或编码错误时继续下一候选。后端要求 **`biz-monitor:read`**，`ne:read` 或 `ops:write` 不代替此 scope。无可用令牌时返回 `netx_token_missing`，不发送请求。
- `NETX_LANG`：默认 `zh`；`en` 时附加 `lang=en`。
- Windows 建议 `PYTHONIOENCODING=utf-8`、`PYTHONUTF8=1`；程序也会将 stdio 配为 UTF-8。

令牌读取遵循现有独立 MCP 的凭据文件约定，仅在内存使用，文件内容与路径不回显；响应脱敏覆盖文件令牌，包括请求中途文件轮换前实际使用的令牌。所有业务请求仅 GET；不会触发采集、评估、CLI 执行或设备写入。通过 `GET /v1/auth/me` 探测权限，每次工具调用重新读取，避免缓存权限撤销；探测失败时仍列出工具，由目标 GET 的后端权限检查最终裁决。

## 工具与 HTTP 路由

工具名与 netxops bizMonitor 组相同，去掉 `netx__` 前缀，仅提供以下 8 个工具：

1. `listBizMonitors` → `GET /v1/biz-migration/projects` 与/或 `GET /v1/biz-state/tasks`。
   `kind=all|projects|tasks`，`purpose` 只转发任务查询；`status/q` 本地过滤，`limit` 默认 100、上限 500，各列表分别限额。项目压缩为名称、状态、任务 ID、设备摘要、模板名称、高频参数与更新时间；任务保留后端目录字段。counts 区分匹配总数与返回数量。
2. `listBizMonitorBatches` → `GET /v1/biz-migration/projects/{project_id}/batches`、`GET /v1/biz-state/tasks/{task_id}/batches`、`GET /v1/biz-migration/batches/{batch_id}/runs`。
   `kind=auto|all|cutover|collect|runs`；auto 根据项目/任务/批次 ID 选择。`all` 需要 project_id 和 task_id；`runs` 需要割接 batch_id。批次 limit 默认 50、上限 200；runs_limit 默认 10、上限 50。include_runs 最多展开前 5 个已筛选割接批次；单个展开失败保留批次并附 runs_error，直接 runs 查询失败则返回工具错误。只保留批次摘要与运行 anomaly/progress，不回传大 summary；counts 为本次返回数量。
3. `getBizMonitorContext` → `GET /v1/biz-migration/monitor-context`。
   project_id/task_id 至少一个；传同名查询参数。保留后端定义包的模板、映射、任务和命令。
4. `getBizMonitorBoard` → `GET /v1/biz-migration/batches/{batch_id}/board`。
   必填割接 batch_id，可选 run_id；保留看板统计与证据。
5. `listBizMonitorReds` → `GET /v1/biz-migration/projects/{project_id}/red-tickets`。
   必填 project_id，可选 status、limit（1..500）。未传 limit 时沿用后端默认 200；保留原始 A/B 键及设备、命令、时间证据。
6. `getBizMonitorDiffs` → `GET /v1/biz-migration/runs/{run_id}/diffs`。
   必填 run_id，可选 metric_id/sheet_id/verdict/color/q/limit/offset。**MCP `q` 映射 HTTP `kw`**，修正 TS 将 q 原样传递导致搜索失效的问题。limit 限 1..500，未传时后端默认 100；offset 下限 0。原始 A/B 和 match_* 键保持不变。
7. `getBizCollectBatch` → `GET /v1/biz-state/batches/{batch_id}`。
   必填采集 batch_id。后端返回工作簿摘要、命令及 sheet 目录，不含原始 CLI 全文和全量指标行。本包保持后端响应，避免误称其会返回全部指标数据。
8. `getBizCollectCommandRaw` → `GET /v1/biz-state/batches/{batch_id}/commands/{command_id}`。
   两个 ID 必填；返回完整 raw_text 和采集证据，按 netxops 不做字符/行数截断。大原文只在明确调用此工具时获取。

路径 ID 均作为单个段编码。数字参数按 netxops 截为整数并限幅；未知字段/类型错误返回可修正的工具错误，不接受过时单数 kind 或旧工具别名。

## 推荐调用链

- 无 ID 时先 `listBizMonitors({"q":"站点名"})`，取得项目 ID。
- `getBizMonitorContext({"project_id":"..."})` 理解模板与 old/new 任务。
- `listBizMonitorBatches({"project_id":"...","include_runs":true})` 找割接批次及运行；也可 `kind=runs,batch_id=...` 单独查运行。
- `getBizMonitorBoard({"batch_id":"...","run_id":"..."})` 看结论，再以 `listBizMonitorReds({"project_id":"..."})` 与 `getBizMonitorDiffs({"run_id":"...","color":"red","limit":100,"offset":0})` 查看证据和分页差异。
- 需要原文时，用上下文中的任务 ID 调 `listBizMonitorBatches({"task_id":"..."})` 找采集批次，`getBizCollectBatch` 找 command_id，再 `getBizCollectCommandRaw` 获取指定命令全文。割接批次 ID 与采集批次 ID 不可混用。

所有返回使用 `{ok,data}` 文本 JSON。参数或上游业务/HTTP 错误使用 MCP `result.isError=true`；JSON-RPC 的解析、请求结构、方法和 tools/call 结构错误分别为 -32700/-32600/-32601/-32602。通知永不回包且不触发业务请求；stdout 只输出协议消息。

凭据字段递归删除，配置令牌、Bearer 与常见 `password=...`/`secret:...` 文本脱敏。HTTP 错误体、异常文本、URL 不回显，避免凭据泄露；业务 evidence、raw A/B、show 命令、时间字段保持。原始设备输出可能含任意格式的敏感业务信息，部署方仍应控制授权采集内容。

## 验证

在 worktree 根目录运行（不安装依赖，不启动项目服务，不连接真实设备）：

```powershell
$env:PYTHONPATH = (Resolve-Path packages/netx-bizmonitor-mcp/src).Path
& D:/project/chatgpt/netx/.venv/Scripts/python.exe -m pytest packages/netx-bizmonitor-mcp/tests -q
```

测试使用 HTTP mock；stdio 子进程清空令牌环境变量并在无令牌文件的临时目录运行，因此不发送网络请求。覆盖 8 工具、目录/批次裁剪与展开、q→kw、scope、业务与协议错误、通知、凭据来源优先级、文件错误回退、令牌轮换与脱敏及长原文不截断。
