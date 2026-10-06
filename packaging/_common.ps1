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
