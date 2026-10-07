@echo off
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\packaging\launch_shortcut.ps1" -Action tray -ProgramRoot "%ROOT%" -DataRoot "%ProgramData%\NetX" -StartOnLaunch
exit /b %ERRORLEVEL%
