# netx-nms quick reference

## Freshness

- `runNmsDiagnostics` / `aggregateNmsAlarms` → `meta.last_seen_min` / `last_seen_max`
- Snapshot: windows inside min~max — do not default to `now()-30m`

## Tools

| Intent | Call |
|--------|------|
| Critical Top | `aggregateNmsAlarms(severity=critical, top_ne=20)` |
| Fiber / LOS | Raw `keyword=LOS` / `Fiber Break` |
| Offline / BN EMS | Raw keyword=`BN EMS` |
| Single host | `queryNmsAlarms(host_name=…)` |
| Inventory | `queryNmsNeInventory(keyword=…)` / `getNmsNe` |
| Paths | `findTopologyPaths(from_nms_ne_id, to_nms_ne_id)` — **common** skill |
| SQL | `sqlQueryNms` SELECT; `statement_timeout_ms=8000` |

DSH: prefix every tool with `netx__`.
