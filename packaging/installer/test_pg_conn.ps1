param(
    [Parameter(Mandatory = $true)][string]$PgHost,
    [int]$Port = 5432,
    [Parameter(Mandatory = $true)][string]$User,
    [string]$Password = "",
    [string]$PasswordFile = "",
    [Parameter(Mandatory = $true)][string]$Database,
    [Parameter(Mandatory = $true)][string]$PsqlPath,
    [string]$OutUrlFile = "",
    [string]$OutErrFile = ""
)

$ErrorActionPreference = "Stop"

function Write-ErrFile([string]$Message) {
    if ($OutErrFile) {
        Set-Content -LiteralPath $OutErrFile -Value $Message -Encoding utf8
    }
    [Console]::Error.WriteLine($Message)
}

if (-not (Test-Path -LiteralPath $PsqlPath)) {
    Write-ErrFile "psql_not_found: $PsqlPath"
    exit 2
}
if ($PasswordFile) {
    if (-not (Test-Path -LiteralPath $PasswordFile)) {
        Write-ErrFile "password_file_missing"
        exit 3
    }
    $Password = [IO.File]::ReadAllText($PasswordFile).TrimEnd("`r", "`n")
}
if ([string]::IsNullOrWhiteSpace($PgHost)) {
    Write-ErrFile "host_required"
    exit 3
}
if ([string]::IsNullOrWhiteSpace($User) -or [string]::IsNullOrWhiteSpace($Database)) {
    Write-ErrFile "user_and_database_required"
    exit 3
}
if ([string]::IsNullOrWhiteSpace($Password)) {
    Write-ErrFile "password_required"
    exit 3
}
if ($Port -lt 1 -or $Port -gt 65535) {
    Write-ErrFile "invalid_port: $Port"
    exit 3
}

$encUser = [uri]::EscapeDataString($User)
$encPw = [uri]::EscapeDataString($Password)
$encDb = [uri]::EscapeDataString($Database)
$url = "postgresql+psycopg://${encUser}:${encPw}@${PgHost}:${Port}/${encDb}"

$psqlDir = Split-Path -Parent $PsqlPath
$prevPath = $env:PATH
$prevPw = $env:PGPASSWORD
try {
    $env:PATH = "$psqlDir;$env:PATH"
    $env:PGPASSWORD = $Password
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PsqlPath
    $psi.Arguments = "-h `"$PgHost`" -p $Port -U `"$User`" -d `"$Database`" -v ON_ERROR_STOP=1 -t -A -c `"SELECT 1`""
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.WorkingDirectory = $psqlDir
    $p = [Diagnostics.Process]::Start($psi)
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) {
        $msg = (($stderr + "`n" + $stdout).Trim())
        if (-not $msg) { $msg = "psql_exit_$($p.ExitCode)" }
        Write-ErrFile "connection_failed: $msg"
        exit 1
    }
    if (($stdout.Trim()) -notmatch '1') {
        Write-ErrFile "unexpected_result: $($stdout.Trim())"
        exit 1
    }
} catch {
    Write-ErrFile ("connection_failed: " + $_.Exception.Message)
    exit 1
} finally {
    $env:PATH = $prevPath
    if ($null -eq $prevPw) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    } else {
        $env:PGPASSWORD = $prevPw
    }
}

if ($OutUrlFile) {
    $dir = Split-Path -Parent $OutUrlFile
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Set-Content -LiteralPath $OutUrlFile -Value $url -Encoding ascii -NoNewline
}

Write-Output "ok"
exit 0
