param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = "",
    [string]$UpdateUrl = "",
    [string]$Channel = "",
    [switch]$Apply = $false,
    [switch]$Quiet = $false
)

# Check for a newer NetX Windows package.
# Sources (first that works):
#   1) -UpdateUrl / NETX_UPDATE_URL  → JSON manifest (see manifest.example.json)
#   2) GitHub Releases API for hansjone/netx (default)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot
$envPath = Join-Path $data ".env"
$map = @{}
if (Test-Path $envPath) { $map = Read-DotEnv -Path $envPath }

if (-not $UpdateUrl) {
    if ($map["NETX_UPDATE_URL"]) { $UpdateUrl = $map["NETX_UPDATE_URL"] }
    elseif ($env:NETX_UPDATE_URL) { $UpdateUrl = $env:NETX_UPDATE_URL }
}
if (-not $Channel) {
    if ($map["NETX_UPDATE_CHANNEL"]) { $Channel = $map["NETX_UPDATE_CHANNEL"] }
    elseif ($env:NETX_UPDATE_CHANNEL) { $Channel = $env:NETX_UPDATE_CHANNEL }
    else { $Channel = "stable" }
}

$current = Get-NetxVersion -ProgramRoot $prog

function Compare-SemVer {
    param([string]$A, [string]$B)
    $pa = @($A.TrimStart('v','V').Split('.') | ForEach-Object { try { [int]$_ } catch { 0 } })
    $pb = @($B.TrimStart('v','V').Split('.') | ForEach-Object { try { [int]$_ } catch { 0 } })
    while ($pa.Count -lt 3) { $pa += 0 }
    while ($pb.Count -lt 3) { $pb += 0 }
    for ($i = 0; $i -lt 3; $i++) {
        if ($pa[$i] -lt $pb[$i]) { return -1 }
        if ($pa[$i] -gt $pb[$i]) { return 1 }
    }
    return 0
}

function Get-ManifestFromUrl {
    param([string]$Url)
    $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 30
    return ($resp.Content | ConvertFrom-Json)
}

function Get-FromGitHubReleases {
    $api = "https://api.github.com/repos/hansjone/netx/releases/latest"
    $headers = @{ "User-Agent" = "NetX-UpdateCheck"; "Accept" = "application/vnd.github+json" }
    $rel = Invoke-RestMethod -Uri $api -Headers $headers -TimeoutSec 30
    $tag = [string]$rel.tag_name
    $ver = $tag.TrimStart('v', 'V')
    $zipAsset = $rel.assets | Where-Object { $_.name -match 'win64\.zip$' } | Select-Object -First 1
    $setupAsset = $rel.assets | Where-Object { $_.name -match 'Setup-.*\.exe$' } | Select-Object -First 1
    if (-not $zipAsset) { throw "github_release_missing_zip" }
    return [pscustomobject]@{
        channel         = "stable"
        latest          = $ver
        min_compatible  = $ver
        notes_url       = [string]$rel.html_url
        windows         = [pscustomobject]@{
            url    = [string]$zipAsset.browser_download_url
            sha256 = ""
            size   = [int64]$zipAsset.size
            setup_url = $(if ($setupAsset) { [string]$setupAsset.browser_download_url } else { "" })
        }
    }
}

Write-Host "==> Current version: $current"
$manifest = $null
try {
    if ($UpdateUrl) {
        Write-Host "==> Fetching manifest: $UpdateUrl"
        $manifest = Get-ManifestFromUrl -Url $UpdateUrl
        if ($manifest.channel -and $Channel -and ($manifest.channel -ne $Channel)) {
            Write-Host "[WARN] manifest channel=$($manifest.channel) requested=$Channel"
        }
    } else {
        Write-Host "==> Fetching latest from GitHub Releases"
        $manifest = Get-FromGitHubReleases
    }
} catch {
    if ($Quiet) { exit 2 }
    throw "update_check_failed: $($_.Exception.Message)"
}

$latest = [string]$manifest.latest
$cmp = Compare-SemVer -A $current -B $latest
$result = [pscustomobject]@{
    current      = $current
    latest       = $latest
    update_available = ($cmp -lt 0)
    download_url = [string]$manifest.windows.url
    setup_url    = $(if ($manifest.windows.setup_url) { [string]$manifest.windows.setup_url } else { "" })
    notes_url    = $(if ($manifest.notes_url) { [string]$manifest.notes_url } else { "" })
    sha256       = $(if ($manifest.windows.sha256) { [string]$manifest.windows.sha256 } else { "" })
}

if (-not $result.update_available) {
    Write-Host "==> Up to date (latest=$latest)" -ForegroundColor Green
    if (-not $Quiet) {
        $result | ConvertTo-Json -Compress | Write-Output
    }
    exit 0
}

Write-Host "==> Update available: $current -> $latest" -ForegroundColor Cyan
if ($result.notes_url) { Write-Host "    Notes: $($result.notes_url)" }

if (-not $Apply) {
    Write-Host "    Download (zip): $($result.download_url)"
    if ($result.setup_url) { Write-Host "    Or install: $($result.setup_url)" }
    Write-Host "    To apply: .\packaging\check_update.ps1 -Apply"
    $result | ConvertTo-Json -Compress | Write-Output
    exit 10
}

$dlDir = Join-Path $data "backups\downloads"
if (-not (Test-Path $dlDir)) {
    New-Item -ItemType Directory -Path $dlDir -Force | Out-Null
}
$zipName = "NetX-$latest-win64.zip"
$zipPath = Join-Path $dlDir $zipName
Write-Host "==> Downloading $zipName ..."
Invoke-WebRequest -Uri $result.download_url -OutFile $zipPath -UseBasicParsing
if ($result.sha256 -and $result.sha256 -notmatch 'REPLACE') {
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
exit 0
