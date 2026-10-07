param(
    [ValidateSet("tray", "start", "stop", "setup", "update")]
    [string]$Action = "tray",
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$StartOnLaunch = $true
)

# Visible entrypoint for Start Menu / desktop shortcuts.
# Shows a message box on failure (explorer-launched PowerShell otherwise flashes and exits).

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

Add-Type -AssemblyName System.Windows.Forms

$zh = ([cultureinfo]::CurrentUICulture.Name -match '^(zh|zh-)')
function T([string]$En, [string]$Zh) {
    if ($zh) { return $Zh } else { return $En }
}

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$logDir = Join-Path $data "data\runtime"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}
$log = Join-Path $logDir "launch.log"

function Write-LaunchLog([string]$Msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
    Add-Content -Path $log -Value $line -Encoding utf8
}

function Show-NetxError([string]$Msg) {
    Write-LaunchLog "ERROR $Msg"
    [void][System.Windows.Forms.MessageBox]::Show(
        $Msg,
        "NetX",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    )
}

function Ensure-NetxEnv {
    $envPath = Join-Path $data ".env"
    if (Test-Path $envPath) { return }
    Write-LaunchLog "missing .env — running setup_first_run -NonInteractive -DbMode bundled"
    $setup = Join-Path $PSScriptRoot "setup_first_run.ps1"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setup `
        -ProgramRoot $prog -DataRoot $data -NonInteractive -DbMode bundled
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $envPath)) {
        throw (T `
            "First-time setup failed. Run Start Menu → NetX → First-time setup.`nLog: $log" `
            "首次配置失败。请运行「开始菜单 → NetX → First-time setup」。`n日志: $log")
    }
}

try {
    Write-LaunchLog "action=$Action prog=$prog data=$data"
    switch ($Action) {
        "setup" {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup_first_run.ps1") `
                -ProgramRoot $prog -DataRoot $data
            if ($LASTEXITCODE -ne 0) { throw "setup_exit_$LASTEXITCODE" }
        }
        "stop" {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop_netx_app.ps1") `
                -ProgramRoot $prog -DataRoot $data
        }
        "update" {
            Ensure-NetxEnv
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "check_update.ps1") `
                -ProgramRoot $prog -DataRoot $data
        }
        "start" {
            Ensure-NetxEnv
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start_netx_app.ps1") `
                -ProgramRoot $prog -DataRoot $data
            if ($LASTEXITCODE -ne 0) { throw "start_exit_$LASTEXITCODE" }
        }
        "tray" {
            Ensure-NetxEnv
            $trayArgs = @(
                "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", (Join-Path $PSScriptRoot "netx_tray.ps1"),
                "-ProgramRoot", $prog, "-DataRoot", $data
            )
            if ($StartOnLaunch) { $trayArgs += "-StartOnLaunch" }
            # Detach tray; keep this process short so Start Menu returns quickly.
            Start-Process -FilePath "powershell.exe" -ArgumentList $trayArgs -WorkingDirectory $prog -WindowStyle Hidden
            Start-Sleep -Milliseconds 800
            [void][System.Windows.Forms.MessageBox]::Show(
                (T `
                    "NetX tray started.`nLook for the NetX icon in the system tray (click ^ if hidden).`nUI: http://127.0.0.1:8890/" `
                    "NetX 托盘已启动。`n请在任务栏右下角找 NetX 图标（被藏起来时点 ^）。`n界面: http://127.0.0.1:8890/"),
                "NetX",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information
            )
        }
    }
    Write-LaunchLog "action=$Action ok"
} catch {
    Show-NetxError ((T "NetX failed to start:`n$($_.Exception.Message)" "NetX 启动失败：`n$($_.Exception.Message)") + "`n`n$log")
    exit 1
}
