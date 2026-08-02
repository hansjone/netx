# Alembic (schema migrations)

Schema evolution uses Alembic. On API startup, netx **automatically** runs
`alembic upgrade head` (same patches as [`netx_api/schema_patches.py`](../netx_api/schema_patches.py)).
You normally do **not** need extra env flags.

## What happens on boot

1. `create_all` — create any missing tables from ORM metadata
2. `alembic upgrade head` — apply revisions (idempotent brownfield patches)
3. Auth column safety-net ensures (before admin bootstrap)
4. Legacy inline ALTER block is **skipped** by default (already covered by Alembic)

## Commands (optional / CI)

```powershell
cd netx
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe history
```

### Brownfield DB that already received old startup DDL

First boot after this change will run `upgrade head`. Patches are idempotent.
If Alembic history was never stamped and you prefer to mark current without re-running:

```powershell
.\.venv\Scripts\alembic.exe stamp head
```

## Env overrides (usually leave defaults)

| Variable | Default | Meaning |
|----------|---------|---------|
| `NETX_ALEMBIC_UPGRADE_ON_START` | `true` | Run `alembic upgrade head` on API start. Set `false` only if a separate migrate job owns upgrades. |
| `NETX_SKIP_LEGACY_STARTUP_DDL` | `true` | Skip the duplicate inline ALTER path. Set `false` only as emergency fallback. |
| `NETX_DATABASE_URL` | — | Same URL Alembic reads via `netx_api.config.settings`. |

## Revisions

| Revision | Purpose |
|----------|---------|
| `20260802_scopes` | `app_user.scopes` / `api_token.scopes` |
| `20260802_legacy` | Shared brownfield patches (alarms, inventory, managed_ne, topology, port traffic, key-alert, …) |
