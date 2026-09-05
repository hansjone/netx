---
name: netx-ops
description: >-
  Netx Ops playbook (MCP + DSH): NMS alarms/inventory/SQL plus managed-NE CLI
  login (execManagedNe batch-first) and findTopologyPaths. Trigger: alarms,
  host_name, Critical Top, LOS, 能否登录, show/display, optical, capacity A<>B,
  path between sites, netx ops.
---

# netx-ops（告警 + 纳管登录）

**一组一个 skill。** 问「能否登录 / CLI / show」必须走本 skill 的 managed 工具，不要只查 NMS inventory。

## Hosts

| Host | Tools |
|------|--------|
| **MCP** `netx-mcp` | 裸名 `queryNmsAlarms` / `execManagedNe` / … |
| **DSH** `dsh-netxops` | `netx__` + 同 stem；能力组 **ops**（默认开） |

REST NMS 仍 `/v1/ume/*`（`nmsProvider=zte-ume`）。优先 `nms_ne_id`；`ume_*` 别名可用。  
画布 / dual_unit → **netx-topology**（能力组 topology）。

## Tools

### NMS

| Purpose | Tool |
|---------|------|
| Alarm list / aggregate / diagnostics | `queryNmsAlarms` / `aggregateNmsAlarms` / `runNmsDiagnostics` |
| Inventory / detail | `queryNmsNeInventory` / `getNmsNe` |
| Raw / fields / SQL | `queryNmsAlarmsRaw` / `listNmsAlarmFields` / `aggregateNmsAlarmsRaw` / `sqlQueryNms` |

### Managed CLI + paths

| Purpose | Tool |
|---------|------|
| List CLI targets | `listManagedNe` / `listCliTargets` |
| Managed detail | `getManagedNe`（**仅**纳管 id） |
| Login / show | `execManagedNe`（batch-first） |
| Fabric paths | `findTopologyPaths` |

## 「能否登录」决策树（强制）

1. `listCliTargets(keyword=host_or_ip)` 或 `listManagedNe(keyword=…, connect_status=pass)`  
   - 命中 → `execManagedNe(ne_id|nms_ne_id, commands=["show version"])` **验证真正能登**  
   - 未命中 → `queryNmsNeInventory(keyword=…)` 说明 NMS 有无 / `connection_status`，并明确：**未纳管 netx CLI 则不能用本通道登录**
2. 禁止只查 inventory 就下「不能登录」或「能登录」结论而不尝试 `execManagedNe`（已纳管时）。
3. NMS UUID 不要塞进 `getManagedNe`；用 `execManagedNe(nms_ne_id=…)` 或先 list 拿 managed `ne_id`。

## NMS 工具顺序

1. Freshness: `runNmsDiagnostics` / `aggregateNmsAlarms` → `meta.last_seen_*`
2. Overview + one-page `queryNmsAlarms`
3. Evidence: `queryNmsAlarmsRaw` (`field_preset=evidence`)
4. Paths / CLI as needed (same skill)

## CLI order

1. `listManagedNe` / `listCliTargets`（每会话最多一次，缓存 id）
2. 多台 → **一次** `execManagedNe`（`ne_ids` / `nms_ne_ids` / `targets`）
3. 路径 → `findTopologyPaths`

### Batch 示例

```json
{ "nms_ne_ids": ["uuid-a", "uuid-b"], "commands": ["show version"], "concurrency": 4 }
```

```json
{
  "targets": [
    {"nms_ne_id": "uuid-zte", "commands": ["show opticalinfo brief"]},
    {"nms_ne_id": "uuid-hw", "commands": ["display optical-module brief"]}
  ]
}
```

## Short recipes

| User says | Recipe |
|-----------|--------|
| 能否登录 / login / SSH | 上表「能否登录」决策树 |
| fiber / LOS | Raw `keyword=LOS` / `Fiber Break` |
| Critical Top | `aggregateNmsAlarms(severity=critical, top_ne=20)` |
| capacity A<>B | paths / LLDP → 两端 optic CLI（一批） |

## Guardrails

- 展示 **host_name**；勿对用户甩裸 UUID
- CLI 白名单：`show` / `display` / `ping` / `traceroute` …
- 同轮禁止 N× 单台 `execManagedNe`

See [reference.md](reference.md).
