param(
    [int]$Port = 8890,
    [int]$WebPort = 5173,
    [switch]$Force = $false
)

$ErrorActionPreference = "Continue"

$runDir = Join-Path $PSScriptRoot ".run"
$pidFile = Join-Path $runDir "netx.pid"
$webPidFile = Join-Path $runDir "web.pid"

Write-Host "==> Stopping netx"

if (Test-Path $pidFile) {
    $pidText = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $procId = 0
    [void][int]::TryParse("$pidText", [ref]$procId)
    if ($procId -gt 0) {
        try {
            Stop-Process -Id $procId -Force:$Force -ErrorAction Stop
            Write-Host "Stopped PID=$procId"
        } catch {
            Write-Host "[WARN] PID file process not running: $procId"
        }
    }
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "==> No PID file, try by port $Port"
}

if (Test-Path $webPidFile) {
    $webPidText = (Get-Content -Path $webPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $webProcId = 0
    [void][int]::TryParse("$webPidText", [ref]$webProcId)
    if ($webProcId -gt 0) {
        try {
            Stop-Process -Id $webProcId -Force:$Force -ErrorAction Stop
            Write-Host "Stopped web PID=$webProcId"
        } catch {
            Write-Host "[WARN] web PID file process not running: $webProcId"
        }
    }
    Remove-Item -Path $webPidFile -Force -ErrorAction SilentlyContinue
}

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listeners) {
    $owning = $listeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($p in $owning) {
        try {
            Stop-Process -Id $p -Force:$Force -ErrorAction Stop
            Write-Host "Stopped by port PID=$p"
        } catch {
            Write-Host "[WARN] Failed to stop PID=$p by port"
        }
    }
} else {
    Write-Host "[INFO] No listener found on port $Port"
}

$webListeners = Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue
if ($webListeners) {
    $webOwning = $webListeners | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($p in $webOwning) {
        try {
            Stop-Process -Id $p -Force:$Force -ErrorAction Stop
            Write-Host "Stopped web by port PID=$p"
        } catch {
            Write-Host "[WARN] Failed to stop web PID=$p by port"
        }
    }
} else {
    Write-Host "[INFO] No listener found on web port $WebPort"
}

