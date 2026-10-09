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

$arg = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$tray`" -ProgramRoot `"$prog`" -DataRoot `"$data`" -StartOnLaunch"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $prog
$trig = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath `
    -Action $action -Trigger $trig -Settings $settings -Principal $principal -Force | Out-Null

Write-Host "==> NetX tray will start at logon (Scheduled Task, RunLevel Highest)" -ForegroundColor Green
Write-Host "    $taskPath$taskName"
Write-Host "    $arg"
