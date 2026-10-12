param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('StopForUpdate', 'ResumeAfterUpdate', 'RemoveForUninstall')]
    [string]$Action,
    [string]$ProgramRoot = '',
    [string]$DataRoot = ''
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_common.ps1"
$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$runtime = Join-Path $data 'data\runtime'
$null = New-Item -ItemType Directory -Path $runtime -Force
$stateFile = Join-Path $runtime 'service-update-state.json'
$logFile = Join-Path $runtime 'service_lifecycle.log'

function Write-LifecycleLog([string]$Message) {
    Add-Content -LiteralPath $logFile -Value "$(Get-Date -Format o) $Message" -Encoding utf8
}

function Get-NetxTask {
    try { return Get-ScheduledTask -TaskName 'NetXService' -TaskPath '\NetX\' -ErrorAction Stop }
    catch { return $null }
}

function Stop-NetxRunners([switch]$Remove) {
    $state = @{ serviceWasRunning = $false; taskWasRunning = $false }
    $svc = Get-Service -Name 'NetX' -ErrorAction SilentlyContinue
    if ($svc) {
        $state.serviceWasRunning = ($svc.Status -ne 'Stopped')
        $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
        if ($svc.Status -ne 'Stopped') {
            Write-LifecycleLog 'Stopping Windows service NetX'
            Stop-Service -Name 'NetX' -Force -ErrorAction Stop
            (Get-Service -Name 'NetX').WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
        }
    }
    $task = Get-NetxTask
    if ($task) {
        $state.taskWasRunning = ([string]$task.State -eq 'Running')
        $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
        if ($state.taskWasRunning) {
            Write-LifecycleLog 'Stopping scheduled task NetXService'
            Stop-ScheduledTask -TaskName 'NetXService' -TaskPath '\NetX\' -ErrorAction Stop
            $deadline = (Get-Date).AddSeconds(60)
            do {
                Start-Sleep -Milliseconds 500
                $task = Get-NetxTask
            } while ($task -and [string]$task.State -eq 'Running' -and (Get-Date) -lt $deadline)
            if ($task -and [string]$task.State -eq 'Running') { throw 'NetXService scheduled task did not stop within 60 seconds.' }
        }
    }
    if ($Remove) {
        if ($svc) {
            $winsw = Join-Path $prog 'packaging\winsw\NetX.exe'
            if (Test-Path -LiteralPath $winsw) {
                Write-LifecycleLog 'Uninstalling WinSW service'
                & $winsw uninstall
                if ($LASTEXITCODE -ne 0) { throw "WinSW uninstall failed: $LASTEXITCODE" }
            } else {
                throw "NetX service exists but WinSW executable is missing: $winsw"
            }
        }
        if ($task) {
            Unregister-ScheduledTask -TaskName 'NetXService' -TaskPath '\NetX\' -Confirm:$false -ErrorAction Stop
        }
    }
    return $state
}

try {
    switch ($Action) {
        'StopForUpdate' {
            $state = Stop-NetxRunners
            $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
            $stopScript = Join-Path $prog 'packaging\stop_netx_app.ps1'
            if (Test-Path -LiteralPath $stopScript) {
                & powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $stopScript `
                    -ProgramRoot $prog -DataRoot $data -KillTray
                if ($LASTEXITCODE -ne 0) { throw "NetX application stop failed: $LASTEXITCODE" }
            }
        }
        'ResumeAfterUpdate' {
            if (-not (Test-Path -LiteralPath $stateFile)) { break }
            $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
            if ($state.serviceWasRunning) {
                Write-LifecycleLog 'Restarting Windows service NetX after update'
                Start-Service -Name 'NetX' -ErrorAction Stop
            }
            if ($state.taskWasRunning) {
                Write-LifecycleLog 'Restarting scheduled task NetXService after update'
                Start-ScheduledTask -TaskName 'NetXService' -TaskPath '\NetX\' -ErrorAction Stop
            }
            Remove-Item -LiteralPath $stateFile -Force
        }
        'RemoveForUninstall' {
            $null = Stop-NetxRunners -Remove
            $stopScript = Join-Path $prog 'packaging\stop_netx_app.ps1'
            if (Test-Path -LiteralPath $stopScript) {
                & powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $stopScript `
                    -ProgramRoot $prog -DataRoot $data -KillTray
                if ($LASTEXITCODE -ne 0) { throw "NetX application stop failed: $LASTEXITCODE" }
            }
        }
    }
    Write-LifecycleLog "Action completed: $Action"
    exit 0
} catch {
    Write-LifecycleLog "Action failed: $Action; $($_.Exception.Message)"
    if ($Action -eq 'StopForUpdate' -and (Test-Path -LiteralPath $stateFile)) {
        try {
            $prior = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
            if ($prior.serviceWasRunning) { Start-Service -Name 'NetX' -ErrorAction Stop }
            if ($prior.taskWasRunning) { Start-ScheduledTask -TaskName 'NetXService' -TaskPath '\NetX\' -ErrorAction Stop }
            Remove-Item -LiteralPath $stateFile -Force
            Write-LifecycleLog 'Restored service/task after update stop failure'
        } catch {
            Write-LifecycleLog "Could not restore service/task after update stop failure: $($_.Exception.Message)"
        }
    }
    Write-Error $_
    exit 1
}
