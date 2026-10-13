param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$Remove = $false
)

# Register / unregister NetX tray at logon with highest privileges (UAC admin).
# HKCU\Run cannot elevate reliably on fresh PCs; use a Scheduled Task instead.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"
Assert-NetxAdminOrRelaunch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters -WindowStyle Normal

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$tray = Join-Path $PSScriptRoot "netx_tray.ps1"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$legacyName = "NetX"
$taskName = "NetXTray"
$taskPath = "\NetX\"

# Always clear legacy per-user Run key (non-elevated).
Remove-ItemProperty -Path $runKey -Name $legacyName -ErrorAction SilentlyContinue

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "==> Removed NetX tray autostart task"
    exit 0
}

# -QuietStart: bring NetX up at logon without popping a browser window.
$arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$tray`" -ProgramRoot `"$prog`" -DataRoot `"$data`" -StartOnLaunch -QuietStart"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $prog
# Any user logon, run for the local Administrators group: Setup runs elevated
# (often as a different admin account), so $env:USERNAME is not the desktop user.
$trig = New-ScheduledTaskTrigger -AtLogOn
$trig.Delay = "PT10S"
$admins = (New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")).Translate(
    [System.Security.Principal.NTAccount]).Value
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances Parallel
$principal = New-ScheduledTaskPrincipal -GroupId $admins -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath `
    -Action $action -Trigger $trig -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "==> NetX tray will start at logon (Scheduled Task, RunLevel Highest)" -ForegroundColor Green
Write-Host "    $taskPath$taskName"
Write-Host "    $arg"
