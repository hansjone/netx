param(
    [string]$ProgramRoot = "",
    [switch]$Remove = $false
)

# Register / unregister NetX tray at current-user logon.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$tray = Join-Path $PSScriptRoot "netx_tray.ps1"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$name = "NetX"

if ($Remove) {
    Remove-ItemProperty -Path $runKey -Name $name -ErrorAction SilentlyContinue
    Write-Host "==> Removed NetX from startup"
    exit 0
}

$cmd = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$tray`" -ProgramRoot `"$prog`" -StartOnLaunch"
New-ItemProperty -Path $runKey -Name $name -Value $cmd -PropertyType String -Force | Out-Null
Write-Host "==> NetX tray will start at logon"
Write-Host "    $cmd"
