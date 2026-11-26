@echo off
setlocal
cd /d "%~dp0"
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)
call venv\Scripts\activate.bat
echo Installing dependencies...
pip install -r requirements.txt
echo Starting Vaagai Pharm...
start http://127.0.0.1:5000
python app.py
