param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$KeepPostgres = $false,
    [switch]$KillTray = $false
)

$ErrorActionPreference = "Continue"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"
$map = @{}
if (Test-Path $envPath) {
    $map = Read-DotEnv -Path $envPath
}

$stopScript = Join-Path $prog "scripts\stop_netx.ps1"
if (-not (Test-Path $stopScript)) {
    $stopScript = Join-Path (Get-NetxRepoRoot) "scripts\stop_netx.ps1"
}
if (Test-Path $stopScript) {
    Write-Host "==> Stopping NetX processes"
    Start-NetxPowerShell -File $stopScript -Arguments @("-Force") `
        -WorkingDirectory $prog -WindowStyle Hidden -Wait | Out-Null
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

Write-Host "==> Stop done"
