param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$KeepPostgres = $false,
    [switch]$KillTray = $false,
    # Used by service_run.ps1 when it restarts NetX itself: do not ask it to exit.
    [switch]$KeepSupervisor = $false
)

$ErrorActionPreference = "Continue"
# Timestamped trace (service stop path is hard to observe otherwise).
$script:stopTrace = $null
function Write-StopTrace([string]$Msg) {
    if (-not $script:stopTrace) { return }
    try { Add-Content -Path $script:stopTrace -Value ("{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss.fff"), $PID, $Msg) -Encoding utf8 } catch {}
}
if ($DataRoot) {
    $traceDir = Join-Path $DataRoot "data\runtime\service"
    if (Test-Path $traceDir) { $script:stopTrace = Join-Path $traceDir "stop_netx_app.log" }
}
Write-StopTrace "begin user=$([Environment]::UserName) keepSupervisor=$KeepSupervisor"
. "$PSScriptRoot\_common.ps1"
Assert-NetxAdminOrRelaunch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters -WindowStyle Hidden

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"
$map = @{}
if (Test-Path $envPath) {
    $map = Read-DotEnv -Path $envPath
}

# Tell the service supervisor (service_run.ps1) to exit first. WinSW runs this script as
# <stopexecutable> and then waits for the supervisor to exit; without the flag it kept
# looping, restarted NetX, and the service never stopped.
if (-not $KeepSupervisor) {
    Write-NetxServiceStopFlag -DataRoot $data
    Write-StopTrace "stop flag written"
}

$stopScript = Join-Path $prog "scripts\stop_netx.ps1"
if (-not (Test-Path $stopScript)) {
    $stopScript = Join-Path (Get-NetxRepoRoot) "scripts\stop_netx.ps1"
}
if (Test-Path $stopScript) {
    Write-Host "==> Stopping NetX processes"
    Write-StopTrace "stop_netx.ps1 begin"
    Start-NetxPowerShell -File $stopScript -Arguments @("-Force") `
        -WorkingDirectory $prog -WindowStyle Hidden -Wait | Out-Null
    Write-StopTrace "stop_netx.ps1 end"
} else {
    Write-Host "[WARN] stop_netx.ps1 not found"
}

if ($KillTray) {
    Write-Host "==> Stopping NetX tray icons"
    Stop-NetxTrayProcesses -ProgramRoot $prog
}

$mode = Get-DbMode -EnvMap $map
if ($mode -eq "bundled" -and -not $KeepPostgres) {
    $pgBinCandidates = @(
        (Join-Path $prog "postgres\pgsql\bin\pg_ctl.exe"),
        (Join-Path $PSScriptRoot "postgres\pgsql\bin\pg_ctl.exe")
    )
    $pgCtl = $null
    foreach ($c in $pgBinCandidates) {
        if (Test-Path $c) { $pgCtl = $c; break }
    }
    $pgData = if ($map["NETX_BUNDLED_PG_DATA_DIR"]) { $map["NETX_BUNDLED_PG_DATA_DIR"] } else { Join-Path $data "pgdata" }
    if ($pgCtl -and (Test-Path $pgData)) {
        Write-Host "==> Stopping bundled PostgreSQL"
        & $pgCtl -D $pgData stop -m fast
    }
}

Write-StopTrace "done"
Write-Host "==> Stop done"
