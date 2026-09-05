@echo off
setlocal EnableDelayedExpansion
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 "%~dp0Install-Tools.py"
  set "INSTALL_EXIT=!errorlevel!"
  goto finish
)

where python >nul 2>nul
if %errorlevel% equ 0 (
  python "%~dp0Install-Tools.py"
  set "INSTALL_EXIT=!errorlevel!"
  goto finish
)

echo Python 3.10 or newer was not found. Install Python, then run this file again.
set "INSTALL_EXIT=1"

:finish
echo.
pause
exit /b %INSTALL_EXIT%
