---
name: netx-common
description: >-
  netx common playbook (MCP + DSH): managed-NE CLI (SSH/Telnet batch-first) plus
  native findTopologyPaths. Not NMS REST alarms/inventory. Trigger: execManagedNe,
  show/display, optical, capacity A<>B, path between sites.
---

# netx-common (managed CLI + paths)

## Naming (MCP + DSH)

Canonical: **`netx/skills/common/netx-common/`**. Mirrored into MCP / dsh-netxops — edit once.

| Host | How tools appear |
|------|------------------|
| **MCP** (`netx-mcp`) | Bare names: `execManagedNe`, `findTopologyPaths`, … |
| **DSH** (`dsh-netxops`) | Prefixed: `netx__execManagedNe`, … |

NMS alarms/inventory → **netx-nms**. Canvas / dual_unit → **netx-topology**.

## Tools

| Purpose | Tool |
|---------|------|
| List managed NEs | `listManagedNe` |
| Managed detail | `getManagedNe` |
| Read-only CLI (batch-first) | `execManagedNe` |
| CLI target index | `listCliTargets` |
| Fabric paths | `findTopologyPaths` |

Prefer `nms_ne_id` / `nms_ne_ids`; legacy `ume_*` accepted. `listCliTargets(source=nms)` preferred (`ume` alias).

## CLI order

1. `listManagedNe` (`connect_status=pass`) or `listCliTargets` (**once** per session, cache ids)
2. Multi-NE → **one** `execManagedNe` with `ne_ids` / `nms_ne_ids` / `targets`
3. Paths → `findTopologyPaths` (`nms_ne_id` **or** `managed_ne_id` per end)

### Batch examples

**Same commands, many NEs**

```json
{
  "nms_ne_ids": ["uuid-a", "uuid-b", "uuid-c"],
  "commands": ["show version"],
  "read_timeout_sec": 60,
  "concurrency": 4
}
```

**Different commands per NE (still one call)**

```json
{
  "targets": [
    {"nms_ne_id": "uuid-zte", "commands": ["show opticalinfo brief"]},
    {"nms_ne_id": "uuid-hw", "commands": ["display optical-module brief"]},
    {"nms_ne_id": "uuid-cisco", "commands": ["show interface transceiver"]}
  ],
  "read_timeout_sec": 90
}
```

**Wrong:** N× single-NE `execManagedNe` in one turn (stdio serial).

## Field recipes

### Capacity / optical A<>B

1. Resolve nicknames → `host_name`
2. `findTopologyPaths` and/or LLDP → both ports
3. Optics CLI on **both** ends; summarize RX/TX / thresholds
4. Do not answer with only NMS bandwidth/optical-power-threshold alarm tallies unless asked

### ZTE optical CLI

Try in order; one failure → switch spelling (do not blind-retry):

| Prefer | Fallback |
|--------|----------|
| `show opticalinfo brief` | Field-confirmed on many ZXR10 |
| `show optical brief` | Some EN platforms |
| `show opticalinfo brief \| begin <if>` | After port known |

## Guardrails

- Never pass NMS alarm UUIDs as managed `ne_id` for `getManagedNe`
- Allowlist prefixes: `show ` / `display ` / `ping ` / `traceroute` …
- Prefer `host_name` for users; keep ids for tool params
