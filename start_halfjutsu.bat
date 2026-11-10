@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Halfjutsu
if not exist ".venv-wangp\Scripts\python.exe" (
  echo Halfjutsu is not installed yet: run install.bat first.
  pause
  exit /b 1
)
if not exist "data\viggle_ready.txt" (
  echo The model weights are missing: run install.bat to finish the download.
  pause
  exit /b 1
)
set PYTHONUTF8=1
echo Starting Halfjutsu on http://127.0.0.1:8787  (close this window to stop it)
start "" http://127.0.0.1:8787
.venv-wangp\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
pause
