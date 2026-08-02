# Alembic (schema migrations)

netx historically evolved the schema with startup `ALTER TABLE … IF NOT EXISTS`.
Alembic is the preferred path going forward.

## Commands

```powershell
cd netx
.\.venv\Scripts\alembic.exe upgrade head
```

## Env

| Variable | Meaning |
|----------|---------|
| `NETX_SKIP_LEGACY_STARTUP_DDL=true` | Skip the large ad-hoc ALTER block in API startup (keep auth `scopes` column ensures). Use after `alembic upgrade head`. |
| `NETX_DATABASE_URL` | Same URL Alembic reads via `netx_api.config.settings`. |

Fresh lab installs can keep the legacy startup DDL (`false`, default) until you adopt Alembic in your deploy checklist.

Revision for capability scopes: `alembic/versions/20260802_scopes.py`.
