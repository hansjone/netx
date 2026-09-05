---
name: netx-nms
description: >-
  netx NMS playbook (MCP + DSH): vendor NMS adapter (zte-ume) for alarm
  query/aggregate/diagnostics, NE inventory, raw fields, and read-only SQL.
  Trigger: NMS alarms, host_name, Critical Top, LOS, BN EMS, netx ops.
---

# netx-nms (vendor NMS adapter)

## Naming (MCP + DSH)

Canonical playbooks live in **`netx/skills/`** (this file is mirrored into MCP packages / dsh-netxops).
Edit the netx repo copy; run sync scripts — do not maintain divergent forks.

| Host | How tools appear |
|------|------------------|
| **MCP** (`netx-mcp`) | Bare names: `queryNmsAlarms`, … |
| **DSH** (`dsh-netxops`) | Prefixed: `netx__queryNmsAlarms`, … (same camelCase stem) |

REST still `/v1/ume/*` for provider `zte-ume`.
Prefer `nms_ne_id` / `nms_ne_ids`; legacy `ume_*` aliases still work.

Path lookup → **common** skill `netx-common` (`findTopologyPaths`).
Canvas → **netx-topology**（含 dual_unit / 布图；DSH 可把部分布局工具放在实验 tool group）。

## Tool names

| Purpose | Tool |
|---------|------|
| Alarm list | `queryNmsAlarms` |
| Alarm aggregate | `aggregateNmsAlarms` |
| Diagnostics | `runNmsDiagnostics` |
| NE inventory | `queryNmsNeInventory` |
| NE detail | `getNmsNe` |
| Field list | `listNmsAlarmFields` |
| Raw rows | `queryNmsAlarmsRaw` |
| Dynamic aggregate | `aggregateNmsAlarmsRaw` |
| SQL | `sqlQueryNms` |
| Managed CLI (other skill) | `listManagedNe` / `getManagedNe` / `execManagedNe` / `listCliTargets` |

## Tool order

1. **Freshness first**: `runNmsDiagnostics` or `aggregateNmsAlarms` → `meta.last_seen_min` / `last_seen_max`.
2. Overview: `aggregateNmsAlarms` + `runNmsDiagnostics`; samples via `queryNmsAlarms` (one page).
3. Evidence: `listNmsAlarmFields` → `queryNmsAlarmsRaw` (`field_preset=evidence`).
4. Custom aggregate: `aggregateNmsAlarmsRaw`.
5. SQL: `sqlQueryNms` (SELECT only; `statement_timeout_ms`).
6. Paths: `findTopologyPaths` via **netx-common**.
7. Device CLI: **netx-common** — multi-NE = **one** `execManagedNe` batch.

## Short-intent recipes

| User says | Recipe |
|-----------|--------|
| fiber cut / LOS / 断纤 | `queryNmsAlarmsRaw(keyword=LOS)` and/or `Fiber Break` → **host_name** list |
| offline / BN EMS | keyword=`BN EMS` |
| Critical Top | `aggregateNmsAlarms(severity=critical, top_ne=20)` |
| CRC in area | Raw `keyword=CRC` + `AREA-` hostname prefix |
| optical power **threshold** | keyword=`optical power` + area — **not** fiber-cut |
| one hostname | host-scoped `queryNmsAlarms` / Raw only |

## Guardrails

- Prefer non-SQL; filter severity → keyword/host → time → ne_id.
- Lists ≤2 pages; `page_size` default 50.
- Display **host_name** only; never bare UUID to users.
- `getManagedNe` needs managed id; NMS UUID → `getNmsNe` / `execManagedNe(nms_ne_id=...)`.

See [reference.md](reference.md).
