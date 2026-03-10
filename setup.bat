@echo off
echo ============================================
echo  SCOS Python Project Setup
echo ============================================

:: Create virtual environment
echo [1/3] Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo ERROR: Python not found or venv creation failed.
    echo Please install Python 3.11 from https://python.org/downloads
    pause
    exit /b 1
)

:: Activate and upgrade pip
echo [2/3] Upgrading pip...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip

:: Install all dependencies
echo [3/3] Installing dependencies...
pip install -r requirements.txt

echo.
echo ============================================
echo  Setup complete!
echo  To activate the environment: venv\Scripts\activate
echo ============================================
pause
