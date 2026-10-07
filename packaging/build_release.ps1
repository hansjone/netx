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
    # Release packaging skips tsc (dev CI still uses `npm run build`).
    & npm.cmd run build:release --prefix $webRoot
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

# Bundle WinSW so offline installs can register a Windows Service without GitHub.
$winswCache = Join-Path $PSScriptRoot "cache\WinSW-x64.exe"
$winswShip = Join-Path $PSScriptRoot "winsw\WinSW-x64.exe"
if (-not (Test-Path $winswShip)) {
    if (-not (Test-Path $winswCache)) {
        $winswUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
        Write-Host "==> Downloading WinSW for offline bundle: $winswUrl"
        New-Item -ItemType Directory -Path (Split-Path $winswCache -Parent) -Force | Out-Null
        Invoke-WebRequest -Uri $winswUrl -OutFile $winswCache -UseBasicParsing
    }
    New-Item -ItemType Directory -Path (Split-Path $winswShip -Parent) -Force | Out-Null
    Copy-Item -Path $winswCache -Destination $winswShip -Force
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

function Write-Utf8BomFile {
    param([string]$Path)
    # Windows PowerShell 4/5 (Server 2012+) mis-parses UTF-8 without BOM when scripts contain CJK.
    $utf8Bom = New-Object System.Text.UTF8Encoding $true
    $bytes = [IO.File]::ReadAllBytes($Path)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $text = if ($hasBom) {
        [Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    } else {
        [Text.Encoding]::UTF8.GetString($bytes)
    }
    $text = $text -replace "`r`n", "`n" -replace "`n", "`r`n"
    if (-not $text.EndsWith("`r`n")) { $text += "`r`n" }
    [IO.File]::WriteAllText($Path, $text, $utf8Bom)
}

$packOut = Join-Path $stage "packaging"
New-Item -ItemType Directory -Path $packOut -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "_common.ps1") -Destination $packOut -Force
foreach ($name in @(
        "download_postgres.ps1", "setup_first_run.ps1", "start_netx_app.ps1",
        "stop_netx_app.ps1", "update_netx.ps1", "check_update.ps1",
        "launch_shortcut.ps1", "netx_tray.ps1", "install_autostart.ps1", "install_service.ps1",
        "service_run.ps1", "install_update_task.ps1", "sign_release.ps1",
        "build_release.ps1", "publish_release.ps1", "publish_forgejo_release.ps1", "README.md", "manifest.example.json"
    )) {
    $src = Join-Path $PSScriptRoot $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $packOut $name) -Force }
}
Get-ChildItem -Path $packOut -Filter *.ps1 -File | ForEach-Object { Write-Utf8BomFile -Path $_.FullName }
Get-ChildItem -Path (Join-Path $stage "scripts") -Filter *.ps1 -File -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Utf8BomFile -Path $_.FullName }
Copy-Item -Path (Join-Path $PSScriptRoot "config") -Destination (Join-Path $packOut "config") -Recurse -Force
Copy-Item -Path (Join-Path $PSScriptRoot "installer") -Destination (Join-Path $packOut "installer") -Recurse -Force
$assetsSrc = Join-Path $PSScriptRoot "assets"
if (Test-Path $assetsSrc) {
    Copy-Item -Path $assetsSrc -Destination (Join-Path $packOut "assets") -Recurse -Force
}
$winswOut = Join-Path $packOut "winsw"
New-Item -ItemType Directory -Path $winswOut -Force | Out-Null
if (Test-Path $winswShip) {
    Copy-Item -Path $winswShip -Destination (Join-Path $winswOut "WinSW-x64.exe") -Force
    Write-Host "==> Bundled WinSW for offline service install"
} else {
    Write-Host "[WARN] WinSW missing — offline service install will fail" -ForegroundColor Yellow
}
New-Item -ItemType Directory -Path (Join-Path $packOut "postgres") -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "postgres\README.md") -Destination (Join-Path $packOut "postgres\README.md") -Force

if (Test-Path (Join-Path $pgsql "bin\pg_ctl.exe")) {
    Write-Host "==> Copying bundled PostgreSQL (bin/lib/share; skip doc/)"
    $pgStage = Join-Path $stage "postgres\pgsql"
    New-Item -ItemType Directory -Path $pgStage -Force | Out-Null
    foreach ($sub in @("bin", "lib", "share")) {
        $srcSub = Join-Path $pgsql $sub
        if (Test-Path $srcSub) {
            Copy-Item -Path $srcSub -Destination (Join-Path $pgStage $sub) -Recurse -Force
        }
    }
    Get-ChildItem -Path $pgsql -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $pgStage $_.Name) -Force
    }
} else {
    throw "bundled_postgres_missing: Setup.exe must ship postgres for offline install. Run download_postgres.ps1 (build machine only)."
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
    $venvPy = Join-Path $stage ".venv\Scripts\python.exe"
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r (Join-Path $stage "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip_install_failed" }

    Write-Host "==> Slimming .venv (drop tests / __pycache__ / *.pyc)"
    $site = Join-Path $stage ".venv\Lib\site-packages"
    if (Test-Path $site) {
        Get-ChildItem -Path $site -Recurse -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -in @('__pycache__', 'tests', 'test', 'testing', 'Tests') -or
                $_.FullName -match '\\pandas\\tests(\\|$)' -or
                $_.FullName -match '\\numpy\\(_*tests|tests)(\\|$)' -or
                $_.FullName -match '\\scipy\\(_*tests|tests)(\\|$)'
            } |
            Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
        Get-ChildItem -Path $site -Recurse -Include *.pyc,*.pyo -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
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
