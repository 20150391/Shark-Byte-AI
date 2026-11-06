@echo off
setlocal
cd /d "%~dp0"
title SharkByte Launcher

where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found.
        echo Install Python 3 from https://www.python.org/downloads/ and enable Add Python to PATH.
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

echo ========================================
echo           SHARKBYTE INSTALLER
echo ========================================
echo.
echo Checking required Python packages...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Package installation failed. Check your internet connection and Python installation.
    pause
    exit /b 1
)

echo.
echo Checking the background Chromium browser (first install may take a few minutes)...
%PYTHON% -m playwright install chromium
if errorlevel 1 (
    echo.
    echo Chromium could not be installed. Check your internet connection and try again.
    pause
    exit /b 1
)

echo.
echo Starting SharkByte...
echo.
%PYTHON% app.py
set "EXIT_CODE=%errorlevel%"
echo.
if not "%EXIT_CODE%"=="0" (
    echo SharkByte exited with code %EXIT_CODE%.
) else (
    echo SharkByte has closed.
)
pause
exit /b %EXIT_CODE%
