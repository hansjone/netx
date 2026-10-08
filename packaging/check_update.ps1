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
# Default (no config needed):
#   GitHub primary + Forgejo mirror fallback (git.avelo.top).
#   Probe every reachable source and pick the highest version;
#   on equal versions prefer GitHub (listed first).
#
# Optional overrides:
#   NETX_UPDATE_SOURCES=github,forgejo
#   NETX_UPDATE_FORGEJO_URL=https://git.avelo.top/api/v1/repos/hansjone/netx/releases/latest
#   NETX_UPDATE_GITHUB_REPO=hansjone/netx
#   NETX_UPDATE_URL=... manifest JSON
#   NETX_UPDATE_TOKEN=... private API token

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
if (-not $ForgejoUrl) {
    $ForgejoUrl = "https://git.avelo.top/api/v1/repos/hansjone/netx/releases/latest"
}
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

function Resolve-AssetUrl {
    param($Asset, $Rel, [string]$SourceName, [string]$ApiLatestUrl = "")
    $url = [string]$Asset.browser_download_url
    if ($url) { return $url }
    # Forgejo/Gitea sometimes omit browser_download_url; synthesize release download URL.
    if ($SourceName -eq "forgejo" -and $ApiLatestUrl -and $Rel.tag_name -and $Asset.name) {
        if ($ApiLatestUrl -match '^(https?://[^/]+)/api/v1/repos/([^/]+)/([^/]+)/releases/latest') {
            $base = $Matches[1]
            $owner = $Matches[2]
            $repo = $Matches[3]
            $tag = [string]$Rel.tag_name
            $name = [uri]::EscapeDataString([string]$Asset.name).Replace('%2F', '/')
            # EscapeDataString encodes too much; use raw name (assets shouldn't need query encoding).
            $name = [string]$Asset.name
            return "$base/$owner/$repo/releases/download/$tag/$name"
        }
    }
    return ""
}

function Convert-ReleaseToManifest {
    param($Rel, [string]$SourceName, [string]$ApiLatestUrl = "")
    $tag = [string]$Rel.tag_name
    $ver = $tag.TrimStart('v', 'V')
    $assets = @($Rel.assets)
    # Prefer Setup.exe (current ship format). Legacy win64.zip still accepted.
    $setupAsset = $assets | Where-Object { $_.name -match 'Setup-.*\.exe$' } | Select-Object -First 1
    $zipAsset = $assets | Where-Object { $_.name -match 'win64\.zip$' } | Select-Object -First 1
    if (-not $setupAsset -and -not $zipAsset) {
        throw "${SourceName}_release_missing_setup_or_zip"
    }

    $setupUrl = ""
    if ($setupAsset) {
        $setupUrl = Resolve-AssetUrl -Asset $setupAsset -Rel $Rel -SourceName $SourceName -ApiLatestUrl $ApiLatestUrl
        if (-not $setupUrl) { throw "${SourceName}_setup_url_missing" }
    }
    $zipUrl = ""
    if ($zipAsset) {
        $zipUrl = Resolve-AssetUrl -Asset $zipAsset -Rel $Rel -SourceName $SourceName -ApiLatestUrl $ApiLatestUrl
    }

    if ($setupUrl) {
        $primaryUrl = $setupUrl
        $packageKind = "setup"
        $primarySize = [int64]$(if ($setupAsset.size) { $setupAsset.size } else { 0 })
    } else {
        if (-not $zipUrl) { throw "${SourceName}_zip_url_missing" }
        $primaryUrl = $zipUrl
        $packageKind = "zip"
        $primarySize = [int64]$(if ($zipAsset.size) { $zipAsset.size } else { 0 })
    }

    return [pscustomobject]@{
        channel        = "stable"
        latest         = $ver
        min_compatible = $ver
        notes_url      = [string]$Rel.html_url
        source         = $SourceName
        windows        = [pscustomobject]@{
            url       = $primaryUrl
            sha256    = ""
            size      = $primarySize
            setup_url = $setupUrl
            package   = $packageKind
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
    $headers = Get-AuthHeaders -Style "token"
    $headers["Accept"] = "application/json"
    $rel = Invoke-RestMethod -Uri $ApiUrl -Headers $headers -TimeoutSec 30
    return Convert-ReleaseToManifest -Rel $rel -SourceName "forgejo" -ApiLatestUrl $ApiUrl
}

function Get-UpdateSourceList {
    if ($Sources) {
        return @($Sources.Split(',') | ForEach-Object { $_.Trim().ToLowerInvariant() } | Where-Object { $_ })
    }
    # Default: GitHub primary, Forgejo mirror backup. Optional manifests prepended if configured.
    $list = [System.Collections.Generic.List[string]]::new()
    if ($UpdateUrl) { [void]$list.Add("manifest") }
    [void]$list.Add("github")
    [void]$list.Add("forgejo")
    if ($FallbackUrl) { [void]$list.Add("manifest_fallback") }
    return @($list)
}

function Get-ManifestFromSource([string]$src) {
    switch ($src) {
        "manifest" {
            if (-not $UpdateUrl) { throw "NETX_UPDATE_URL empty" }
            Write-Host "==> Probing manifest: $UpdateUrl"
            return Get-ManifestFromUrl -Url $UpdateUrl
        }
        "manifest_fallback" {
            if (-not $FallbackUrl) { throw "NETX_UPDATE_FALLBACK_URL empty" }
            Write-Host "==> Probing fallback manifest: $FallbackUrl"
            $m = Get-ManifestFromUrl -Url $FallbackUrl
            if ($m -and -not $m.source) {
                $m | Add-Member -NotePropertyName source -NotePropertyValue "manifest_fallback" -Force
            }
            return $m
        }
        "forgejo" {
            Write-Host "==> Probing Forgejo: $ForgejoUrl"
            return Get-FromForgejoReleases -ApiUrl $ForgejoUrl
        }
        "github" {
            Write-Host "==> Probing GitHub: $GithubRepo"
            return Get-FromGitHubReleases
        }
        default { throw "unknown_source: $src" }
    }
}

Write-Host "==> Current version: $current"
$sourceList = Get-UpdateSourceList
Write-Host "==> Probe sources (pick newest reachable; tie → earlier in list): $($sourceList -join ', ')"

$candidates = @()
$errors = @()
foreach ($src in $sourceList) {
    try {
        $m = Get-ManifestFromSource -src $src
        if ($m.channel -and $Channel -and ($m.channel -ne $Channel)) {
            Write-Host "[WARN] $src channel=$($m.channel) requested=$Channel"
        }
        if (-not $m.windows -or -not $m.windows.url) {
            throw "missing_windows_download_url"
        }
        Write-Host "    OK $($m.source) latest=$($m.latest)" -ForegroundColor DarkGreen
        $candidates += $m
    } catch {
        $msg = $_.Exception.Message
        $errors += "${src}: $msg"
        Write-Host "[WARN] source '$src' unreachable/failed: $msg" -ForegroundColor Yellow
    }
}

if ($candidates.Count -eq 0) {
    $joined = ($errors -join "; ")
    if ($Quiet) { exit 2 }
    throw "update_check_failed: all sources failed ($joined)"
}

# Prefer highest semver; on tie keep earlier source in $sourceList (GitHub before Forgejo by default).
$manifest = $candidates[0]
foreach ($c in $candidates) {
    $cmpCand = Compare-SemVer -A ([string]$manifest.latest) -B ([string]$c.latest)
    if ($cmpCand -lt 0) {
        $manifest = $c
    }
}

Write-Host "==> Selected source=$($manifest.source) latest=$($manifest.latest) (from $($candidates.Count) reachable)" -ForegroundColor Green

# Prefer Setup.exe when a manifest still lists a legacy zip as windows.url but also has setup_url.
if ($manifest.windows -and $manifest.windows.setup_url) {
    $setupCandidate = [string]$manifest.windows.setup_url
    $urlNow = [string]$manifest.windows.url
    $pkgNow = if ($manifest.windows.package) { [string]$manifest.windows.package } else { "" }
    if ($setupCandidate -and ($pkgNow -eq "zip" -or $urlNow -match '\.zip(\?|$)' -or -not $urlNow)) {
        $manifest.windows | Add-Member -NotePropertyName url -NotePropertyValue $setupCandidate -Force
        $manifest.windows | Add-Member -NotePropertyName package -NotePropertyValue "setup" -Force
    }
}

$latest = [string]$manifest.latest
$cmp = Compare-SemVer -A $current -B $latest
$packageKind = "setup"
if ($manifest.windows.package) {
    $packageKind = [string]$manifest.windows.package
} elseif ([string]$manifest.windows.url -match '\.zip(\?|$)') {
    $packageKind = "zip"
}

$result = [pscustomobject]@{
    current          = $current
    latest           = $latest
    update_available = ($cmp -lt 0)
    source           = $(if ($manifest.source) { [string]$manifest.source } else { "unknown" })
    download_url     = [string]$manifest.windows.url
    setup_url        = $(if ($manifest.windows.setup_url) { [string]$manifest.windows.setup_url } else { "" })
    package          = $packageKind
    notes_url        = $(if ($manifest.notes_url) { [string]$manifest.notes_url } else { "" })
    sha256           = $(if ($manifest.windows.sha256) { [string]$manifest.windows.sha256 } else { "" })
    reachable        = @($candidates | ForEach-Object { "$($_.source)=$($_.latest)" })
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
    Write-Host "    Download ($($result.package)): $($result.download_url)"
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

    $isSetup = ($result.package -eq "setup") -or ($result.download_url -match '\.exe(\?|$)')
    if ($isSetup) {
        $pkgName = "NetX-Setup-$latest.exe"
    } else {
        $pkgName = "NetX-$latest-win64.zip"
    }
    $pkgPath = Join-Path $dlDir $pkgName

    $needDownload = $true
    if ((Test-Path -LiteralPath $pkgPath) -and ((Get-Item -LiteralPath $pkgPath).Length -gt 1MB)) {
        Write-Host "==> Using existing download: $pkgPath ($([math]::Round((Get-Item $pkgPath).Length/1MB,1)) MB)"
        $needDownload = $false
    }
    if ($needDownload) {
        Write-Host "==> Downloading $pkgName from $($result.source) ..."
        # Only attach token for non-GitHub hosts (Forgejo/private). Public GitHub assets need no auth;
        # a Forgejo token would break anonymous GitHub downloads.
        $dlHeaders = @{ "User-Agent" = "NetX-UpdateCheck" }
        $dlUri = [uri]$result.download_url
        $isGithub = ($dlUri.Host -match '(^|\.)github\.com$' -or $dlUri.Host -match '(^|\.)githubusercontent\.com$')
        if ($UpdateToken -and -not $isGithub) {
            $dlHeaders["Authorization"] = "token $UpdateToken"
        }
        Invoke-WebRequest -Uri $result.download_url -OutFile $pkgPath -Headers $dlHeaders -UseBasicParsing
    }
    if ($result.sha256 -and $result.sha256 -notmatch 'REPLACE' -and $result.sha256.Trim()) {
        $hash = (Get-FileHash -Path $pkgPath -Algorithm SHA256).Hash.ToLowerInvariant()
        $expect = $result.sha256.ToLowerInvariant()
        if ($hash -ne $expect) {
            throw "sha256_mismatch: got $hash expected $expect"
        }
        Write-Host "==> SHA256 OK"
    }

    # Program Files installs need admin; elevate once (avoid loop via NETX_UPDATE_ELEVATED).
    $progWritable = $false
    try {
        $probe = Join-Path $prog (".netx_w_" + [guid]::NewGuid().ToString("n"))
        [IO.File]::WriteAllText($probe, "x")
        Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
        $progWritable = $true
    } catch {
        $progWritable = $false
    }
    if (-not $progWritable -and -not $env:NETX_UPDATE_ELEVATED) {
        Write-Host "==> Program directory is not writable; relaunching update as Administrator (UAC)..." -ForegroundColor Yellow
        $arg = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -ProgramRoot `"$prog`" -DataRoot `"$data`" -Apply"
        $ep = Start-Process -FilePath "powershell.exe" -ArgumentList $arg -WorkingDirectory $prog `
            -Verb RunAs -Wait -PassThru
        if ($null -eq $ep.ExitCode) { exit 1 }
        exit $ep.ExitCode
    }

    $env:NETX_UPDATE_ELEVATED = "1"
    if ($isSetup) {
        # Silent Setup over an existing configured install skips the DB wizard
        # (see installer/netx.iss GSkipDbPage) and preserves ProgramData.
        Write-Host "==> Applying update via silent Setup.exe"
        $setupArgs = "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES /DIR=`"$prog`" /SkipDbPage=1"
        $sp = Start-Process -FilePath $pkgPath -ArgumentList $setupArgs -Wait -PassThru
        if ($null -eq $sp.ExitCode -or $sp.ExitCode -ne 0) {
            $code = if ($null -eq $sp.ExitCode) { "null" } else { $sp.ExitCode }
            throw "setup_update_failed: exit $code"
        }
        Write-Host "==> Restarting NetX after Setup"
        & (Join-Path $PSScriptRoot "start_netx_app.ps1") `
            -ProgramRoot $prog -DataRoot $data -SkipBrowser
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Write-Host "==> Applying legacy zip update via update_netx.ps1"
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "update_netx.ps1") `
            -PackagePath $pkgPath -ProgramRoot $prog -DataRoot $data
    }
    Write-Host "==> Update applied to $latest" -ForegroundColor Green
} finally {
    Remove-Item -Force $lockFile -ErrorAction SilentlyContinue
}
exit 0
