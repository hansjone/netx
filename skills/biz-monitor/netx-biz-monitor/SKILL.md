---
name: netx-biz-monitor
description: >-
  Overnight cutover / biz_state monitor analysis via netxops host tools
  (netx__listBizMonitors / listBizMonitorBatches / getBizMonitor*). List
  projects/tasks then batches when no id is given; read definitions, board,
  reds/diffs with evidence (device-raw A/B, show commands), and judge tool
  false-positive vs real business fault. Does not create templates/tasks.
---

# netx-biz-monitor（割接 / 业务监控只读分析）

人配好监控与任务；AI 当晚做归因。  
工具在 **netxops**（`netx__*`），直连 netx REST，**不是** netx-mcp。

## 工具（能力组 `bizMonitor`）

| 工具 | 用途 |
|------|------|
| `netx__listBizMonitors` | **入口目录**：列出割接/监控对比项目 + 业务监控任务（**无需** project_id） |
| `netx__listBizMonitorBatches` | **批次目录**：`project_id`→割接批次；`task_id`→采集批次；`kind=runs`→evaluate run |
| `netx__getBizMonitorContext` | 任务定义：项目、监控/对比模板、归一化规则、端口映射、关联任务与命令项 |
| `netx__getBizMonitorBoard` | 批次进度、evaluate run、sheet 卡、verdict 统计 |
| `netx__listBizMonitorReds` | 红单 + **evidence** |
| `netx__getBizMonitorDiffs` | run 明细（可滤 color=red） |
| `netx__getBizCollectBatch` | 某次采集：commands / metrics |
| `netx__getBizCollectCommandRaw` | 单命令全量 show 输出 |

交叉验证真障时再用 ops：`netx__execManagedNe` / 拓扑路径。

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
5. `listBizMonitorReds` 或 `getBizMonitorDiffs(color=red)`  
6. 看 evidence：parse 失败？current_missing？映射 miss？归一化误伤？  
7. 需要原文 → `getBizCollectCommandRaw`  
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
