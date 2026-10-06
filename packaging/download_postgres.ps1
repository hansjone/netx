param(
    [string]$Version = "16.4-1",
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

# EnterpriseDB binary zip (Windows x86-64). Override with -Version if the URL 404s.
$ver = $Version
$url = "https://get.enterprisedb.com/postgresql/postgresql-$ver-windows-x64-binaries.zip"

$cache = if ($OutDir) { $OutDir } else { Join-Path $PSScriptRoot "cache" }
$zipPath = Join-Path $cache "postgresql-$ver-windows-x64-binaries.zip"
$extractRoot = Join-Path $cache "pgsql-$ver"
$destPgsql = Join-Path $PSScriptRoot "postgres\pgsql"

if (-not (Test-Path $cache)) {
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
}

Write-Host "==> PostgreSQL binaries URL: $url"
if (-not (Test-Path $zipPath)) {
    Write-Host "==> Downloading (large; may take several minutes)..."
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
} else {
    Write-Host "==> Using cached zip: $zipPath"
}

if (Test-Path $extractRoot) {
    Remove-Item -Recurse -Force $extractRoot
}
New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
Write-Host "==> Extracting..."
Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force

# Zip usually contains a top-level "pgsql" folder.
$found = Get-ChildItem -Path $extractRoot -Recurse -Filter "pg_ctl.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $found) {
    throw "pg_ctl.exe not found after extract; check zip layout"
}
$pgsqlSrc = Split-Path -Parent (Split-Path -Parent $found.FullName)
Write-Host "==> Found pgsql tree: $pgsqlSrc"

if (Test-Path $destPgsql) {
    Remove-Item -Recurse -Force $destPgsql
}
New-Item -ItemType Directory -Path (Split-Path -Parent $destPgsql) -Force | Out-Null
Copy-Item -Path $pgsqlSrc -Destination $destPgsql -Recurse -Force

$pgCtl = Join-Path $destPgsql "bin\pg_ctl.exe"
if (-not (Test-Path $pgCtl)) {
    throw "install_failed: missing $pgCtl"
}
Write-Host "==> Bundled PostgreSQL ready: $destPgsql" -ForegroundColor Green
Write-Host "    Tip: data directory is NOT here; setup_first_run / start scripts use the NetX data root."
