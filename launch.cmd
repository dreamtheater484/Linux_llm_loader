@echo off
rem Start Inflect from a terminal. The Start menu shortcut runs launch.py without a console.
setlocal
set "INFLECT_PROJECT=%~dp0"
set "INFLECT_PROJECT=%INFLECT_PROJECT:~0,-1%"
set "RUNTIME=%LOCALAPPDATA%\Inflect"
if defined INFLECT_RUNTIME set "RUNTIME=%INFLECT_RUNTIME%"
set "PYTHON=%RUNTIME%\manager\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Inflect setup is incomplete. Run: powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -ModelDir PATH
    exit /b 1
)
"%PYTHON%" "%~dp0launch.py" %*
