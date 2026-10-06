param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [int]$HealthFailLimit = 6,
    [int]$HealthIntervalSec = 10
)

# Long-running supervisor for Windows Service / Task Scheduler.
# Starts NetX, probes /health; repeated failures trigger restart (or exit for WinSW).

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$logDir = Join-Path $data "data\runtime"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$logFile = Join-Path $logDir "service_run.log"
$stopFlag = Join-Path $logDir "service.stop"

function Write-SvcLog([string]$Msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $line
}

function Get-BindInfo {
    $envPath = Join-Path $data ".env"
    $hostBind = "127.0.0.1"
    $port = 8890
    if (Test-Path $envPath) {
        $map = Read-DotEnv -Path $envPath
        if ($map["NETX_HOST"]) { $hostBind = $map["NETX_HOST"] }
        if ($map["NETX_PORT"]) { try { $port = [int]$map["NETX_PORT"] } catch {} }
    }
    return @{ Host = $hostBind; Port = $port }
}

Remove-Item -Force $stopFlag -ErrorAction SilentlyContinue
Write-SvcLog "service_run starting program=$prog data=$data"

$startPs1 = Join-Path $PSScriptRoot "start_netx_app.ps1"
$stopPs1 = Join-Path $PSScriptRoot "stop_netx_app.ps1"
$bind = Get-BindInfo
$failStreak = 0
$restartCount = 0

function Start-NetxChildren {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startPs1 `
        -ProgramRoot $prog -DataRoot $data -SkipBrowser -Force
    if ($LASTEXITCODE -ne 0) {
        throw "start_netx_app_failed exit=$LASTEXITCODE"
    }
}

function Stop-NetxChildren {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopPs1 `
        -ProgramRoot $prog -DataRoot $data
}

try {
    Start-NetxChildren
    Write-SvcLog "NetX started; health watch host=$($bind.Host) port=$($bind.Port)"

    while ($true) {
        if (Test-Path $stopFlag) {
            Write-SvcLog "stop flag detected"
            break
        }

        if (Test-NetxApiHealthy -HostName $bind.Host -Port $bind.Port -TimeoutSec 3) {
            $failStreak = 0
        } else {
            $failStreak++
            Write-SvcLog "health fail streak=$failStreak/$HealthFailLimit"
            if ($failStreak -ge $HealthFailLimit) {
                $restartCount++
                Write-SvcLog "restarting NetX (attempt=$restartCount)"
                try { Stop-NetxChildren } catch { Write-SvcLog "stop warning: $($_.Exception.Message)" }
                Start-Sleep -Seconds 3
                Start-NetxChildren
                $failStreak = 0
                # Give API time to come up before counting failures again.
                Start-Sleep -Seconds ([Math]::Max(15, $HealthIntervalSec))
                continue
            }
        }
        Start-Sleep -Seconds $HealthIntervalSec
    }
} catch {
    Write-SvcLog "ERROR: $($_.Exception.Message)"
    throw
} finally {
    Write-SvcLog "stopping NetX"
    try { Stop-NetxChildren } catch { Write-SvcLog "stop warning: $($_.Exception.Message)" }
    Remove-Item -Force $stopFlag -ErrorAction SilentlyContinue
    Write-SvcLog "service_run exited"
}
