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

**Installer:** English + 简体中文 (language dialog). Icons use `packaging/assets/netx.ico`.

**Offline:** Setup ships portable PostgreSQL, **`python/runtime` + `.venv`** (portable; not tied to the build PC’s user profile), and WinSW. The installer wizard chooses **built-in or external** PostgreSQL and validates external credentials (`psql SELECT 1`) before files are installed — **no GitHub/EDB download on the target PC**. Service install uses bundled WinSW (pass `-AllowDownload` only on a build/dev machine if the binary is missing). Auto-update still needs network later, and is unchecked by default.

**OS:** Windows 10/11 or Windows Server **2016+** recommended. Packaging scripts are UTF-8 **with BOM** so Chinese UI works on Windows PowerShell 5.x. Bundled Python in current releases is **3.13+**, which does **not** support Windows Server 2012 R2 — use Server 2016+ or a newer desktop OS.

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
2. On the **Database** page: choose built-in (offline) or external PostgreSQL (host/port/user/password/db; connection must succeed to continue).
3. Files → `%ProgramFiles%\NetX\` (program root).
4. Data → `%ProgramData%\NetX\` (`.env`, `pgdata`, spool, secrets). Post-install writes `.env` automatically.
5. Start menu: **Start NetX** / **Stop NetX** / **Open NetX UI** / **Reconfigure database** (repair only).

Silent (bundled default): `/SILENT /DbMode=bundled`  
Silent external: `/SILENT /DbMode=external /DbHost=... /DbPort=5432 /DbUser=... /DbPassword=... /DbName=...`  
Optional credential key (reuse encrypted NE passwords from another install): `/CredentialSecretKey=...` — omit to auto-generate.

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

Both sides should publish the same release assets (`NetX-*-win64.zip`). **Code mirror alone is not enough** — Forgejo pull-mirror syncs git/tags only; Release zip/exe must be uploaded separately (or clients can only fall back to GitHub).

### Maintainer release checklist (Windows)

Do this on the **dev PC on the home LAN** after bumping version:

1. **Build** — `.\packaging\build_release.ps1 -CreateVenv` (and Inno Setup for Setup.exe if needed)
2. **GitHub** — `.\packaging\publish_release.ps1 -Version x.y.z` (or `gh release create …`)
3. **Forgejo (intranet)** — upload the same assets to QNAP Forgejo; public `git.avelo.top` is only a reverse proxy to the same instance:

```powershell
# One-time: User env var (never commit the token)
# Forgejo → Settings → Applications → Generate New Token (repo write)
[Environment]::SetEnvironmentVariable("NETX_FORGEJO_TOKEN", "your_token", "User")
# Restart Cursor / open a new terminal so the agent/scripts see it

# Default publishes to LAN Forgejo; public git.avelo.top shows the same Release.
.\packaging\publish_forgejo_release.ps1 -Version 0.4.0

# Only when off the home LAN:
# .\packaging\publish_forgejo_release.ps1 -Version 0.4.0 -ForgejoBase "https://git.avelo.top"
```

| Role | URL |
|------|-----|
| Publish default (home LAN) | `http://10.0.0.131:3000` (`-ForgejoBase` default) |
| Clients / remote clone | `https://git.avelo.top` (Caddy → `10.0.0.131:3000`) |
| Update probe (default) | GitHub + `https://git.avelo.top/.../releases/latest` |

Verify:

- LAN: http://10.0.0.131:3000/hansjone/netx/releases
- Public: https://git.avelo.top/hansjone/netx/releases
- Probe: `.\packaging\check_update.ps1` → both `github` and `forgejo` reachable with the new version

Token: `NETX_FORGEJO_TOKEN` (or `FORGEJO_TOKEN`).

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
