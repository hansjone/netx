param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [ValidateSet("Daily", "AtLogOn", "Hourly")]
    [string]$Trigger = "Daily",
    [string]$At = "03:30",
    [switch]$Remove = $false,
    [switch]$EnableAutoEnv = $true
)

# Schedule silent update checks. With NETX_UPDATE_AUTO=true, applies updates when available.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$taskName = "NetXUpdate"
$taskPath = "\NetX\"
$checkPs1 = Join-Path $PSScriptRoot "check_update.ps1"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "==> Update task removed"
    exit 0
}

if ($EnableAutoEnv) {
    $envFile = Join-Path $data ".env"
    if (-not (Test-Path $data)) {
        New-Item -ItemType Directory -Path $data -Force | Out-Null
    }
    Write-DotEnvValue -Path $envFile -Values @{ "NETX_UPDATE_AUTO" = "true" }
    Write-Host "==> Set NETX_UPDATE_AUTO=true in $envFile"
}

$arg = "-NoProfile -ExecutionPolicy Bypass -File `"$checkPs1`" -ProgramRoot `"$prog`" -DataRoot `"$data`" -Apply -Quiet -AutoOnly"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $prog

switch ($Trigger) {
    "Hourly" {
        $trig = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1) `
            -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration ([TimeSpan]::MaxValue)
    }
    "AtLogOn" {
        $trig = New-ScheduledTaskTrigger -AtLogOn
    }
    default {
        $trig = New-ScheduledTaskTrigger -Daily -At $At
    }
}

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -TaskPath $taskPath -Action $action -Trigger $trig -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "==> Scheduled update task: $taskPath$taskName ($Trigger)" -ForegroundColor Green
Write-Host "    Manual run: .\packaging\check_update.ps1 -Apply -Quiet -AutoOnly"
