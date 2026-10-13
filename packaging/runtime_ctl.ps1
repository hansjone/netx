param(
    [ValidateSet("start", "stop", "restart")]
    [string]$Action = "start",
    [string]$ProgramRoot = "",
    [string]$DataRoot = ""
)

# Single start/stop/restart entry for tray + shortcuts. Routes through the
# Windows service / SYSTEM task when one is installed, else the plain scripts.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"
Assert-NetxAdminOrRelaunch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters -WindowStyle Hidden

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot

function Invoke-NetxChildScript([string]$Name, [string[]]$Extra = @()) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot $Name) `
        -ProgramRoot $prog -DataRoot $data @Extra
    return $LASTEXITCODE
}

$runner = Get-NetxManagedRunner
switch ($runner) {
    "service" {
        switch ($Action) {
            "start" { Start-Service -Name "NetX" }
            "stop" { Stop-Service -Name "NetX" -Force }
            "restart" { Restart-Service -Name "NetX" -Force }
        }
    }
    "task" {
        if ($Action -ne "start") {
            Stop-ScheduledTask -TaskName "NetXService" -TaskPath "\NetX\"
            $null = Invoke-NetxChildScript "stop_netx_app.ps1"
        }
        if ($Action -ne "stop") {
            Start-ScheduledTask -TaskName "NetXService" -TaskPath "\NetX\"
        }
    }
    default {
        if ($Action -ne "start") {
            $null = Invoke-NetxChildScript "stop_netx_app.ps1"
        }
        if ($Action -ne "stop") {
            $code = Invoke-NetxChildScript "start_netx_app.ps1" @("-SkipBrowser")
            if ($code) { exit $code }
        }
    }
}
exit 0
