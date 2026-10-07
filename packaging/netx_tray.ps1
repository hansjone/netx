param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$StartOnLaunch = $true,
    [switch]$CheckUpdateOnLaunch = $false,
    [switch]$AutoUpdate = $false
)

# System-tray controller for NetX (Windows packaged installs).

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Custom tray menu: hover/selected highlight + flat light chrome (default OS menu looks dead).
if (-not ("NetxMenuRenderer" -as [type])) {
    Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

public sealed class NetxMenuColorTable : ProfessionalColorTable {
    static readonly Color Hover = Color.FromArgb(232, 240, 252);
    static readonly Color Press = Color.FromArgb(214, 228, 248);
    static readonly Color Border = Color.FromArgb(170, 198, 240);
    static readonly Color Edge = Color.FromArgb(210, 214, 220);
    static readonly Color Sep = Color.FromArgb(228, 230, 235);
    static readonly Color Bg = Color.FromArgb(252, 252, 253);

    public override Color MenuItemSelected { get { return Hover; } }
    public override Color MenuItemSelectedGradientBegin { get { return Hover; } }
    public override Color MenuItemSelectedGradientEnd { get { return Hover; } }
    public override Color MenuItemBorder { get { return Border; } }
    public override Color MenuItemPressedGradientBegin { get { return Press; } }
    public override Color MenuItemPressedGradientMiddle { get { return Press; } }
    public override Color MenuItemPressedGradientEnd { get { return Press; } }
    public override Color MenuBorder { get { return Edge; } }
    public override Color ToolStripDropDownBackground { get { return Bg; } }
    public override Color ImageMarginGradientBegin { get { return Bg; } }
    public override Color ImageMarginGradientMiddle { get { return Bg; } }
    public override Color ImageMarginGradientEnd { get { return Bg; } }
    public override Color SeparatorDark { get { return Sep; } }
    public override Color SeparatorLight { get { return Sep; } }
}

public sealed class NetxMenuRenderer : ToolStripProfessionalRenderer {
    public NetxMenuRenderer() : base(new NetxMenuColorTable()) {
        RoundedEdges = false;
    }

    protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e) {
        ToolStripMenuItem item = e.Item as ToolStripMenuItem;
        if (item == null || !item.Selected || !item.Enabled) {
            base.OnRenderMenuItemBackground(e);
            return;
        }
        Rectangle r = new Rectangle(2, 0, e.Item.Width - 4, e.Item.Height);
        using (SolidBrush brush = new SolidBrush(Color.FromArgb(232, 240, 252)))
        using (Pen pen = new Pen(Color.FromArgb(170, 198, 240))) {
            e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
            e.Graphics.FillRectangle(brush, r);
            e.Graphics.DrawRectangle(pen, r.X, r.Y, r.Width - 1, r.Height - 1);
        }
    }

    protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e) {
        Rectangle r = new Rectangle(0, 0, e.AffectedBounds.Width - 1, e.AffectedBounds.Height - 1);
        using (Pen pen = new Pen(Color.FromArgb(210, 214, 220))) {
            e.Graphics.DrawRectangle(pen, r);
        }
    }

    protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e) {
        int y = e.Item.Height / 2;
        using (Pen pen = new Pen(Color.FromArgb(228, 230, 235))) {
            e.Graphics.DrawLine(pen, 10, y, e.Item.Width - 10, y);
        }
    }
}
"@
}

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
$script:ver = Get-NetxVersion -ProgramRoot $prog
$ver = $script:ver
$dbMode = Get-DbMode -EnvMap $map
$dbModeLabel = if ($dbMode -eq "bundled") {
    if (([cultureinfo]::CurrentUICulture.Name -match '^(zh|zh-)')) { "内置库" } else { "built-in DB" }
} else {
    if (([cultureinfo]::CurrentUICulture.Name -match '^(zh|zh-)')) { "外置库" } else { "external DB" }
}
$zh = ([cultureinfo]::CurrentUICulture.Name -match '^(zh|zh-)')
function T([string]$En, [string]$Zh) {
    if ($zh) { return $Zh } else { return $En }
}

if (-not (Test-Path $envPath)) {
    [void][System.Windows.Forms.MessageBox]::Show(
        (T `
            "NetX is not configured yet (missing $envPath).`nRe-run the installer and choose built-in or external DB,`nor use Start Menu → NetX → Reconfigure database." `
            "尚未完成数据库配置（缺少 $envPath）。`n请重新运行安装向导并选择内置或外置数据库，`n或使用「开始菜单 → NetX → 重新配置数据库」。"),
        "NetX",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    exit 1
}

# Only one tray icon per machine session (desktop + postinstall + autostart can race).
$script:netxTrayMutex = $null
try {
    $createdNew = $false
    $script:netxTrayMutex = New-Object System.Threading.Mutex($true, "Local\NetX.WinTray.SingleInstance", [ref]$createdNew)
    if (-not $createdNew) {
        if (Test-NetxApiHealthy -HostName $hostBind -Port $port -TimeoutSec 2) {
            try { Start-Process $uiUrl } catch {}
        } elseif ($StartOnLaunch) {
            Start-NetxPowerShell -File (Join-Path $PSScriptRoot "start_netx_app.ps1") `
                -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data, "-SkipBrowser") `
                -WorkingDirectory $prog -WindowStyle Hidden | Out-Null
        }
        exit 0
    }
} catch {
    # If mutex fails, continue with a tray (better than no UI control).
}

$startPs1 = Join-Path $PSScriptRoot "start_netx_app.ps1"
$stopPs1 = Join-Path $PSScriptRoot "stop_netx_app.ps1"
$checkPs1 = Join-Path $PSScriptRoot "check_update.ps1"
$setupPs1 = Join-Path $PSScriptRoot "setup_first_run.ps1"

function Invoke-NetxScript {
    param(
        [string]$File,
        [string[]]$ExtraArgs = @(),
        [switch]$Wait = $false
    )
    return Start-NetxPowerShell -File $File -Arguments (@("-ProgramRoot", $prog, "-DataRoot", $data) + $ExtraArgs) `
        -WorkingDirectory $prog -WindowStyle Hidden -PassThru -Wait:$Wait
}

function Update-NetxTrayTip {
    # Refresh version after in-place updates (tray process may outlive version.json).
    try {
        $nowVer = Get-NetxVersion -ProgramRoot $prog
        if ($nowVer -and $nowVer -ne $script:ver) {
            $script:ver = $nowVer
            if ($null -ne $script:miHeader) { $script:miHeader.Text = "NetX  $script:ver" }
        }
    } catch {}
    $running = Test-NetxApiHealthy -HostName $hostBind -Port $port -TimeoutSec 1
    $state = if ($running) {
        (T "Running" "运行中")
    } else {
        (T "Stopped" "已停止")
    }
    $v = $script:ver
    $tip = "NetX $v  ·  $state  ·  $dbModeLabel`n$uiUrl"
    if ($tip.Length -gt 63) {
        # NotifyIcon.Text max ~63 chars on older Windows.
        $tip = "NetX $v · $state · $dbModeLabel"
    }
    $notify.Text = $tip
}

function Close-NetxTrayMenu {
    # ContextMenuStrip stays modal if Click handlers block; close before dialogs/waits.
    try { $menu.Close() } catch {}
    try { [System.Windows.Forms.Application]::DoEvents() } catch {}
}

function Start-NetxTrayDeferred {
    param([scriptblock]$Work)
    Close-NetxTrayMenu
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 50
    $script:netxTrayDeferredWork = $Work
    $timer.add_Tick({
        $t = $script:netxTrayDeferredTimer
        try { if ($t) { $t.Stop(); $t.Dispose() } } catch {}
        $script:netxTrayDeferredTimer = $null
        $w = $script:netxTrayDeferredWork
        $script:netxTrayDeferredWork = $null
        if ($w) { & $w }
    })
    $script:netxTrayDeferredTimer = $timer
    $timer.Start()
}

function Start-NetxTrayWatchProcess {
    # Never Start-Process -Wait on the WinForms UI thread — it freezes the tray menu.
    param(
        [Parameter(Mandatory = $true)]$Process,
        [scriptblock]$OnExit
    )
    if ($null -eq $Process) {
        if ($OnExit) { & $OnExit $null }
        return
    }
    $script:netxWatchProc = $Process
    $script:netxWatchOnExit = $OnExit
    if ($script:netxWatchTimer) {
        try { $script:netxWatchTimer.Stop(); $script:netxWatchTimer.Dispose() } catch {}
    }
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 400
    $timer.add_Tick({
        $p = $script:netxWatchProc
        if ($null -eq $p) {
            try { $script:netxWatchTimer.Stop(); $script:netxWatchTimer.Dispose() } catch {}
            $script:netxWatchTimer = $null
            return
        }
        $done = $false
        try { $done = [bool]$p.HasExited } catch { $done = $true }
        if (-not $done) { return }
        try { $script:netxWatchTimer.Stop(); $script:netxWatchTimer.Dispose() } catch {}
        $script:netxWatchTimer = $null
        $cb = $script:netxWatchOnExit
        $script:netxWatchOnExit = $null
        $script:netxWatchProc = $null
        if ($cb) { & $cb $p }
    })
    $script:netxWatchTimer = $timer
    $timer.Start()
}

function Restart-NetxTraySelf {
    # Fresh process picks up new version.json / scripts after in-place update.
    try {
        $notify.ShowBalloonTip(
            2500, "NetX",
            (T "Restarting tray…" "正在重启托盘…"),
            [System.Windows.Forms.ToolTipIcon]::Info
        )
    } catch {}
    $trayFile = Join-Path $PSScriptRoot "netx_tray.ps1"
    $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$trayFile`" -ProgramRoot `"$prog`" -DataRoot `"$data`""
    try {
        Start-Process -FilePath "powershell.exe" -ArgumentList $arg -WindowStyle Hidden | Out-Null
    } catch {}
    try { $notify.Visible = $false } catch {}
    try { $form.Close() } catch {}
    [System.Windows.Forms.Application]::Exit()
}

function Test-NetxSetupCancelled {
    param([AllowNull()][Nullable[int]]$ExitCode)
    if ($null -eq $ExitCode) { return $false }
    # STATUS_CONTROL_C_EXIT (0xC000013A): console closed / Ctrl+C — not a setup failure.
    return ([int]$ExitCode -eq -1073741510)
}

function Complete-NetxTrayReconfig {
    # Reload .env after reconfigure (host/port/mode may change).
    if (Test-Path $envPath) {
        $map = Read-DotEnv -Path $envPath
        if ($map["NETX_HOST"]) { $script:hostBind = $map["NETX_HOST"]; $hostBind = $script:hostBind }
        if ($map["NETX_PORT"]) {
            try {
                $script:port = [int]$map["NETX_PORT"]
                $port = $script:port
            } catch {}
        }
        $script:uiUrl = "http://${hostBind}:${port}/"
        $uiUrl = $script:uiUrl
        $dbMode = Get-DbMode -EnvMap $map
        $script:dbModeLabel = if ($dbMode -eq "bundled") {
            (T "built-in DB" "内置库")
        } else {
            (T "external DB" "外置库")
        }
        $dbModeLabel = $script:dbModeLabel
        if ($miSub) { $miSub.Text = "$dbModeLabel  ·  ${hostBind}:$port" }
    }

    $restart = [System.Windows.Forms.MessageBox]::Show(
        (T "Database configuration saved.`nStart NetX now?" "数据库配置已保存。`n是否立即启动 NetX？"),
        "NetX",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Information
    )
    if ($restart -eq [System.Windows.Forms.DialogResult]::Yes) {
        Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser") | Out-Null
        Open-NetxUiWhenReady
    } else {
        Update-NetxTrayTip
    }
}

function Open-NetxUiWhenReady {
    param([int]$TimeoutSec = 180)
    $notify.ShowBalloonTip(
        5000,
        "NetX",
        (T "Starting… first run may take a minute." "正在启动…首次迁移可能需要一分钟。"),
        [System.Windows.Forms.ToolTipIcon]::Info
    )
    # Poll on a timer — do not block the tray UI thread with Wait-NetxApiHealthy.
    if ($script:netxUiReadyTimer) {
        try { $script:netxUiReadyTimer.Stop(); $script:netxUiReadyTimer.Dispose() } catch {}
    }
    $script:netxUiReadyTicks = 0
    $script:netxUiReadyMax = [Math]::Max(1, [int]($TimeoutSec * 2))
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 500
    $timer.add_Tick({
        $script:netxUiReadyTicks++
        if (Test-NetxApiHealthy -HostName $hostBind -Port $port -TimeoutSec 2) {
            try { $script:netxUiReadyTimer.Stop(); $script:netxUiReadyTimer.Dispose() } catch {}
            $script:netxUiReadyTimer = $null
            try { Start-Process $uiUrl } catch {}
            Update-NetxTrayTip
            return
        }
        if ($script:netxUiReadyTicks -ge $script:netxUiReadyMax) {
            try { $script:netxUiReadyTimer.Stop(); $script:netxUiReadyTimer.Dispose() } catch {}
            $script:netxUiReadyTimer = $null
            $notify.ShowBalloonTip(
                8000,
                "NetX",
                (T "UI not ready yet. Use tray → Open UI when ready, or check data\runtime\netx.err.log." "界面尚未就绪。就绪后请用托盘「打开界面」，或查看 data\runtime\netx.err.log。"),
                [System.Windows.Forms.ToolTipIcon]::Warning
            )
            Update-NetxTrayTip
        }
    })
    $script:netxUiReadyTimer = $timer
    $timer.Start()
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "NetX"
$form.ShowInTaskbar = $false
$form.WindowState = "Minimized"
$form.Visible = $false
$form.Size = New-Object System.Drawing.Size(0, 0)

$notify = New-Object System.Windows.Forms.NotifyIcon
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
Update-NetxTrayTip

function New-NetxMenuItem {
    param(
        [string]$Text,
        [switch]$Header,
        [switch]$Primary,
        [switch]$Muted
    )
    $item = New-Object System.Windows.Forms.ToolStripMenuItem
    $item.Text = $Text
    $item.AutoSize = $true
    $item.Padding = New-Object System.Windows.Forms.Padding(10, 5, 28, 5)
    $item.Margin = New-Object System.Windows.Forms.Padding(0)
    if ($Header) {
        $item.Enabled = $false
        $item.Font = New-Object System.Drawing.Font("Segoe UI", 9.0, [System.Drawing.FontStyle]::Bold)
        $item.ForeColor = [System.Drawing.Color]::FromArgb(55, 65, 80)
        $item.Padding = New-Object System.Windows.Forms.Padding(10, 6, 28, 2)
    } elseif ($Muted) {
        $item.Enabled = $false
        $item.Font = New-Object System.Drawing.Font("Segoe UI", 8.25)
        $item.ForeColor = [System.Drawing.Color]::FromArgb(120, 128, 140)
        $item.Padding = New-Object System.Windows.Forms.Padding(10, 1, 28, 6)
    } elseif ($Primary) {
        $item.Font = New-Object System.Drawing.Font("Segoe UI", 9.0, [System.Drawing.FontStyle]::Bold)
        $item.ForeColor = [System.Drawing.Color]::FromArgb(20, 40, 70)
    } else {
        $item.Font = New-Object System.Drawing.Font("Segoe UI", 9.0)
        $item.ForeColor = [System.Drawing.Color]::FromArgb(35, 40, 48)
    }
    return $item
}

function New-NetxMenuSeparator {
    $sep = New-Object System.Windows.Forms.ToolStripSeparator
    $sep.Margin = New-Object System.Windows.Forms.Padding(0, 3, 0, 3)
    $sep.Padding = New-Object System.Windows.Forms.Padding(0)
    return $sep
}

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menu.ShowImageMargin = $false
$menu.ShowCheckMargin = $false
$menu.Font = New-Object System.Drawing.Font("Segoe UI", 9.0)
$menu.BackColor = [System.Drawing.Color]::FromArgb(252, 252, 253)
$menu.ForeColor = [System.Drawing.Color]::FromArgb(35, 40, 48)
$menu.Padding = New-Object System.Windows.Forms.Padding(4, 4, 4, 4)
$menu.Renderer = New-Object NetxMenuRenderer

# --- header (info only) ---
$script:miHeader = New-NetxMenuItem -Text "NetX  $script:ver" -Header
$miHeader = $script:miHeader
[void]$menu.Items.Add($miHeader)

$miSub = New-NetxMenuItem -Text "$dbModeLabel  ·  ${hostBind}:$port" -Muted
[void]$menu.Items.Add($miSub)

[void]$menu.Items.Add((New-NetxMenuSeparator))

# --- primary ---
$miOpen = New-NetxMenuItem -Text (T "Open UI" "打开界面") -Primary
$miOpen.add_Click({
    Start-NetxTrayDeferred {
        if (Test-NetxApiHealthy -HostName $hostBind -Port $port -TimeoutSec 2) {
            Start-Process $uiUrl
        } else {
            $notify.ShowBalloonTip(4000, "NetX", (T "NetX is not running. Starting…" "NetX 未运行，正在启动…"), [System.Windows.Forms.ToolTipIcon]::Info)
            Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser") | Out-Null
            Open-NetxUiWhenReady
        }
    }
})
[void]$menu.Items.Add($miOpen)

[void]$menu.Items.Add((New-NetxMenuSeparator))

# --- service control ---
$miStart = New-NetxMenuItem -Text (T "Start" "启动")
$miStart.add_Click({
    Start-NetxTrayDeferred {
        Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser") | Out-Null
        Open-NetxUiWhenReady
    }
})
[void]$menu.Items.Add($miStart)

$miStop = New-NetxMenuItem -Text (T "Stop" "停止")
$miStop.add_Click({
    Start-NetxTrayDeferred {
        $notify.ShowBalloonTip(3000, "NetX", (T "Stopping…" "正在停止…"), [System.Windows.Forms.ToolTipIcon]::Info)
        Invoke-NetxScript -File $stopPs1 | Out-Null
        Update-NetxTrayTip
    }
})
[void]$menu.Items.Add($miStop)

[void]$menu.Items.Add((New-NetxMenuSeparator))

# --- setup / update ---
$miReconfig = New-NetxMenuItem -Text (T "Reconfigure database…" "重新配置数据库…")
$miReconfig.add_Click({
    Start-NetxTrayDeferred {
        $ans = [System.Windows.Forms.MessageBox]::Show(
            (T `
                "NetX will stop while you reconfigure the database.`n`nChoose built-in or external PostgreSQL in the console window.`nContinue?" `
                "重新配置数据库时会先停止 NetX。`n`n请在随后的控制台窗口中选择内置或外置 PostgreSQL。`n是否继续？"),
            (T "Reconfigure database" "重新配置数据库"),
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question
        )
        if ($ans -ne [System.Windows.Forms.DialogResult]::Yes) { return }

        $notify.ShowBalloonTip(3000, "NetX", (T "Stopping…" "正在停止…"), [System.Windows.Forms.ToolTipIcon]::Info)
        Invoke-NetxScript -File $stopPs1 | Out-Null

        # ProgramData\.env is often admin-owned after Setup; repair ACL before reconfigure.
        $null = Ensure-NetxDataAcl -DataRoot $data -Quiet

        try {
            $p = Start-NetxPowerShell -File $setupPs1 `
                -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data) `
                -WorkingDirectory $prog -WindowStyle Normal -PassThru
            Start-NetxTrayWatchProcess -Process $p -OnExit {
                param($sp)
                if ($null -eq $sp) { Update-NetxTrayTip; return }
                if (Test-NetxSetupCancelled -ExitCode $sp.ExitCode) {
                    $notify.ShowBalloonTip(
                        4000, "NetX",
                        (T "Database setup cancelled." "已取消数据库配置。"),
                        [System.Windows.Forms.ToolTipIcon]::Info
                    )
                    Update-NetxTrayTip
                    return
                }
                if ($null -ne $sp.ExitCode -and $sp.ExitCode -ne 0) {
                    $elev = [System.Windows.Forms.MessageBox]::Show(
                        (T `
                            "Database setup failed (exit $($sp.ExitCode)).`n`nIf this was a permission error on %ProgramData%\NetX\.env, click Yes to retry as Administrator." `
                            "数据库配置失败（退出码 $($sp.ExitCode)）。`n`n若是 %ProgramData%\NetX\.env 权限问题，点「是」将以管理员身份重试。"),
                        "NetX",
                        [System.Windows.Forms.MessageBoxButtons]::YesNo,
                        [System.Windows.Forms.MessageBoxIcon]::Warning
                    )
                    if ($elev -ne [System.Windows.Forms.DialogResult]::Yes) {
                        Update-NetxTrayTip
                        return
                    }
                    $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$setupPs1`" -ProgramRoot `"$prog`" -DataRoot `"$data`""
                    try {
                        $ep = Start-Process -FilePath "powershell.exe" -ArgumentList $arg `
                            -WorkingDirectory $prog -Verb RunAs -PassThru
                    } catch {
                        Update-NetxTrayTip
                        return
                    }
                    Start-NetxTrayWatchProcess -Process $ep -OnExit {
                        param($ep2)
                        if ($null -eq $ep2 -or (Test-NetxSetupCancelled -ExitCode $ep2.ExitCode)) {
                            Update-NetxTrayTip
                            return
                        }
                        if ($null -ne $ep2.ExitCode -and $ep2.ExitCode -ne 0) {
                            [System.Windows.Forms.MessageBox]::Show(
                                (T "Elevated database setup still failed (exit $($ep2.ExitCode))." "管理员配置仍失败（退出码 $($ep2.ExitCode)）。"),
                                "NetX",
                                [System.Windows.Forms.MessageBoxButtons]::OK,
                                [System.Windows.Forms.MessageBoxIcon]::Error
                            )
                            Update-NetxTrayTip
                            return
                        }
                        Complete-NetxTrayReconfig
                    }
                    return
                }
                Complete-NetxTrayReconfig
            }
        } catch {
            [System.Windows.Forms.MessageBox]::Show(
                (T "Database setup failed: $($_.Exception.Message)" "数据库配置失败：$($_.Exception.Message)"),
                "NetX",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            )
            Update-NetxTrayTip
        }
    }
})
[void]$menu.Items.Add($miReconfig)

$miUpdate = New-NetxMenuItem -Text (T "Check for updates…" "检查更新…")
$miUpdate.add_Click({
    Start-NetxTrayDeferred {
        try {
            $notify.ShowBalloonTip(
                3000, "NetX",
                (T "Checking for updates…" "正在检查更新…"),
                [System.Windows.Forms.ToolTipIcon]::Info
            )
            # No -Wait on UI thread (freezes ContextMenuStrip / tray).
            $p = Start-NetxPowerShell -File $checkPs1 -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data) `
                -WorkingDirectory $prog -WindowStyle Normal -PassThru
            Start-NetxTrayWatchProcess -Process $p -OnExit {
                param($cp)
                try {
                    if ($null -eq $cp) { return }
                    if (Test-NetxSetupCancelled -ExitCode $cp.ExitCode) { return }
                    if ($cp.ExitCode -eq 10) {
                        $ans = [System.Windows.Forms.MessageBox]::Show(
                            (T "A newer NetX is available. Download and apply now?" "发现新版本，是否立即下载并更新？"),
                            (T "NetX update" "NetX 更新"),
                            [System.Windows.Forms.MessageBoxButtons]::YesNo,
                            [System.Windows.Forms.MessageBoxIcon]::Question
                        )
                        if ($ans -ne [System.Windows.Forms.DialogResult]::Yes) { return }
                        $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$checkPs1`" -ProgramRoot `"$prog`" -DataRoot `"$data`" -Apply"
                        $notify.ShowBalloonTip(
                            5000, "NetX",
                            (T "Downloading / applying update… keep the console open." "正在下载/应用更新…请勿关闭控制台窗口。"),
                            [System.Windows.Forms.ToolTipIcon]::Info
                        )
                        try {
                            $ap = Start-Process -FilePath "powershell.exe" -ArgumentList $arg `
                                -WorkingDirectory $prog -WindowStyle Normal -Verb RunAs -PassThru
                        } catch {
                            $notify.ShowBalloonTip(4000, "NetX", (T "Update cancelled (UAC)." "已取消更新（UAC）。"), [System.Windows.Forms.ToolTipIcon]::Info)
                            return
                        }
                        Start-NetxTrayWatchProcess -Process $ap -OnExit {
                            param($ap2)
                            if ($null -eq $ap2) { return }
                            if (Test-NetxSetupCancelled -ExitCode $ap2.ExitCode) {
                                $notify.ShowBalloonTip(4000, "NetX", (T "Update cancelled." "已取消更新。"), [System.Windows.Forms.ToolTipIcon]::Info)
                                return
                            }
                            if ($null -ne $ap2.ExitCode -and $ap2.ExitCode -eq 0) {
                                $newVer = Get-NetxVersion -ProgramRoot $prog
                                [System.Windows.Forms.MessageBox]::Show(
                                    (T "Updated to $newVer.`nTray will restart." "已更新到 $newVer。`n即将重启托盘。"),
                                    (T "NetX update" "NetX 更新"),
                                    [System.Windows.Forms.MessageBoxButtons]::OK,
                                    [System.Windows.Forms.MessageBoxIcon]::Information
                                )
                                Restart-NetxTraySelf
                                return
                            }
                            [System.Windows.Forms.MessageBox]::Show(
                                (T "Update failed (exit $($ap2.ExitCode)). Re-run Check for updates, or install NetX-Setup manually." "更新失败（退出码 $($ap2.ExitCode)）。请重试「检查更新」，或手动运行 Setup 安装包。"),
                                (T "NetX update" "NetX 更新"),
                                [System.Windows.Forms.MessageBoxButtons]::OK,
                                [System.Windows.Forms.MessageBoxIcon]::Warning
                            )
                            Update-NetxTrayTip
                        }
                    } elseif ($cp.ExitCode -eq 0) {
                        [System.Windows.Forms.MessageBox]::Show(
                            (T "NetX is up to date ($script:ver)." "已是最新版本 ($script:ver)。"),
                            (T "NetX update" "NetX 更新")
                        )
                    }
                } catch {
                    [System.Windows.Forms.MessageBox]::Show(
                        (T "Update check failed: $($_.Exception.Message)" "检查更新失败：$($_.Exception.Message)"),
                        "NetX"
                    )
                }
            }
        } catch {
            [System.Windows.Forms.MessageBox]::Show(
                (T "Update check failed: $($_.Exception.Message)" "检查更新失败：$($_.Exception.Message)"),
                "NetX"
            )
        }
    }
})
[void]$menu.Items.Add($miUpdate)

[void]$menu.Items.Add((New-NetxMenuSeparator))

# --- exit ---
$miExit = New-NetxMenuItem -Text (T "Exit tray" "退出托盘")
$miExit.ToolTipText = (T "Leave NetX services running" "NetX 服务继续运行")
$miExit.add_Click({
    Close-NetxTrayMenu
    $notify.Visible = $false
    $form.Close()
    [System.Windows.Forms.Application]::Exit()
})
[void]$menu.Items.Add($miExit)

$miStopExit = New-NetxMenuItem -Text (T "Stop and exit" "停止并退出")
$miStopExit.add_Click({
    Start-NetxTrayDeferred {
        $notify.ShowBalloonTip(3000, "NetX", (T "Stopping…" "正在停止…"), [System.Windows.Forms.ToolTipIcon]::Info)
        Invoke-NetxScript -File $stopPs1 -Wait | Out-Null
        $notify.Visible = $false
        $form.Close()
        [System.Windows.Forms.Application]::Exit()
    }
})
[void]$menu.Items.Add($miStopExit)

$notify.ContextMenuStrip = $menu
$notify.add_DoubleClick({
    if (Test-NetxApiHealthy -HostName $hostBind -Port $port -TimeoutSec 2) {
        Start-Process $uiUrl
    } else {
        Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser") | Out-Null
        Open-NetxUiWhenReady
    }
})

# Refresh tooltip periodically (running / stopped).
$script:tipTimer = New-Object System.Windows.Forms.Timer
$script:tipTimer.Interval = 5000
$script:tipTimer.add_Tick({ Update-NetxTrayTip })
$script:tipTimer.Start()

$form.add_Shown({
    if ($AutoUpdate) {
        $notify.ShowBalloonTip(4000, "NetX", (T "Checking for updates…" "正在检查更新…"), [System.Windows.Forms.ToolTipIcon]::Info)
        Start-NetxPowerShell -File $checkPs1 `
            -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data, "-Apply", "-Quiet", "-AutoOnly") `
            -WorkingDirectory $prog -WindowStyle Hidden -Wait | Out-Null
    } elseif ($CheckUpdateOnLaunch) {
        Start-NetxPowerShell -File $checkPs1 `
            -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data, "-Quiet") `
            -WorkingDirectory $prog -WindowStyle Hidden | Out-Null
    }
    if ($StartOnLaunch) {
        Invoke-NetxScript -File $startPs1 -ExtraArgs @("-SkipBrowser") | Out-Null
        $script:netxUiWaitTicks = 0
        $script:netxUiWaitMax = 360  # 360 * 500ms = 180s
        $script:netxUiTimer = New-Object System.Windows.Forms.Timer
        $script:netxUiTimer.Interval = 500
        $script:netxUiTimer.add_Tick({
            $script:netxUiWaitTicks++
            if (Test-NetxApiHealthy -HostName $hostBind -Port $port -TimeoutSec 2) {
                $script:netxUiTimer.Stop()
                $script:netxUiTimer.Dispose()
                try { Start-Process $uiUrl } catch {}
                Update-NetxTrayTip
                return
            }
            if ($script:netxUiWaitTicks -eq 1) {
                $notify.ShowBalloonTip(
                    5000,
                    "NetX",
                    (T "Starting… first run may take a minute." "正在启动…首次迁移可能需要一分钟。"),
                    [System.Windows.Forms.ToolTipIcon]::Info
                )
            }
            if ($script:netxUiWaitTicks -ge $script:netxUiWaitMax) {
                $script:netxUiTimer.Stop()
                $script:netxUiTimer.Dispose()
                $notify.ShowBalloonTip(
                    8000,
                    "NetX",
                    (T "UI not ready yet. Use tray → Open UI later, or check data\runtime\netx.err.log." "界面尚未就绪。请稍后用托盘「打开界面」，或查看 data\runtime\netx.err.log。"),
                    [System.Windows.Forms.ToolTipIcon]::Warning
                )
                Update-NetxTrayTip
            }
        })
        $script:netxUiTimer.Start()
    }
})

$form.add_FormClosing({
    try { $script:tipTimer.Stop(); $script:tipTimer.Dispose() } catch {}
    $notify.Visible = $false
    $notify.Dispose()
})

[System.Windows.Forms.Application]::Run($form)
