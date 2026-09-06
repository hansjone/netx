# netx-ops 速查

## 两套「一批」

- **多台一批**：一次调用打多台（`nms_ne_ids`/`targets`）
- **同台多条**：一台上一次 `commands[]` 多条（含多个 `… ?` 分裂）
- 禁止：同轮多台各调一次 `execManagedNe`

## 新鲜度

- `runNmsDiagnostics` / `aggregateNmsAlarms` → `meta.last_seen_*`
- 快照：时间窗 ∈ min~max；勿默认「现在−30 分钟」

## 能否登录

1. `listCliTargets` / `listManagedNe` → 命中则实测 `show version` 或 `display version`
2. 未命中 → inventory 说明有无；未进 CLI 通道则本通道不能登

## 短路径

| 意图 | 调用 |
|------|------|
| Critical Top | `aggregateNmsAlarms(severity=critical, top_ne=20)` |
| 态势 | `runNmsDiagnostics` / `aggregateNmsAlarms` |
| 断纤 / LOS | Raw `keyword=LOS` / `Fiber Break` |
| 光功率门限 | Raw `keyword=optical power` + 前缀（≠ 断纤） |
| 离线 | Raw `keyword=BN EMS` |
| 单机告警 | 限定 `host_name` |
| 证据 | `queryNmsAlarmsRaw(field_preset=evidence)` |
| 清单 | `queryNmsNeInventory` / `getNmsNe` |
| 多台 CLI | `execManagedNe(nms_ne_ids=…)` 或 `targets` |
| 路径 | `findTopologyPaths` |
| A<>B 光 | 两端 host → 路径 → 多台一批 optic CLI |

## 展示

- 只展示 **host_name**；严禁 UUID
- 无 `host_name`：丢弃
- DSH：前缀 `netx__`

## 厂商与 `?`

- 华为 `display`；其他多 `show`；方法同一套 `?`
- **同厂也可能命令不一**：一台失败 ≠ 全网作废；失败台另探，成功台继续原命令；**已成功过的命令优先复用**
- `^` 前正确 → 加 `?`（可接词尾）
- **同台分裂**：一台 `commands[]` 多条 `… ?`；**不是**多台同探
- ZTE 光：`opticalinfo brief` → `optical brief` → `show optical?`
