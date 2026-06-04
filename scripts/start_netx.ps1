param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8890,
    [int]$WebPort = 5173,
    [switch]$SkipInstall = $false,
    [switch]$Background = $false,
    [switch]$WithWeb = $false
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$runDir = Join-Path $PSScriptRoot ".run"
if (-not (Test-Path $runDir)) {
    New-Item -ItemType Directory -Path $runDir | Out-Null
}

$pidFile = Join-Path $runDir "netx.pid"
$logFile = Join-Path $runDir "netx.out.log"
$errFile = Join-Path $runDir "netx.err.log"
$webPidFile = Join-Path $runDir "web.pid"
$webLogFile = Join-Path $runDir "web.out.log"
$webErrFile = Join-Path $runDir "web.err.log"

$venvPython = Join-Path $projectRoot ".venv\\Scripts\\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "==> .venv not found, creating virtual environment"
    python -m venv (Join-Path $projectRoot ".venv")
}
if (-not (Test-Path $venvPython)) {
    throw "failed_to_create_venv"
}
$pythonExe = $venvPython

Write-Host "==> Project root: $projectRoot"
Write-Host "==> Using python: $pythonExe"

if (-not $SkipInstall) {
    Write-Host "==> Installing dependencies"
    & $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
} else {
    Write-Host "==> Skip dependency install (if API fails with ModuleNotFoundError, run once without -SkipInstall)"
}

if ($WithWeb) {
    $webRoot = Join-Path $projectRoot "web"
    $webNodeModules = Join-Path $webRoot "node_modules"
    if (-not (Get-Command "npm.cmd" -ErrorAction SilentlyContinue)) {
        throw "npm_not_found: install Node.js (includes npm) and reopen terminal"
    }
    $needWebInstall = (-not (Test-Path $webNodeModules)) -or (-not $SkipInstall)
    if ($needWebInstall) {
        Write-Host "==> Installing web dependencies (npm install)"
        & npm.cmd install --prefix $webRoot
        if ($LASTEXITCODE -ne 0) {
            throw "npm_install_failed"
        }
    } else {
        Write-Host "==> Skip web dependency install (node_modules exists)"
    }
}

$env:NETX_HOST = $BindHost
$env:NETX_PORT = "$Port"
# netx_api is a source tree (not always pip -e installed); ensure imports work from any launcher cwd.
$env:PYTHONPATH = $projectRoot
$baseUrl = "http://$BindHost`:$Port"
$webUrl = "http://$BindHost`:$WebPort"

Write-Host ""
Write-Host "==> netx API URL"
Write-Host "Base:          $baseUrl/"
Write-Host "Health:        $baseUrl/health"
Write-Host "Integrations:  $baseUrl/v1/integrations/status"
if ($WithWeb) {
    Write-Host ""
    Write-Host "==> netx UI URL"
    Write-Host "Vite Dev UI:   $webUrl/"
}
Write-Host ""

function Show-LogTail {
    param([string]$Path, [int]$Lines = 40)
    if (Test-Path $Path) {
        $tail = Get-Content -Path $Path -Tail $Lines -ErrorAction SilentlyContinue
        if ($tail) {
            Write-Host "--- tail $Path ---"
            $tail | ForEach-Object { Write-Host $_ }
        }
    }
}

function Test-NetxApiListening {
    param([string]$HostName, [int]$LocalPort, [int]$WaitSec = 45)
    $deadline = (Get-Date).AddSeconds($WaitSec)
    $healthUrl = "http://${HostName}:${LocalPort}/health"
    while ((Get-Date) -lt $deadline) {
        try {
            # Only HTTP 200 with {"status":"ok"} counts; no redirects / other codes.
            $r = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
            if ($r.StatusCode -ne 200) {
                Start-Sleep -Milliseconds 500
                continue
            }
            $body = $r.Content | ConvertFrom-Json -ErrorAction Stop
            if ($body.status -eq "ok") { return $true }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    return $false
}

if ($Background) {
    # Truncate logs so a failed start is not confused with an old run.
    Set-Content -Path $logFile -Value "" -Encoding utf8
    Set-Content -Path $errFile -Value "" -Encoding utf8
    Write-Host "==> Starting netx in background"
    $proc = Start-Process -FilePath $pythonExe `
        -ArgumentList @("-m", "netx_api.main") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $logFile `
        -RedirectStandardError $errFile `
        -PassThru
    Set-Content -Path $pidFile -Value "$($proc.Id)"
    Write-Host "netx.pid = $pidFile"
    Write-Host "PID = $($proc.Id)"
    Write-Host "Log = $logFile"
    Write-Host "Err = $errFile"
    Start-Sleep -Seconds 2
    $alive = $false
    try {
        $alive = -not $proc.HasExited
    } catch {
        $alive = $false
    }
    if (-not $alive) {
        Write-Host "[ERR] netx process exited immediately (PID $($proc.Id))." -ForegroundColor Red
        Show-LogTail -Path $errFile
        Show-LogTail -Path $logFile
        exit 1
    }
    $healthWaitSec = 45
    if (-not (Test-NetxApiListening -HostName $BindHost -LocalPort $Port -WaitSec $healthWaitSec)) {
        Write-Host "[ERR] Port $Port not listening /health not OK within ${healthWaitSec}s (process may be stuck on DB or schema migration)." -ForegroundColor Red
        Show-LogTail -Path $errFile
        Show-LogTail -Path $logFile
        exit 1
    }
    Write-Host "==> netx API ready: http://${BindHost}:${Port}/health" -ForegroundColor Green
    if ($WithWeb) {
        Write-Host "==> Starting Vite dev server in background"
        $webRoot = Join-Path $projectRoot "web"
        $webProc = Start-Process -FilePath "npm.cmd" `
            -ArgumentList @("run", "dev", "--", "--host", $BindHost, "--port", "$WebPort") `
            -WorkingDirectory $webRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $webLogFile `
            -RedirectStandardError $webErrFile `
            -PassThru
        Set-Content -Path $webPidFile -Value "$($webProc.Id)"
        Write-Host "web.pid = $webPidFile"
        Write-Host "PID = $($webProc.Id)"
        Write-Host "Log = $webLogFile"
        Write-Host "Err = $webErrFile"
    }
    Write-Host ""
    Write-Host "==> Background services started; this script exits (API/web keep running)." -ForegroundColor Cyan
    exit 0
}

if ($WithWeb) {
    Write-Host "==> Starting Vite dev server in background (foreground API mode)"
    $webRoot = Join-Path $projectRoot "web"
    $webProc = Start-Process -FilePath "npm.cmd" `
        -ArgumentList @("run", "dev", "--", "--host", $BindHost, "--port", "$WebPort") `
        -WorkingDirectory $webRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $webLogFile `
        -RedirectStandardError $webErrFile `
        -PassThru
    Set-Content -Path $webPidFile -Value "$($webProc.Id)"
    Write-Host "web.pid = $webPidFile"
    Write-Host "PID = $($webProc.Id)"
    Write-Host "Log = $webLogFile"
    Write-Host "Err = $webErrFile"
}

Write-Host "==> Starting netx in foreground"
& $pythonExe -m netx_api.main

