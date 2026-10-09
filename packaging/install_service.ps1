param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [ValidateSet("winsw", "task")]
    [string]$Mode = "winsw",
    [switch]$Uninstall = $false,
    [switch]$Start = $false,
    # Offline-safe by default: use WinSW shipped in the package. Opt-in to download from GitHub.
    [switch]$AllowDownload = $false
)

# Install NetX as a Windows Service (WinSW) or as a SYSTEM startup Scheduled Task.
# Requires elevation for winsw / task modes that run as SYSTEM.
# WinSW binary is bundled at packaging\winsw\WinSW-x64.exe by build_release.ps1 — no network needed.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"
Assert-NetxAdminOrRelaunch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters -WindowStyle Normal

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$svcName = "NetX"
$winswDir = Join-Path $prog "packaging\winsw"
$winswExe = Join-Path $winswDir "NetX.exe"
$winswXml = Join-Path $winswDir "NetX.xml"
$cacheDir = Join-Path $PSScriptRoot "cache"

function Test-IsAdmin {
    return Test-NetxIsAdmin
}

function ConvertTo-WinSWPath([string]$Path) {
    return ($Path -replace '\\', '/')
}

function Ensure-WinSW {
    if (-not (Test-Path $winswDir)) {
        New-Item -ItemType Directory -Path $winswDir -Force | Out-Null
    }
    if (-not (Test-Path $cacheDir)) {
        New-Item -ItemType Directory -Path $cacheDir -Force | Out-Null
    }
    $bundled = Join-Path $PSScriptRoot "winsw\WinSW-x64.exe"
    $dl = Join-Path $cacheDir "WinSW-x64.exe"
    $src = $null
    if (Test-Path $bundled) {
        $src = $bundled
        Write-Host "==> Using bundled WinSW: $bundled"
    } elseif (Test-Path $dl) {
        $src = $dl
        Write-Host "==> Using cached WinSW: $dl"
    } elseif ($AllowDownload) {
        $url = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"
        Write-Host "==> Downloading WinSW: $url"
        Invoke-WebRequest -Uri $url -OutFile $dl -UseBasicParsing
        $src = $dl
    } else {
        throw "winsw_missing_offline: expected $bundled (rebuild with build_release.ps1). Or pass -AllowDownload when online."
    }
    Copy-Item -Path $src -Destination $winswExe -Force

    $runPs1 = ConvertTo-WinSWPath (Join-Path $PSScriptRoot "service_run.ps1")
    $stopPs1 = ConvertTo-WinSWPath (Join-Path $PSScriptRoot "stop_netx_app.ps1")
    $progFs = ConvertTo-WinSWPath $prog
    $dataFs = ConvertTo-WinSWPath $data
    $logPath = ConvertTo-WinSWPath (Join-Path $data "data\runtime")
    if (-not (Test-Path (Join-Path $data "data\runtime"))) {
        New-Item -ItemType Directory -Path (Join-Path $data "data\runtime") -Force | Out-Null
    }

    $xml = @"
<service>
  <id>$svcName</id>
  <name>NetX Ops</name>
  <description>NetX API, workers, and optional bundled PostgreSQL</description>
  <executable>powershell.exe</executable>
  <arguments>-NoProfile -ExecutionPolicy Bypass -File "$runPs1" -ProgramRoot "$progFs" -DataRoot "$dataFs"</arguments>
  <logpath>$logPath</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>4</keepFiles>
  </log>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>60sec</stoptimeout>
  <stopexecutable>powershell.exe</stopexecutable>
  <stoparguments>-NoProfile -ExecutionPolicy Bypass -File "$stopPs1" -ProgramRoot "$progFs" -DataRoot "$dataFs"</stoparguments>
  <workingdirectory>$progFs</workingdirectory>
</service>
"@
    # WinSW expects UTF-8 without BOM issues; ASCII-compatible paths preferred.
    [System.IO.File]::WriteAllText($winswXml, $xml)
}

if ($Uninstall) {
    if (-not (Test-IsAdmin)) { throw "admin_required_for_uninstall" }
    if (Test-Path $winswExe) {
        Write-Host "==> Stopping/uninstalling WinSW service"
        & $winswExe stop 2>$null
        & $winswExe uninstall 2>$null
    }
    Unregister-ScheduledTask -TaskName "NetXService" -TaskPath "\NetX\" -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "==> Service/task removed"
    exit 0
}

if (-not (Test-IsAdmin)) {
    throw "admin_required: run elevated PowerShell to install the NetX service"
}

if ($Mode -eq "winsw") {
    Ensure-WinSW
    Write-Host "==> Installing Windows Service via WinSW"
    & $winswExe stop 2>$null
    & $winswExe uninstall 2>$null
    & $winswExe install
    if ($LASTEXITCODE -ne 0) { throw "winsw_install_failed" }
    if ($Start) {
        & $winswExe start
        Write-Host "==> Service started" -ForegroundColor Green
    } else {
        Write-Host "==> Installed. Start with: Start-Service NetX   or   `"$winswExe`" start"
    }
} else {
    Write-Host "==> Registering Scheduled Task NetX\NetXService (At startup, SYSTEM)"
    $runPs1 = Join-Path $PSScriptRoot "service_run.ps1"
    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$runPs1`" -ProgramRoot `"$prog`" -DataRoot `"$data`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arg -WorkingDirectory $prog
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName "NetXService" -TaskPath "\NetX\" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    if ($Start) {
        Start-ScheduledTask -TaskName "NetXService" -TaskPath "\NetX\"
        Write-Host "==> Task started" -ForegroundColor Green
    } else {
        Write-Host "==> Task registered. Start with: Start-ScheduledTask -TaskPath '\NetX\' -TaskName NetXService"
    }
}
