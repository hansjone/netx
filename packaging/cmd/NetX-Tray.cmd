@echo off
setlocal EnableDelayedExpansion
REM Elevate once, then start tray (inherits admin for Start/Stop/Update).
net session >nul 2>&1
if not "%errorLevel%"=="0" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath '%~f0' -Verb RunAs -Wait -PassThru; if ($null -eq $p -or $null -eq $p.ExitCode) { exit 1 }; exit $p.ExitCode } catch { exit 1 }"
  exit /b !ERRORLEVEL!
)
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%ROOT%\packaging\netx_tray.ps1" -ProgramRoot "%ROOT%" -DataRoot "%ProgramData%\NetX" -StartOnLaunch
exit /b 0
