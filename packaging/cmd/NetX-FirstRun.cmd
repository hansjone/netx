@echo off
setlocal EnableDelayedExpansion
net session >nul 2>&1
if not "%errorLevel%"=="0" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $p = Start-Process -FilePath '%~f0' -Verb RunAs -Wait -PassThru; if ($null -eq $p -or $null -eq $p.ExitCode) { exit 1 }; exit $p.ExitCode } catch { exit 1 }"
  exit /b !ERRORLEVEL!
)
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"
title NetX - Reconfigure database
echo.
echo  NetX database reconfigure / 重新配置数据库
echo  Prefer the installer wizard for first-time setup.
echo  首次安装请用安装向导选择内置或外置数据库。
echo  This window is for repair / reconfigure only.
echo  本窗口仅用于修复或重新配置。
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\packaging\setup_first_run.ps1" -ProgramRoot "%ROOT%" -DataRoot "%ProgramData%\NetX"
set "EC=!ERRORLEVEL!"
if not "!EC!"=="0" (
  echo.
  echo Setup failed / 配置失败  exit=!EC!
  pause
  exit /b !EC!
)
echo.
echo Starting NetX tray... / 正在启动托盘...
start "" powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "%ROOT%\packaging\netx_tray.ps1" -ProgramRoot "%ROOT%" -DataRoot "%ProgramData%\NetX" -StartOnLaunch
echo.
echo Done. You can close this window. / 完成，可关闭本窗口。
pause
exit /b 0
