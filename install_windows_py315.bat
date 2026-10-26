@echo off
setlocal
cd /d "%~dp0"
py -3.15 --version
if errorlevel 1 (
  echo Python 3.15 was not found. Install it from python.org first.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  py -3.15 -m venv .venv
  if errorlevel 1 goto fail
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto fail
python -m pip install -r requirements.lock.txt
if errorlevel 1 goto fail
echo.
echo Dependencies installed successfully.
echo Start API with: run_api_windows.bat
echo Start web interface with: run_web_windows.bat
pause
exit /b 0
:fail
echo.
echo Installation failed. Copy the last lines of the error and send them for troubleshooting.
pause
exit /b 1
