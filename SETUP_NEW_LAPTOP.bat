@echo off
title Desk Control Hub - Setup
cd /d "%~dp0"

echo ===================================================
echo     DESK CONTROL HUB - WINDOWS QUICK SETUP
echo ===================================================
echo.

:: 1. Check Python
py --version >nul 2>&1
if %errorlevel% neq 0 (
    python --version >nul 2>&1
    if %errorlevel% neq 0 (
        echo [ERROR] Python is not installed or not in PATH!
        echo Please download and install Python from: https://www.python.org/downloads/
        echo (Make sure to check "Add Python to PATH" during installation)
        echo.
        pause
        exit /b 1
    )
    set PY_CMD=python
) else (
    set PY_CMD=py
)

echo [1/3] Creating Python Virtual Environment...
if not exist ".venv" (
    %PY_CMD% -m venv .venv
)

echo [2/3] Installing Dependencies...
call .\.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install PySide6 pyserial pyautogui screen-brightness-control pycaw comtypes

echo.
echo [3/3] Setup complete!
echo.
echo ===================================================
echo Launching Desk Control Hub...
echo ===================================================
start "" ".\.venv\Scripts\pythonw.exe" main_app.py
exit
