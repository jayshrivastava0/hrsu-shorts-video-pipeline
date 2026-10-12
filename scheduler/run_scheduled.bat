@echo off
REM Task Scheduler entry point: new blog post -> explainer video -> YouTube.
REM Set HRSU_PYTHON to a specific python.exe (e.g. a venv) if "python" on PATH is not the right one.
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
if "%HRSU_PYTHON%"=="" (set "PY=python") else (set "PY=%HRSU_PYTHON%")
if exist "venv\Scripts\activate.bat" call "venv\Scripts\activate.bat"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
"%PY%" -m scheduler.run_daily %*
exit /b %ERRORLEVEL%
