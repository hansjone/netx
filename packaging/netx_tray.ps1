param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$StartOnLaunch = $true,
    [switch]$CheckUpdateOnLaunch = $false
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
if (Test-Path $envPath) {
    $map = Read-DotEnv -Path $envPath
    if ($map["NETX_HOST"]) { $hostBind = $map["NETX_HOST"] }
    if ($map["NETX_PORT"]) { try { $port = [int]$map["NETX_PORT"] } catch {} }
}
$uiUrl = "http://${hostBind}:${port}/"
$ver = Get-NetxVersion -ProgramRoot $prog

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
try {
    $notify.Icon = [System.Drawing.SystemIcons]::Application
} catch {}

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$miOpen = $menu.Items.Add("Open UI ($uiUrl)")
$miOpen.add_Click({ Start-Process $uiUrl })

$miStart = $menu.Items.Add("Start NetX")
$miStart.add_Click({
    $notify.ShowBalloonTip(3000, "NetX", "Starting…", [System.Windows.Forms.ToolTipIcon]::Info)
    Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser")
})

$miStop = $menu.Items.Add("Stop NetX")
$miStop.add_Click({
    $notify.ShowBalloonTip(3000, "NetX", "Stopping…", [System.Windows.Forms.ToolTipIcon]::Info)
    Invoke-NetxScript -File $stopPs1
})

$miUpdate = $menu.Items.Add("Check for updates")
$miUpdate.add_Click({
    try {
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $checkPs1,
            "-ProgramRoot", $prog, "-DataRoot", $data
        ) -Wait -PassThru -WindowStyle Normal
        if ($p.ExitCode -eq 10) {
            $ans = [System.Windows.Forms.MessageBox]::Show(
                "A newer NetX is available. Download and apply now?",
                "NetX update",
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
            [System.Windows.Forms.MessageBox]::Show("NetX is up to date ($ver).", "NetX update")
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show("Update check failed: $($_.Exception.Message)", "NetX")
    }
})

[void]$menu.Items.Add("-")

$miExit = $menu.Items.Add("Exit tray (services keep running)")
$miExit.add_Click({
    $notify.Visible = $false
    $form.Close()
    [System.Windows.Forms.Application]::Exit()
})

$notify.ContextMenuStrip = $menu
$notify.add_DoubleClick({ Start-Process $uiUrl })

$form.add_Shown({
    if ($StartOnLaunch) {
        Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser")
        Start-Sleep -Seconds 2
        try { Start-Process $uiUrl } catch {}
    }
    if ($CheckUpdateOnLaunch) {
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $checkPs1,
            "-ProgramRoot", $prog, "-DataRoot", $data, "-Quiet"
        ) -WindowStyle Hidden
    }
})

$form.add_FormClosing({
    $notify.Visible = $false
    $notify.Dispose()
})

[System.Windows.Forms.Application]::Run($form)
