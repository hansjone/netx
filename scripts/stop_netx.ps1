param(
    [int]$Port = 8890,
    [int]$WebPort = 5173,
    # Windows often ignores graceful Stop-Process on python; default hard-kill.
    [switch]$Force = $true,
    [switch]$NoForce = $false
)

$ErrorActionPreference = "Continue"
$useForce = if ($NoForce) { $false } else { [bool]$Force }

$runDir = Join-Path $PSScriptRoot ".run"
$pidFile = Join-Path $runDir "netx.pid"
$workerPidFile = Join-Path $runDir "worker.pid"
$webPidFile = Join-Path $runDir "web.pid"

function Stop-OnePid {
    param([int]$ProcId, [string]$Label)
    if ($ProcId -le 4) { return }
    if ($ProcId -eq $PID) { return }
    try {
        Stop-Process -Id $ProcId -Force:$useForce -ErrorAction Stop
        Write-Host "Stopped $Label PID=$ProcId"
    } catch {
        $msg = $_.Exception.Message
        Write-Host "[WARN] Failed to stop $Label PID=$ProcId : $msg"
        if ($msg -match 'Access|Denied|拒绝|拒绝访问') {
            Write-Host "       Run elevated PowerShell, then: taskkill /F /PID $ProcId" -ForegroundColor Yellow
        }
    }
}

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
            # Windows PowerShell 5.1 does not support -Force on Stop-Job/Remove-Job.
            Stop-Job $job -ErrorAction SilentlyContinue
            Remove-Job $job -ErrorAction SilentlyContinue
        }
    }
    @($ids)
}

function Stop-NetxByCommandLine {
    # Orphan workers often have no worker.pid but still hold worker.out.log.
    $hits = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'netx_api\.(main|worker)' })
    if ($hits.Count -eq 0) {
        Write-Host "[INFO] No netx_api.main/worker process by command line"
        return
    }
    foreach ($p in $hits) {
        $kind = if ($p.CommandLine -match 'netx_api\.worker') { "worker(cmd)" } else { "api(cmd)" }
        Stop-OnePid -ProcId ([int]$p.ProcessId) -Label $kind
    }
}

Write-Host "==> Stopping netx (Force=$useForce)"

if (Test-Path $pidFile) {
    $pidText = (Get-Content -Path $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $procId = 0
    [void][int]::TryParse("$pidText", [ref]$procId)
    if ($procId -gt 0) {
        Stop-OnePid -ProcId $procId -Label "API"
    }
    Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "==> No API PID file, try by port $Port"
}

if (Test-Path $workerPidFile) {
    $workerPidText = (Get-Content -Path $workerPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $workerProcId = 0
    [void][int]::TryParse("$workerPidText", [ref]$workerProcId)
    if ($workerProcId -gt 0) {
        Stop-OnePid -ProcId $workerProcId -Label "worker"
    }
    Remove-Item -Path $workerPidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[INFO] No worker PID file"
}

if (Test-Path $webPidFile) {
    $webPidText = (Get-Content -Path $webPidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    $webProcId = 0
    [void][int]::TryParse("$webPidText", [ref]$webProcId)
    if ($webProcId -gt 0) {
        Stop-OnePid -ProcId $webProcId -Label "web"
    }
    Remove-Item -Path $webPidFile -Force -ErrorAction SilentlyContinue
}

Stop-NetxByCommandLine

$owningApi = @(Get-ListenPids -LocalPort $Port)
if ($owningApi.Count -gt 0) {
    foreach ($procId in $owningApi) {
        Stop-OnePid -ProcId $procId -Label "port:$Port"
    }
} else {
    Write-Host "[INFO] No listener found on port $Port"
}

$owningWeb = @(Get-ListenPids -LocalPort $WebPort)
if ($owningWeb.Count -gt 0) {
    foreach ($procId in $owningWeb) {
        Stop-OnePid -ProcId $procId -Label "port:$WebPort"
    }
} else {
    Write-Host "[INFO] No listener found on web port $WebPort"
}

Write-Host "==> netx stop finished" -ForegroundColor Green
exit 0
