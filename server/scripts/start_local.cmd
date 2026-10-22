@echo off
rem FlowMask one-click start for Windows (double-click or command line).
rem Keep this file ASCII-only: classic cmd.exe decodes batch files with the OEM codepage,
rem so non-ASCII comments can be misparsed as commands.
rem Usage:
rem   scripts\start_local.cmd
rem   scripts\start_local.cmd -WithL2 -EnableNer
rem   scripts\start_local.cmd -WithL2 -WithL3 -L3Device cuda -NoBrowser
rem   scripts\start_local.cmd -Port 8095
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local.ps1" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
