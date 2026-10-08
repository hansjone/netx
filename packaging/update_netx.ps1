param(
    [Parameter(Mandatory = $true)]
    [string]$PackagePath,
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [switch]$SkipBackup = $false,
    [switch]$NoStart = $false
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot

if (-not (Test-Path $PackagePath)) {
    throw "package_not_found: $PackagePath"
}

$work = Join-Path $env:TEMP ("netx-update-" + [guid]::NewGuid().ToString("n"))
New-Item -ItemType Directory -Path $work -Force | Out-Null

try {
    Write-Host "==> Extracting update package"
    # Preferred end-user path is NetX-Setup-*.exe (check_update / offline installer).
    # This script remains for legacy/dev .zip packages (build_release.ps1 -CreateZip).
    if ($PackagePath.ToLowerInvariant().EndsWith(".zip")) {
        Expand-Archive -Path $PackagePath -DestinationPath $work -Force
    } elseif ($PackagePath.ToLowerInvariant().EndsWith(".exe")) {
        throw "unsupported_package: run NetX-Setup-*.exe (upgrade keeps DB), or use check_update.ps1 -Apply"
    } else {
        throw "unsupported_package: use a .zip from build_release.ps1 -CreateZip, or NetX-Setup-*.exe"
    }

    $src = $work
    # If zip has a single top-level folder, use it.
    $kids = @(Get-ChildItem -Path $work -Directory)
    if ($kids.Count -eq 1 -and (Test-Path (Join-Path $kids[0].FullName "version.json"))) {
        $src = $kids[0].FullName
    } elseif (-not (Test-Path (Join-Path $work "version.json"))) {
        $nested = Get-ChildItem -Path $work -Recurse -Filter "version.json" | Select-Object -First 1
        if ($nested) { $src = Split-Path -Parent $nested.FullName }
    }

    $newVerFile = Join-Path $src "version.json"
    if (-not (Test-Path $newVerFile)) {
        throw "version.json missing in package"
    }
    $newVer = (Get-Content -Raw $newVerFile | ConvertFrom-Json).version
    $oldVer = Get-NetxVersion -ProgramRoot $prog
    Write-Host "==> Updating $oldVer -> $newVer"

    if (-not $SkipBackup) {
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $bak = Join-Path $data "backups\pre-update-$stamp"
        New-Item -ItemType Directory -Path $bak -Force | Out-Null
        $envFile = Join-Path $data ".env"
        if (Test-Path $envFile) {
            Copy-Item $envFile (Join-Path $bak ".env")
        }
        # Best-effort logical backup when psql is available (bundled or PATH).
        $map = Read-DotEnv -Path $envFile
        $pgDump = $null
        foreach ($c in @(
                (Join-Path $prog "postgres\pgsql\bin\pg_dump.exe"),
                (Join-Path $PSScriptRoot "postgres\pgsql\bin\pg_dump.exe")
            )) {
            if (Test-Path $c) { $pgDump = $c; break }
        }
        if (-not $pgDump) {
            $cmd = Get-Command pg_dump -ErrorAction SilentlyContinue
            if ($cmd) { $pgDump = $cmd.Source }
        }
        if ($pgDump -and $map["NETX_DATABASE_URL"] -match 'postgresql\+?[^:]*://([^:]+):([^@]+)@([^:/]+):?(\d+)?/([^?\s]+)') {
            $u = $Matches[1]; $p = [uri]::UnescapeDataString($Matches[2]); $h = $Matches[3]
            $pt = if ($Matches[4]) { $Matches[4] } else { "5432" }
            $dbn = $Matches[5]
            $dumpOut = Join-Path $bak "netx.dump"
            Write-Host "==> pg_dump -> $dumpOut"
            $env:PGPASSWORD = $p
            try {
                & $pgDump -h $h -p $pt -U $u -d $dbn -Fc -f $dumpOut
            } catch {
                Write-Host "[WARN] pg_dump failed: $($_.Exception.Message)" -ForegroundColor Yellow
            } finally {
                Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
            }
        }
        Write-Host "==> Backup: $bak"
    }

    Write-Host "==> Stopping services"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop_netx_app.ps1") `
        -ProgramRoot $prog -DataRoot $data

    # Replace program layers only — never wipe data root.
    # Include python/ (portable runtime) — required with shipped .venv since 0.4.6.
    $replaceDirs = @("netx_api", "alembic", "web", "packaging", "scripts", "postgres", "packages", "python")
    foreach ($name in $replaceDirs) {
        $from = Join-Path $src $name
        $to = Join-Path $prog $name
        if (-not (Test-Path $from)) { continue }
        Write-Host "==> Replacing $name"
        if (Test-Path $to) {
            Remove-Item -Recurse -Force $to
        }
        Copy-Item -Path $from -Destination $to -Recurse -Force
    }
    foreach ($f in @("requirements.txt", "pyproject.toml", "alembic.ini", "version.json", "README.md", "LICENSE")) {
        $from = Join-Path $src $f
        if (Test-Path $from) {
            Copy-Item -Path $from -Destination (Join-Path $prog $f) -Force
        }
    }
    # Legacy top-level runtime/ (older packages) + shipped .venv
    if (Test-Path (Join-Path $src "runtime")) {
        $rt = Join-Path $prog "runtime"
        if (Test-Path $rt) { Remove-Item -Recurse -Force $rt }
        Copy-Item -Path (Join-Path $src "runtime") -Destination $rt -Recurse -Force
    }
    if (Test-Path (Join-Path $src ".venv")) {
        Write-Host "==> Replacing .venv from package"
        $venvTo = Join-Path $prog ".venv"
        if (Test-Path $venvTo) { Remove-Item -Recurse -Force $venvTo }
        Copy-Item -Path (Join-Path $src ".venv") -Destination $venvTo -Recurse -Force
    }

    # Builder-path pyvenv.cfg → local python/runtime (same as first install).
    if (Get-Command Repair-NetxShippedVenv -ErrorAction SilentlyContinue) {
        if (Repair-NetxShippedVenv -ProgramRoot $prog) {
            Write-Host "==> Relinked .venv to bundled python/runtime" -ForegroundColor Green
        }
    }

    Write-Host "==> Update files applied" -ForegroundColor Green
    if (-not $NoStart) {
        # Same process (no nested powershell) so the update console can exit cleanly.
        & (Join-Path $PSScriptRoot "start_netx_app.ps1") `
            -ProgramRoot $prog -DataRoot $data -SkipBrowser
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    Write-Host "==> Update complete. You can close this window." -ForegroundColor Green
} finally {
    if (Test-Path $work) {
        Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    }
}
