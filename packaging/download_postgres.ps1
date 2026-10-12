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
$partialPath = "$zipPath.partial"
$extractRoot = Join-Path $cache "pgsql-$ver"
$destPgsql = Join-Path $PSScriptRoot "postgres\pgsql"

if (-not (Test-Path $cache)) {
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
}

Write-Host "==> PostgreSQL binaries URL: $url"
if (-not (Test-Path $zipPath)) {
    Write-Host "==> Downloading (large; may take several minutes)..."
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        # PostgreSQL's archive is several hundred MB. Resume interrupted transfers
        # instead of restarting the entire download when the CDN drops a connection.
        & $curl.Source --fail --location --retry 8 --retry-all-errors --retry-delay 3 `
            --connect-timeout 30 --continue-at - --output $partialPath $url
        if ($LASTEXITCODE -ne 0) {
            throw "postgres_download_failed: curl exit $LASTEXITCODE (partial file kept at $partialPath)"
        }
        Move-Item -LiteralPath $partialPath -Destination $zipPath -Force
    } else {
        # Fallback for environments without curl; retry from scratch on transient errors.
        $downloaded = $false
        for ($attempt = 1; $attempt -le 3 -and -not $downloaded; $attempt++) {
            try {
                Invoke-WebRequest -Uri $url -OutFile $partialPath -UseBasicParsing
                Move-Item -LiteralPath $partialPath -Destination $zipPath -Force
                $downloaded = $true
            } catch {
                if ($attempt -eq 3) { throw }
                Remove-Item -LiteralPath $partialPath -Force -ErrorAction SilentlyContinue
                Start-Sleep -Seconds (5 * $attempt)
            }
        }
    }
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
