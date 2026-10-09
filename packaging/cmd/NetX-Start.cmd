@echo off
setlocal EnableDelayedExpansion
REM Always elevate: Program Files install needs admin to start API / repair venv.
net session >nul 2>&1
if not "%errorLevel%"=="0" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath '%~f0' -Verb RunAs -Wait -PassThru; if ($null -eq $p -or $null -eq $p.ExitCode) { exit 1 }; exit $p.ExitCode } catch { exit 1 }"
  exit /b !ERRORLEVEL!
)
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\packaging\launch_shortcut.ps1" -Action start -ProgramRoot "%ROOT%" -DataRoot "%ProgramData%\NetX"
exit /b !ERRORLEVEL!
