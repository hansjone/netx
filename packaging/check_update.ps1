param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [string]$UpdateUrl = "",
    [string]$FallbackUrl = "",
    [string]$ForgejoUrl = "",
    [string]$GithubRepo = "",
    [string]$Channel = "",
    [string]$Sources = "",
    [switch]$Apply = $false,
    [switch]$Quiet = $false,
    # Only apply when NETX_UPDATE_AUTO=true (scheduled silent updates).
    [switch]$AutoOnly = $false
)

# Check for a newer NetX Windows package.
#
# Source order (first success wins), controlled by NETX_UPDATE_SOURCES or defaults:
#   manifest  → NETX_UPDATE_URL JSON (manifest.example.json)
#   forgejo   → NETX_UPDATE_FORGEJO_URL (Forgejo/Gitea releases/latest API)
#   github    → GitHub releases/latest (NETX_UPDATE_GITHUB_REPO, default hansjone/netx)
#
# Examples:
#   NETX_UPDATE_FORGEJO_URL=https://git.example.com/api/v1/repos/ops/netx/releases/latest
#   NETX_UPDATE_SOURCES=forgejo,github          # Forgejo primary, GitHub fallback
#   NETX_UPDATE_URL=https://cdn/.../manifest.json
#   NETX_UPDATE_FALLBACK_URL=https://cdn/.../manifest-backup.json
#   NETX_UPDATE_TOKEN=...                      # optional Bearer/token for private APIs

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"
$map = @{}
if (Test-Path $envPath) { $map = Read-DotEnv -Path $envPath }

function Get-Cfg([string]$Key, [string]$ParamVal = "") {
    if ($ParamVal) { return $ParamVal }
    if ($map.ContainsKey($Key) -and $map[$Key]) { return [string]$map[$Key] }
    $envVal = [Environment]::GetEnvironmentVariable($Key)
    if ($envVal) { return $envVal }
    return ""
}

$UpdateUrl = Get-Cfg "NETX_UPDATE_URL" $UpdateUrl
$FallbackUrl = Get-Cfg "NETX_UPDATE_FALLBACK_URL" $FallbackUrl
$ForgejoUrl = Get-Cfg "NETX_UPDATE_FORGEJO_URL" $ForgejoUrl
$GithubRepo = Get-Cfg "NETX_UPDATE_GITHUB_REPO" $GithubRepo
if (-not $GithubRepo) { $GithubRepo = "hansjone/netx" }
$Channel = Get-Cfg "NETX_UPDATE_CHANNEL" $Channel
if (-not $Channel) { $Channel = "stable" }
$Sources = Get-Cfg "NETX_UPDATE_SOURCES" $Sources
$UpdateToken = Get-Cfg "NETX_UPDATE_TOKEN" ""

$current = Get-NetxVersion -ProgramRoot $prog

function Compare-SemVer {
    param([string]$A, [string]$B)
    $pa = @($A.TrimStart('v', 'V').Split('.') | ForEach-Object { try { [int]$_ } catch { 0 } })
    $pb = @($B.TrimStart('v', 'V').Split('.') | ForEach-Object { try { [int]$_ } catch { 0 } })
    while ($pa.Count -lt 3) { $pa += 0 }
    while ($pb.Count -lt 3) { $pb += 0 }
    for ($i = 0; $i -lt 3; $i++) {
        if ($pa[$i] -lt $pb[$i]) { return -1 }
        if ($pa[$i] -gt $pb[$i]) { return 1 }
    }
    return 0
}

function Get-AuthHeaders {
    param([string]$Style = "bearer")
    $h = @{ "User-Agent" = "NetX-UpdateCheck" }
    if (-not $UpdateToken) { return $h }
    if ($Style -eq "token") {
        $h["Authorization"] = "token $UpdateToken"
    } else {
        $h["Authorization"] = "Bearer $UpdateToken"
    }
    return $h
}

function Convert-ReleaseToManifest {
    param($Rel, [string]$SourceName)
    $tag = [string]$Rel.tag_name
    $ver = $tag.TrimStart('v', 'V')
    $assets = @($Rel.assets)
    $zipAsset = $assets | Where-Object { $_.name -match 'win64\.zip$' } | Select-Object -First 1
    $setupAsset = $assets | Where-Object { $_.name -match 'Setup-.*\.exe$' } | Select-Object -First 1
    if (-not $zipAsset) { throw "${SourceName}_release_missing_zip" }

    $zipUrl = [string]$zipAsset.browser_download_url
    if (-not $zipUrl -and $zipAsset.id -and $Rel.html_url) {
        # Some Gitea/Forgejo builds omit browser_download_url; leave empty to fail clearly.
        $zipUrl = [string]$zipAsset.browser_download_url
    }
    if (-not $zipUrl) { throw "${SourceName}_zip_url_missing" }

    $setupUrl = ""
    if ($setupAsset -and $setupAsset.browser_download_url) {
        $setupUrl = [string]$setupAsset.browser_download_url
    }

    return [pscustomobject]@{
        channel        = "stable"
        latest         = $ver
        min_compatible = $ver
        notes_url      = [string]$Rel.html_url
        source         = $SourceName
        windows        = [pscustomobject]@{
            url       = $zipUrl
            sha256    = ""
            size      = [int64]$(if ($zipAsset.size) { $zipAsset.size } else { 0 })
            setup_url = $setupUrl
        }
    }
}

function Get-ManifestFromUrl {
    param([string]$Url)
    $headers = Get-AuthHeaders -Style "bearer"
    $resp = Invoke-WebRequest -Uri $Url -Headers $headers -UseBasicParsing -TimeoutSec 30
    $m = $resp.Content | ConvertFrom-Json
    if (-not $m.source) {
        $m | Add-Member -NotePropertyName source -NotePropertyValue "manifest" -Force
    }
    return $m
}

function Get-FromGitHubReleases {
    $api = "https://api.github.com/repos/$GithubRepo/releases/latest"
    $headers = Get-AuthHeaders -Style "bearer"
    $headers["Accept"] = "application/vnd.github+json"
    $rel = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 30
    return Convert-ReleaseToManifest -Rel $rel -SourceName "github"
}

function Get-FromForgejoReleases {
    param([string]$ApiUrl)
    if (-not $ApiUrl) { throw "forgejo_url_empty" }
    # Forgejo/Gitea: /api/v1/repos/{owner}/{repo}/releases/latest
    $headers = Get-AuthHeaders -Style "token"
    $headers["Accept"] = "application/json"
    $rel = Invoke-RestMethod -Uri $ApiUrl -Headers $headers -TimeoutSec 30
    return Convert-ReleaseToManifest -Rel $rel -SourceName "forgejo"
}

function Get-UpdateSourceList {
    if ($Sources) {
        return @($Sources.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
    }
    $list = [System.Collections.Generic.List[string]]::new()
    if ($UpdateUrl) { [void]$list.Add("manifest") }
    if ($ForgejoUrl) { [void]$list.Add("forgejo") }
    if ($FallbackUrl) { [void]$list.Add("manifest_fallback") }
    # Always keep GitHub as last resort unless explicitly disabled via SOURCES.
    if (-not $list.Contains("github")) { [void]$list.Add("github") }
    # Prefer Forgejo before GitHub when both configured and no explicit SOURCES.
    if ($ForgejoUrl -and $list.Contains("forgejo") -and $list.Contains("github")) {
        $ordered = [System.Collections.Generic.List[string]]::new()
        foreach ($s in @("manifest", "forgejo", "manifest_fallback", "github")) {
            if ($list.Contains($s) -and -not $ordered.Contains($s)) { [void]$ordered.Add($s) }
        }
        foreach ($s in $list) {
            if (-not $ordered.Contains($s)) { [void]$ordered.Add($s) }
        }
        return @($ordered)
    }
    return @($list)
}

Write-Host "==> Current version: $current"
$sourceList = Get-UpdateSourceList
Write-Host "==> Update sources: $($sourceList -join ' -> ')"

$manifest = $null
$errors = @()
foreach ($src in $sourceList) {
    try {
        switch ($src) {
            "manifest" {
                if (-not $UpdateUrl) { throw "NETX_UPDATE_URL empty" }
                Write-Host "==> Trying manifest: $UpdateUrl"
                $manifest = Get-ManifestFromUrl -Url $UpdateUrl
            }
            "manifest_fallback" {
                if (-not $FallbackUrl) { throw "NETX_UPDATE_FALLBACK_URL empty" }
                Write-Host "==> Trying fallback manifest: $FallbackUrl"
                $manifest = Get-ManifestFromUrl -Url $FallbackUrl
                if ($manifest -and -not $manifest.source) {
                    $manifest | Add-Member -NotePropertyName source -NotePropertyValue "manifest_fallback" -Force
                }
            }
            "forgejo" {
                Write-Host "==> Trying Forgejo/Gitea: $ForgejoUrl"
                $manifest = Get-FromForgejoReleases -ApiUrl $ForgejoUrl
            }
            "github" {
                Write-Host "==> Trying GitHub Releases: $GithubRepo"
                $manifest = Get-FromGitHubReleases
            }
            default { throw "unknown_source: $src" }
        }
        if ($manifest.channel -and $Channel -and ($manifest.channel -ne $Channel)) {
            Write-Host "[WARN] manifest channel=$($manifest.channel) requested=$Channel"
        }
        Write-Host "==> Using source: $(if ($manifest.source) { $manifest.source } else { $src })" -ForegroundColor Green
        break
    } catch {
        $msg = $_.Exception.Message
        $errors += "${src}: $msg"
        Write-Host "[WARN] source '$src' failed: $msg" -ForegroundColor Yellow
        $manifest = $null
    }
}

if (-not $manifest) {
    $joined = ($errors -join "; ")
    if ($Quiet) { exit 2 }
    throw "update_check_failed: all sources failed ($joined)"
}

$latest = [string]$manifest.latest
$cmp = Compare-SemVer -A $current -B $latest
$result = [pscustomobject]@{
    current          = $current
    latest           = $latest
    update_available = ($cmp -lt 0)
    source           = $(if ($manifest.source) { [string]$manifest.source } else { "unknown" })
    download_url     = [string]$manifest.windows.url
    setup_url        = $(if ($manifest.windows.setup_url) { [string]$manifest.windows.setup_url } else { "" })
    notes_url        = $(if ($manifest.notes_url) { [string]$manifest.notes_url } else { "" })
    sha256           = $(if ($manifest.windows.sha256) { [string]$manifest.windows.sha256 } else { "" })
}

if (-not $result.update_available) {
    Write-Host "==> Up to date (latest=$latest, source=$($result.source))" -ForegroundColor Green
    if (-not $Quiet) {
        $result | ConvertTo-Json -Compress | Write-Output
    }
    exit 0
}

Write-Host "==> Update available: $current -> $latest (source=$($result.source))" -ForegroundColor Cyan
if ($result.notes_url) { Write-Host "    Notes: $($result.notes_url)" }

$autoEnabled = $false
$autoVal = Get-Cfg "NETX_UPDATE_AUTO" ""
if ($autoVal -match '^(1|true|yes|on)$') { $autoEnabled = $true }

if (-not $Apply) {
    Write-Host "    Download (zip): $($result.download_url)"
    if ($result.setup_url) { Write-Host "    Or install: $($result.setup_url)" }
    Write-Host "    To apply: .\packaging\check_update.ps1 -Apply"
    if (-not $Quiet) {
        $result | ConvertTo-Json -Compress | Write-Output
    }
    exit 10
}

if ($AutoOnly -and -not $autoEnabled) {
    Write-Host "==> Update available but NETX_UPDATE_AUTO is not enabled; skip apply"
    if (-not $Quiet) {
        $result | ConvertTo-Json -Compress | Write-Output
    }
    exit 10
}

$lockFile = Join-Path $data "data\runtime\update.lock"
$lockDir = Split-Path -Parent $lockFile
if (-not (Test-Path $lockDir)) {
    New-Item -ItemType Directory -Path $lockDir -Force | Out-Null
}
if (Test-Path $lockFile) {
    $ageHrs = ((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalHours
    if ($ageHrs -lt 2) {
        Write-Host "==> Another update appears in progress ($lockFile); abort"
        exit 3
    }
    Remove-Item -Force $lockFile -ErrorAction SilentlyContinue
}
Set-Content -Path $lockFile -Value (Get-Date).ToString("o") -Encoding ascii

try {
    $dlDir = Join-Path $data "backups\downloads"
    if (-not (Test-Path $dlDir)) {
        New-Item -ItemType Directory -Path $dlDir -Force | Out-Null
    }
    $zipName = "NetX-$latest-win64.zip"
    $zipPath = Join-Path $dlDir $zipName
    Write-Host "==> Downloading $zipName from $($result.source) ..."
    $dlHeaders = Get-AuthHeaders -Style "token"
    Invoke-WebRequest -Uri $result.download_url -OutFile $zipPath -Headers $dlHeaders -UseBasicParsing
    if ($result.sha256 -and $result.sha256 -notmatch 'REPLACE' -and $result.sha256.Trim()) {
        $hash = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $expect = $result.sha256.ToLowerInvariant()
        if ($hash -ne $expect) {
            throw "sha256_mismatch: got $hash expected $expect"
        }
        Write-Host "==> SHA256 OK"
    }

    Write-Host "==> Applying update via update_netx.ps1"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "update_netx.ps1") `
        -PackagePath $zipPath -ProgramRoot $prog -DataRoot $data
    Write-Host "==> Update applied to $latest" -ForegroundColor Green
} finally {
    Remove-Item -Force $lockFile -ErrorAction SilentlyContinue
}
exit 0
