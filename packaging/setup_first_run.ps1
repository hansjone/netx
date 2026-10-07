param(
    [ValidateSet("bundled", "external", "")]
    [string]$DbMode = "",
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [string]$ExternalDatabaseUrl = "",
    [string]$BundledPassword = "",
    [int]$BundledPort = 15432,
    [switch]$NonInteractive = $false
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
        Write-Host (T "Choose database mode:" "选择数据库模式:")
        Write-Host (T "  1) bundled  — use NetX portable PostgreSQL (default for new installs)" "  1) bundled  — 使用 NetX 内置便携 PostgreSQL（新安装默认）")
        Write-Host (T "  2) external — connect to an existing PostgreSQL (Linux / already deployed)" "  2) external — 连接已有 PostgreSQL")
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
        $role = & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='netx'"
        if ($role -notmatch "1") {
            & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -v ON_ERROR_STOP=1 `
                -c "CREATE ROLE netx LOGIN PASSWORD '$sqlPw';"
        } else {
            & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -v ON_ERROR_STOP=1 `
                -c "ALTER ROLE netx WITH LOGIN PASSWORD '$sqlPw';"
        }
        $db = & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='netx'"
        if ($db -notmatch "1") {
            & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -v ON_ERROR_STOP=1 `
                -c "CREATE DATABASE netx OWNER netx;"
        }
        & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -v ON_ERROR_STOP=1 `
            -c "GRANT ALL PRIVILEGES ON DATABASE netx TO netx;"
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    Write-Host "==> Bundled Postgres ready on 127.0.0.1:$BundledPort" -ForegroundColor Green
} else {
    if (-not $ExternalDatabaseUrl) {
        if ($existing.ContainsKey("NETX_DATABASE_URL") -and $existing["NETX_DATABASE_URL"]) {
            $ExternalDatabaseUrl = $existing["NETX_DATABASE_URL"]
        } elseif ($NonInteractive) {
            throw "external_url_required"
        } else {
            $ExternalDatabaseUrl = Read-Host "NETX_DATABASE_URL (postgresql+psycopg://user:pass@host:5432/netx)"
        }
    }
    if (-not $ExternalDatabaseUrl) {
        throw "external_url_required"
    }
    $values["NETX_DATABASE_URL"] = $ExternalDatabaseUrl
    Write-Host "==> External Postgres configured (ensure role/db exist; see scripts\init_pg.ps1)" -ForegroundColor Green
}

Write-DotEnvValue -Path $envPath -Values $values
Write-Host ""
Write-Host "Wrote $envPath" -ForegroundColor Green
Write-Host "Next: .\packaging\start_netx_app.ps1"
