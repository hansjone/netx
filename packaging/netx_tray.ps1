param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$StartOnLaunch = $true,
    [switch]$CheckUpdateOnLaunch = $false,
    [switch]$AutoUpdate = $false
)

# Simple system-tray controller for NetX (Windows packaged installs).
# Start / Stop / Open UI / Check update / Exit

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$hostBind = "127.0.0.1"
$port = 8890
$envPath = Join-Path $data ".env"
$map = @{}
if (Test-Path $envPath) {
    $map = Read-DotEnv -Path $envPath
    if ($map["NETX_HOST"]) { $hostBind = $map["NETX_HOST"] }
    if ($map["NETX_PORT"]) { try { $port = [int]$map["NETX_PORT"] } catch {} }
}
$autoVal = ""
if ($map["NETX_UPDATE_AUTO"]) { $autoVal = $map["NETX_UPDATE_AUTO"] }
elseif ($env:NETX_UPDATE_AUTO) { $autoVal = $env:NETX_UPDATE_AUTO }
if ($autoVal -match '^(1|true|yes|on)$') {
    $AutoUpdate = $true
    $CheckUpdateOnLaunch = $true
}
$uiUrl = "http://${hostBind}:${port}/"
$ver = Get-NetxVersion -ProgramRoot $prog
$zh = ([cultureinfo]::CurrentUICulture.Name -match '^(zh|zh-)')
function T([string]$En, [string]$Zh) {
    if ($zh) { return $Zh } else { return $En }
}

if (-not (Test-Path $envPath)) {
    [void][System.Windows.Forms.MessageBox]::Show(
        (T `
            "First-time setup incomplete (missing $envPath).`nRun Start Menu → NetX → First-time setup." `
            "尚未完成首次配置（缺少 $envPath）。`n请运行「开始菜单 → NetX → First-time setup」。"),
        "NetX",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    exit 1
}

$startPs1 = Join-Path $PSScriptRoot "start_netx_app.ps1"
$stopPs1 = Join-Path $PSScriptRoot "stop_netx_app.ps1"
$checkPs1 = Join-Path $PSScriptRoot "check_update.ps1"

function Invoke-NetxScript {
    param([string]$File, [string[]]$ExtraArgs = @())
    $args = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $File,
        "-ProgramRoot", $prog, "-DataRoot", $data
    ) + $ExtraArgs
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $prog -WindowStyle Hidden
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "NetX"
$form.ShowInTaskbar = $false
$form.WindowState = "Minimized"
$form.Visible = $false
$form.Size = New-Object System.Drawing.Size(0, 0)

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Text = "NetX $ver"
$notify.Visible = $true
$iconPath = Join-Path $PSScriptRoot "assets\netx.ico"
try {
    if (Test-Path $iconPath) {
        $notify.Icon = New-Object System.Drawing.Icon $iconPath
    } else {
        $notify.Icon = [System.Drawing.SystemIcons]::Application
    }
} catch {
    try { $notify.Icon = [System.Drawing.SystemIcons]::Application } catch {}
}

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$miOpen = $menu.Items.Add((T "Open UI ($uiUrl)" "打开界面 ($uiUrl)"))
$miOpen.add_Click({ Start-Process $uiUrl })

$miStart = $menu.Items.Add((T "Start NetX" "启动 NetX"))
$miStart.add_Click({
    $notify.ShowBalloonTip(3000, "NetX", (T "Starting…" "正在启动…"), [System.Windows.Forms.ToolTipIcon]::Info)
    Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser")
})

$miStop = $menu.Items.Add((T "Stop NetX" "停止 NetX"))
$miStop.add_Click({
    $notify.ShowBalloonTip(3000, "NetX", (T "Stopping…" "正在停止…"), [System.Windows.Forms.ToolTipIcon]::Info)
    Invoke-NetxScript -File $stopPs1
})

$miUpdate = $menu.Items.Add((T "Check for updates" "检查更新"))
$miUpdate.add_Click({
    try {
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $checkPs1,
            "-ProgramRoot", $prog, "-DataRoot", $data
        ) -Wait -PassThru -WindowStyle Normal
        if ($p.ExitCode -eq 10) {
            $ans = [System.Windows.Forms.MessageBox]::Show(
                (T "A newer NetX is available. Download and apply now?" "发现新版本，是否立即下载并更新？"),
                (T "NetX update" "NetX 更新"),
                [System.Windows.Forms.MessageBoxButtons]::YesNo,
                [System.Windows.Forms.MessageBoxIcon]::Question
            )
            if ($ans -eq [System.Windows.Forms.DialogResult]::Yes) {
                Start-Process -FilePath "powershell.exe" -ArgumentList @(
                    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $checkPs1,
                    "-ProgramRoot", $prog, "-DataRoot", $data, "-Apply"
                ) -WorkingDirectory $prog
            }
        } elseif ($p.ExitCode -eq 0) {
            [System.Windows.Forms.MessageBox]::Show((T "NetX is up to date ($ver)." "已是最新版本 ($ver)。"), (T "NetX update" "NetX 更新"))
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show((T "Update check failed: $($_.Exception.Message)" "检查更新失败：$($_.Exception.Message)"), "NetX")
    }
})

[void]$menu.Items.Add("-")

$miExit = $menu.Items.Add((T "Exit tray (services keep running)" "退出托盘（服务继续运行）"))
$miExit.add_Click({
    $notify.Visible = $false
    $form.Close()
    [System.Windows.Forms.Application]::Exit()
})

$notify.ContextMenuStrip = $menu
$notify.add_DoubleClick({ Start-Process $uiUrl })

$form.add_Shown({
    if ($AutoUpdate) {
        $notify.ShowBalloonTip(4000, "NetX", (T "Checking for updates…" "正在检查更新…"), [System.Windows.Forms.ToolTipIcon]::Info)
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $checkPs1,
            "-ProgramRoot", $prog, "-DataRoot", $data, "-Apply", "-Quiet", "-AutoOnly"
        ) -Wait -WindowStyle Hidden
    } elseif ($CheckUpdateOnLaunch) {
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $checkPs1,
            "-ProgramRoot", $prog, "-DataRoot", $data, "-Quiet"
        ) -WindowStyle Hidden
    }
    if ($StartOnLaunch) {
        Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser")
        Start-Sleep -Seconds 2
        try { Start-Process $uiUrl } catch {}
    }
})

$form.add_FormClosing({
    $notify.Visible = $false
    $notify.Dispose()
})

[System.Windows.Forms.Application]::Run($form)
