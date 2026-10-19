@echo off
setlocal
cd /d "%~dp0."
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python was not found. Install it from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
%PY% -m pip install -r requirements.txt
if not exist config.yaml copy config.example.yaml config.yaml >nul
echo.
echo Setup finished. Open config.yaml, paste your YouTube API key, then run:
echo     %PY% sog.py research
pause
