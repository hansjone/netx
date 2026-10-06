param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = ""
)

# Long-running supervisor for Windows Service / Task Scheduler.
# Starts NetX (and bundled PG), waits until stopped, then tears down.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$logDir = Join-Path $data "data\runtime"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$logFile = Join-Path $logDir "service_run.log"

function Write-SvcLog([string]$Msg) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
    Add-Content -Path $logFile -Value $line -Encoding utf8
    Write-Host $line
}

$stopFlag = Join-Path $logDir "service.stop"
Remove-Item -Force $stopFlag -ErrorAction SilentlyContinue

Write-SvcLog "service_run starting program=$prog data=$data"

$startPs1 = Join-Path $PSScriptRoot "start_netx_app.ps1"
$stopPs1 = Join-Path $PSScriptRoot "stop_netx_app.ps1"

try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startPs1 `
        -ProgramRoot $prog -DataRoot $data -SkipBrowser
    if ($LASTEXITCODE -ne 0) {
        throw "start_netx_app_failed exit=$LASTEXITCODE"
    }
    Write-SvcLog "NetX started; entering watch loop"

    while ($true) {
        if (Test-Path $stopFlag) {
            Write-SvcLog "stop flag detected"
            break
        }
        Start-Sleep -Seconds 5
    }
} catch {
    Write-SvcLog "ERROR: $($_.Exception.Message)"
    throw
} finally {
    Write-SvcLog "stopping NetX"
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopPs1 `
            -ProgramRoot $prog -DataRoot $data
    } catch {
        Write-SvcLog "stop warning: $($_.Exception.Message)"
    }
    Remove-Item -Force $stopFlag -ErrorAction SilentlyContinue
    Write-SvcLog "service_run exited"
}
