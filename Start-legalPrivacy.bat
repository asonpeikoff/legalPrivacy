@echo off
REM Double-click to run legalPrivacy from source. Sets up a private environment on first run,
REM and updates it whenever the requirements (pyproject.toml) change.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
set NEED=0
if not exist .venv\Scripts\python.exe (
  echo First run: setting things up, one moment...
  %PY% -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
  set NEED=1
)
fc /b pyproject.toml .venv\installed-pyproject.toml >nul 2>nul || set NEED=1
if "%NEED%"=="1" (
  echo Installing or updating what legalPrivacy needs...
  .venv\Scripts\python -m pip install --quiet -e . || (echo Install failed & pause & exit /b 1)
  copy /y pyproject.toml .venv\installed-pyproject.toml >nul
)
.venv\Scripts\python -m legalprivacy
if errorlevel 1 pause
