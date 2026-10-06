param(
    [string]$Version = "",
    [string]$OutDir = "",
    [switch]$SkipWebBuild = $false,
    [switch]$SkipPostgresDownload = $false,
    [switch]$SkipZip = $false,
    [switch]$CreateVenv = $false
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$repo = Get-NetxRepoRoot
if (-not $Version) {
    $Version = Get-NetxVersion -ProgramRoot $repo
}
$stage = if ($OutDir) { $OutDir } else { Join-Path $PSScriptRoot "release\netx-win64" }

Write-Host "==> Building NetX Windows release $Version"
Write-Host "    Repo:  $repo"
Write-Host "    Stage: $stage"

if (Test-Path $stage) {
    Remove-Item -Recurse -Force $stage
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$webRoot = Join-Path $repo "web"
$dist = Join-Path $webRoot "dist"
if (-not $SkipWebBuild) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "npm_not_found"
    }
    Write-Host "==> npm build"
    if (-not (Test-Path (Join-Path $webRoot "node_modules"))) {
        & npm.cmd install --prefix $webRoot
        if ($LASTEXITCODE -ne 0) { throw "npm_install_failed" }
    }
    & npm.cmd run build --prefix $webRoot
    if ($LASTEXITCODE -ne 0) { throw "web_build_failed" }
}
if (-not (Test-Path (Join-Path $dist "index.html"))) {
    throw "web_dist_missing: build web/ or drop -SkipWebBuild"
}

$pgsql = Join-Path $PSScriptRoot "postgres\pgsql"
if (-not $SkipPostgresDownload) {
    if (-not (Test-Path (Join-Path $pgsql "bin\pg_ctl.exe"))) {
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "download_postgres.ps1")
    }
}

# Flat layout = same as repo so scripts\start_netx.ps1 works unchanged.
foreach ($d in @("netx_api", "alembic", "scripts")) {
    Copy-Item -Path (Join-Path $repo $d) -Destination (Join-Path $stage $d) -Recurse -Force
}
foreach ($f in @("pyproject.toml", "requirements.txt", "alembic.ini", "README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md")) {
    $src = Join-Path $repo $f
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $stage $f) -Force
    }
}

$webOut = Join-Path $stage "web\dist"
New-Item -ItemType Directory -Path (Split-Path $webOut -Parent) -Force | Out-Null
Copy-Item -Path $dist -Destination $webOut -Recurse -Force

$packOut = Join-Path $stage "packaging"
New-Item -ItemType Directory -Path $packOut -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "_common.ps1") -Destination $packOut -Force
foreach ($name in @(
        "download_postgres.ps1", "setup_first_run.ps1", "start_netx_app.ps1",
        "stop_netx_app.ps1", "update_netx.ps1", "build_release.ps1",
        "README.md", "manifest.example.json"
    )) {
    $src = Join-Path $PSScriptRoot $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $packOut $name) -Force }
}
Copy-Item -Path (Join-Path $PSScriptRoot "config") -Destination (Join-Path $packOut "config") -Recurse -Force
Copy-Item -Path (Join-Path $PSScriptRoot "installer") -Destination (Join-Path $packOut "installer") -Recurse -Force
New-Item -ItemType Directory -Path (Join-Path $packOut "postgres") -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "postgres\README.md") -Destination (Join-Path $packOut "postgres\README.md") -Force

if (Test-Path (Join-Path $pgsql "bin\pg_ctl.exe")) {
    Write-Host "==> Copying bundled PostgreSQL"
    New-Item -ItemType Directory -Path (Join-Path $stage "postgres") -Force | Out-Null
    Copy-Item -Path $pgsql -Destination (Join-Path $stage "postgres\pgsql") -Recurse -Force
}

$builtAt = (Get-Date).ToUniversalTime().ToString("o")
@{
    version         = $Version
    channel         = "stable"
    min_data_layout = 1
    built_at        = $builtAt
    platform        = "win64"
} | ConvertTo-Json | Set-Content -Path (Join-Path $stage "version.json") -Encoding utf8

Set-Content -Path (Join-Path $stage ".portable") -Value "1" -Encoding ascii

if ($CreateVenv) {
    Write-Host "==> Creating .venv in stage (requires Python 3.11+ on PATH)"
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) { throw "python_not_found_for_venv" }
    & $py.Source -m venv (Join-Path $stage ".venv")
    & (Join-Path $stage ".venv\Scripts\python.exe") -m pip install --upgrade pip
    & (Join-Path $stage ".venv\Scripts\python.exe") -m pip install -r (Join-Path $stage "requirements.txt")
}

Write-Host "==> Stage ready: $stage" -ForegroundColor Green

if (-not $SkipZip) {
    $zip = Join-Path (Split-Path -Parent $stage) "NetX-$Version-win64.zip"
    if (Test-Path $zip) { Remove-Item -Force $zip }
    Compress-Archive -Path $stage -DestinationPath $zip -Force
    Write-Host "==> Zip: $zip" -ForegroundColor Green
}

Write-Host ""
Write-Host "Ship options:"
Write-Host "  Zip:  user unpacks, runs packaging\setup_first_run.ps1 then start_netx_app.ps1"
Write-Host "  Exe:  compile packaging\installer\netx.iss with Inno Setup (needs stage above)"
Write-Host "Python: install 3.11+ on target, or rebuild with -CreateVenv and ship .venv"
