param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = ""
)

# Fast, non-blocking uninstall prep for Inno [UninstallRun].
# Must return within a few seconds and never kill unins000.exe / _unins.tmp.

$ErrorActionPreference = "Continue"

$prog = if ($ProgramRoot) { $ProgramRoot } else { "C:\Program Files\NetX" }
$data = if ($DataRoot) { $DataRoot } else { Join-Path $env:ProgramData "NetX" }
$prog = ($prog -replace '/', '\').TrimEnd('\')
$data = ($data -replace '/', '\').TrimEnd('\')

function Test-Protected([string]$Name, [string]$Cmd) {
    if ($Name -match '^(unins\d*|_unins\.tmp|setup)\.exe$') { return $true }
    if ($Cmd -match 'unins\d*\.exe|_unins\.tmp|uninstall_prepare\.ps1|uninstall_delete_data\.ps1') { return $true }
    return $false
}

function Stop-Pid([int]$Id, [string]$Label) {
    if ($Id -le 4 -or $Id -eq $PID) { return }
    Write-Host "stop $Id $Label"
    try { Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue } catch {}
}

Write-Host "==> uninstall_prepare fast-stop prog=$prog"

$patterns = @(
    'netx_tray\.ps1',
    'netx_api\.(main|worker|biz_state_worker)',
    'start_netx_app\.ps1',
    'stop_netx_app\.ps1',
    'launch_shortcut\.ps1',
    [regex]::Escape((Join-Path $prog '.venv\Scripts\python.exe')),
    [regex]::Escape((Join-Path $prog 'postgres\pgsql\bin'))
)

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | ForEach-Object {
    $cl = [string]$_.CommandLine
    $name = [string]$_.Name
    if (-not $cl) { return }
    if (Test-Protected -Name $name -Cmd $cl) { return }
    foreach ($pat in $patterns) {
        if ($cl -match $pat) {
            Stop-Pid -Id ([int]$_.ProcessId) -Label $name
            break
        }
    }
}

# Best-effort postgres stop (no hang: immediate + ignore)
$pgCtl = Join-Path $prog "postgres\pgsql\bin\pg_ctl.exe"
$pgData = Join-Path $data "pgdata"
if ((Test-Path $pgCtl) -and (Test-Path $pgData)) {
    try {
        $p = Start-Process -FilePath $pgCtl -ArgumentList @('-D', $pgData, 'stop', '-m', 'immediate') `
            -WindowStyle Hidden -PassThru
        if (-not $p.WaitForExit(5000)) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    } catch {}
}

try {
    Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "NetX" -ErrorAction SilentlyContinue
} catch {}

Write-Host "==> uninstall_prepare done"
exit 0
