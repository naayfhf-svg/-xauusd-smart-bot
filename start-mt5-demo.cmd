@echo off
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.12 64-bit from python.org, then run this file again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt -r requirements-mt5.txt
if errorlevel 1 (
  echo Dependency installation failed. Check your connection.
  pause
  exit /b 1
)
set MT5_READ_ONLY_ENABLED=true
".venv\Scripts\python.exe" -m streamlit run app.py --server.address 127.0.0.1
pause
