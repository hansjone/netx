param(
    [string]$Version = "",
    [switch]$SkipBuild = $false,
    [switch]$SkipInstaller = $false,
    [switch]$SkipGitHub = $false,
    [switch]$Draft = $false,
    [switch]$Sign = $false
)

$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"

$repo = Get-NetxRepoRoot
if (-not $Version) {
    $Version = Get-NetxVersion -ProgramRoot $repo
}
$tag = "v$Version"
$releaseDir = Join-Path $PSScriptRoot "release"
$setup = Join-Path $releaseDir "NetX-Setup-$Version.exe"

Write-Host "==> NetX publish $tag"

if (-not $SkipBuild) {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build_release.ps1") `
        -Version $Version -CreateVenv
}

if (-not $SkipInstaller) {
    $isccCmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $isccCandidates = @(
        $(if ($isccCmd) { $isccCmd.Source }),
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    $iscc = $isccCandidates | Select-Object -First 1
    if (-not $iscc) {
        throw "innosetup_not_found: install with winget install JRSoftware.InnoSetup"
    }
    Write-Host "==> Compiling installer: $iscc"
    & $iscc "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "installer\netx.iss")
    if (-not (Test-Path $setup)) {
        throw "installer_build_failed: expected $setup"
    }
    Write-Host "==> Setup.exe: $setup" -ForegroundColor Green
}

if (-not (Test-Path $setup)) {
    throw "missing_setup: $setup"
}

if ($Sign) {
    Write-Host "==> Signing release artifacts"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "sign_release.ps1") -Files @($setup)
}

if ($SkipGitHub) {
    Write-Host "==> Skip GitHub release (-SkipGitHub)"
    exit 0
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "gh_cli_not_found"
}

Set-Location $repo
# gh writes "release not found" to stderr; keep Stop for the rest of the script.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$existing = gh release view $tag 2>$null
$viewCode = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($viewCode -eq 0) {
    Write-Host "==> Release $tag exists; uploading assets"
    gh release upload $tag $setup --clobber
} else {
    $notes = @"
## NetX $Version

### Downloads
- **NetX-Setup-$Version.exe** — Windows installer (Program Files + ProgramData)

### Requirements
- Windows 10/11 x64 (or Windows Server 2016+)
- No separate Python or PostgreSQL install required (bundled)

### Quick start
1. Run ``NetX-Setup-$Version.exe`` as administrator.
2. **First install:** choose built-in or external PostgreSQL.
3. **Already installed:** Setup detects existing data and updates in place (keeps DB settings).
4. Start menu → **Start NetX** → ``http://127.0.0.1:8890/``
5. Default login: ``admin`` / ``admin123`` (change after first login)

### Linux
Unchanged — use your own PostgreSQL and ``scripts/start_netx.sh``.

See [packaging/README.md](https://github.com/hansjone/netx/blob/main/packaging/README.md) for details.
"@
    $args = @("release", "create", $tag, "--title", "NetX $Version", "--notes", $notes)
    if ($Draft) { $args += "--draft" }
    $args += $setup
    & gh @args
}

Write-Host "==> Published $tag" -ForegroundColor Green
gh release view $tag --web 2>$null
