@echo off
echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Starting Ping Monitor...
echo.
echo Web interface will be available at: http://localhost:5000
echo.
python app.py

echo.
echo ============================================
echo The server has stopped or failed to start.
echo Scroll up to read any error message above.
echo ============================================
pause