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
    # Never read the child's stdout: postgres / NetX processes it starts inherit the
    # pipe, so `& powershell.exe` with captured output blocks until they exit (forever).
    # Start-Process -Wait is no good either (it waits for the whole process tree).
    $p = Start-NetxPowerShell -File (Join-Path $PSScriptRoot $Name) `
        -Arguments (@("-ProgramRoot", $prog, "-DataRoot", $data) + $Extra) `
        -WorkingDirectory $prog -WindowStyle Hidden -PassThru
    $null = $p.Handle  # keep a handle so ExitCode is available after exit
    $p.WaitForExit()
    return $p.ExitCode
}

function Stop-NetxService {
    # Stop-Service waits forever on a service that will not stop; poll with a deadline
    # and kill the WinSW process tree as a last resort.
    $svc = Get-Service -Name "NetX" -ErrorAction SilentlyContinue
    if (-not $svc -or $svc.Status -eq "Stopped") { return }
    # Ask service_run.ps1 to exit ourselves rather than relying only on WinSW's stop hook.
    Write-NetxServiceStopFlag -DataRoot $data
    & sc.exe stop NetX | Out-Null
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        $svc.Refresh()
        if ($svc.Status -eq "Stopped") { return }
    }
    $wmi = Get-CimInstance Win32_Service -Filter "Name='NetX'" -ErrorAction SilentlyContinue
    if ($wmi -and $wmi.ProcessId) { & taskkill.exe /PID $wmi.ProcessId /T /F | Out-Null }
    $null = Invoke-NetxChildScript "stop_netx_app.ps1"
}

function Start-NetxService {
    # Don't block on SERVICE_RUNNING: callers wait for /health instead.
    $svc = Get-Service -Name "NetX" -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq "Stopped") { & sc.exe start NetX | Out-Null }
}

$runner = Get-NetxManagedRunner
switch ($runner) {
    "service" {
        if ($Action -ne "start") { Stop-NetxService }
        if ($Action -ne "stop") { Start-NetxService }
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
