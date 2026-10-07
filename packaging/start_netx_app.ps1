param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$SkipBrowser = $false,
    [switch]$InlineSchedulers = $false,
    [switch]$Force = $false
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"

if (-not (Test-Path $envPath)) {
    Write-Host "[ERR] Missing $envPath — run setup_first_run.ps1 first." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $prog "netx_api"))) {
    throw "netx_api not found under program root: $prog"
}

Ensure-NetxDataDirectories -DataRoot $data

$map = Import-NetxEnvFile -Path $envPath
# Force data-root paths even if an older .env missed them.
$dataEnv = Get-NetxDataEnvMap -DataRoot $data
foreach ($k in $dataEnv.Keys) {
    Set-Item -Path "Env:$k" -Value $dataEnv[$k]
}

Set-Location $prog
$env:PYTHONPATH = $prog

$dist = Join-Path $prog "web\dist"
if (Test-Path (Join-Path $dist "index.html")) {
    $env:NETX_UI_DIST_DIR = $dist
}

$mode = Get-DbMode -EnvMap $map
$hostBind = if ($env:NETX_HOST) { $env:NETX_HOST } else { "127.0.0.1" }
$port = if ($env:NETX_PORT) { [int]$env:NETX_PORT } else { 8890 }

Write-Host "==> Program: $prog"
Write-Host "==> Data:    $data"
Write-Host "==> DB mode: $mode"

if (-not $Force -and (Test-NetxApiHealthy -HostName $hostBind -Port $port)) {
    Write-Host "==> NetX already healthy at http://${hostBind}:${port}/ — skip start (use -Force to restart)" -ForegroundColor Green
    if (-not $SkipBrowser) {
        try { Start-Process "http://${hostBind}:${port}/" } catch {}
    }
    exit 0
}

function Get-PgsqlBin {
    foreach ($b in @(
            (Join-Path $prog "postgres\pgsql\bin"),
            (Join-Path $PSScriptRoot "postgres\pgsql\bin")
        )) {
        if (Test-Path (Join-Path $b "pg_ctl.exe")) { return $b }
    }
    return $null
}

if ($mode -eq "bundled") {
    $pgBin = Get-PgsqlBin
    if (-not $pgBin) { throw "bundled_postgres_missing" }
    $pgData = if ($map["NETX_BUNDLED_PG_DATA_DIR"]) {
        $map["NETX_BUNDLED_PG_DATA_DIR"] -replace '/', '\'
    } else {
        Join-Path $data "pgdata"
    }
    $pgPort = 15432
    if ($map["NETX_BUNDLED_PG_PORT"]) {
        try { $pgPort = [int]$map["NETX_BUNDLED_PG_PORT"] } catch {}
    }
    $pgCtl = Join-Path $pgBin "pg_ctl.exe"
    $status = & $pgCtl -D $pgData status 2>&1 | Out-String
    if ($status -notmatch "server is running") {
        if (-not (Test-NetxTcpPortFree -HostName "127.0.0.1" -Port $pgPort)) {
            throw "port_in_use: 127.0.0.1:$pgPort (bundled Postgres)"
        }
        Write-Host "==> Starting bundled PostgreSQL"
        if (-not (Test-Path $pgData)) {
            New-Item -ItemType Directory -Path $pgData -Force | Out-Null
        }
        $log = Join-Path $pgData "pg.log"
        & $pgCtl -D $pgData -l $log start
        if ($LASTEXITCODE -ne 0) { throw "pg_start_failed" }
        Start-Sleep -Seconds 2
    } else {
        Write-Host "==> Bundled PostgreSQL already running"
    }
}

$venvPy = Join-Path $prog ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "==> Creating .venv (one-time)"
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pyCmd) { throw "python_not_found: install Python 3.11+ and re-run" }
    & $pyCmd.Source -m venv (Join-Path $prog ".venv")
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r (Join-Path $prog "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "pip_install_failed" }
}

if (-not (Test-NetxTcpPortFree -HostName $hostBind -Port $port)) {
    if (Test-NetxApiHealthy -HostName $hostBind -Port $port) {
        Write-Host "==> Port $port already serves NetX — skip start" -ForegroundColor Green
        exit 0
    }
    throw "port_in_use: ${hostBind}:${port} occupied by non-NetX process"
}

$startScript = Join-Path $prog "scripts\start_netx.ps1"
if (-not (Test-Path $startScript)) {
    throw "start_netx.ps1 not found at $startScript"
}

$psArgs = @(
    "-ExecutionPolicy", "Bypass", "-File", $startScript,
    "-SkipInstall", "-Background",
    "-BindHost", $hostBind, "-Port", "$port"
)
if ($InlineSchedulers) { $psArgs += "-InlineSchedulers" }

Write-Host "==> Starting NetX (API + workers; UI via API on :$port)"
& powershell @psArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$url = "http://${hostBind}:${port}/"
Write-Host "==> Open: $url" -ForegroundColor Green
if (-not $SkipBrowser) {
    try { Start-Process $url } catch {}
}
