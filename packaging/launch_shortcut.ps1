param(
    [ValidateSet("tray", "start", "stop", "setup", "update")]
    [string]$Action = "tray",
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$StartOnLaunch = $true
)

# Visible entrypoint for Start Menu / desktop shortcuts.
# Nested PowerShell runs are hidden; only failures show a message box.

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

function Invoke-NetxHiddenPs1 {
    param(
        [string]$File,
        [string[]]$ExtraArgs = @(),
        [switch]$NoWait = $false
    )
    return Start-NetxPowerShell -File $File `
        -Arguments (@("-ProgramRoot", $prog, "-DataRoot", $data) + $ExtraArgs) `
        -WorkingDirectory $prog -WindowStyle Hidden -PassThru -Wait:(-not $NoWait)
}

function Ensure-NetxEnv {
    $envPath = Join-Path $data ".env"
    if (Test-Path $envPath) { return }
    # Do not silently force bundled DB — external-DB users must run interactive First-time setup.
    $cmd = Join-Path $prog "NetX-FirstRun.cmd"
    throw (T `
        "First-time setup not done (missing $envPath).`nDouble-click desktop/Start Menu: First-time setup`nOr run: $cmd`nThere you can choose built-in or external PostgreSQL." `
        "尚未完成首次配置（缺少 $envPath）。`n请双击桌面/开始菜单的「首次配置」`n或运行: $cmd`n在向导里可选内置或外置 PostgreSQL。")
}

try {
    Write-LaunchLog "action=$Action prog=$prog data=$data"
    switch ($Action) {
        "setup" {
            # Visible console so user can pick bundled vs external DB.
            $p = Start-NetxPowerShell -File (Join-Path $PSScriptRoot "setup_first_run.ps1") `
                -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data) `
                -WorkingDirectory $prog -WindowStyle Normal -Wait -PassThru
            if ($p.ExitCode -ne 0) { throw "setup_exit_$($p.ExitCode)" }
            if (Test-Path (Join-Path $data ".env")) {
                Start-NetxPowerShell -File (Join-Path $PSScriptRoot "netx_tray.ps1") `
                    -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data, "-StartOnLaunch") `
                    -WorkingDirectory $prog -WindowStyle Hidden | Out-Null
            }
        }
        "stop" {
            $p = Invoke-NetxHiddenPs1 -File (Join-Path $PSScriptRoot "stop_netx_app.ps1")
            if ($null -ne $p.ExitCode -and $p.ExitCode -ne 0) { throw "stop_exit_$($p.ExitCode)" }
        }
        "update" {
            Ensure-NetxEnv
            # Update UI may need a visible window for prompts.
            $p = Start-NetxPowerShell -File (Join-Path $PSScriptRoot "check_update.ps1") `
                -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data) `
                -WorkingDirectory $prog -WindowStyle Normal -Wait -PassThru
            if ($null -ne $p.ExitCode -and $p.ExitCode -ne 0 -and $p.ExitCode -ne 10) {
                throw "update_exit_$($p.ExitCode)"
            }
        }
        "start" {
            Ensure-NetxEnv
            $hostBind = "127.0.0.1"
            $port = 8890
            $envPath = Join-Path $data ".env"
            if (Test-Path $envPath) {
                $map = Read-DotEnv -Path $envPath
                if ($map["NETX_HOST"]) { $hostBind = $map["NETX_HOST"] }
                if ($map["NETX_PORT"]) { try { $port = [int]$map["NETX_PORT"] } catch {} }
            }
            $p = Invoke-NetxHiddenPs1 -File (Join-Path $PSScriptRoot "start_netx_app.ps1") -ExtraArgs @("-SkipBrowser")
            if ($p.ExitCode -ne 0) { throw "start_exit_$($p.ExitCode)" }
            if (Wait-NetxApiHealthy -HostName $hostBind -Port $port -TimeoutSec 180) {
                try { Start-Process "http://${hostBind}:${port}/" } catch {}
            } else {
                throw (T `
                    "NetX started but /health not ready within 180s.`nSee: $logDir\netx.err.log" `
                    "NetX 已启动但 /health 在 180 秒内未就绪。`n请查看: $logDir\netx.err.log")
            }
        }
        "tray" {
            Ensure-NetxEnv
            $extra = @("-ProgramRoot", $prog, "-DataRoot", $data)
            if ($StartOnLaunch) { $extra += "-StartOnLaunch" }
            # Detach tray; do not leave a success MessageBox (keeps a console open).
            Start-NetxPowerShell -File (Join-Path $PSScriptRoot "netx_tray.ps1") `
                -Arguments $extra -WorkingDirectory $prog -WindowStyle Hidden | Out-Null
        }
    }
    Write-LaunchLog "action=$Action ok"
} catch {
    Show-NetxError ((T "NetX failed to start:`n$($_.Exception.Message)" "NetX 启动失败：`n$($_.Exception.Message)") + "`n`n$log")
    exit 1
}
