param(
    [string]$Version = "",
    [string]$ForgejoBase = "http://10.0.0.131:3000",
    [string]$Owner = "hansjone",
    [string]$Repo = "netx",
    [string]$Token = "",
    [string]$ReleaseDir = "",
    [switch]$SkipSetup = $false
)

# Publish Windows release assets to Forgejo/Gitea (mirror sync does NOT copy GitHub Release files).
# Requires a Forgejo token with repo write (Settings → Applications → Generate New Token).
# Default: home LAN Forgejo. Public git.avelo.top is the same instance (VPS reverse proxy).
#
# Example (usual — LAN):
#   $env:NETX_FORGEJO_TOKEN = "..."   # or User env NETX_FORGEJO_TOKEN
#   .\packaging\publish_forgejo_release.ps1 -Version 0.4.0
#
# Away from LAN only (hairpins via VPS):
#   .\packaging\publish_forgejo_release.ps1 -Version 0.4.0 -ForgejoBase "https://git.avelo.top"

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$repoRoot = Get-NetxRepoRoot
if (-not $Version) {
    $Version = Get-NetxVersion -ProgramRoot $repoRoot
}
$tag = "v$Version"
$relDir = if ($ReleaseDir) { $ReleaseDir } else { Join-Path $PSScriptRoot "release" }
$zip = Join-Path $relDir "NetX-$Version-win64.zip"
$setup = Join-Path $relDir "NetX-Setup-$Version.exe"

if (-not $Token) {
    $Token = $env:NETX_FORGEJO_TOKEN
    if (-not $Token) { $Token = $env:FORGEJO_TOKEN }
}
if (-not $Token) {
    throw "forgejo_token_required: set NETX_FORGEJO_TOKEN or pass -Token"
}
if (-not (Test-Path $zip)) {
    throw "missing_zip: $zip (run build_release.ps1 first)"
}

$apiBase = "$ForgejoBase/api/v1/repos/$Owner/$Repo"
$headers = @{
    "Authorization" = "token $Token"
    "Accept"        = "application/json"
}

function Invoke-ForgejoJson {
    param(
        [string]$Method,
        [string]$Url,
        [object]$Body = $null
    )
    $params = @{
        Method      = $Method
        Uri         = $Url
        Headers     = $headers
        ContentType = "application/json"
    }
    if ($null -ne $Body) {
        $params["Body"] = ($Body | ConvertTo-Json -Depth 5)
    }
    return Invoke-RestMethod @params
}

Write-Host "==> Forgejo publish $tag -> $ForgejoBase/$Owner/$Repo"

# Ensure git tag exists on Forgejo (mirror usually already has it).
try {
    $null = Invoke-ForgejoJson -Method GET -Url "$apiBase/git/refs/tags/$tag"
    Write-Host "    tag $tag present on Forgejo"
} catch {
    Write-Host "[WARN] tag $tag not found on Forgejo yet — run mirror-sync or push tag first" -ForegroundColor Yellow
}

$existing = $null
try {
    $existing = Invoke-ForgejoJson -Method GET -Url "$apiBase/releases/tags/$tag"
} catch {
    $existing = $null
}

$notes = @"
NetX $Version Windows install package (published from GitHub release build).

- NetX-Setup-$Version.exe
- NetX-$Version-win64.zip
"@

if ($existing -and $existing.id) {
    Write-Host "==> Release $tag already exists (id=$($existing.id)); deleting old release to re-upload assets"
    Invoke-ForgejoJson -Method DELETE -Url "$apiBase/releases/$($existing.id)" | Out-Null
}

Write-Host "==> Creating release $tag"
$rel = Invoke-ForgejoJson -Method POST -Url "$apiBase/releases" -Body @{
    tag_name    = $tag
    target      = "main"
    name        = "NetX $Version"
    body        = $notes
    draft       = $false
    prerelease  = $false
}
$relId = $rel.id
if (-not $relId) { throw "forgejo_create_release_failed" }

function Upload-ForgejoAsset {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    $name = Split-Path -Leaf $Path
    Write-Host "==> Uploading $name ..."
    $url = "$apiBase/releases/$relId/assets"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { throw "curl.exe required for asset upload" }
    & $curl.Source -f -sS -X POST `
        -H "Authorization: token $Token" `
        -F "attachment=@$Path" `
        $url
    if ($LASTEXITCODE -ne 0) { throw "upload_failed: $name" }
    Write-Host "    OK $name" -ForegroundColor Green
}

Upload-ForgejoAsset -Path $zip
if (-not $SkipSetup -and (Test-Path $setup)) {
    Upload-ForgejoAsset -Path $setup
}

$releaseUrl = "$ForgejoBase/$Owner/$Repo/releases/tag/$tag"
Write-Host ""
Write-Host "==> Forgejo release ready: $releaseUrl" -ForegroundColor Green
Write-Host "    Update API: $ForgejoBase/api/v1/repos/$Owner/$Repo/releases/latest"
