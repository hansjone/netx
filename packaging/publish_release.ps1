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
$checksum = "$setup.sha256"
$stage = Join-Path $releaseDir "netx-win64"

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) { throw "$Operation failed with exit code $LASTEXITCODE" }
}

Write-Host "==> NetX publish $tag"

if (-not $SkipBuild) {
    if (Test-Path -LiteralPath $setup) { Remove-Item -LiteralPath $setup -Force }
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build_release.ps1") `
        -Version $Version -CreateVenv
    Assert-NativeSuccess 'Build stage'
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
    if (Test-Path -LiteralPath $setup) { Remove-Item -LiteralPath $setup -Force }
    & $iscc "/DMyAppVersion=$Version" (Join-Path $PSScriptRoot "installer\netx.iss")
    Assert-NativeSuccess 'Inno Setup compilation'
    if (-not (Test-Path -LiteralPath $setup) -or (Get-Item -LiteralPath $setup).Length -lt 1MB) {
        throw "installer_build_failed: expected $setup"
    }
    Write-Host "==> Setup.exe: $setup" -ForegroundColor Green
}

if (-not (Test-Path $setup)) {
    throw "missing_setup: $setup"
}

$stageVersionPath = Join-Path $stage 'version.json'
if (-not (Test-Path -LiteralPath $stageVersionPath)) { throw "missing_release_stage: $stageVersionPath" }
$stageVersion = (Get-Content -LiteralPath $stageVersionPath -Raw | ConvertFrom-Json).version
if ([string]$stageVersion -ne $Version) {
    throw "release_version_mismatch: requested=$Version stage=$stageVersion"
}
if ((Get-Item -LiteralPath $setup).LastWriteTimeUtc -lt (Get-Item -LiteralPath $stageVersionPath).LastWriteTimeUtc) {
    throw "stale_installer: $setup predates the release stage; rebuild or recompile before publishing"
}

if ($Sign) {
    Write-Host "==> Signing release artifacts"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "sign_release.ps1") -Files @($setup)
    Assert-NativeSuccess 'Code signing'
}

$hash = (Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $checksum -Value $hash -Encoding ascii -NoNewline
Write-Host "==> SHA-256 $hash"

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
    gh release upload $tag $setup $checksum --clobber
    Assert-NativeSuccess 'GitHub asset upload'
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
- **netx-$Version-linux-x86_64.tar.gz** — offline package for x86_64 Linux (glibc 2.17+: CentOS 7+, Ubuntu 18.04+, Debian, openEuler, Kylin, UOS…), built and attached by the Linux workflow.
- ``tar xzf netx-$Version-linux-x86_64.tar.gz && cd netx-$Version-linux-x86_64 && sudo ./install.sh``
- See [packaging/linux/README.md](https://github.com/hansjone/netx/blob/main/packaging/linux/README.md).

See [packaging/README.md](https://github.com/hansjone/netx/blob/main/packaging/README.md) for details.
"@
    $args = @("release", "create", $tag, "--title", "NetX $Version", "--notes", $notes)
    if ($Draft) { $args += "--draft" }
    $args += $setup
    $args += $checksum
    & gh @args
    Assert-NativeSuccess 'GitHub release creation'
}

Write-Host "==> Published $tag" -ForegroundColor Green
gh release view $tag --web 2>$null
Assert-NativeSuccess 'GitHub release verification'
