@echo off
setlocal
title OpenAlgo Research
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run Setup.cmd first. It installs everything OpenAlgo Research needs.
  if not defined CI if not defined OPENALGO_SETUP_NO_PAUSE pause
  exit /b 1
)
pushd "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0tools\research_desktop.py" %*
set "start_result=%errorlevel%"
popd
if not "%start_result%"=="0" (
  echo.
  echo If Windows reports that Python was blocked, run Setup.cmd again.
  echo Setup can use signed 64-bit Python 3.12 from python.org while keeping your settings and results.
)
if not "%start_result%"=="0" if not defined CI if not defined OPENALGO_SETUP_NO_PAUSE pause
exit /b %start_result%
