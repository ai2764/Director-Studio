@echo off
rem Director Studio with a console window (live logs). The Start-menu shortcut runs the
rem same launcher without a console (pythonw). Usage: DirectorStudio.cmd [run|stop|status] [--no-open]
setlocal
set "DS_ROOT=%~dp0"
"%DS_ROOT%runtime\python\python.exe" -s "%DS_ROOT%director-studio.pyw" %*
exit /b %ERRORLEVEL%
