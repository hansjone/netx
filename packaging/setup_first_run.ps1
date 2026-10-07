param(
    [ValidateSet("bundled", "external", "")]
    [string]$DbMode = "",
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [string]$ExternalDatabaseUrl = "",
    [string]$ExternalDatabaseUrlFile = "",
    [string]$CredentialSecretKey = "",
    [string]$CredentialSecretKeyFile = "",
    [string]$BundledPassword = "",
    [int]$BundledPort = 15432,
    [switch]$NonInteractive = $false,
    [switch]$SkipExternalProbe = $false
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"
$zh = ([cultureinfo]::CurrentUICulture.Name -match '^(zh|zh-)')

function T([string]$En, [string]$Zh) {
    if ($zh) { return $Zh } else { return $En }
}

Write-Host ("==> " + (T "Program root:" "程序目录:") + " $prog")
Write-Host ("==> " + (T "Data root:" "数据目录:") + "    $data")
Write-Host ("==> " + (T "Env file:" "环境文件:") + "     $envPath")

Ensure-NetxDataDirectories -DataRoot $data

$existing = Read-DotEnv -Path $envPath
if (-not $DbMode) {
    if ($NonInteractive) {
        if ($existing.ContainsKey("NETX_DB_MODE")) {
            $DbMode = $existing["NETX_DB_MODE"]
        } elseif ($existing.ContainsKey("NETX_DATABASE_URL")) {
            $DbMode = "external"
        } else {
            $DbMode = "bundled"
        }
    } else {
        Write-Host ""
        Write-Host (T "Choose database mode:" "选择数据库模式:") -ForegroundColor Cyan
        Write-Host (T "  1) bundled  — NetX portable PostgreSQL (offline / default)" "  1) bundled  — NetX 内置便携 PostgreSQL（可离线，默认）")
        Write-Host (T "  2) external — existing PostgreSQL (you provide connection URL)" "  2) external — 已有外置 PostgreSQL（需填写连接串）")
        Write-Host ""
        $choice = Read-Host (T "Enter 1 or 2" "请输入 1 或 2")
        if ($choice -eq "2") { $DbMode = "external" } else { $DbMode = "bundled" }
    }
}
$DbMode = $DbMode.Trim().ToLowerInvariant()
if ($DbMode -ne "bundled" -and $DbMode -ne "external") {
    throw "invalid_db_mode: $DbMode"
}

$values = @{}
$values["NETX_DB_MODE"] = $DbMode
$values["NETX_HOST"] = "127.0.0.1"
$values["NETX_PORT"] = "8890"
# Absolute UI path when present; else relative for source trees.
$distAbs = Join-Path $prog "web\dist"
if (Test-Path (Join-Path $distAbs "index.html")) {
    $values["NETX_UI_DIST_DIR"] = (ConvertTo-NetxFsPath $distAbs)
} else {
    $values["NETX_UI_DIST_DIR"] = "web/dist"
}

# All runtime data under data root (never under Program Files).
$dataEnv = Get-NetxDataEnvMap -DataRoot $data
foreach ($k in $dataEnv.Keys) { $values[$k] = $dataEnv[$k] }

# Fernet key for managed-NE password encryption (required to save SSH passwords).
# Priority: -CredentialSecretKey(File) > interactive prompt > existing .env > generate.
if ($CredentialSecretKeyFile) {
    if (-not (Test-Path -LiteralPath $CredentialSecretKeyFile)) {
        throw "credential_secret_key_file_missing: $CredentialSecretKeyFile"
    }
    $CredentialSecretKey = [IO.File]::ReadAllText($CredentialSecretKeyFile).Trim()
}
if (-not $CredentialSecretKey -and -not $NonInteractive) {
    Write-Host ""
    Write-Host (T `
        "Optional: paste NETX_CREDENTIAL_SECRET_KEY from an existing install" `
        "可选: 粘贴已有安装的 NETX_CREDENTIAL_SECRET_KEY（便于沿用已加密凭据）") -ForegroundColor Cyan
    Write-Host (T `
        "Leave empty to keep existing .env key, or auto-generate if none." `
        "留空则保留已有 .env 中的密钥；若没有则自动生成。")
    $CredentialSecretKey = (Read-Host "NETX_CREDENTIAL_SECRET_KEY").Trim()
}
if ($CredentialSecretKey) {
    $values["NETX_CREDENTIAL_SECRET_KEY"] = $CredentialSecretKey.Trim()
    Write-Host "==> Using provided NETX_CREDENTIAL_SECRET_KEY" -ForegroundColor Green
} elseif ($existing.ContainsKey("NETX_CREDENTIAL_SECRET_KEY") -and $existing["NETX_CREDENTIAL_SECRET_KEY"]) {
    $values["NETX_CREDENTIAL_SECRET_KEY"] = $existing["NETX_CREDENTIAL_SECRET_KEY"]
    Write-Host "==> Keeping existing NETX_CREDENTIAL_SECRET_KEY from .env" -ForegroundColor Green
} else {
    $values["NETX_CREDENTIAL_SECRET_KEY"] = New-NetxFernetKey
    Write-Host "==> Generated NETX_CREDENTIAL_SECRET_KEY (saved in .env)" -ForegroundColor Green
}

if ($DbMode -eq "bundled") {
    $pgsql = Join-Path $prog "postgres\pgsql"
    if (-not (Test-Path (Join-Path $pgsql "bin\initdb.exe"))) {
        $alt = Join-Path $PSScriptRoot "postgres\pgsql"
        if (Test-Path (Join-Path $alt "bin\initdb.exe")) {
            $pgsql = $alt
        } else {
            throw (T `
                "bundled_postgres_missing: incomplete package (expected $pgsql). Offline Setup must include postgres; rebuild with build_release.ps1 on a machine that already ran download_postgres.ps1." `
                "缺少内置 PostgreSQL（期望路径 $pgsql）。离线安装包必须自带数据库；请在已运行过 download_postgres.ps1 的机器上重新 build_release。")
        }
    }
    if (-not $BundledPassword) {
        if ($NonInteractive) {
            $BundledPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
        } else {
            $sec = Read-Host -Prompt (T "Password for bundled role 'netx' (empty = auto-generate)" "内置数据库用户 netx 的密码（回车=自动生成）") -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            try {
                $BundledPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
            if (-not $BundledPassword) {
                $BundledPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
                Write-Host (T "Generated password (saved in .env only)." "已自动生成密码（仅保存在 .env）。")
            }
        }
    }
    $pgData = Join-Path $data "pgdata"
    $values["NETX_BUNDLED_PG_PORT"] = "$BundledPort"
    $values["NETX_BUNDLED_PG_DATA_DIR"] = (ConvertTo-NetxFsPath $pgData)
    $pwFile = Join-Path $data "pg_netx.pw"
    Set-Content -Path $pwFile -Value $BundledPassword -Encoding ascii -NoNewline
    $encPw = [uri]::EscapeDataString($BundledPassword)
    $values["NETX_DATABASE_URL"] = "postgresql+psycopg://netx:${encPw}@127.0.0.1:${BundledPort}/netx"

    $initdb = Join-Path $pgsql "bin\initdb.exe"
    $pgCtl = Join-Path $pgsql "bin\pg_ctl.exe"
    $psql = Join-Path $pgsql "bin\psql.exe"
    $sqlPw = ConvertTo-NetxSqlLiteral $BundledPassword

    if (-not (Test-Path (Join-Path $pgData "PG_VERSION"))) {
        Write-Host "==> initdb $pgData"
        $pwSuper = Join-Path $data "pg_super.pw"
        Set-Content -Path $pwSuper -Value $BundledPassword -Encoding ascii -NoNewline
        & $initdb -D $pgData -U postgres -A password --pwfile=$pwSuper -E UTF8 --locale=C
        if ($LASTEXITCODE -ne 0) { throw "initdb_failed" }
    }

    $conf = Join-Path $pgData "postgresql.conf"
    $hba = Join-Path $pgData "pg_hba.conf"
    if (Test-Path $conf) {
        $confText = Get-Content -Raw -Path $conf
        if ($confText -notmatch "(?m)^\s*port\s*=") {
            Add-Content -Path $conf -Value "`nport = $BundledPort`nlisten_addresses = '127.0.0.1'`n"
        } else {
            $confText = $confText -replace '(?m)^\s*port\s*=\s*\d+', "port = $BundledPort"
            if ($confText -notmatch "(?m)^\s*listen_addresses\s*=") {
                $confText += "`nlisten_addresses = '127.0.0.1'`n"
            } else {
                $confText = $confText -replace "(?m)^\s*listen_addresses\s*=\s*'[^']*'", "listen_addresses = '127.0.0.1'"
            }
            Set-Content -Path $conf -Value $confText -Encoding utf8
        }
    }
    if (Test-Path $hba) {
        $hbaText = Get-Content -Raw -Path $hba
        if ($hbaText -notmatch "127\.0\.0\.1/32") {
            Add-Content -Path $hba -Value "`nhost all all 127.0.0.1/32 scram-sha-256`n"
        }
    }

    $status = & $pgCtl -D $pgData status 2>&1 | Out-String
    if ($status -notmatch "server is running") {
        if (-not (Test-NetxTcpPortFree -HostName "127.0.0.1" -Port $BundledPort)) {
            throw "port_in_use: 127.0.0.1:$BundledPort already occupied (bundled Postgres)"
        }
        Write-Host "==> Starting bundled PostgreSQL for bootstrap"
        & $pgCtl -D $pgData -l (Join-Path $pgData "pg.log") start
        if ($LASTEXITCODE -ne 0) { throw "pg_start_failed" }
        Start-Sleep -Seconds 2
    }

    $env:PGPASSWORD = $BundledPassword
    try {
        function Invoke-NetxPsql {
            param([string]$Sql, [string]$Database = "postgres")
            $out = & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d $Database -v ON_ERROR_STOP=1 -tAc $Sql 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw "psql_failed ($LASTEXITCODE): $Sql`n$out"
            }
            return (($out | Out-String).Trim())
        }

        $role = Invoke-NetxPsql -Sql "SELECT 1 FROM pg_roles WHERE rolname='netx'"
        if ($role -eq "1") {
            Invoke-NetxPsql -Sql "ALTER ROLE netx WITH LOGIN PASSWORD '$sqlPw'" | Out-Null
        } else {
            Invoke-NetxPsql -Sql "CREATE ROLE netx LOGIN PASSWORD '$sqlPw'" | Out-Null
        }
        $db = Invoke-NetxPsql -Sql "SELECT 1 FROM pg_database WHERE datname='netx'"
        if ($db -ne "1") {
            Invoke-NetxPsql -Sql "CREATE DATABASE netx OWNER netx" | Out-Null
        }
        Invoke-NetxPsql -Sql "GRANT ALL PRIVILEGES ON DATABASE netx TO netx" | Out-Null
        # Verify login as app role (catches auth/hba mismatches early).
        $prev = $env:PGPASSWORD
        $env:PGPASSWORD = $BundledPassword
        try {
            $ping = & $psql -h 127.0.0.1 -p $BundledPort -U netx -d netx -v ON_ERROR_STOP=1 -tAc "SELECT 1" 2>&1
            if ($LASTEXITCODE -ne 0 -or (($ping | Out-String).Trim()) -ne "1") {
                throw "netx_role_login_failed: $ping"
            }
        } finally {
            $env:PGPASSWORD = $prev
        }
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    Write-Host "==> Bundled Postgres ready on 127.0.0.1:$BundledPort" -ForegroundColor Green
} else {
    if ($ExternalDatabaseUrlFile) {
        if (-not (Test-Path -LiteralPath $ExternalDatabaseUrlFile)) {
            throw "external_url_file_missing: $ExternalDatabaseUrlFile"
        }
        $ExternalDatabaseUrl = [IO.File]::ReadAllText($ExternalDatabaseUrlFile).Trim()
    }
    if (-not $ExternalDatabaseUrl) {
        if ($existing.ContainsKey("NETX_DATABASE_URL") -and $existing["NETX_DATABASE_URL"]) {
            $ExternalDatabaseUrl = $existing["NETX_DATABASE_URL"]
        } elseif ($NonInteractive) {
            throw "external_url_required"
        } else {
            Write-Host ""
            Write-Host (T `
                "Enter PostgreSQL URL (database/role must already exist):" `
                "请输入 PostgreSQL 连接串（库和用户需事先建好）:") -ForegroundColor Cyan
            Write-Host "  postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME"
            Write-Host (T `
                "Example: postgresql+psycopg://netx:secret@10.0.0.8:5432/netx" `
                "示例: postgresql+psycopg://netx:secret@10.0.0.8:5432/netx")
            $ExternalDatabaseUrl = Read-Host "NETX_DATABASE_URL"
        }
    }
    if (-not $ExternalDatabaseUrl) {
        throw "external_url_required"
    }
    $ExternalDatabaseUrl = $ExternalDatabaseUrl.Trim()
    $values["NETX_DATABASE_URL"] = $ExternalDatabaseUrl
    Write-Host "==> External Postgres configured" -ForegroundColor Green

    if (-not $SkipExternalProbe) {
        # Probe with bundled psql when available (wizard already tested; this covers CLI reconfigure).
        $psqlProbe = Join-Path $prog "postgres\pgsql\bin\psql.exe"
        $testHelper = Join-Path $PSScriptRoot "installer\test_pg_conn.ps1"
        if (-not (Test-Path $testHelper)) {
            $testHelper = Join-Path $PSScriptRoot "test_pg_conn.ps1"
        }
        if ((Test-Path $psqlProbe) -and ($ExternalDatabaseUrl -match '^(?:postgresql(?:\+psycopg)?|postgres)://([^:]+):([^@]*)@([^:/]+):?(\d+)?/([^?\s]+)')) {
            $u = [uri]::UnescapeDataString($Matches[1])
            $pw = [uri]::UnescapeDataString($Matches[2])
            $h = $Matches[3]
            $po = if ($Matches[4]) { [int]$Matches[4] } else { 5432 }
            $dbn = [uri]::UnescapeDataString($Matches[5])
            if (Test-Path $testHelper) {
                Write-Host "==> Probing external PostgreSQL..."
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $testHelper `
                    -PgHost $h -Port $po -User $u -Password $pw -Database $dbn -PsqlPath $psqlProbe
                if ($LASTEXITCODE -ne 0) {
                    throw "external_db_probe_failed"
                }
                Write-Host "==> External PostgreSQL OK" -ForegroundColor Green
            } else {
                $env:PGPASSWORD = $pw
                try {
                    $ping = & $psqlProbe -h $h -p $po -U $u -d $dbn -t -A -c "SELECT 1" 2>&1
                    if ($LASTEXITCODE -ne 0 -or (($ping | Out-String).Trim()) -notmatch '1') {
                        throw "external_db_probe_failed: $ping"
                    }
                } finally {
                    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
                }
            }
        }
    }
}

Write-DotEnvValue -Path $envPath -Values $values
Write-Host ""
Write-Host "Wrote $envPath" -ForegroundColor Green

# Official Setup ships python/runtime + .venv (build_release -CreateVenv).
$venvPy = Join-Path $prog ".venv\Scripts\python.exe"
try {
    $null = Ensure-NetxVenv -ProgramRoot $prog
    Write-Host "==> Bundled Python venv OK: $venvPy" -ForegroundColor Green
} catch {
    if (Test-Path $venvPy) {
        throw
    }
    Write-Host "==> Bundled .venv missing — creating one (Setup should normally ship it)." -ForegroundColor Yellow
    Write-Host "[WARN] $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "       Install a Setup built with: packaging\build_release.ps1 -CreateVenv" -ForegroundColor Yellow
}

Write-Host ""
Write-Host (T "Setup complete." "首次配置完成。") -ForegroundColor Green
if (-not $NonInteractive) {
    Write-Host (T `
        "Next: Start Menu → NetX Tray  (or press Y below)." `
        "下一步: 开始菜单 → NetX 托盘  （或在下方按 Y 立即启动）。")
    $ans = Read-Host (T "Start NetX tray now? [Y/n]" "现在启动 NetX 托盘？[Y/n]")
    if ($ans -notmatch '^[Nn]') {
        try {
            Start-NetxPowerShell -File (Join-Path $PSScriptRoot "netx_tray.ps1") `
                -Arguments @("-ProgramRoot", $prog, "-DataRoot", $data, "-StartOnLaunch") `
                -WorkingDirectory $prog -WindowStyle Hidden | Out-Null
            Write-Host (T "Tray started." "托盘已启动。") -ForegroundColor Green
        } catch {
            Write-Host "[WARN] $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "Next: .\packaging\start_netx_app.ps1  or NetX-Tray.cmd"
}
