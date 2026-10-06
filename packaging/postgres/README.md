# Windows packaging — portable PostgreSQL binaries

Place an extracted EnterpriseDB Windows x64 PostgreSQL tree here as `pgsql/` so that:

```
packaging/postgres/pgsql/bin/pg_ctl.exe
packaging/postgres/pgsql/bin/initdb.exe
packaging/postgres/pgsql/bin/psql.exe
```

Do not commit the binaries. Use:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\download_postgres.ps1
```

Bundled mode uses a separate data directory under the NetX **data root** (never under `pgsql/`), so upgrading the binary tree does not wipe the database.
