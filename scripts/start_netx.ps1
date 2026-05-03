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
$pythonExe = if (Test-Path $venvPython) { $venvPython } else { "python" }

Write-Host "==> Project root: $projectRoot"
Write-Host "==> Using python: $pythonExe"

if (-not $SkipInstall) {
    Write-Host "==> Installing dependencies"
    & $pythonExe -m pip install -r (Join-Path $projectRoot "requirements.txt")
} else {
    Write-Host "==> Skip dependency install"
}

$env:NETX_HOST = $BindHost
$env:NETX_PORT = "$Port"
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

if ($Background) {
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
    exit 0
}

Write-Host "==> Starting netx in foreground"
& $pythonExe -m netx_api.main

