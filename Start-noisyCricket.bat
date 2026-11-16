@echo off
REM Double-click to run noisyCricket from source (creates a private venv on first run).
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv\Scripts\python.exe (
  echo First run: setting things up, one moment...
  %PY% -m venv .venv || (echo Python 3.10+ is required: https://www.python.org/downloads/ & pause & exit /b 1)
  .venv\Scripts\python -m pip install --quiet -e . || (echo Install failed & pause & exit /b 1)
)
.venv\Scripts\python -m noisycricket
if errorlevel 1 pause
