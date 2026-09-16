@echo off
setlocal
title OpenAlgo Research - Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\research_setup.ps1" %*
set "setup_result=%errorlevel%"
if not "%setup_result%"=="0" (
  echo.
  echo Setup did not finish. Read the message above, then run Setup.cmd again.
  if not defined CI if not defined OPENALGO_SETUP_NO_PAUSE pause
)
exit /b %setup_result%
