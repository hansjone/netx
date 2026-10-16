---
name: netx-biz-monitor
description: >-
  通过独立 netx-bizmonitor-mcp 或 netxops 分析割接与业务状态。
  无 ID 先查目录和批次，结合看板、变红项、双侧原始 A/B 键和采集命令原文判断故障。
  只读已有项目与评价结果，不创建模板、触发采集、执行评价或关闭红票。
---

# netx-biz-monitor（割接 / 业务监控只读分析）

人配好监控与任务；AI 当晚做归因。  
独立 **netx-bizmonitor-mcp** 提供下表裸名；netxops 使用 `netx__` 加同名。两者均访问 netx REST，需 `biz-monitor:read`。安装和三组配置见 [MCP_BIZMONITOR.md](../../../docs/MCP_BIZMONITOR.md)。

## 工具（能力组 `bizMonitor`）

| 工具 | 用途 |
|------|------|
| `listBizMonitors` | **入口目录**：列出割接/监控对比项目 + 业务监控任务（**无需** project_id） |
| `listBizMonitorBatches` | **批次目录**：`project_id`→割接批次；`task_id`→采集批次；`batch_id,kind=runs`→evaluate run |
| `getBizMonitorContext` | 任务定义：项目、监控/对比模板、归一化规则、端口映射、关联任务与命令项 |
| `getBizMonitorBoard` | 批次进度、evaluate run、sheet 卡、verdict 统计 |
| `listBizMonitorReds` | 红单 + **evidence** |
| `getBizMonitorDiffs` | run 明细，`color=red`；`offset/limit` 分页；`q` 为关键词 |
| `getBizCollectBatch` | 采集工作簿摘要、commands 和 sheet 目录；不含全部指标行或原文 |
| `getBizCollectCommandRaw` | 以采集 batch_id + command_id 获取单命令原文，按需调用，可能很大 |

交叉验证真障时再用 ops 的 `execManagedNe` / 拓扑路径。独立 MCP 仅允许单行 `show` / `display`，不执行配置或 shell 脚本。

## 字段约定（必守）

- **`old_key` / `new_key`**：设备采集原文 **A/B**（大屏与结论用这个）
- **`match_*` / evidence.iface.normalized|map_***：内部配对，**不要**把映射后的 BB 说成两侧端口
- **evidence.*.command.raw_command**：实际 show 命令
- **evidence.*.device / collect**：哪台设备、何时采的

## 推荐顺序

1. 无 id → `listBizMonitors`（`kind=projects|tasks|all`，可加 `q` / `purpose`）  
2. `listBizMonitorBatches(project_id)` 或 `(task_id)` — 拿到 **完整 batch_id**；需要 run 时再 `kind=runs` / `include_runs`  
3. `getBizMonitorContext(project_id)` — 弄清盯什么  
4. `getBizMonitorBoard(batch_id)` — 进度 / missing / skipped  
5. `listBizMonitorReds` 或 `getBizMonitorDiffs(run_id,color=red,limit=100,offset=0)`；按 total 翻页，不能只看第一页就宣称全部红项。
6. 看 evidence：parse 失败？current_missing？映射 miss？归一化误伤？  
7. 从差异 evidence 的 collect/command 取两侧**采集** batch_id/command_id → `getBizCollectCommandRaw`；若证据不全，用对应 task_id 查采集批次，再 getBizCollectBatch 查命令。不要把割接 batch_id 当成采集 batch_id。
8. 像真障 → `execManagedNe` 现场核对  

## Verdict 速查（skill 解释，工具不硬编码）

| verdict | 含义（窗口内） |
|---------|----------------|
| migrated | expect 项已迁到新侧 |
| anomaly | 异常（多为红） |
| unfinished | expect 未完成（验收常红） |
| migrating / expected / ok | 过程态 / 正常 |
| lost / not_involved | 丢失或无关 |

## 误报优先查

1. `parse_status` 非 ok / 行数为 0  
2. board：`current_missing` / `collect_skipped`  
3. 端口映射漏配、归一化前后不一致导致假 anomaly  
4. 再谈真实业务问题  

配置整改与关红单由人做；本 skill **只读分析**。

目录或批次为空时，明确说明没有可评价的数据；不把空红票当作割接通过。run 尚未完成或存在 current_missing/采集失败时，不给通过结论。工具本身不启动 evaluate；需要人先在 Web 完成定义、采集和评价。`old_key/new_key` 是设备原始端口；例：归一化和映射将 GE-old 匹配到 xgei-new，结论仍分别展示 GE-old 与 xgei-new，不能用内部匹配键替代。
