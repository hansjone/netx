# Shared path helpers for Windows packaging scripts.
# Dot-source: . "$PSScriptRoot\_common.ps1"

$ErrorActionPreference = "Stop"

function Get-NetxRepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-NetxProgramRoot {
    param([string]$Override = "")
    if ($Override) { return (Resolve-Path $Override).Path }
    if ($env:NETX_PROGRAM_ROOT) { return $env:NETX_PROGRAM_ROOT }
    # Packaging scripts live in <program>\packaging when installed; in-repo they live under netx\packaging.
    $parent = Split-Path -Parent $PSScriptRoot
    return $parent
}

function Get-NetxDataRoot {
    param(
        [string]$ProgramRoot,
        [string]$Override = ""
    )
    if ($Override) { return $Override }
    if ($env:NETX_DATA_ROOT) { return $env:NETX_DATA_ROOT }
    $programData = [Environment]::GetFolderPath("CommonApplicationData")
    $defaultPd = Join-Path $programData "NetX"
    # Portable layout: prefer sibling NetXData next to program root when marker exists or Program Files not writable.
    $portable = Join-Path (Split-Path -Parent $ProgramRoot) "NetXData"
    if (Test-Path (Join-Path $ProgramRoot ".portable")) {
        return $portable
    }
    if (Test-Path $defaultPd) {
        return $defaultPd
    }
    # Dev / first run from repo: use repo-local data packaging area under ProgramData if possible.
    try {
        if (-not (Test-Path $defaultPd)) {
            New-Item -ItemType Directory -Path $defaultPd -Force -ErrorAction Stop | Out-Null
        }
        return $defaultPd
    } catch {
        return $portable
    }
}

function Get-NetxVersion {
    param([string]$ProgramRoot)
    $vj = Join-Path $ProgramRoot "version.json"
    if (Test-Path $vj) {
        try {
            $j = Get-Content -Raw -Path $vj | ConvertFrom-Json
            if ($j.version) { return [string]$j.version }
        } catch {}
    }
    $toml = Join-Path $ProgramRoot "pyproject.toml"
    if (-not (Test-Path $toml)) {
        $toml = Join-Path (Get-NetxRepoRoot) "pyproject.toml"
    }
    if (Test-Path $toml) {
        $m = Select-String -Path $toml -Pattern '^\s*version\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($m) { return $m.Matches[0].Groups[1].Value }
    }
    return "0.0.0"
}

function Read-DotEnv {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    Get-Content -Path $Path -Encoding utf8 | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) { return }
        $i = $line.IndexOf("=")
        if ($i -lt 1) { return }
        $k = $line.Substring(0, $i).Trim()
        $v = $line.Substring($i + 1).Trim()
        if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $map[$k] = $v
    }
    return $map
}

function Write-DotEnvValue {
    param(
        [string]$Path,
        [hashtable]$Values
    )
    $lines = @()
    $seen = @{}
    if (Test-Path $Path) {
        Get-Content -Path $Path -Encoding utf8 | ForEach-Object {
            $line = $_
            $t = $line.Trim()
            if ($t -and -not $t.StartsWith("#") -and $t.Contains("=")) {
                $k = $t.Substring(0, $t.IndexOf("=")).Trim()
                if ($Values.ContainsKey($k)) {
                    $lines += "$k=$($Values[$k])"
                    $seen[$k] = $true
                    return
                }
            }
            $lines += $line
        }
    }
    foreach ($k in $Values.Keys) {
        if (-not $seen.ContainsKey($k)) {
            $lines += "$k=$($Values[$k])"
        }
    }
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $text = ($lines -join "`r`n")
    try {
        Set-Content -LiteralPath $Path -Value $text -Encoding utf8
    } catch {
        # Admin-created .env is often Users:RX only — repair ACL then retry once.
        $root = $dir
        if ((Split-Path -Leaf $dir) -eq "NetX" -or $dir -match '\\NetX$') {
            $root = $dir
        } else {
            $parent = Split-Path -Parent $dir
            if ($parent -and ((Split-Path -Leaf $parent) -eq "NetX")) { $root = $parent }
        }
        $null = Ensure-NetxDataAcl -DataRoot $root -Quiet
        try {
            # Reset .env ACL explicitly if still blocked.
            $icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
            if (Test-Path -LiteralPath $icacls) {
                & $icacls $Path /grant "*S-1-5-32-545:M" /C /Q 2>$null | Out-Null
            }
            Set-Content -LiteralPath $Path -Value $text -Encoding utf8
        } catch {
            throw (New-Object System.UnauthorizedAccessException(
                ("access_denied: cannot write {0}. Run Start Menu → NetX → Reconfigure database as Administrator, or: icacls `"{1}`" /grant Users:(OI)(CI)M /T" -f $Path, $root),
                $_.Exception
            ))
        }
    }
}

function Get-DbMode {
    param([hashtable]$EnvMap)
    $m = ""
    if ($EnvMap.ContainsKey("NETX_DB_MODE")) { $m = [string]$EnvMap["NETX_DB_MODE"] }
    if (-not $m -and $env:NETX_DB_MODE) { $m = $env:NETX_DB_MODE }
    $m = $m.Trim().ToLowerInvariant()
    if ($m -eq "bundled") { return "bundled" }
    return "external"
}

function Import-NetxEnvFile {
    param([string]$Path)
    $map = Read-DotEnv -Path $Path
    foreach ($k in $map.Keys) {
        Set-Item -Path "Env:$k" -Value $map[$k]
    }
    return $map
}

function ConvertTo-NetxFsPath([string]$Path) {
    # Forward slashes work in .env and most Windows APIs via Python pathlib.
    return ($Path -replace '\\', '/')
}

function Get-NetxDataEnvMap {
    param([string]$DataRoot)
    $d = $DataRoot
    return @{
        "NETX_AUTH_MCP_TOKEN_FILE"       = (ConvertTo-NetxFsPath (Join-Path $d "data\auth\mcp_token"))
        "NETX_AUTH_SECRET_FILE"          = (ConvertTo-NetxFsPath (Join-Path $d "data\auth\jwt_secret"))
        "NETX_CREDENTIAL_SECRET_FILE"    = (ConvertTo-NetxFsPath (Join-Path $d "data\auth\credential_secret"))
        "NETX_SCHEDULER_HEARTBEAT_PATH"  = (ConvertTo-NetxFsPath (Join-Path $d "data\runtime\scheduler_heartbeat.json"))
        "NETX_RUN_DIR"                   = (ConvertTo-NetxFsPath (Join-Path $d "data\runtime"))
        "NETX_BIZ_STATE_SPOOL_DIR"       = (ConvertTo-NetxFsPath (Join-Path $d "data\biz_state_spool"))
        "NETX_NE_COLLECTION_DATA_DIR"    = (ConvertTo-NetxFsPath (Join-Path $d "data\ne_collections"))
        "NETX_NE_EXEC_JOB_DIR"           = (ConvertTo-NetxFsPath (Join-Path $d "data\ne_exec_jobs"))
        "NETX_WEBCRT_DATA_DIR"           = (ConvertTo-NetxFsPath (Join-Path $d "data\webcrt"))
    }
}

function New-NetxFernetKey {
    # cryptography.fernet.Fernet.generate_key() == urlsafe_b64encode(os.urandom(32))
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
}

function Ensure-NetxDataAcl {
    param(
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [switch]$Quiet = $false
    )
    # Installer creates %ProgramData%\NetX as Administrators. Tray / normal users must
    # be able to update .env and runtime files when reconfiguring DB.
    if (-not (Test-Path -LiteralPath $DataRoot)) { return $false }
    try {
        $acl = Get-Acl -LiteralPath $DataRoot
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            "BUILTIN\Users",
            "Modify",
            "ContainerInherit,ObjectInherit",
            "None",
            "Allow"
        )
        $acl.SetAccessRule($rule)
        Set-Acl -LiteralPath $DataRoot -AclObject $acl

        # Also fix already-created files that only inherited RX for Users.
        $icacls = Join-Path $env:SystemRoot "System32\icacls.exe"
        if (Test-Path -LiteralPath $icacls) {
            & $icacls $DataRoot /grant "*S-1-5-32-545:(OI)(CI)M" /T /C /Q 2>$null | Out-Null
        }
        if (-not $Quiet) {
            Write-Host "==> Data ACL: BUILTIN\Users Modify on $DataRoot" -ForegroundColor DarkGray
        }
        return $true
    } catch {
        if (-not $Quiet) {
            Write-Host "[WARN] Ensure-NetxDataAcl: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        return $false
    }
}

function Ensure-NetxDataDirectories {
    param([string]$DataRoot)
    if (-not (Test-Path -LiteralPath $DataRoot)) {
        New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
    }
    $subs = @(
        "data", "data\auth", "data\runtime", "data\biz_state_spool",
        "data\ne_collections", "data\ne_exec_jobs", "data\webcrt",
        "backups", "pgdata"
    )
    foreach ($sub in $subs) {
        $p = Join-Path $DataRoot $sub
        if (-not (Test-Path $p)) {
            New-Item -ItemType Directory -Path $p -Force | Out-Null
        }
    }
    # Best-effort; succeeds when running elevated (installer / first-run as admin).
    $null = Ensure-NetxDataAcl -DataRoot $DataRoot -Quiet
}

function Test-NetxTcpPortFree {
    param([string]$HostName = "127.0.0.1", [int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(400)
        if ($ok -and $client.Connected) {
            $client.Close()
            return $false
        }
        $client.Close()
        return $true
    } catch {
        return $true
    }
}

function Test-NetxApiHealthy {
    param([string]$HostName = "127.0.0.1", [int]$Port = 8890, [int]$TimeoutSec = 2)
    try {
        $url = "http://${HostName}:${Port}/health"
        $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $TimeoutSec -MaximumRedirection 0
        if ($r.StatusCode -ne 200) { return $false }
        $body = $r.Content | ConvertFrom-Json -ErrorAction Stop
        return ($body.status -eq "ok")
    } catch {
        return $false
    }
}

function Wait-NetxApiHealthy {
    param(
        [string]$HostName = "127.0.0.1",
        [int]$Port = 8890,
        [int]$TimeoutSec = 180,
        [int]$PollMs = 500
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-NetxApiHealthy -HostName $HostName -Port $Port -TimeoutSec 2) {
            return $true
        }
        Start-Sleep -Milliseconds $PollMs
    }
    return $false
}

function ConvertTo-NetxProcessArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)
    # Start-Process joins string[] args with spaces and does NOT quote values that
    # contain spaces (e.g. C:\Program Files\NetX). Always emit one argv token.
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function ConvertTo-NetxProcessArgumentString {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    return (($Arguments | ForEach-Object { ConvertTo-NetxProcessArgument -Value "$_" }) -join " ")
}

function Start-NetxPowerShell {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [ValidateSet("Hidden", "Normal", "Minimized")]
        [string]$WindowStyle = "Hidden",
        [switch]$Wait = $false,
        [switch]$PassThru = $false
    )
    $parts = @(
        "-NoProfile",
        "-WindowStyle", $WindowStyle,
        "-ExecutionPolicy", "Bypass",
        "-File", $File
    ) + $Arguments
    $sp = @{
        FilePath     = "powershell.exe"
        ArgumentList = (ConvertTo-NetxProcessArgumentString -Arguments $parts)
        WindowStyle  = $WindowStyle
    }
    if ($WorkingDirectory) { $sp.WorkingDirectory = $WorkingDirectory }
    if ($Wait) { $sp.Wait = $true }
    if ($PassThru -or $Wait) { $sp.PassThru = $true }
    return Start-Process @sp
}

function Get-NetxSystemPython {
    # Prefer a real interpreter; skip the WindowsApps Store stub.
    $candidates = @()
    foreach ($cmd in @("python", "python3")) {
        $c = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($c -and $c.Source -and ($c.Source -notmatch '\\WindowsApps\\')) {
            $candidates += $c.Source
        }
    }
    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher -and $pyLauncher.Source -and ($pyLauncher.Source -notmatch '\\WindowsApps\\')) {
        try {
            $resolved = (& $pyLauncher.Source -3 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
            if ($resolved -and (Test-Path -LiteralPath $resolved)) {
                $candidates += $resolved
            }
        } catch {}
    }
    foreach ($p in @(
            "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "$env:ProgramFiles\Python312\python.exe",
            "$env:ProgramFiles\Python311\python.exe"
        )) {
        if ($p -and (Test-Path $p)) { $candidates += $p }
    }
    foreach ($p in ($candidates | Select-Object -Unique)) {
        try {
            $v = & $p -c "import sys; print('%d.%d'%sys.version_info[:2])" 2>$null
            if ($v -match '^(3\.(1[1-9]|[2-9]\d))$') { return $p }
        } catch {}
    }
    return $null
}

function Get-NetxBundledPythonRoot {
    param([Parameter(Mandatory = $true)][string]$ProgramRoot)
    return Join-Path $ProgramRoot "python\runtime"
}

function Repair-NetxShippedVenv {
    param([Parameter(Mandatory = $true)][string]$ProgramRoot)
    $cfgPath = Join-Path $ProgramRoot ".venv\pyvenv.cfg"
    $rtRoot = Get-NetxBundledPythonRoot -ProgramRoot $ProgramRoot
    $rtPy = Join-Path $rtRoot "python.exe"
    if (-not (Test-Path -LiteralPath $rtPy)) { return $false }
    if (-not (Test-Path -LiteralPath $cfgPath)) { return $false }

    $rtRootAbs = (Resolve-Path -LiteralPath $rtRoot).Path
    $rtPyAbs = (Resolve-Path -LiteralPath $rtPy).Path
    $ver = "3.14.0"
    try {
        $verOut = & $rtPyAbs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
        if ($verOut) { $ver = ($verOut | Out-String).Trim() }
    } catch {}

    $lines = @(
        "home = $rtRootAbs",
        "include-system-site-packages = false",
        "version = $ver",
        "executable = $rtPyAbs"
    )
    Set-Content -LiteralPath $cfgPath -Value ($lines -join "`r`n") -Encoding ascii
    return $true
}

function Test-NetxVenvRunnable {
    param([Parameter(Mandatory = $true)][string]$VenvPython)
    if (-not (Test-Path -LiteralPath $VenvPython)) { return $false }

    $outFile = Join-Path $env:TEMP ("netx-venv-out-" + [Guid]::NewGuid().ToString("n") + ".txt")
    $errFile = Join-Path $env:TEMP ("netx-venv-err-" + [Guid]::NewGuid().ToString("n") + ".txt")
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $VenvPython
        $psi.Arguments = '-c "import encodings, sys; sys.exit(0 if sys.prefix else 1)"'
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $p = [Diagnostics.Process]::Start($psi)
        $stderr = $p.StandardError.ReadToEnd()
        $null = $p.StandardOutput.ReadToEnd()
        $p.WaitForExit()
        if ($stderr -match 'did not find executable|No Python at|Fatal Python error') {
            return $false
        }
        if ($p.ExitCode -ne 0) { return $false }
        return $true
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Ensure-NetxVenv {
    param(
        [Parameter(Mandatory = $true)][string]$ProgramRoot,
        [switch]$ForcePip = $false
    )
    $venvPy = Join-Path $ProgramRoot ".venv\Scripts\python.exe"
    $req = Join-Path $ProgramRoot "requirements.txt"
    $rtPy = Join-Path (Get-NetxBundledPythonRoot -ProgramRoot $ProgramRoot) "python.exe"

    if (Test-Path -LiteralPath $rtPy) {
        $null = Repair-NetxShippedVenv -ProgramRoot $ProgramRoot
    }

    if ((Test-Path -LiteralPath $venvPy) -and -not $ForcePip) {
        if (Test-NetxVenvRunnable -VenvPython $venvPy) {
            return $venvPy
        }
        Write-Host "[WARN] Shipped .venv exists but Python cannot start (broken pyvenv.cfg or missing runtime)." -ForegroundColor Yellow
        if (Test-Path -LiteralPath $rtPy) {
            $null = Repair-NetxShippedVenv -ProgramRoot $ProgramRoot
            if (Test-NetxVenvRunnable -VenvPython $venvPy) {
                Write-Host "==> Repaired .venv to use bundled python/runtime" -ForegroundColor Green
                return $venvPy
            }
        }
        if (-not $ForcePip) {
            throw "venv_not_runnable: reinstall NetX Setup (ships python/runtime + .venv) or run repair as admin"
        }
    }
    if (-not (Test-Path $venvPy)) {
        $py = Get-NetxSystemPython
        if (-not $py) {
            throw "python_not_found: install Python 3.11+ (or ship Setup built with -CreateVenv)"
        }
        Write-Host "==> Creating .venv with $py"
        $venvDir = Join-Path $ProgramRoot ".venv"
        try {
            & $py -m venv $venvDir
        } catch {
            throw "venv_create_failed: cannot write $venvDir (need admin / ship -CreateVenv). $($_.Exception.Message)"
        }
        if (-not (Test-Path $venvPy)) {
            throw "venv_create_failed: missing $venvPy (Program Files may be read-only without elevation)"
        }
        $ForcePip = $true
    }
    if ($ForcePip) {
        if (-not (Test-Path $req)) { throw "requirements_missing: $req" }
        Write-Host "==> pip install -r requirements.txt"
        & $venvPy -m pip install --upgrade pip
        & $venvPy -m pip install -r $req
        if ($LASTEXITCODE -ne 0) { throw "pip_install_failed" }
    }
    return $venvPy
}

function Stop-NetxTrayProcesses {
    param([string]$ProgramRoot = "")
    $hits = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -and $_.CommandLine -match 'netx_tray\.ps1'
        })
    if ($ProgramRoot) {
        $esc = [regex]::Escape($ProgramRoot)
        $hits = @($hits | Where-Object { $_.CommandLine -match $esc -or $_.CommandLine -match 'netx_tray\.ps1' })
    }
    foreach ($p in $hits) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host "Stopped NetX tray PID=$($p.ProcessId)"
        } catch {}
    }
}

function ConvertTo-NetxSqlLiteral([string]$Value) {
    # Single-quote escaping for PostgreSQL string literals.
    return ($Value -replace "'", "''")
}
