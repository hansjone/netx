param(
    [string]$PgHost = "127.0.0.1",
    [int]$PgPort = 5432,
    [string]$SuperUser = "postgres",
    [string]$SuperPassword = "",
    [string]$NetxUser = "netx",
    [string]$NetxPassword = "netx",
    [string]$NetxDatabase = "netx",
    [switch]$SkipConnectionTest = $false
)

$ErrorActionPreference = "Stop"

function Invoke-Psql {
    param(
        [string]$Database,
        [string]$Sql
    )
    & psql -h $PgHost -p $PgPort -U $SuperUser -d $Database -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "psql_failed (db=$Database)"
    }
}

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    throw "psql_not_found: please install PostgreSQL client tools and ensure psql is in PATH"
}

if (-not $SuperPassword) {
    $sec = Read-Host -Prompt "PostgreSQL superuser password for '$SuperUser'" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        $SuperPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

if (-not $SuperPassword) {
    throw "superuser_password_required"
}

$env:PGPASSWORD = $SuperPassword

try {
    Write-Host "==> Ensuring role '$NetxUser'"
    $roleSql = @"
DO `$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$NetxUser') THEN
        CREATE ROLE $NetxUser LOGIN PASSWORD '$NetxPassword';
    ELSE
        ALTER ROLE $NetxUser WITH LOGIN PASSWORD '$NetxPassword';
    END IF;
END
`$\$;
"@
    Invoke-Psql -Database "postgres" -Sql $roleSql

    Write-Host "==> Ensuring database '$NetxDatabase'"
    $dbExists = & psql -h $PgHost -p $PgPort -U $SuperUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$NetxDatabase'"
    if ($LASTEXITCODE -ne 0) {
        throw "psql_failed (query database exists)"
    }
    if (-not ($dbExists -match "1")) {
        Invoke-Psql -Database "postgres" -Sql "CREATE DATABASE $NetxDatabase OWNER $NetxUser;"
    } else {
        Write-Host "Database already exists: $NetxDatabase"
    }

    Write-Host "==> Granting database privileges"
    Invoke-Psql -Database "postgres" -Sql "GRANT ALL PRIVILEGES ON DATABASE $NetxDatabase TO $NetxUser;"
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

if (-not $SkipConnectionTest) {
    Write-Host "==> Verifying netx connection"
    $env:PGPASSWORD = $NetxPassword
    try {
        & psql -h $PgHost -p $PgPort -U $NetxUser -d $NetxDatabase -c "select current_user, current_database();"
        if ($LASTEXITCODE -ne 0) {
            throw "netx_connection_test_failed"
        }
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Done."
Write-Host "Use this DATABASE URL in netx:"
Write-Host "postgresql+psycopg://${NetxUser}:${NetxPassword}@${PgHost}:$PgPort/$NetxDatabase"
