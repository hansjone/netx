param(
    [string]$Version = "",
    [string]$OutDir = "",
    [switch]$SkipWebBuild = $false,
    [switch]$SkipPostgresDownload = $false,
    # Zip is no longer published; stage + Setup.exe only. Opt-in for local/dev testing.
    [switch]$CreateZip = $false,
    # Deprecated no-op (zip is off by default). Kept so older scripts that pass -SkipZip still run.
    [switch]$SkipZip = $false,
    # Default on: offline Setup must ship .venv so target PCs never pip-install.
    [switch]$CreateVenv = $true
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$repo = Get-NetxRepoRoot
if (-not $Version) {
    $Version = Get-NetxVersion -ProgramRoot $repo
}
$stage = if ($OutDir) { $OutDir } else { Join-Path $PSScriptRoot "release\netx-win64" }

Write-Host "==> Building NetX Windows release $Version"
Write-Host "    Repo:  $repo"
Write-Host "    Stage: $stage"

if (Test-Path $stage) {
    Remove-Item -Recurse -Force $stage
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null

$webRoot = Join-Path $repo "web"
$dist = Join-Path $webRoot "dist"
if (-not $SkipWebBuild) {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw "npm_not_found"
    }
    Write-Host "==> npm build"
    Push-Location $webRoot
    try {
        # npm ci wipes node_modules first; on a dev box a running Vite locks native
        # .node files and leaves it half-deleted. Skip when installed deps match the lockfile.
        $installedLock = Join-Path $webRoot "node_modules\.package-lock.json"
        $lock = Join-Path $webRoot "package-lock.json"
        if ((Test-Path $installedLock) -and
            (Get-Item $installedLock).LastWriteTimeUtc -ge (Get-Item $lock).LastWriteTimeUtc) {
            Write-Host "==> node_modules up to date with package-lock.json - skip npm ci"
        } else {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw "npm_install_failed" }
        }
        # Release packaging skips tsc (dev CI still uses `npm run build`).
        & npm.cmd run build:release
        if ($LASTEXITCODE -ne 0) { throw "web_build_failed" }
    } finally {
        Pop-Location
    }
}
if (-not (Test-Path (Join-Path $dist "index.html"))) {
    throw "web_dist_missing: build web/ or drop -SkipWebBuild"
}

$pgsql = Join-Path $PSScriptRoot "postgres\pgsql"
if (-not $SkipPostgresDownload) {
    if (-not (Test-Path (Join-Path $pgsql "bin\pg_ctl.exe"))) {
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "download_postgres.ps1")
    }
}

# Bundle WinSW so offline installs can register a Windows Service without GitHub.
# .NET Framework 4.6.1 build (~0.6MB; in-box on Win10 1607+ / Server 2016+) instead of
# the self-contained x64 build (~18MB).
$winswCache = Join-Path $PSScriptRoot "cache\WinSW.NET461.exe"
$winswShip = Join-Path $PSScriptRoot "winsw\WinSW.exe"
if (-not (Test-Path $winswShip)) {
    if (-not (Test-Path $winswCache)) {
        $winswUrl = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe"
        Write-Host "==> Downloading WinSW for offline bundle: $winswUrl"
        New-Item -ItemType Directory -Path (Split-Path $winswCache -Parent) -Force | Out-Null
        Invoke-WebRequest -Uri $winswUrl -OutFile $winswCache -UseBasicParsing
    }
    New-Item -ItemType Directory -Path (Split-Path $winswShip -Parent) -Force | Out-Null
    Copy-Item -Path $winswCache -Destination $winswShip -Force
}

# Flat layout = same as repo so scripts\start_netx.ps1 works unchanged.
foreach ($d in @("netx_api", "alembic", "scripts")) {
    Copy-Item -Path (Join-Path $repo $d) -Destination (Join-Path $stage $d) -Recurse -Force
}
foreach ($f in @("pyproject.toml", "requirements.txt", "alembic.ini", "README.md", "LICENSE", "CONTRIBUTING.md", "SECURITY.md")) {
    $src = Join-Path $repo $f
    if (Test-Path $src) {
        Copy-Item -Path $src -Destination (Join-Path $stage $f) -Force
    }
}

$webOut = Join-Path $stage "web\dist"
New-Item -ItemType Directory -Path (Split-Path $webOut -Parent) -Force | Out-Null
Copy-Item -Path $dist -Destination $webOut -Recurse -Force

function Write-Utf8BomFile {
    param([string]$Path)
    # Windows PowerShell 4/5 (Server 2012+) mis-parses UTF-8 without BOM when scripts contain CJK.
    $utf8Bom = New-Object System.Text.UTF8Encoding $true
    $bytes = [IO.File]::ReadAllBytes($Path)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $text = if ($hasBom) {
        [Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    } else {
        [Text.Encoding]::UTF8.GetString($bytes)
    }
    $text = $text -replace "`r`n", "`n" -replace "`n", "`r`n"
    if (-not $text.EndsWith("`r`n")) { $text += "`r`n" }
    [IO.File]::WriteAllText($Path, $text, $utf8Bom)
}

$packOut = Join-Path $stage "packaging"
New-Item -ItemType Directory -Path $packOut -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "_common.ps1") -Destination $packOut -Force
foreach ($name in @(
        "download_postgres.ps1", "setup_first_run.ps1", "start_netx_app.ps1",
        "stop_netx_app.ps1", "update_netx.ps1", "check_update.ps1",
        "launch_shortcut.ps1", "netx_tray.ps1", "install_autostart.ps1", "install_service.ps1",
        "service_run.ps1", "service_lifecycle.ps1", "install_update_task.ps1", "repair_venv.ps1", "runtime_ctl.ps1", "uninstall_prepare.ps1", "uninstall_delete_data.ps1", "sign_release.ps1",
        "build_release.ps1", "publish_release.ps1", "publish_forgejo_release.ps1", "README.md", "manifest.example.json"
    )) {
    $src = Join-Path $PSScriptRoot $name
    if (Test-Path $src) { Copy-Item $src (Join-Path $packOut $name) -Force }
}
Get-ChildItem -Path $packOut -Filter *.ps1 -File | ForEach-Object { Write-Utf8BomFile -Path $_.FullName }
Get-ChildItem -Path (Join-Path $stage "scripts") -Filter *.ps1 -File -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Utf8BomFile -Path $_.FullName }
Copy-Item -Path (Join-Path $PSScriptRoot "config") -Destination (Join-Path $packOut "config") -Recurse -Force
Copy-Item -Path (Join-Path $PSScriptRoot "installer") -Destination (Join-Path $packOut "installer") -Recurse -Force
$assetsSrc = Join-Path $PSScriptRoot "assets"
if (Test-Path $assetsSrc) {
    Copy-Item -Path $assetsSrc -Destination (Join-Path $packOut "assets") -Recurse -Force
}
$winswOut = Join-Path $packOut "winsw"
New-Item -ItemType Directory -Path $winswOut -Force | Out-Null
if (Test-Path $winswShip) {
    Copy-Item -Path $winswShip -Destination (Join-Path $winswOut "WinSW.exe") -Force
    Write-Host "==> Bundled WinSW for offline service install"
} else {
    Write-Host "[WARN] WinSW missing - offline service install will fail" -ForegroundColor Yellow
}
New-Item -ItemType Directory -Path (Join-Path $packOut "postgres") -Force | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "postgres\README.md") -Destination (Join-Path $packOut "postgres\README.md") -Force

if (Test-Path (Join-Path $pgsql "bin\pg_ctl.exe")) {
    Write-Host '==> Copying bundled PostgreSQL (bin/lib/share; skip doc/)'
    $pgStage = Join-Path $stage "postgres\pgsql"
    New-Item -ItemType Directory -Path $pgStage -Force | Out-Null
    foreach ($sub in @("bin", "lib", "share")) {
        $srcSub = Join-Path $pgsql $sub
        if (Test-Path $srcSub) {
            Copy-Item -Path $srcSub -Destination (Join-Path $pgStage $sub) -Recurse -Force
        }
    }
    # Runtime-only subset: NetX uses postgres/pg_ctl/initdb/psql/pg_dump (+ restore/isready
    # for manual ops). Drop StackBuilder (wx GUI), dev/test tools, static libs, pgxs and
    # non-Chinese message catalogs.
    $pgBinKeep = @("postgres", "pg_ctl", "initdb", "psql", "pg_dump", "pg_dumpall", "pg_restore", "pg_isready")
    Get-ChildItem (Join-Path $pgStage "bin") -File | Where-Object {
        ($_.Extension -eq ".exe" -and $pgBinKeep -notcontains $_.BaseName) -or $_.Name -like "wx*.dll"
    } | Remove-Item -Force
    Get-ChildItem (Join-Path $pgStage "lib") -File -Filter *.lib | Remove-Item -Force
    Remove-Item -Recurse -Force (Join-Path $pgStage "lib\pgxs") -ErrorAction SilentlyContinue
    Get-ChildItem (Join-Path $pgStage "share\locale") -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "zh_CN*" } | Remove-Item -Recurse -Force
    # The EDB binaries link the VC++ 2015-2022 runtime (vcruntime140, msvcp140 via ICU), which a
    # clean Windows install does not have. Deploy it app-local (allowed by the VC++ redist terms)
    # so the bundled PostgreSQL starts on fresh / offline machines without the redistributable.
    $sys32 = Join-Path $env:SystemRoot "System32"
    foreach ($dll in @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll")) {
        $src = Join-Path $sys32 $dll
        if (-not (Test-Path -LiteralPath $src)) { throw "vc_runtime_missing_on_build_machine: $src (install the VC++ 2015-2022 x64 redistributable)" }
        Copy-Item -LiteralPath $src -Destination (Join-Path $pgStage "bin\$dll") -Force
    }
    Get-ChildItem -Path $pgsql -File -ErrorAction SilentlyContinue | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination (Join-Path $pgStage $_.Name) -Force
    }
} else {
    throw "bundled_postgres_missing: Setup.exe must ship postgres for offline install. Run download_postgres.ps1 (build machine only)."
}

$builtAt = (Get-Date).ToUniversalTime().ToString("o")
@{
    version         = $Version
    channel         = "stable"
    min_data_layout = 1
    built_at        = $builtAt
    platform        = "win64"
} | ConvertTo-Json | Set-Content -Path (Join-Path $stage "version.json") -Encoding utf8

Set-Content -Path (Join-Path $stage ".portable") -Value "1" -Encoding ascii

# Root .cmd launchers (Explorer / Start Menu friendly; ASCII-only).
$cmdSrc = Join-Path $PSScriptRoot "cmd"
foreach ($cmdName in @("NetX-FirstRun.cmd", "NetX-Start.cmd", "NetX-Tray.cmd", "NetX-Stop.cmd")) {
    $c = Join-Path $cmdSrc $cmdName
    if (Test-Path $c) {
        Copy-Item -Path $c -Destination (Join-Path $stage $cmdName) -Force
    }
}

if ($CreateVenv) {
    Write-Host "==> Creating portable python/runtime + .venv (must not reference build-machine paths)"
    # Releases pin 3.12 (same as CI); prefer it even when another version is first on PATH.
    $pyPath = $null
    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source -notmatch '\\WindowsApps\\') {
        try {
            $resolved = (& $pyLauncher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
            if ($resolved -and (Test-Path -LiteralPath $resolved)) { $pyPath = $resolved }
        } catch {}
    }
    if (-not $pyPath) {
        foreach ($p in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
            if (Test-Path -LiteralPath $p) { $pyPath = $p; break }
        }
    }
    if (-not $pyPath) { $pyPath = Get-NetxSystemPython }
    if (-not $pyPath) { throw "python_not_found_for_venv: need Python 3.12.x (not WindowsApps stub)" }
    $pyVersion = (& $pyPath -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Out-String).Trim()
    if ($pyVersion -ne '3.12') { throw "python_version_mismatch: Windows releases require Python 3.12.x for reproducible runtimes; found $pyVersion at $pyPath" }
    Write-Host "    Build Python: $pyPath"

    $prefix = (& $pyPath -c "import sys; print(sys.base_prefix)" 2>$null | Out-String).Trim()
    if (-not $prefix -or -not (Test-Path -LiteralPath $prefix)) {
        throw "python_base_prefix_not_found: $prefix"
    }
    $rtDir = Join-Path $stage "python\runtime"
    if (Test-Path $rtDir) { Remove-Item -Recurse -Force $rtDir }
    New-Item -ItemType Directory -Path $rtDir -Force | Out-Null

    Write-Host "==> Copying Python stdlib/runtime to $rtDir (exclude base site-packages)"
    $spEx = Join-Path $prefix "Lib\site-packages"
    $robocopy = Join-Path $env:SystemRoot "System32\robocopy.exe"
    if (-not (Test-Path -LiteralPath $robocopy)) { throw "robocopy_not_found" }
    # Headless server: drop docs, the stdlib test suite and Tk/IDLE (~100MB unpacked).
    # Keep ensurepip - venv creation needs it. Same result on CI and local builds.
    $xdRuntime = @("Doc", "tcl", "Tools", "Lib\test", "Lib\idlelib", "Lib\tkinter", "Lib\turtledemo") |
        ForEach-Object { Join-Path $prefix $_ }
    # python-*.exe: the GitHub runner's toolcache keeps the full Python installer (~25MB)
    # in the prefix; _test*.pyd: stdlib test extensions.
    $xfRuntime = @("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "*.pyc", "python-*.exe", "_test*.pyd", "_ctypes_test.pyd", "xxlimited*.pyd")
    & $robocopy $prefix $rtDir /E /XD $spEx __pycache__ @xdRuntime /XF @xfRuntime /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy_python_runtime_failed: exit $LASTEXITCODE" }

    $rtPy = Join-Path $rtDir "python.exe"
    if (-not (Test-Path -LiteralPath $rtPy)) { throw "python_runtime_missing: $rtPy" }

    $venvDir = Join-Path $stage ".venv"
    if (Test-Path $venvDir) { Remove-Item -Recurse -Force $venvDir }
    Write-Host '==> Creating .venv from shipped runtime (--copies)'
    & $rtPy -m venv $venvDir --copies
    if ($LASTEXITCODE -ne 0) { throw "venv_create_failed" }

    $venvPy = Join-Path $venvDir "Scripts\python.exe"
    $null = Repair-NetxShippedVenv -ProgramRoot $stage
    if (-not (Test-NetxVenvRunnable -VenvPython $venvPy)) {
        throw "venv_not_runnable_after_build: check python/runtime copy"
    }
    & $venvPy -c "from pathlib import Path; import glob; print(Path.cwd()); print(glob.__file__)"
    if ($LASTEXITCODE -ne 0) { throw "python_stdlib_smoke_failed: pathlib/glob import mismatch" }

    # --no-warn-script-location: the PATH check realpath()s every entry and aborts on a
    # broken reparse point (e.g. a stale app folder on a detached volume). Irrelevant here.
    & $venvPy -m pip install --upgrade pip --no-warn-script-location
    & $venvPy -m pip install -r (Join-Path $stage "requirements.txt") --no-warn-script-location
    if ($LASTEXITCODE -ne 0) { throw "pip_install_failed" }

    $null = Repair-NetxShippedVenv -ProgramRoot $stage
    if (-not (Test-NetxVenvRunnable -VenvPython $venvPy)) {
        throw "venv_not_runnable_after_pip"
    }
    & $venvPy -c "from pathlib import Path; import glob; print(Path.cwd()); print(glob.__file__)"
    if ($LASTEXITCODE -ne 0) { throw "python_stdlib_smoke_failed_after_pip: pathlib/glob import mismatch" }

    Write-Host "==> Slimming .venv (drop tests / __pycache__ / *.pyc)"
    $site = Join-Path $stage ".venv\Lib\site-packages"
    if (Test-Path $site) {
        Get-ChildItem -Path $site -Recurse -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -in @('__pycache__', 'tests', 'test', 'testing', 'Tests') -or
                $_.FullName -match '\\pandas\\tests(\\|$)' -or
                $_.FullName -match '\\numpy\\(_*tests|tests)(\\|$)' -or
                $_.FullName -match '\\scipy\\(_*tests|tests)(\\|$)'
            } |
            Sort-Object { $_.FullName.Length } -Descending |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
        Get-ChildItem -Path $site -Recurse -Include *.pyc,*.pyo -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "==> Stage ready: $stage" -ForegroundColor Green

if ($SkipZip -and $CreateZip) {
    Write-Host "[WARN] -SkipZip ignored because -CreateZip was set" -ForegroundColor Yellow
}
if ($CreateZip) {
    $zip = Join-Path (Split-Path -Parent $stage) "NetX-$Version-win64.zip"
    if (Test-Path $zip) { Remove-Item -Force $zip }
    Compress-Archive -Path $stage -DestinationPath $zip -Force
    Write-Host "==> Zip (optional/dev): $zip" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Ship:"
Write-Host "  Exe:  compile packaging\installer\netx.iss with Inno Setup (needs stage above)"
Write-Host "  (Zip packages are not published; use -CreateZip only for local testing.)"
Write-Host "Python: bundled python/runtime + .venv; target PCs do not need Python installed"
