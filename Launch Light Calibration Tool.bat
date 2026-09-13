@echo off
cd /d "%~dp0"
echo Starting Light Calibration Tool 2.0...
start "" http://127.0.0.1:5000
python app.py
pause
