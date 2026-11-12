@echo off
rem Stop every FlowMask process started by scripts\start_local.ps1 on this machine.
rem Keep this file ASCII-only: classic cmd.exe decodes batch files with the OEM codepage.
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_local.ps1" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
