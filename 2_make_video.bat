@echo off
cd /d "%~dp0"
rem --- find Python: prefer the "py" launcher, fall back to "python"
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python was not found. Install it from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Files are read from:  input\script   input\voice   input\music
set /p EP=Episode name (same as step 1): 
%PY% sleep_editor.py "%EP%"
pause
