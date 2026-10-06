# NetX Windows packaging

**Linux is unchanged:** keep using your own PostgreSQL and `NETX_DATABASE_URL` with `scripts/start_netx.sh`. Nothing under this folder is required on Linux.

This directory builds a Windows deliverable with:

- Optional **bundled** portable PostgreSQL **or** **external** existing Postgres
- API-hosted UI (`web/dist`) — no separate Vite process for end users
- Program / data split so upgrades do not wipe the database
- Manual update script + check/apply updates (GitHub Releases or custom manifest)
- Optional system tray + start-at-logon

## What users get

| Artifact | How |
|----------|-----|
| `NetX-x.y.z-win64.zip` | `build_release.ps1` |
| `NetX-Setup-x.y.z.exe` | Compile `installer/netx.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) after staging |

## Build (developer machine)

Prerequisites: Python 3.11+, Node 20+, PowerShell, network (to download PG binaries once).

```powershell
cd netx
# Optional: download portable Postgres into packaging\postgres\pgsql
powershell -ExecutionPolicy Bypass -File .\packaging\download_postgres.ps1

# Build web + stage + zip (-CreateVenv ships a ready .venv; large)
powershell -ExecutionPolicy Bypass -File .\packaging\build_release.ps1 -CreateVenv
```

Output:

- `packaging/release/netx-win64/` — stage tree
- `packaging/release/NetX-<ver>-win64.zip`

Inno Setup:

```text
ISCC.exe packaging\installer\netx.iss
```

Override version in the `.iss` or edit `#define MyAppVersion`.

## End-user install

### Setup.exe

1. Run `NetX-Setup-x.y.z.exe` (admin).
2. Files → `%ProgramFiles%\NetX\` (program root).
3. Data → `%ProgramData%\NetX\` (`.env`, `pgdata`, spool, secrets).
4. Optional post-install task runs `setup_first_run.ps1` (choose bundled vs external DB).
5. Start menu: **Start NetX** / **Stop NetX** / **Open NetX UI**.

### Zip (portable)

1. Unpack anywhere.
2. Marker `.portable` → data root is sibling `NetXData\`.
3. Target needs **Python 3.11+** on PATH unless the zip was built with `-CreateVenv`.
4. Run:

```powershell
.\packaging\setup_first_run.ps1
.\packaging\start_netx_app.ps1
```

## Database modes

| Mode | Behavior |
|------|----------|
| `bundled` | Start portable PG on `127.0.0.1:15432`, data in data-root `pgdata\` |
| `external` | Do not start PG; use `NETX_DATABASE_URL` (same as today / Linux) |
| unset | Treated as **external** |

Existing Windows deploys that only set `NETX_DATABASE_URL` keep working: never set `NETX_DB_MODE=bundled` unless you want the portable engine.

## Manual update

```powershell
.\packaging\update_netx.ps1 -PackagePath .\NetX-0.4.0-win64.zip
```

Stops services, replaces program folders (`netx_api`, `web`, `packaging`, `postgres`, …), **keeps** the data root, restarts. Schema migrations still run via Alembic on API start.

Or reinstall a newer `NetX-Setup-*.exe` over the same program directory.

## Check / apply updates

Default source: latest GitHub Release for `hansjone/netx`. Optional custom manifest via `NETX_UPDATE_URL` (see `manifest.example.json`).

```powershell
# Report only (exit 0 = up to date, 10 = update available)
.\packaging\check_update.ps1

# Download zip + run update_netx.ps1
.\packaging\check_update.ps1 -Apply
```

## Tray & autostart

```powershell
# System tray: Start / Stop / Open UI / Check updates
.\packaging\netx_tray.ps1 -StartOnLaunch

# Start tray at Windows logon (current user)
.\packaging\install_autostart.ps1
# Remove: .\packaging\install_autostart.ps1 -Remove
```

## Windows Service (admin)

Uses [WinSW](https://github.com/winsw/winsw) (downloaded on first install into `packaging/cache`).

```powershell
# Elevated PowerShell
.\packaging\install_service.ps1 -Start
# Uninstall: .\packaging\install_service.ps1 -Uninstall

# Fallback without WinSW binary management: SYSTEM scheduled task at startup
.\packaging\install_service.ps1 -Mode task -Start
```

## Silent auto-update

```powershell
# Writes NETX_UPDATE_AUTO=true and registers a daily task (default 03:30)
.\packaging\install_update_task.ps1

# Manual silent path (only applies when NETX_UPDATE_AUTO=true)
.\packaging\check_update.ps1 -Apply -Quiet -AutoOnly
```

Tray also auto-applies on launch when `NETX_UPDATE_AUTO=true`.

## Code signing (optional, publisher machine)

```powershell
.\packaging\sign_release.ps1 -Files .\packaging\release\NetX-Setup-0.4.0.exe -Thumbprint <cert-sha1>
# or: -PfxPath .\certs\code.pfx -PfxPassword ***
```

Needs `signtool.exe` (Windows SDK) and a real code-signing certificate. Without a trusted cert, SmartScreen may still warn.

## Layout reminder

```
Program root (replaceable)     Data root (never wiped by update)
  netx_api\  web\dist\           .env
  postgres\pgsql\                pgdata\     (bundled only)
  packaging\  scripts\           data\auth\  data\runtime\
  version.json                   backups\
```
