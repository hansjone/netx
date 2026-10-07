param(
    [string]$DataRoot = ""
)

# Post-uninstall optional data wipe with retries (handles briefly locked runtime logs).

$ErrorActionPreference = "Continue"
if (-not $DataRoot) {
    $DataRoot = Join-Path ([Environment]::GetFolderPath("CommonApplicationData")) "NetX"
}

if (-not (Test-Path -LiteralPath $DataRoot)) {
    Write-Host "==> Data root already gone: $DataRoot"
    exit 0
}

Write-Host "==> Deleting data root: $DataRoot"

# Kill anything still holding files under data root.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and ($_.CommandLine.IndexOf($DataRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
} | ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    try { & taskkill.exe /F /PID $_.ProcessId 2>$null | Out-Null } catch {}
}

$ok = $false
for ($i = 1; $i -le 5; $i++) {
    try {
        cmd /c "rmdir /s /q `"$DataRoot`""
    } catch {}
    if (-not (Test-Path -LiteralPath $DataRoot)) {
        $ok = $true
        break
    }
    Start-Sleep -Seconds 1
}

if (-not $ok -and (Test-Path -LiteralPath $DataRoot)) {
    try {
        takeown /F $DataRoot /R /D Y | Out-Null
        icacls $DataRoot /grant Administrators:F /T | Out-Null
        cmd /c "rmdir /s /q `"$DataRoot`""
    } catch {}
}

if (Test-Path -LiteralPath $DataRoot) {
    Write-Host "[WARN] Could not fully delete $DataRoot"
    exit 1
}

Write-Host "==> Data root deleted"
exit 0
