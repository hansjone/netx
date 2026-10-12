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
| `NetX-Setup-x.y.z.exe` | Stage with `build_release.ps1`, then compile `installer/netx.iss` with [Inno Setup](https://jrsoftware.org/isinfo.php) |

Zip packages are **not** published. Releases ship **Setup.exe only**.

**Installer:** English + 简体中文 (language dialog). Icons use `packaging/assets/netx.ico`.

**Offline:** Setup ships portable PostgreSQL, **`python/runtime` + `.venv`** (portable; not tied to the build PC’s user profile), and WinSW. On upgrade, Setup removes the old runtime and virtual environment before copying the bundled pair, preventing stale standard-library files when Python versions have different layouts. **First install** shows the Database page (built-in or external PostgreSQL; external credentials validated with `psql SELECT 1`). **Re-running Setup when `%ProgramData%\NetX\.env` has `NETX_DB_MODE` or a non-empty legacy `NETX_DATABASE_URL`** skips the DB wizard by default. Update stops and resumes an installed Windows service or startup task while replacing files. Service install uses bundled WinSW. Auto-update still needs network later, and is unchecked by default.

**OS:** Windows 10/11 or Windows Server **2016+** recommended. Packaging scripts are UTF-8 **with BOM** so Chinese UI works on Windows PowerShell 5.x. The Windows release workflow pins bundled Python to **3.12**, so local release builds should use the same version for consistent artifacts.

## Build (developer machine)

Prerequisites: Python **3.12.x**, Node 20+, PowerShell, network (to download PostgreSQL binaries once).

```powershell
cd netx
# Required once per clean build machine: download portable PostgreSQL into packaging\postgres\pgsql
powershell -ExecutionPolicy Bypass -File .\packaging\download_postgres.ps1

# Build web + offline stage containing Python, .venv, PostgreSQL and WinSW. No zip by default.
powershell -ExecutionPolicy Bypass -File .\packaging\build_release.ps1 -CreateVenv
```

Output:

- `packaging/release/netx-win64/` — stage tree (input to Inno Setup)

Inno Setup:

```text
ISCC.exe packaging\installer\netx.iss
```

Override version in the `.iss` or edit `#define MyAppVersion`.  
Optional local zip only: `build_release.ps1 -CreateZip` (not for release upload).

## End-user install

### Setup.exe

1. Run `NetX-Setup-x.y.z.exe` (admin).
2. **First install:** Database page — choose built-in (offline) or external PostgreSQL (host/port/user/password/db; connection must succeed to continue).
3. **Already installed:** if `%ProgramData%\NetX\.env` already has `NETX_DB_MODE`, Setup shows an **Install mode** page:
   - **Update** (default) — skip Database page; stop NetX; replace program files; **keep** ProgramData / DB settings.
   - **Reinstall** — same Database wizard as first install (`ApplyDatabaseConfig`); does **not** delete ProgramData (use Uninstall → delete data for a wipe).
4. Files → `%ProgramFiles%\NetX\` (program root).
5. Data → `%ProgramData%\NetX\` (`.env`, `pgdata`, spool, secrets).
6. Start menu: **Start NetX** / **Stop NetX** / **Open NetX UI** / **Reconfigure database**.

**Elevation (required on fresh PCs):** all end-user entry points (`NetX-Start.cmd`, `NetX-Tray.cmd`, `NetX-Stop.cmd`, `NetX-FirstRun.cmd`, tray, start/stop/setup/update) **self-elevate via UAC**. Program Files installs cannot start reliably as a normal user (`.venv` repair / writes). Approve the UAC prompt once per launch (tray stays elevated afterward).

Silent first install (bundled default): `/SILENT /DbMode=bundled`  
Silent external: `/SILENT /DbMode=external /DbHost=... /DbPort=5432 /DbUser=... /DbPassword=... /DbName=...`  
Silent update over existing data: `/VERYSILENT /NORESTART /InstallMode=update` (also `/SkipDbPage=1`)  
Silent reinstall (DB wizard via params): `/VERYSILENT /InstallMode=reinstall /DbMode=bundled` (or external `/DbHost`…) — also `/ForceDbPage=1`  
Optional credential key (reuse encrypted NE passwords from another install): `/CredentialSecretKey=...` — omit to auto-generate.

## Database modes

| Mode | Behavior |
|------|----------|
| `bundled` | Start portable PG on `127.0.0.1:15432`, data in data-root `pgdata\` |
| `external` | Do not start PG; use `NETX_DATABASE_URL` (same as today / Linux) |
| unset | Treated as **external** |

Existing Windows deploys that only set `NETX_DATABASE_URL` keep working: never set `NETX_DB_MODE=bundled` unless you want the portable engine.

## Manual update

Preferred: run a newer `NetX-Setup-*.exe` and choose **Update** (or silent `/InstallMode=update`) so ProgramData is kept. Setup (elevated) always relinks `.venv` → `python/runtime` after file copy; runtime scripts also self-elevate so Start/Tray work on locked-down fresh PCs.

Legacy/dev zip (only if you built with `-CreateZip`):

```powershell
.\packaging\update_netx.ps1 -PackagePath .\NetX-0.4.0-win64.zip
```

Stops services, replaces program folders (`netx_api`, `web`, `packaging`, `postgres`, …), **keeps** the data root, restarts. Schema migrations still run via Alembic on API start.

## Check / apply updates

**Defaults (no `.env` needed):** probe **GitHub** (`hansjone/netx`) and Forgejo mirror (`https://git.avelo.top/hansjone/netx`), then use the **newest reachable** version. Same version → prefer GitHub.

```env
# Optional overrides in ProgramData\NetX\.env
# NETX_UPDATE_SOURCES=github,forgejo
# NETX_UPDATE_FORGEJO_URL=https://git.avelo.top/api/v1/repos/hansjone/netx/releases/latest
# NETX_UPDATE_GITHUB_REPO=hansjone/netx
# NETX_UPDATE_URL=https://cdn.example.com/netx/manifest.json
# NETX_UPDATE_TOKEN=******
```

| Variable | Role |
|----------|------|
| `NETX_UPDATE_FORGEJO_URL` | Forgejo/Gitea `releases/latest` API (default: git.avelo.top mirror) |
| `NETX_UPDATE_GITHUB_REPO` | GitHub `owner/repo` (default `hansjone/netx`) |
| `NETX_UPDATE_SOURCES` | Probe order / tie-break order, e.g. `github,forgejo` |
| `NETX_UPDATE_URL` | Optional custom JSON manifest |

```powershell
.\packaging\check_update.ps1
.\packaging\check_update.ps1 -Apply
```

Both release hosts must publish the same **Setup.exe and `.sha256` sidecar**. `check_update.ps1` prefers `NetX-Setup-*.exe` (legacy `win64.zip` is still accepted); it verifies the Setup against a manifest hash or the adjacent checksum asset. Old releases without a sidecar remain supported with a warning. **Code mirror alone is not enough** — Forgejo pull-mirror syncs git/tags only; release assets are uploaded separately.

### Maintainer release checklist (Windows)

Release from a clean, version-matched checkout in this order:

1. **Build and publish GitHub** — ` .\packaging\publish_release.ps1 -Version x.y.z` builds, verifies and uploads Setup.exe plus SHA-256.
2. **Forgejo** — publish the same Setup.exe and checksum sidecar (default: public domain `https://git.avelo.top`):

```powershell
# One-time: User env var (never commit the token)
# Forgejo → Settings → Applications → Generate New Token (repo write)
[Environment]::SetEnvironmentVariable("NETX_FORGEJO_TOKEN", "your_token", "User")
# Restart Cursor / open a new terminal so the agent/scripts see it

# Default: https://git.avelo.top
.\packaging\publish_forgejo_release.ps1 -Version 0.4.11

# Optional when LAN IP is reachable:
# .\packaging\publish_forgejo_release.ps1 -Version 0.4.11 -ForgejoBase "http://10.0.0.131:3000"
```

| Role | URL |
|------|-----|
| Publish default | `https://git.avelo.top` (`-ForgejoBase` default) |
| Optional LAN | `http://10.0.0.131:3000` |
| Update probe (default) | GitHub + `https://git.avelo.top/.../releases/latest` |

Verify:
- Confirm each release has `NetX-Setup-x.y.z.exe` and `NetX-Setup-x.y.z.exe.sha256`.
- https://git.avelo.top/hansjone/netx/releases
- Probe: `.\packaging\check_update.ps1` → both `github` and `forgejo` reachable with the new version

Token: `NETX_FORGEJO_TOKEN` (or `FORGEJO_TOKEN`).

## Tray & autostart

```powershell
# System tray: Start / Stop / Open UI / Check updates (self-elevates)
.\packaging\netx_tray.ps1 -StartOnLaunch

# Start tray at Windows logon — Scheduled Task with RunLevel Highest
# (replaces legacy HKCU\Run which cannot elevate on fresh installs)
.\packaging\install_autostart.ps1
# Remove: .\packaging\install_autostart.ps1 -Remove
```

## Windows Service (admin)

Uses [WinSW](https://github.com/winsw/winsw) (downloaded on first install into `packaging/cache`).
`service_run.ps1` probes `/health` and restarts children after consecutive failures.

```powershell
# Elevated PowerShell
.\packaging\install_service.ps1 -Start
# Uninstall: .\packaging\install_service.ps1 -Uninstall

# Fallback without WinSW binary management: SYSTEM scheduled task at startup
.\packaging\install_service.ps1 -Mode task -Start
```

## Silent auto-update

```powershell
# Writes NETX_UPDATE_AUTO=true and registers a daily task (default 03:30, RunLevel Highest)
.\packaging\install_update_task.ps1

# Manual silent path (only applies when NETX_UPDATE_AUTO=true; self-elevates)
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
