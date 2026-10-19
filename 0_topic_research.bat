@echo off
cd /d "%~dp0topic_research"
rem --- find Python: prefer the "py" launcher, fall back to "python"
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python was not found. Install it from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Researching what's winning right now...
%PY% sog.py research
if exist "data\research\topics_prompt.md" (
  echo.
  echo Next: paste data\research\topics_prompt.md into your Claude Project,
  echo save the numbered answer as data\research\ideas.md, then run:  %PY% sog.py ideas
  start "" "data\research"
)
pause
