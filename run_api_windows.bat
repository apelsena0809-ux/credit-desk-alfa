@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run install_windows_py315.bat first.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"
python -m uvicorn api:app --host 127.0.0.1 --port 8000
pause
