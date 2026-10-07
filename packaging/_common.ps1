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
    Set-Content -Path $Path -Value ($lines -join "`r`n") -Encoding utf8
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
        "NETX_SCHEDULER_HEARTBEAT_PATH"  = (ConvertTo-NetxFsPath (Join-Path $d "data\runtime\scheduler_heartbeat.json"))
        "NETX_BIZ_STATE_SPOOL_DIR"       = (ConvertTo-NetxFsPath (Join-Path $d "data\biz_state_spool"))
        "NETX_NE_COLLECTION_DATA_DIR"    = (ConvertTo-NetxFsPath (Join-Path $d "data\ne_collections"))
        "NETX_NE_EXEC_JOB_DIR"           = (ConvertTo-NetxFsPath (Join-Path $d "data\ne_exec_jobs"))
        "NETX_WEBCRT_DATA_DIR"           = (ConvertTo-NetxFsPath (Join-Path $d "data\webcrt"))
    }
}

function Ensure-NetxDataDirectories {
    param([string]$DataRoot)
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

function ConvertTo-NetxSqlLiteral([string]$Value) {
    # Single-quote escaping for PostgreSQL string literals.
    return ($Value -replace "'", "''")
}
