param(
    [Parameter(Mandatory = $true)]
    [string[]]$Files,
    [string]$Thumbprint = "",
    [string]$PfxPath = "",
    [string]$PfxPassword = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$Description = "NetX"
)

# Sign release artifacts with signtool (Authenticode).
# Requires Windows SDK / signtool.exe and a code-signing certificate.
#
# Examples:
#   .\sign_release.ps1 -Files .\packaging\release\NetX-Setup-0.4.0.exe -Thumbprint ABCDEF...
#   .\sign_release.ps1 -Files .\packaging\release\*.exe -PfxPath .\certs\netx.pfx -PfxPassword ***

$ErrorActionPreference = "Stop"

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $hit = Get-ChildItem -Path $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

$signtool = Find-SignTool
if (-not $signtool) {
    throw "signtool_not_found: install Windows SDK or add signtool.exe to PATH"
}

if (-not $Thumbprint -and -not $PfxPath) {
    if ($env:NETX_SIGN_THUMBPRINT) { $Thumbprint = $env:NETX_SIGN_THUMBPRINT }
    if ($env:NETX_SIGN_PFX) { $PfxPath = $env:NETX_SIGN_PFX }
    if ($env:NETX_SIGN_PFX_PASSWORD) { $PfxPassword = $env:NETX_SIGN_PFX_PASSWORD }
}
if (-not $Thumbprint -and -not $PfxPath) {
    throw "provide -Thumbprint or -PfxPath (or NETX_SIGN_THUMBPRINT / NETX_SIGN_PFX)"
}

$resolved = @()
foreach ($pattern in $Files) {
    $resolved += @(Resolve-Path -Path $pattern -ErrorAction Stop)
}
if ($resolved.Count -eq 0) { throw "no_files_to_sign" }

foreach ($f in $resolved) {
    $path = $f.Path
    Write-Host "==> Signing $path"
    $args = @(
        "sign", "/fd", "SHA256", "/td", "SHA256", "/tr", $TimestampUrl,
        "/d", $Description
    )
    if ($PfxPath) {
        $args += @("/f", $PfxPath)
        if ($PfxPassword) { $args += @("/p", $PfxPassword) }
    } else {
        $args += @("/sha1", $Thumbprint)
    }
    $args += $path
    & $signtool @args
    if ($LASTEXITCODE -ne 0) { throw "sign_failed: $path" }
    & $signtool verify /pa $path
    if ($LASTEXITCODE -ne 0) { throw "verify_failed: $path" }
    Write-Host "    OK" -ForegroundColor Green
}

Write-Host "==> Done. Without a trusted CA certificate, Windows SmartScreen may still warn."
