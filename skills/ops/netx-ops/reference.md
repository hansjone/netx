# netx-ops quick reference

## Can it log in?

1. `listCliTargets` / `listManagedNe` → if found, `execManagedNe(…, commands=["show version"])`
2. Else `queryNmsNeInventory` → report NMS presence; say CLI not managed if absent from managed list

## NMS freshness

- `runNmsDiagnostics` / `aggregateNmsAlarms` → `meta.last_seen_min` / `max`

## Shortcuts

| Intent | Call |
|--------|------|
| Critical Top | `aggregateNmsAlarms(severity=critical, top_ne=20)` |
| Fiber / LOS | Raw `keyword=LOS` / `Fiber Break` |
| Inventory | `queryNmsNeInventory(keyword=…)` / `getNmsNe` |
| CLI batch | `execManagedNe(nms_ne_ids=[…], commands=[…])` |
| Paths | `findTopologyPaths(from_nms_ne_id, to_nms_ne_id)` |

DSH: prefix tools with `netx__`.
