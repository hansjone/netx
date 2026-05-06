param(
    [int]$Port = 8890,
    [int]$WebPort = 5173,
    [switch]$Force = $false
)

$ErrorActionPreference = "Continue"

$runDir = Join-Path $PSScriptRoot ".run"
$pidFile = Join-Path $runDir "netx.pid"
$webPidFile = Join-Path $runDir "web.pid"

function Get-ListenPids {
    param([int]$LocalPort)
    $ids = [System.Collections.Generic.HashSet[int]]::new()
    $job = $null
    try {
        $job = Start-Job -ScriptBlock {
            param($pt)
            Get-NetTCPConnection -LocalPort $pt -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        } -ArgumentList $LocalPort
        $null = Wait-Job $job -Timeout 5
        foreach ($x in @(Receive-Job $job -ErrorAction SilentlyContinue)) {
            try {
                $n = [int]$x
                if ($n -gt 4) { [void]$ids.Add($n) }
            } catch {}
        }
    } catch {
        Write-Host "[WARN] port query failed for $LocalPort : $($_.Exception.Message)"
    } finally {
        if ($job) {
            Stop-Job $job -Force -ErrorAction SilentlyContinue
            Remove-Job $job -Force -ErrorAction SilentlyContinue
        }
    }
    @($ids)
}

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

$owningApi = @(Get-ListenPids -LocalPort $Port)
if ($owningApi.Count -gt 0) {
    foreach ($procId in $owningApi) {
        if ($procId -eq $PID) { continue }
        try {
            Stop-Process -Id $procId -Force:$Force -ErrorAction Stop
            Write-Host "Stopped by port PID=$procId"
        } catch {
            Write-Host "[WARN] Failed to stop PID=$procId by port"
        }
    }
} else {
    Write-Host "[INFO] No listener found on port $Port"
}

$owningWeb = @(Get-ListenPids -LocalPort $WebPort)
if ($owningWeb.Count -gt 0) {
    foreach ($procId in $owningWeb) {
        if ($procId -eq $PID) { continue }
        try {
            Stop-Process -Id $procId -Force:$Force -ErrorAction Stop
            Write-Host "Stopped web by port PID=$procId"
        } catch {
            Write-Host "[WARN] Failed to stop web PID=$procId by port"
        }
    }
} else {
    Write-Host "[INFO] No listener found on web port $WebPort"
}

Write-Host "==> netx stop finished" -ForegroundColor Green
exit 0
