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

Write-Host "==> Program root: $prog"
Write-Host "==> Data root:    $data"
Write-Host "==> Env file:     $envPath"

if (-not (Test-Path $data)) {
    New-Item -ItemType Directory -Path $data -Force | Out-Null
}
foreach ($sub in @("data", "data\auth", "data\runtime", "backups", "pgdata")) {
    $p = Join-Path $data $sub
    if (-not (Test-Path $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
    }
}

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
        Write-Host "Choose database mode:"
        Write-Host "  1) bundled  — use NetX portable PostgreSQL (default for new installs)"
        Write-Host "  2) external — connect to an existing PostgreSQL (Linux / already deployed)"
        $choice = Read-Host "Enter 1 or 2"
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
$values["NETX_UI_DIST_DIR"] = "web/dist"

# Point runtime data dirs into the data root (absolute).
$values["NETX_AUTH_MCP_TOKEN_FILE"] = ((Join-Path $data "data\auth\mcp_token") -replace '\\', '/')
$values["NETX_SCHEDULER_HEARTBEAT_PATH"] = ((Join-Path $data "data\runtime\scheduler_heartbeat.json") -replace '\\', '/')

if ($DbMode -eq "bundled") {
    $pgsql = Join-Path $prog "postgres\pgsql"
    if (-not (Test-Path (Join-Path $pgsql "bin\initdb.exe"))) {
        # In-repo layout
        $alt = Join-Path $PSScriptRoot "postgres\pgsql"
        if (Test-Path (Join-Path $alt "bin\initdb.exe")) {
            $pgsql = $alt
        } else {
            throw "bundled_postgres_missing: run packaging\download_postgres.ps1 first (expected $pgsql)"
        }
    }
    if (-not $BundledPassword) {
        if ($NonInteractive) {
            $BundledPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
        } else {
            $sec = Read-Host -Prompt "Password for bundled role 'netx' (empty = auto-generate)" -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            try {
                $BundledPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            }
            if (-not $BundledPassword) {
                $BundledPassword = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
                Write-Host "Generated password (saved in .env only)."
            }
        }
    }
    $pgData = Join-Path $data "pgdata"
    $values["NETX_BUNDLED_PG_PORT"] = "$BundledPort"
    $values["NETX_BUNDLED_PG_DATA_DIR"] = ($pgData -replace '\\', '/')
    $pwFile = Join-Path $data "pg_netx.pw"
    Set-Content -Path $pwFile -Value $BundledPassword -Encoding ascii -NoNewline
    $encPw = [uri]::EscapeDataString($BundledPassword)
    $values["NETX_DATABASE_URL"] = "postgresql+psycopg://netx:${encPw}@127.0.0.1:${BundledPort}/netx"

    # Initialize cluster if needed
    $initdb = Join-Path $pgsql "bin\initdb.exe"
    $pgCtl = Join-Path $pgsql "bin\pg_ctl.exe"
    $psql = Join-Path $pgsql "bin\psql.exe"
    $createdb = Join-Path $pgsql "bin\createdb.exe"
    $createuser = Join-Path $pgsql "bin\createuser.exe"

    if (-not (Test-Path (Join-Path $pgData "PG_VERSION"))) {
        Write-Host "==> initdb $pgData"
        $pwSuper = Join-Path $data "pg_super.pw"
        Set-Content -Path $pwSuper -Value $BundledPassword -Encoding ascii -NoNewline
        & $initdb -D $pgData -U postgres -A password --pwfile=$pwSuper -E UTF8 --locale=C
        if ($LASTEXITCODE -ne 0) { throw "initdb_failed" }
    }

    # Ensure listen / port in postgresql.conf
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

    Write-Host "==> Starting bundled PostgreSQL for bootstrap"
    & $pgCtl -D $pgData -l (Join-Path $data "pgdata\pg.log") start
    Start-Sleep -Seconds 2

    $env:PGPASSWORD = $BundledPassword
    try {
        $role = & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='netx'"
        if ($role -notmatch "1") {
            & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -v ON_ERROR_STOP=1 `
                -c "CREATE ROLE netx LOGIN PASSWORD '$BundledPassword';"
        } else {
            & $psql -h 127.0.0.1 -p $BundledPort -U postgres -d postgres -v ON_ERROR_STOP=1 `
                -c "ALTER ROLE netx WITH LOGIN PASSWORD '$BundledPassword';"
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
