@echo off
setlocal enabledelayedexpansion
pushd "%~dp0"

echo Installing dependencies...
"%~dp0\.venv\Scripts\python.exe" -m pip install --upgrade pip
"%~dp0\.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"

echo.
echo Starting Ping Monitor...
echo.
echo Web interface will be available at: http://localhost:5000
echo.
"%~dp0\.venv\Scripts\python.exe" app.py

popd
endlocal

echo.
echo ============================================
echo The server has stopped or failed to start.
echo Scroll up to read any error message above.
echo ============================================
pause