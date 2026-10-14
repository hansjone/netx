param(
    [int]$Port = 0,
    [string]$ProgramRoot = "",
    [string]$DataRoot = ""
)

# Change the bundled PostgreSQL port: postgresql.conf + .env (NETX_BUNDLED_PG_PORT and
# NETX_DATABASE_URL), restarting NetX when it was running. Rolls back on failure.
#   powershell -ExecutionPolicy Bypass -File set_pg_port.ps1 -Port 25432

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"
Assert-NetxAdminOrRelaunch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters -WindowStyle Normal

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"
$zh = ([cultureinfo]::CurrentUICulture.Name -match '^(zh|zh-)')
function T([string]$En, [string]$Zh) { if ($zh) { return $Zh } else { return $En } }

function Invoke-NetxRuntimeCtl([string]$Action) {
    $p = Start-NetxPowerShell -File (Join-Path $PSScriptRoot "runtime_ctl.ps1") `
        -Arguments @("-Action", $Action, "-ProgramRoot", $prog, "-DataRoot", $data) `
        -WorkingDirectory $prog -WindowStyle Hidden -PassThru
    $null = $p.Handle
    $p.WaitForExit()
    return $p.ExitCode
}

try {
    if (-not (Test-Path $envPath)) { throw "env_missing: $envPath" }
    $map = Read-DotEnv -Path $envPath
    if ((Get-DbMode -EnvMap $map) -ne "bundled") {
        throw (T "NetX uses an external database; use Reconfigure database instead." "当前使用外置数据库，请改用“重新配置数据库”。")
    }
    $old = 15432
    if ($map["NETX_BUNDLED_PG_PORT"]) { try { $old = [int]$map["NETX_BUNDLED_PG_PORT"] } catch {} }
    if ($Port -eq 0) {
        $entered = (Read-Host (T "New bundled PostgreSQL port (current $old)" "新的内置 PostgreSQL 端口（当前 $old）")).Trim()
        if (-not $entered) { Write-Host (T "Cancelled." "已取消。"); exit 0 }
        $Port = [int]$entered
    }
    if ($Port -lt 1024 -or $Port -gt 65535) { throw "invalid_port: $Port (1024-65535)" }
    if ($Port -eq $old) {
        Write-Host ((T "Port unchanged: " "端口未变：") + $Port) -ForegroundColor Green
        exit 0
    }
    if (-not (Test-NetxTcpPortFree -HostName "127.0.0.1" -Port $Port)) {
        throw ((T "Port $Port is already in use." "端口 $Port 已被占用。"))
    }

    $pgData = if ($map["NETX_BUNDLED_PG_DATA_DIR"]) { $map["NETX_BUNDLED_PG_DATA_DIR"] -replace '/', '\' } else { Join-Path $data "pgdata" }
    $conf = Join-Path $pgData "postgresql.conf"
    if (-not (Test-Path $conf)) { throw "postgresql_conf_missing: $conf" }
    $pgCtl = Join-Path $prog "postgres\pgsql\bin\pg_ctl.exe"

    $bind = if ($map["NETX_HOST"]) { $map["NETX_HOST"] } else { "127.0.0.1" }
    $apiPort = if ($map["NETX_PORT"]) { [int]$map["NETX_PORT"] } else { 8890 }
    $clientHost = Get-NetxClientHost -BindHost $bind
    $wasRunning = Test-NetxApiHealthy -HostName $clientHost -Port $apiPort

    Write-Host ("==> " + (T "Stopping NetX" "停止 NetX"))
    $null = Invoke-NetxRuntimeCtl -Action "stop"
    if (Test-Path $pgCtl) {
        $status = & $pgCtl -D $pgData status 2>&1 | Out-String
        if ($status -match "server is running") { & $pgCtl -D $pgData -m fast -w stop | Out-Null }
    }

    $confBak = "$conf.bak"
    $envBak = "$envPath.bak"
    Copy-Item -LiteralPath $conf -Destination $confBak -Force
    Copy-Item -LiteralPath $envPath -Destination $envBak -Force

    $confText = Get-Content -Raw -Path $conf
    if ($confText -match '(?m)^\s*port\s*=') {
        $confText = $confText -replace '(?m)^\s*port\s*=\s*\d+', "port = $Port"
    } else {
        $confText += "`nport = $Port`n"
    }
    # No BOM: PowerShell 5.1 `Set-Content -Encoding utf8` adds one and postgres then
    # rejects postgresql.conf (syntax error at line 1) and will not start.
    [IO.File]::WriteAllText($conf, $confText, (New-Object System.Text.UTF8Encoding($false)))

    $values = @{ "NETX_BUNDLED_PG_PORT" = "$Port" }
    $url = [string]$map["NETX_DATABASE_URL"]
    if ($url) {
        $values["NETX_DATABASE_URL"] = ($url -replace '@(127\.0\.0\.1|localhost):\d+/', "@`$1:$Port/")
    }
    Write-DotEnvValue -Path $envPath -Values $values
    Write-Host ("==> " + (T "Bundled PostgreSQL port set to " "内置 PostgreSQL 端口已改为 ") + $Port)

    if ($wasRunning) {
        Write-Host ("==> " + (T "Starting NetX" "启动 NetX"))
        $null = Invoke-NetxRuntimeCtl -Action "start"
        if (-not (Wait-NetxApiHealthy -HostName $clientHost -Port $apiPort -TimeoutSec 180)) {
            Write-Host ("[ERR] " + (T "NetX did not start; restoring the previous port." "NetX 未能启动，正在恢复原端口。")) -ForegroundColor Red
            $null = Invoke-NetxRuntimeCtl -Action "stop"
            Copy-Item -LiteralPath $confBak -Destination $conf -Force
            Copy-Item -LiteralPath $envBak -Destination $envPath -Force
            $null = Invoke-NetxRuntimeCtl -Action "start"
            throw "set_pg_port_failed"
        }
    }
    Remove-Item -LiteralPath $confBak, $envBak -Force -ErrorAction SilentlyContinue
    Write-Host ("==> " + (T "Done." "完成。")) -ForegroundColor Green
} catch {
    Write-Host "[ERR] $($_.Exception.Message)" -ForegroundColor Red
    if ([Environment]::UserInteractive) { Read-Host (T "Press Enter to close" "按回车关闭") | Out-Null }
    exit 1
}
