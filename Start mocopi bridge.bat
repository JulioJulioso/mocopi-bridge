@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title mocopi bridge

set "PYTHON="
where py >nul 2>&1 && set "PYTHON=py -3"
if not defined PYTHON (
  where python >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON goto :NoPython

%PYTHON% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto :OldPython

if not exist ".venv\Scripts\python.exe" (
  echo Setting up. This only takes a moment the first time.
  %PYTHON% -m venv .venv
  if errorlevel 1 goto :NoPython
)

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :PipFailed

set "PYTHONPATH=%~dp0src"
start "" ".venv\Scripts\pythonw.exe" -m mocopi_bridge
exit /b 0

:OldPython
call :Tell "Python is too old." "Install Python 3.11 or newer from https://www.python.org/downloads/ and check Add python.exe to PATH. Then double-click this file again."
exit /b 1

:NoPython
call :Tell "Python was not found." "Install Python 3.11 or newer from https://www.python.org/downloads/ and check Add python.exe to PATH. Then double-click this file again."
exit /b 1

:PipFailed
call :Tell "Setup could not finish." "The bridge could not install its one required library. Check the internet connection and double-click this file again."
exit /b 1

:Tell
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('%~2', 'mocopi bridge')"
echo %~1
echo %~2
pause
exit /b 0
