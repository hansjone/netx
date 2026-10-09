param(
    [string]$ProgramRoot = "",
    [string]$DataRoot = ""
)

# Relink shipped .venv to local python/runtime (must be elevated under Program Files).
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\_common.ps1"
Assert-NetxAdminOrRelaunch -ScriptPath $PSCommandPath -BoundParameters $PSBoundParameters -WindowStyle Hidden

$prog = Get-NetxProgramRoot -Override $ProgramRoot
$data = Get-NetxDataRoot -ProgramRoot $prog -Override $DataRoot

Write-Host "==> Repairing .venv under $prog"
if (Repair-NetxShippedVenv -ProgramRoot $prog) {
    $venvPy = Join-Path $prog ".venv\Scripts\python.exe"
    if (Test-NetxVenvRunnable -VenvPython $venvPy) {
        Write-Host "==> .venv OK -> bundled python/runtime" -ForegroundColor Green
        Get-Content (Join-Path $prog ".venv\pyvenv.cfg")
        exit 0
    }
    Write-Host "[ERR] pyvenv.cfg written but venv still not runnable" -ForegroundColor Red
    exit 2
}
Write-Host "[ERR] repair failed (missing runtime/.venv or access denied)" -ForegroundColor Red
exit 1
