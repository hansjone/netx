param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$StartOnLaunch = $true,
    # Logon autostart / post-update relaunch: bring NetX up but never pop a browser.
    [switch]$QuietStart = $false,
    [switch]$CheckUpdateOnLaunch = $false,
    [switch]$AutoUpdate = $false
)

# System-tray controller for NetX (Windows packaged installs).
# Always elevated: Start/Stop/Update touch Program Files + services.
# The UI thread never blocks: /health and scheduled-task state are polled in a
# background runspace, child scripts are watched by a timer.

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"
Assert-NetxAdminOrRelaunch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters -WindowStyle Hidden

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not ("NetxMenuRenderer" -as [type])) {
    Add-Type -ReferencedAssemblies System.Windows.Forms, System.Drawing -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Runtime.InteropServices;
using System.Windows.Forms;

public static class NetxNative {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr handle);
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    // Windows 11: rounded popup corners + themed 1px border drawn by DWM.
    public static bool RoundCorners(IntPtr hwnd, Color border) {
        try {
            int pref = 2; // DWMWCP_ROUND
            if (DwmSetWindowAttribute(hwnd, 33, ref pref, 4) != 0) return false;
            int c = ColorTranslator.ToWin32(border);
            DwmSetWindowAttribute(hwnd, 34, ref c, 4);
            return true;
        } catch { return false; }
    }
}

public sealed class NetxTheme {
    public Color Back, Hover, Text, Muted, Sep, Border;
    public bool DwmRounded;
}

public sealed class NetxMenuColorTable : ProfessionalColorTable {
    readonly NetxTheme t;
    public NetxMenuColorTable(NetxTheme theme) { t = theme; UseSystemColors = false; }
    public override Color ToolStripDropDownBackground { get { return t.Back; } }
    public override Color ImageMarginGradientBegin { get { return t.Back; } }
    public override Color ImageMarginGradientMiddle { get { return t.Back; } }
    public override Color ImageMarginGradientEnd { get { return t.Back; } }
    public override Color MenuBorder { get { return t.Border; } }
    public override Color MenuItemBorder { get { return t.Hover; } }
    public override Color MenuItemSelected { get { return t.Hover; } }
    public override Color MenuItemSelectedGradientBegin { get { return t.Hover; } }
    public override Color MenuItemSelectedGradientEnd { get { return t.Hover; } }
    public override Color MenuItemPressedGradientBegin { get { return t.Hover; } }
    public override Color MenuItemPressedGradientMiddle { get { return t.Hover; } }
    public override Color MenuItemPressedGradientEnd { get { return t.Hover; } }
    public override Color SeparatorDark { get { return t.Sep; } }
    public override Color SeparatorLight { get { return t.Sep; } }
}

public sealed class NetxMenuRenderer : ToolStripProfessionalRenderer {
    readonly NetxTheme t;
    readonly float scale;

    public NetxMenuRenderer(NetxTheme theme, float dpiScale) : base(new NetxMenuColorTable(theme)) {
        t = theme;
        scale = dpiScale;
        RoundedEdges = false;
    }

    int S(int px) { return (int)Math.Round(px * scale); }

    static GraphicsPath Round(Rectangle r, int radius) {
        int d = radius * 2;
        GraphicsPath p = new GraphicsPath();
        p.AddArc(r.X, r.Y, d, d, 180, 90);
        p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }

    protected override void OnRenderToolStripBackground(ToolStripRenderEventArgs e) {
        using (SolidBrush b = new SolidBrush(t.Back)) e.Graphics.FillRectangle(b, e.AffectedBounds);
    }

    protected override void OnRenderImageMargin(ToolStripRenderEventArgs e) { }

    protected override void OnRenderToolStripBorder(ToolStripRenderEventArgs e) {
        if (t.DwmRounded) return; // DWM draws the rounded border on Windows 11
        Rectangle r = new Rectangle(0, 0, e.AffectedBounds.Width - 1, e.AffectedBounds.Height - 1);
        using (Pen pen = new Pen(t.Border)) e.Graphics.DrawRectangle(pen, r);
    }

    protected override void OnRenderMenuItemBackground(ToolStripItemRenderEventArgs e) {
        ToolStripItem item = e.Item;
        if (!item.Selected || !item.Enabled) return;
        Rectangle r = new Rectangle(S(4), S(1), item.Width - S(8), item.Height - S(2));
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        using (GraphicsPath path = Round(r, S(4)))
        using (SolidBrush b = new SolidBrush(t.Hover)) e.Graphics.FillPath(b, path);
    }

    protected override void OnRenderItemText(ToolStripItemTextRenderEventArgs e) {
        // Drawn directly: the base renderer forces grey on disabled (header) items.
        string role = e.Item.Tag as string;
        Color c;
        if (role == "header") c = t.Text;
        else if (role == "status") c = t.Muted;
        else c = e.Item.Enabled ? t.Text : t.Muted;
        TextRenderer.DrawText(e.Graphics, e.Text, e.TextFont, e.TextRectangle, c, e.TextFormat);
    }

    protected override void OnRenderItemImage(ToolStripItemImageRenderEventArgs e) {
        string role = e.Item.Tag as string;
        if (e.Image != null && (e.Item.Enabled || role == "status" || role == "header")) {
            e.Graphics.DrawImage(e.Image, e.ImageRectangle);
            return;
        }
        base.OnRenderItemImage(e);
    }

    protected override void OnRenderArrow(ToolStripArrowRenderEventArgs e) {
        e.ArrowColor = t.Muted;
        base.OnRenderArrow(e);
    }

    protected override void OnRenderSeparator(ToolStripSeparatorRenderEventArgs e) {
        int y = e.Item.Height / 2;
        using (Pen pen = new Pen(t.Sep)) e.Graphics.DrawLine(pen, S(12), y, e.Item.Width - S(12), y);
    }
}
"@
}

# Crisp text on high-DPI screens (powershell.exe is DPI-unaware by default).
try { [void][NetxNative]::SetProcessDPIAware() } catch {}
[System.Windows.Forms.Application]::EnableVisualStyles()

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"
$logDir = Join-Path $data "data\runtime"

$zh = ([cultureinfo]::CurrentUICulture.Name -match '^zh')
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

function Read-NetxTrayConfig {
    $map = Read-DotEnv -Path $envPath
    $bind = if ($map["NETX_HOST"]) { $map["NETX_HOST"] } else { "127.0.0.1" }
    $p = 8890
    if ($map["NETX_PORT"]) { try { $p = [int]$map["NETX_PORT"] } catch {} }
    $script:clientHost = Get-NetxClientHost -BindHost $bind
    $script:port = $p
    $script:uiUrl = "http://$($script:clientHost):$p/"
    $script:dbModeLabel = if ((Get-DbMode -EnvMap $map) -eq "bundled") { (T "Built-in DB" "内置库") } else { (T "External DB" "外置库") }
    $script:autoUpdateEnv = ([string]$map["NETX_UPDATE_AUTO"] -match '^(1|true|yes|on)$') -or
        ([string]$env:NETX_UPDATE_AUTO -match '^(1|true|yes|on)$')
}
Read-NetxTrayConfig
if ($script:autoUpdateEnv) {
    $AutoUpdate = $true
    $CheckUpdateOnLaunch = $true
}
$script:ver = Get-NetxVersion -ProgramRoot $prog

# Only one tray icon per logon session (desktop + postinstall + autostart can race).
$script:netxTrayMutex = $null
try {
    $createdNew = $false
    $script:netxTrayMutex = New-Object System.Threading.Mutex($true, "Local\NetX.WinTray.SingleInstance", [ref]$createdNew)
    if (-not $createdNew) {
        if (-not $QuietStart) {
            if (Test-NetxApiHealthy -HostName $script:clientHost -Port $script:port -TimeoutSec 2) {
                Open-NetxUrl -Url $script:uiUrl
            } elseif ($StartOnLaunch) {
                Start-NetxPowerShell -File (Join-Path $PSScriptRoot "runtime_ctl.ps1") `
                    -Arguments @("-Action", "start", "-ProgramRoot", $prog, "-DataRoot", $data) `
                    -WorkingDirectory $prog -WindowStyle Hidden | Out-Null
            }
        }
        exit 0
    }
} catch {
    # If mutex fails, continue with a tray (better than no UI control).
}

$runtimePs1 = Join-Path $PSScriptRoot "runtime_ctl.ps1"
$checkPs1 = Join-Path $PSScriptRoot "check_update.ps1"
$setupPs1 = Join-Path $PSScriptRoot "setup_first_run.ps1"
$pgPortPs1 = Join-Path $PSScriptRoot "set_pg_port.ps1"
$autostartPs1 = Join-Path $PSScriptRoot "install_autostart.ps1"
$updateTaskPs1 = Join-Path $PSScriptRoot "install_update_task.ps1"

# ---------------------------------------------------------------- background probe
# /health + runner/task state off the UI thread; the UI timer only reads $sync.
$script:sync = [hashtable]::Synchronized(@{
        HealthUrl  = "$($script:uiUrl)health"
        Healthy    = $false
        Seen       = $false
        Runner     = ""
        Autostart  = $false
        AutoUpdate = $false
        Stop       = $false
    })
$script:probeRunspace = [runspacefactory]::CreateRunspace()
$script:probeRunspace.Open()
$script:probeRunspace.SessionStateProxy.SetVariable("sync", $script:sync)
$script:probePs = [powershell]::Create()
$script:probePs.Runspace = $script:probeRunspace
[void]$script:probePs.AddScript({
        $i = 0
        while (-not $sync.Stop) {
            $ok = $false
            try {
                $r = Invoke-WebRequest -Uri $sync.HealthUrl -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
                if ($r.StatusCode -eq 200) { $ok = (($r.Content | ConvertFrom-Json).status -eq "ok") }
            } catch {}
            $sync.Healthy = $ok
            $sync.Seen = $true
            if (($i % 6) -eq 0) {
                $runner = ""
                $svc = Get-Service -Name "NetX" -ErrorAction SilentlyContinue
                if ($svc -and [string]$svc.StartType -ne "Disabled") { $runner = "service" }
                $tasks = @{}
                try {
                    Get-ScheduledTask -TaskPath "\NetX\" -ErrorAction Stop | ForEach-Object { $tasks[$_.TaskName] = [string]$_.State }
                } catch {}
                if (-not $runner -and $tasks["NetXService"] -and $tasks["NetXService"] -ne "Disabled") { $runner = "task" }
                $sync.Runner = $runner
                $sync.Autostart = [bool]($tasks["NetXTray"] -and $tasks["NetXTray"] -ne "Disabled")
                $sync.AutoUpdate = [bool]($tasks["NetXUpdate"] -and $tasks["NetXUpdate"] -ne "Disabled")
            }
            $i++
            for ($k = 0; $k -lt 15 -and -not $sync.Stop -and -not $sync.Poke; $k++) { Start-Sleep -Milliseconds 100 }
            $sync.Poke = $false
        }
    })
$script:probeHandle = $script:probePs.BeginInvoke()

function Request-NetxProbe { $script:sync.Poke = $true }

# ---------------------------------------------------------------- theme + drawing
$script:dpiScale = 1.0
try {
    $g = [System.Drawing.Graphics]::FromHwnd([IntPtr]::Zero)
    $script:dpiScale = $g.DpiX / 96.0
    $g.Dispose()
} catch {}
function Get-NetxPx([int]$Px) { return [int][Math]::Round($Px * $script:dpiScale) }

$script:glyphFont = "Segoe MDL2 Assets"
try {
    $installed = (New-Object System.Drawing.Text.InstalledFontCollection).Families | ForEach-Object { $_.Name }
    if ($installed -contains "Segoe Fluent Icons") { $script:glyphFont = "Segoe Fluent Icons" }
} catch {}
$script:uiFontName = if ($zh) { "Microsoft YaHei UI" } else { "Segoe UI" }

function Test-NetxDarkMode {
    try {
        $v = Get-ItemPropertyValue -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" -Name "AppsUseLightTheme" -ErrorAction Stop
        return ([int]$v -eq 0)
    } catch { return $false }
}

function New-NetxTheme([bool]$Dark) {
    $t = New-Object NetxTheme
    $c = { param($r, $g, $b) [System.Drawing.Color]::FromArgb($r, $g, $b) }
    if ($Dark) {
        $t.Back = & $c 28 32 40
        $t.Hover = & $c 44 54 72
        $t.Text = & $c 232 237 244
        $t.Muted = & $c 140 150 166
        $t.Sep = & $c 50 56 68
        $t.Border = & $c 58 66 82
    } else {
        $t.Back = & $c 251 252 253
        $t.Hover = & $c 228 237 251
        $t.Text = & $c 26 31 38
        $t.Muted = & $c 108 117 130
        $t.Sep = & $c 229 232 237
        $t.Border = & $c 212 218 226
    }
    return $t
}

$script:stateColors = @{
    running  = [System.Drawing.Color]::FromArgb(34, 197, 94)
    stopped  = [System.Drawing.Color]::FromArgb(148, 160, 178)
    starting = [System.Drawing.Color]::FromArgb(245, 166, 35)
    stopping = [System.Drawing.Color]::FromArgb(245, 166, 35)
    checking = [System.Drawing.Color]::FromArgb(148, 160, 178)
}

function New-NetxGlyph([char]$Glyph, [System.Drawing.Color]$Color) {
    $sz = Get-NetxPx 16
    $bmp = New-Object System.Drawing.Bitmap($sz, $sz)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $font = New-Object System.Drawing.Font($script:glyphFont, [float]($sz * 0.78), [System.Drawing.GraphicsUnit]::Pixel)
    $brush = New-Object System.Drawing.SolidBrush($Color)
    $fmt = New-Object System.Drawing.StringFormat
    $fmt.Alignment = [System.Drawing.StringAlignment]::Center
    $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
    $g.DrawString([string]$Glyph, $font, $brush, (New-Object System.Drawing.RectangleF(0, 1, $sz, $sz)), $fmt)
    $fmt.Dispose(); $brush.Dispose(); $font.Dispose(); $g.Dispose()
    return $bmp
}

function New-NetxDot([System.Drawing.Color]$Color) {
    $sz = Get-NetxPx 16
    $bmp = New-Object System.Drawing.Bitmap($sz, $sz)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $d = [Math]::Max(6, [int]($sz * 0.5))
    $o = [int](($sz - $d) / 2)
    $brush = New-Object System.Drawing.SolidBrush($Color)
    $g.FillEllipse($brush, $o, $o, $d, $d)
    $brush.Dispose(); $g.Dispose()
    return $bmp
}

$script:glyphs = @{
    open     = [char]0xE8A7
    start    = [char]0xE768
    stop     = [char]0xE71A
    restart  = [char]0xE72C
    settings = [char]0xE713
    update   = [char]0xE895
    repair   = [char]0xE90F
    folder   = [char]0xE8B7
    log      = [char]0xE8A5
    check    = [char]0xE73E
    exit     = [char]0xE8BB
    power    = [char]0xE7E8
}

# Brand mark from the 256px PNG (small .ico frames do not convert cleanly).
$script:brandPng = $null
try { $script:brandPng = [System.Drawing.Image]::FromFile((Join-Path $PSScriptRoot "assets\netx-256.png")) } catch {}
function New-NetxBrandBitmap([int]$Size) {
    $bmp = New-Object System.Drawing.Bitmap($Size, $Size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $rect = New-Object System.Drawing.Rectangle(0, 0, $Size, $Size)
    if ($script:brandPng) { $g.DrawImage($script:brandPng, $rect) }
    else { $g.DrawIcon([System.Drawing.SystemIcons]::Application, $rect) }
    $g.Dispose()
    return $bmp
}

# Tray icons with a status dot, built once per state.
$script:iconHandles = @()
function New-NetxStateIcon([System.Drawing.Color]$Dot) {
    $sz = [System.Windows.Forms.SystemInformation]::SmallIconSize.Width
    $bmp = New-NetxBrandBitmap $sz
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $d = [Math]::Max(5, [int][Math]::Round($sz * 0.42))
    $x = $sz - $d
    $y = $sz - $d
    $ring = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(15, 23, 36))
    $g.FillEllipse($ring, $x - 1, $y - 1, $d + 1, $d + 1)
    $brush = New-Object System.Drawing.SolidBrush($Dot)
    $g.FillEllipse($brush, $x + 1, $y + 1, $d - 3, $d - 3)
    $ring.Dispose(); $brush.Dispose(); $g.Dispose()
    $h = $bmp.GetHicon()
    $bmp.Dispose()
    $script:iconHandles += $h
    return [System.Drawing.Icon]::FromHandle($h)
}
$script:trayIcons = @{}
foreach ($k in $script:stateColors.Keys) {
    try { $script:trayIcons[$k] = New-NetxStateIcon $script:stateColors[$k] } catch {}
}

# ---------------------------------------------------------------- window + icon
$form = New-Object System.Windows.Forms.Form
$form.Text = "NetX"
$form.ShowInTaskbar = $false
$form.WindowState = "Minimized"
$form.Visible = $false
$form.Size = New-Object System.Drawing.Size(0, 0)

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = if ($script:trayIcons["checking"]) { $script:trayIcons["checking"] } else { [System.Drawing.SystemIcons]::Application }
$notify.Text = "NetX"
$notify.Visible = $true

function Show-NetxToast([string]$Text, [string]$Kind = "Info", [int]$Ms = 4000) {
    try { $notify.ShowBalloonTip($Ms, "NetX", $Text, [System.Windows.Forms.ToolTipIcon]::$Kind) } catch {}
}

function Show-NetxDialog([string]$Text, [string]$Kind = "Information", [string]$Buttons = "OK") {
    return [System.Windows.Forms.MessageBox]::Show(
        $Text, "NetX",
        [System.Windows.Forms.MessageBoxButtons]::$Buttons,
        [System.Windows.Forms.MessageBoxIcon]::$Kind
    )
}

# ---------------------------------------------------------------- child processes
# Never Start-Process -Wait on the WinForms UI thread — it freezes the tray menu.
$script:watches = New-Object System.Collections.ArrayList
$script:watchTimer = New-Object System.Windows.Forms.Timer
$script:watchTimer.Interval = 300
$script:watchTimer.add_Tick({
        foreach ($w in @($script:watches)) {
            $done = $true
            try { $done = [bool]$w.Proc.HasExited } catch {}
            if (-not $done) { continue }
            [void]$script:watches.Remove($w)
            if ($w.OnExit) {
                try { & $w.OnExit $w.Proc $w.Context } catch { Show-NetxToast $_.Exception.Message "Warning" }
            }
        }
        if ($script:watches.Count -eq 0) { $script:watchTimer.Stop() }
    })

function Watch-NetxProcess($Process, [scriptblock]$OnExit, [hashtable]$Context = @{}) {
    # OnExit runs later from the timer: pass anything it needs via -Context
    # (function locals are gone by then).
    if ($null -eq $Process) {
        if ($OnExit) { & $OnExit $null $Context }
        return
    }
    [void]$script:watches.Add(@{ Proc = $Process; OnExit = $OnExit; Context = $Context })
    $script:watchTimer.Start()
}

function Start-NetxChild([string]$File, [string[]]$ExtraArgs = @(), [string]$WindowStyle = "Hidden") {
    return Start-NetxPowerShell -File $File -Arguments (@("-ProgramRoot", $prog, "-DataRoot", $data) + $ExtraArgs) `
        -WorkingDirectory $prog -WindowStyle $WindowStyle -PassThru
}

function Get-NetxExitCode($Process) {
    if ($null -eq $Process) { return $null }
    try { return $Process.ExitCode } catch { return $null }
}

function Test-NetxSetupCancelled($ExitCode) {
    # STATUS_CONTROL_C_EXIT (0xC000013A): console closed / Ctrl+C — not a failure.
    return ($null -ne $ExitCode -and [int]$ExitCode -eq -1073741510)
}

# ---------------------------------------------------------------- runtime state
# pending = an in-flight start/stop/restart; it clears once the child exits and
# /health matches the target (or on timeout).
$script:pending = $null
$script:openWhenReady = $null
$script:lastState = ""

function Invoke-NetxRuntime([ValidateSet("start", "stop", "restart")][string]$Action, [switch]$OpenUi, [scriptblock]$Then) {
    if ($script:pending) { return }
    $kind = if ($Action -eq "stop") { "stopping" } else { "starting" }
    $timeout = if ($Action -eq "stop") { 90 } else { 200 }
    $proc = $null
    try {
        $proc = Start-NetxChild -File $runtimePs1 -ExtraArgs @("-Action", $Action)
    } catch {
        Show-NetxToast ((T "Could not run NetX control script: " "无法执行 NetX 控制脚本：") + $_.Exception.Message) "Error"
        return
    }
    $script:pending = @{
        Kind   = $kind
        Target = ($Action -ne "stop")
        Done   = $false
        Until  = (Get-Date).AddSeconds($timeout)
        Then   = $Then
    }
    if ($OpenUi) { $script:openWhenReady = (Get-Date).AddSeconds($timeout) }
    Update-NetxTrayState
    Watch-NetxProcess $proc -Context @{ Action = $Action } -OnExit {
        param($p, $ctx)
        $code = Get-NetxExitCode $p
        $pend = $script:pending
        if ($null -ne $code -and $code -ne 0) {
            $script:pending = $null
            $script:openWhenReady = $null
            Show-NetxToast ((T "NetX $($ctx.Action) failed (exit $code). Logs: " "NetX 操作失败（退出码 $code）。日志：") + $logDir) "Error" 8000
        } elseif ($pend) {
            $pend.Done = $true
        }
        Request-NetxProbe
        Update-NetxTrayState
        if ($pend -and $pend.Then) { & $pend.Then $code }
    }
}

function Get-NetxTrayState {
    $healthy = [bool]$script:sync.Healthy
    $p = $script:pending
    if ($p) {
        if ((Get-Date) -gt $p.Until) {
            $script:pending = $null
        } elseif ($p.Done -and ($healthy -eq $p.Target)) {
            $script:pending = $null
        } else {
            return $p.Kind
        }
    }
    if (-not $script:sync.Seen) { return "checking" }
    if ($healthy) { return "running" }
    return "stopped"
}

$script:stateText = @{
    running  = (T "Running" "运行中")
    stopped  = (T "Stopped" "已停止")
    starting = (T "Starting…" "正在启动…")
    stopping = (T "Stopping…" "正在停止…")
    checking = (T "Checking…" "检测中…")
}

function Update-NetxTrayState {
    $state = Get-NetxTrayState
    if ($script:openWhenReady) {
        if ($state -eq "running") {
            $script:openWhenReady = $null
            Open-NetxUrl -Url $script:uiUrl
        } elseif ((Get-Date) -gt $script:openWhenReady -or ($state -eq "stopped" -and -not $script:pending)) {
            $script:openWhenReady = $null
            Show-NetxToast (T "NetX did not become ready. Check data\runtime\netx.err.log." "NetX 未能就绪，请查看 data\runtime\netx.err.log。") "Warning" 8000
        }
    }
    $runner = [string]$script:sync.Runner
    $key = "$state|$runner|$($script:ver)"
    if ($key -eq $script:lastState) { return }
    $prevState = ($script:lastState -split '\|')[0]
    $script:lastState = $key

    $text = $script:stateText[$state]
    $miStatus.Text = $text
    $miStatus.Image = $script:dots[$state]
    $mode = if ($runner -eq "service") { (T "Service" "服务") } elseif ($runner -eq "task") { (T "Startup task" "启动任务") } else { $script:dbModeLabel }
    $miStatus.ShortcutKeyDisplayString = "$mode  ·  $($script:clientHost):$($script:port)"
    $miHeader.ShortcutKeyDisplayString = "v$($script:ver)"

    $busy = ($state -in @("starting", "stopping", "checking"))
    $miStart.Enabled = (-not $busy) -and ($state -eq "stopped")
    $miStop.Enabled = (-not $busy) -and ($state -eq "running")
    $miRestart.Enabled = (-not $busy) -and ($state -eq "running")
    $miOpen.Enabled = ($state -ne "stopping")

    if ($script:trayIcons[$state]) { $notify.Icon = $script:trayIcons[$state] }
    $tip = "NetX $($script:ver) · $text"
    if ($tip.Length -gt 63) { $tip = $tip.Substring(0, 63) }
    $notify.Text = $tip

    # Requested stops pass through "stopping"; a direct running→stopped is external.
    if ($prevState -eq "running" -and $state -eq "stopped") {
        Show-NetxToast (T "NetX has stopped." "NetX 已停止运行。") "Warning"
    }
}

# ---------------------------------------------------------------- menu
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menu.ShowImageMargin = $true
$menu.ShowCheckMargin = $false
$menu.ImageScalingSize = New-Object System.Drawing.Size((Get-NetxPx 16), (Get-NetxPx 16))
$menu.Font = New-Object System.Drawing.Font($script:uiFontName, 9.0)
$menu.Padding = New-Object System.Windows.Forms.Padding((Get-NetxPx 4), (Get-NetxPx 6), (Get-NetxPx 4), (Get-NetxPx 6))
$menu.DropShadowEnabled = $true

$script:itemGlyph = @{}
function New-NetxMenuItem([string]$Text, [string]$Glyph = "", [string]$Role = "", [switch]$Bold) {
    $item = New-Object System.Windows.Forms.ToolStripMenuItem
    $item.Text = $Text
    $item.Tag = $Role
    $item.AutoSize = $true
    $item.Padding = New-Object System.Windows.Forms.Padding((Get-NetxPx 6), (Get-NetxPx 6), (Get-NetxPx 18), (Get-NetxPx 6))
    $item.Margin = New-Object System.Windows.Forms.Padding(0)
    if ($Glyph) { $script:itemGlyph[$item] = $Glyph }
    $style = if ($Bold) { [System.Drawing.FontStyle]::Bold } else { [System.Drawing.FontStyle]::Regular }
    $size = if ($Role -eq "status") { 8.5 } else { 9.0 }
    $item.Font = New-Object System.Drawing.Font($script:uiFontName, [float]$size, $style)
    if ($Role -in @("header", "status")) { $item.Enabled = $false }
    return $item
}

function New-NetxMenuSeparator {
    $sep = New-Object System.Windows.Forms.ToolStripSeparator
    $sep.Margin = New-Object System.Windows.Forms.Padding(0, (Get-NetxPx 3), 0, (Get-NetxPx 3))
    return $sep
}

function Set-NetxDropDownChrome($DropDown) {
    $DropDown.Renderer = $script:renderer
    $DropDown.add_HandleCreated({
            param($s, $e)
            [void][NetxNative]::RoundCorners($s.Handle, $script:theme.Border)
        })
}

$miHeader = New-NetxMenuItem -Text "NetX" -Role "header" -Bold
$miStatus = New-NetxMenuItem -Text $script:stateText["checking"] -Role "status"
$miOpen = New-NetxMenuItem -Text (T "Open NetX" "打开界面") -Glyph "open" -Bold
$miStart = New-NetxMenuItem -Text (T "Start" "启动") -Glyph "start"
$miStop = New-NetxMenuItem -Text (T "Stop" "停止") -Glyph "stop"
$miRestart = New-NetxMenuItem -Text (T "Restart" "重启") -Glyph "restart"
$miSettings = New-NetxMenuItem -Text (T "Settings" "设置") -Glyph "settings"
$miAutostart = New-NetxMenuItem -Text (T "Start with Windows" "登录 Windows 时自动启动") -Glyph "check"
$miAutoUpdate = New-NetxMenuItem -Text (T "Daily automatic updates" "每日自动更新") -Glyph "check"
$miSource = New-NetxMenuItem -Text (T "Update source" "更新源") -Glyph "update"
$miSourceGithub = New-NetxMenuItem -Text (T "GitHub (default)" "GitHub（默认）") -Glyph "check"
$miSourceForgejo = New-NetxMenuItem -Text "git.avelo.top" -Glyph "check"
$miReconfig = New-NetxMenuItem -Text (T "Reconfigure database…" "重新配置数据库…") -Glyph "repair"
$miPgPort = New-NetxMenuItem -Text (T "Built-in database port…" "内置数据库端口…") -Glyph "settings"
$miLogs = New-NetxMenuItem -Text (T "Open log folder" "打开日志目录") -Glyph "log"
$miData = New-NetxMenuItem -Text (T "Open data folder" "打开数据目录") -Glyph "folder"
$miUpdate = New-NetxMenuItem -Text (T "Check for updates…" "检查更新…") -Glyph "update"
$miExit = New-NetxMenuItem -Text (T "Exit tray (keep NetX running)" "退出托盘（NetX 保持运行）") -Glyph "exit"
$miStopExit = New-NetxMenuItem -Text (T "Stop NetX and exit" "停止 NetX 并退出") -Glyph "power"

[void]$miSource.DropDownItems.AddRange(@($miSourceGithub, $miSourceForgejo))
[void]$miSettings.DropDownItems.AddRange(@(
        $miAutostart, $miAutoUpdate, $miSource, (New-NetxMenuSeparator),
        $miReconfig, $miPgPort, $miLogs, $miData
    ))
[void]$menu.Items.AddRange(@(
        $miHeader, $miStatus, (New-NetxMenuSeparator),
        $miOpen, (New-NetxMenuSeparator),
        $miStart, $miStop, $miRestart, (New-NetxMenuSeparator),
        $miSettings, $miUpdate, (New-NetxMenuSeparator),
        $miExit, $miStopExit
    ))

$script:themeDark = $null
function Set-NetxTrayTheme {
    $dark = Test-NetxDarkMode
    if ($dark -eq $script:themeDark) { return }
    $script:themeDark = $dark
    $script:theme = New-NetxTheme $dark
    $script:theme.DwmRounded = ([Environment]::OSVersion.Version.Build -ge 22000)
    $script:renderer = New-Object NetxMenuRenderer($script:theme, [float]$script:dpiScale)
    $menu.Renderer = $script:renderer
    $miSettings.DropDown.Renderer = $script:renderer
    $miSource.DropDown.Renderer = $script:renderer
    $script:glyphImages = @{}
    foreach ($k in $script:glyphs.Keys) {
        $script:glyphImages[$k] = New-NetxGlyph $script:glyphs[$k] $script:theme.Text
    }
    $script:glyphImages["open"] = New-NetxGlyph $script:glyphs["open"] ([System.Drawing.Color]::FromArgb(64, 140, 255))
    foreach ($it in $script:itemGlyph.Keys) {
        $gk = $script:itemGlyph[$it]
        if ($gk -ne "check") { $it.Image = $script:glyphImages[$gk] }
    }
    $script:dots = @{}
    foreach ($k in $script:stateColors.Keys) { $script:dots[$k] = New-NetxDot $script:stateColors[$k] }
    if (-not $miHeader.Image) { $miHeader.Image = New-NetxBrandBitmap (Get-NetxPx 16) }
    $script:lastState = ""
}
Set-NetxTrayTheme
Set-NetxDropDownChrome $menu
Set-NetxDropDownChrome $miSettings.DropDown
Set-NetxDropDownChrome $miSource.DropDown

# Update source = NETX_UPDATE_SOURCES in .env (read by check_update.ps1).
# Empty = default (GitHub first, git.avelo.top mirror as fallback); "forgejo" = mirror only.
function Get-NetxUpdateSource {
    $v = [string](Read-DotEnv -Path $envPath)["NETX_UPDATE_SOURCES"]
    if ($v.Trim().ToLowerInvariant() -eq "forgejo") { return "forgejo" }
    return "github"
}

$menu.add_Opening({
        Set-NetxTrayTheme
        if ($menu.IsHandleCreated) { [void][NetxNative]::RoundCorners($menu.Handle, $script:theme.Border) }
        Update-NetxTrayState
    })
$miSettings.DropDown.add_Opening({
        $dd = $miSettings.DropDown
        if ($dd.IsHandleCreated) { [void][NetxNative]::RoundCorners($dd.Handle, $script:theme.Border) }
        $miAutostart.Image = if ($script:sync.Autostart) { $script:glyphImages["check"] } else { $null }
        $miAutoUpdate.Image = if ($script:sync.AutoUpdate) { $script:glyphImages["check"] } else { $null }
    })
$miSource.DropDown.add_Opening({
        $dd = $miSource.DropDown
        if ($dd.IsHandleCreated) { [void][NetxNative]::RoundCorners($dd.Handle, $script:theme.Border) }
        $src = Get-NetxUpdateSource
        $miSourceGithub.Image = if ($src -eq "github") { $script:glyphImages["check"] } else { $null }
        $miSourceForgejo.Image = if ($src -eq "forgejo") { $script:glyphImages["check"] } else { $null }
    })

function Invoke-NetxTrayDeferred([scriptblock]$Work) {
    # Close the menu first: ContextMenuStrip stays modal while a Click handler runs.
    try { $menu.Close() } catch {}
    $script:deferredWork = $Work
    $t = New-Object System.Windows.Forms.Timer
    $t.Interval = 30
    $t.add_Tick({
            param($s, $e)
            $s.Stop(); $s.Dispose()
            $w = $script:deferredWork
            $script:deferredWork = $null
            if ($w) { & $w }
        })
    $t.Start()
}

function Open-NetxUi {
    if ($script:sync.Healthy) {
        Open-NetxUrl -Url $script:uiUrl
    } elseif ($script:pending -and $script:pending.Kind -eq "starting") {
        $script:openWhenReady = $script:pending.Until
    } else {
        Show-NetxToast (T "NetX is not running. Starting…" "NetX 未运行，正在启动…")
        Invoke-NetxRuntime -Action start -OpenUi
    }
}

$miOpen.add_Click({ Invoke-NetxTrayDeferred { Open-NetxUi } })
$miStart.add_Click({ Invoke-NetxTrayDeferred { Invoke-NetxRuntime -Action start -OpenUi:(-not $QuietStart) } })
$miStop.add_Click({ Invoke-NetxTrayDeferred { Invoke-NetxRuntime -Action stop } })
$miRestart.add_Click({ Invoke-NetxTrayDeferred { Invoke-NetxRuntime -Action restart } })
$miLogs.add_Click({ Invoke-NetxTrayDeferred { Open-NetxUrl -Url $logDir } })
$miData.add_Click({ Invoke-NetxTrayDeferred { Open-NetxUrl -Url $data } })

function Switch-NetxTaskToggle([string]$Script, [string]$SyncKey, [string]$OnText, [string]$OffText) {
    $enable = -not [bool]$script:sync[$SyncKey]
    $extra = if ($enable) { @() } else { @("-Remove") }
    try {
        $p = Start-NetxChild -File $Script -ExtraArgs $extra
    } catch {
        Show-NetxToast $_.Exception.Message "Error"
        return
    }
    $ctx = @{ Key = $SyncKey; Enable = $enable; Text = $(if ($enable) { $OnText } else { $OffText }) }
    Watch-NetxProcess $p -Context $ctx -OnExit {
        param($proc, $c)
        $code = Get-NetxExitCode $proc
        if ($null -eq $code -or $code -eq 0) {
            $script:sync[$c.Key] = $c.Enable
            Show-NetxToast $c.Text
        } else {
            Show-NetxToast (T "Setting change failed (exit $code)." "设置修改失败（退出码 $code）。") "Error"
        }
    }
}

$miAutostart.add_Click({
        Invoke-NetxTrayDeferred {
            Switch-NetxTaskToggle $autostartPs1 "Autostart" `
                (T "NetX will start when you sign in." "已开启：登录 Windows 时自动启动 NetX。") `
                (T "Start with Windows turned off." "已关闭登录自动启动。")
        }
    })
function Set-NetxUpdateSource([string]$Source) {
    $value = if ($Source -eq "forgejo") { "forgejo" } else { "" }
    try {
        Write-DotEnvValue -Path $envPath -Values @{ "NETX_UPDATE_SOURCES" = $value }
    } catch {
        Show-NetxToast ((T "Could not save the update source: " "无法保存更新源：") + $_.Exception.Message) "Error"
        return
    }
    Show-NetxToast $(if ($value) {
            (T "Updates now come from git.avelo.top." "更新源已切换为 git.avelo.top。")
        } else {
            (T "Updates now come from GitHub (git.avelo.top as fallback)." "更新源已切换为 GitHub（git.avelo.top 作为备用）。")
        })
}
$miSourceGithub.add_Click({ Invoke-NetxTrayDeferred { Set-NetxUpdateSource "github" } })
$miSourceForgejo.add_Click({ Invoke-NetxTrayDeferred { Set-NetxUpdateSource "forgejo" } })

$miAutoUpdate.add_Click({
        Invoke-NetxTrayDeferred {
            Switch-NetxTaskToggle $updateTaskPs1 "AutoUpdate" `
                (T "Daily automatic updates turned on." "已开启每日自动更新。") `
                (T "Daily automatic updates turned off." "已关闭每日自动更新。")
        }
    })

# --- reconfigure database (console wizard) ---
function Complete-NetxTrayReconfig {
    Read-NetxTrayConfig
    $script:sync.HealthUrl = "$($script:uiUrl)health"
    $script:lastState = ""
    $ans = Show-NetxDialog (T "Database configuration saved.`nStart NetX now?" "数据库配置已保存。`n是否立即启动 NetX？") "Information" "YesNo"
    if ($ans -eq [System.Windows.Forms.DialogResult]::Yes) {
        Invoke-NetxRuntime -Action start -OpenUi
    }
    Update-NetxTrayState
}

function Start-NetxReconfigWizard {
    $null = Ensure-NetxDataAcl -DataRoot $data -Quiet
    try {
        $p = Start-NetxChild -File $setupPs1 -WindowStyle Normal
    } catch {
        Show-NetxDialog ((T "Database setup failed: " "数据库配置失败：") + $_.Exception.Message) "Error" | Out-Null
        return
    }
    Watch-NetxProcess $p {
        param($sp)
        $code = Get-NetxExitCode $sp
        if (Test-NetxSetupCancelled $code) {
            Show-NetxToast (T "Database setup cancelled." "已取消数据库配置。")
            return
        }
        if ($null -ne $code -and $code -ne 0) {
            Show-NetxDialog ((T "Database setup failed (exit $code).`nLog: " "数据库配置失败（退出码 $code）。`n日志：") + $logDir) "Error" | Out-Null
            return
        }
        Complete-NetxTrayReconfig
    }
}

$miReconfig.add_Click({
        Invoke-NetxTrayDeferred {
            $ans = Show-NetxDialog (T `
                    "NetX will stop while you reconfigure the database.`n`nChoose built-in or external PostgreSQL in the console window that opens.`nContinue?" `
                    "重新配置数据库时会先停止 NetX。`n`n请在随后打开的控制台窗口中选择内置或外置 PostgreSQL。`n是否继续？") "Question" "YesNo"
            if ($ans -ne [System.Windows.Forms.DialogResult]::Yes) { return }
            if ($script:sync.Healthy -or $script:sync.Runner) {
                Invoke-NetxRuntime -Action stop -Then { param($c) Start-NetxReconfigWizard }
            } else {
                Start-NetxReconfigWizard
            }
        }
    })

$miPgPort.add_Click({
        Invoke-NetxTrayDeferred {
            if ($script:dbModeLabel -ne (T "Built-in DB" "内置库")) {
                Show-NetxDialog (T "NetX uses an external database; use Reconfigure database instead." "当前使用外置数据库，请改用“重新配置数据库”。") "Information" | Out-Null
                return
            }
            try {
                $p = Start-NetxChild -File $pgPortPs1 -WindowStyle Normal
            } catch {
                Show-NetxDialog ((T "Could not change the port: " "无法修改端口：") + $_.Exception.Message) "Error" | Out-Null
                return
            }
            Watch-NetxProcess $p {
                param($sp)
                $code = Get-NetxExitCode $sp
                if ($null -ne $code -and $code -ne 0) {
                    Show-NetxDialog ((T "Changing the port failed (exit $code)." "修改端口失败（退出码 $code）。")) "Error" | Out-Null
                    return
                }
                Read-NetxTrayConfig
            }
        }
    })

# --- updates ---
function Restart-NetxTraySelf {
    $trayFile = Join-Path $PSScriptRoot "netx_tray.ps1"
    try {
        Start-NetxPowerShell -File $trayFile -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data, "-QuietStart") `
            -WorkingDirectory $prog -WindowStyle Hidden | Out-Null
    } catch {}
    Exit-NetxTray
}

function Start-NetxUpdateApply {
    Show-NetxToast (T "Downloading and applying the update… keep the console open." "正在下载并应用更新…请勿关闭控制台窗口。") "Info" 5000
    try {
        $ap = Start-NetxChild -File $checkPs1 -ExtraArgs @("-Apply") -WindowStyle Normal
    } catch {
        Show-NetxToast (T "Update cancelled." "已取消更新。")
        return
    }
    Watch-NetxProcess $ap {
        param($p)
        $code = Get-NetxExitCode $p
        if (Test-NetxSetupCancelled $code) {
            Show-NetxToast (T "Update cancelled." "已取消更新。")
            return
        }
        if ($code -eq 0) {
            $newVer = Get-NetxVersion -ProgramRoot $prog
            Show-NetxDialog (T "Updated to $newVer.`nThe tray will restart." "已更新到 $newVer。`n托盘即将重启。") | Out-Null
            Restart-NetxTraySelf
            return
        }
        Show-NetxDialog (T "Update failed (exit $code). Retry Check for updates, or run NetX-Setup manually." "更新失败（退出码 $code）。请重试「检查更新」，或手动运行 Setup 安装包。") "Warning" | Out-Null
    }
}

$miUpdate.add_Click({
        Invoke-NetxTrayDeferred {
            Show-NetxToast (T "Checking for updates…" "正在检查更新…") "Info" 3000
            try {
                $cp = Start-NetxChild -File $checkPs1 -WindowStyle Normal
            } catch {
                Show-NetxDialog ((T "Update check failed: " "检查更新失败：") + $_.Exception.Message) "Warning" | Out-Null
                return
            }
            Watch-NetxProcess $cp {
                param($p)
                $code = Get-NetxExitCode $p
                if (Test-NetxSetupCancelled $code) { return }
                if ($code -eq 10) {
                    $ans = Show-NetxDialog (T "A newer NetX is available. Download and apply now?" "发现新版本，是否立即下载并更新？") "Question" "YesNo"
                    if ($ans -eq [System.Windows.Forms.DialogResult]::Yes) { Start-NetxUpdateApply }
                } elseif ($code -eq 0) {
                    Show-NetxDialog (T "NetX is up to date ($($script:ver))." "已是最新版本（$($script:ver)）。") | Out-Null
                } else {
                    Show-NetxDialog (T "Update check failed (exit $code)." "检查更新失败（退出码 $code）。") "Warning" | Out-Null
                }
            }
        }
    })

# --- exit ---
function Exit-NetxTray {
    try { $menu.Close() } catch {}
    $notify.Visible = $false
    $form.Close()
    [System.Windows.Forms.Application]::Exit()
}
$miExit.add_Click({ Invoke-NetxTrayDeferred { Exit-NetxTray } })
$miStopExit.add_Click({
        Invoke-NetxTrayDeferred {
            if (-not $script:sync.Healthy -and -not $script:sync.Runner) { Exit-NetxTray; return }
            Show-NetxToast (T "Stopping NetX…" "正在停止 NetX…") "Info" 3000
            $script:pending = $null
            Invoke-NetxRuntime -Action stop -Then { param($c) Exit-NetxTray }
        }
    })

$notify.ContextMenuStrip = $menu
# Single left click opens the menu like modern tray apps; double click opens the UI.
$notify.add_MouseUp({
        param($s, $e)
        if ($e.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
            $mi = [System.Windows.Forms.NotifyIcon].GetMethod("ShowContextMenu", [System.Reflection.BindingFlags]"Instance,NonPublic")
            if ($mi) { $mi.Invoke($notify, $null) }
        }
    })
$notify.add_DoubleClick({ Invoke-NetxTrayDeferred { Open-NetxUi } })

# ---------------------------------------------------------------- lifecycle
$script:uiTimer = New-Object System.Windows.Forms.Timer
$script:uiTimer.Interval = 400
$script:uiTimer.add_Tick({
        # Pick up in-place updates of version.json.
        if (((Get-Date) - $script:verCheckedAt).TotalSeconds -gt 30) {
            $script:verCheckedAt = Get-Date
            try { $script:ver = Get-NetxVersion -ProgramRoot $prog } catch {}
        }
        Update-NetxTrayState
    })
$script:verCheckedAt = Get-Date

function Start-NetxOnLaunch {
    if (-not $StartOnLaunch) { return }
    Invoke-NetxRuntime -Action start -OpenUi:(-not $QuietStart)
    if (-not $QuietStart) {
        Show-NetxToast (T "Starting NetX… the first run may take a minute." "正在启动 NetX…首次运行可能需要一分钟。")
    }
}

$form.add_Shown({
        $script:uiTimer.Start()
        if ($AutoUpdate) {
            # Apply silently first, then start; watched so the tray stays responsive.
            try {
                $up = Start-NetxChild -File $checkPs1 -ExtraArgs @("-Apply", "-Quiet", "-AutoOnly")
                Watch-NetxProcess $up { param($p) Start-NetxOnLaunch }
            } catch {
                Start-NetxOnLaunch
            }
        } else {
            if ($CheckUpdateOnLaunch) {
                try { Start-NetxChild -File $checkPs1 -ExtraArgs @("-Quiet") | Out-Null } catch {}
            }
            Start-NetxOnLaunch
        }
    })

$form.add_FormClosing({
        try { $script:uiTimer.Stop() } catch {}
        try { $script:watchTimer.Stop() } catch {}
        $script:sync.Stop = $true
        $notify.Visible = $false
        $notify.Dispose()
        foreach ($h in $script:iconHandles) { try { [void][NetxNative]::DestroyIcon($h) } catch {} }
    })

[System.Windows.Forms.Application]::Run($form)
try { $script:probePs.EndInvoke($script:probeHandle) } catch {}
try { $script:probePs.Dispose(); $script:probeRunspace.Close() } catch {}
