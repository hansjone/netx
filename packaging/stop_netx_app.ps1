param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$KeepPostgres = $false
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
    & powershell -ExecutionPolicy Bypass -File $stopScript -Force
} else {
    Write-Host "[WARN] stop_netx.ps1 not found"
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
