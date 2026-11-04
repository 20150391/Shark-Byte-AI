@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title SharkByte - Full Dependency Installer

echo.
echo  ==========================================================
echo             SHARKBYTE - FULL INSTALLER
echo  ==========================================================
echo  This installer checks Python, all app packages, Chromium,
echo  and Ollama. It does not delete your chats or settings.
echo.

set "PYTHON_CMD="
set "PYTHON_ARGS="

REM Prefer Python 3.13 because SharkByte has been tested with it.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.13 --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_ARGS=-3.13"
        goto :python_found
    )
    py -3.12 --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_ARGS=-3.12"
        goto :python_found
    )
    py -3.11 --version >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_ARGS=-3.11"
        goto :python_found
    )
)

REM If supported Python is missing, offer to install Python 3.13.
echo [CHECK] Looking for a supported Python installation...
where winget >nul 2>nul
if errorlevel 1 goto :python_manual

echo Python 3.13/3.12/3.11 was not found.
choice /C YN /N /M "Install Python 3.13 using Windows Package Manager? [Y/N] "
if errorlevel 2 goto :python_manual

echo.
echo [INSTALL] Installing Python 3.13...
winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo.
    echo Python installation failed. Install Python 3.13 from:
    echo https://www.python.org/downloads/release/python-313/
    goto :failed
)

REM Refresh PATH for this installer process and check the Python launcher again.
call :refresh_path
where py >nul 2>nul
if errorlevel 1 goto :python_manual
py -3.13 --version >nul 2>nul
if errorlevel 1 goto :python_manual
set "PYTHON_CMD=py"
set "PYTHON_ARGS=-3.13"

:python_found
echo.
echo [OK] Using:
%PYTHON_CMD% %PYTHON_ARGS% --version
if errorlevel 1 goto :failed

echo.
echo [INSTALL] Updating pip...
%PYTHON_CMD% %PYTHON_ARGS% -m ensurepip --upgrade
%PYTHON_CMD% %PYTHON_ARGS% -m pip install --upgrade pip
if errorlevel 1 goto :pip_failed

echo.
echo [INSTALL] Installing every third-party Python package used by SharkByte...
%PYTHON_CMD% %PYTHON_ARGS% -m pip install --upgrade requests prompt_toolkit pypdf python-docx playwright
if errorlevel 1 goto :packages_failed

REM If a requirements.txt exists beside this BAT, install it too.
if exist "requirements.txt" (
    echo.
    echo [INSTALL] Installing additional packages from requirements.txt...
    %PYTHON_CMD% %PYTHON_ARGS% -m pip install --upgrade -r requirements.txt
    if errorlevel 1 goto :packages_failed
)

echo.
echo [VERIFY] Checking Python imports...
%PYTHON_CMD% %PYTHON_ARGS% -c "import requests, prompt_toolkit, pypdf, docx, playwright; print('All SharkByte Python packages imported successfully.')"
if errorlevel 1 goto :packages_failed

echo.
echo [INSTALL] Installing Playwright Chromium browser...
%PYTHON_CMD% %PYTHON_ARGS% -m playwright install chromium
if errorlevel 1 goto :chromium_failed

echo.
echo [CHECK] Checking Ollama...
where ollama >nul 2>nul
if not errorlevel 1 goto :ollama_found

REM Check common Windows install locations in case Ollama is installed but PATH is stale.
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
    goto :ollama_found
)
if exist "%ProgramFiles%\Ollama\ollama.exe" (
    set "PATH=%ProgramFiles%\Ollama;%PATH%"
    goto :ollama_found
)

echo Ollama is required by SharkByte but was not found.
where winget >nul 2>nul
if errorlevel 1 (
    echo Install Ollama manually from https://ollama.com/download
    goto :ollama_manual
)
choice /C YN /N /M "Install Ollama using Windows Package Manager? [Y/N] "
if errorlevel 2 goto :ollama_manual

echo.
echo [INSTALL] Installing Ollama...
winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo Ollama installation did not complete.
    goto :ollama_manual
)
call :refresh_path
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%LOCALAPPDATA%\Programs\Ollama;%PATH%"
if exist "%ProgramFiles%\Ollama\ollama.exe" set "PATH=%ProgramFiles%\Ollama;%PATH%"
where ollama >nul 2>nul
if errorlevel 1 goto :ollama_manual

:ollama_found
echo [OK] Ollama executable found.
ollama --version
goto :launch_choice

:ollama_manual
echo.
echo [WARNING] Ollama is not installed or could not be added to PATH.
echo SharkByte needs Ollama running at http://localhost:11434.
echo Install it from https://ollama.com/download, then run this installer again.
echo.
choice /C YN /N /M "Continue anyway and try to launch SharkByte? [Y/N] "
if errorlevel 2 goto :finished

:launch_choice
echo.
echo ==========================================================
echo  INSTALLATION CHECKS COMPLETE
echo ==========================================================
echo.
choice /C YN /N /M "Launch SharkByte now? [Y/N] "
if errorlevel 2 goto :finished

if not exist "app.py" (
    echo.
    echo ERROR: app.py was not found in:
    echo %CD%
    echo Put this installer in the same folder as app.py.
    goto :failed
)
if not exist "sharkbyte_browser.py" (
    echo.
    echo ERROR: sharkbyte_browser.py was not found in:
    echo %CD%
    echo Put sharkbyte_browser.py beside app.py.
    goto :failed
)

echo.
echo [START] Launching SharkByte...
%PYTHON_CMD% %PYTHON_ARGS% -u app.py
set "APP_EXIT=%ERRORLEVEL%"
echo.
echo SharkByte exited with code %APP_EXIT%.
goto :finished

:python_manual
echo.
echo ERROR: A supported Python version could not be installed or found.
echo Install Python 3.13 from https://www.python.org/downloads/
echo During installation, enable the Python Launcher.
goto :failed

:pip_failed
echo.
echo ERROR: pip could not be updated.
goto :failed

:packages_failed
echo.
echo ERROR: One or more Python packages failed to install.
echo Check your internet connection, then run this installer again.
goto :failed

:chromium_failed
echo.
echo ERROR: Playwright Chromium failed to install.
echo Try: %PYTHON_CMD% %PYTHON_ARGS% -m playwright install chromium
goto :failed

:refresh_path
REM Pick up PATH changes made by winget installers in this process.
set "PATH=%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%LOCALAPPDATA%\Programs\Ollama;%ProgramFiles%\Ollama;%PATH%"
exit /b 0

:failed
echo.
echo INSTALLATION DID NOT FINISH SUCCESSFULLY.
echo Fix the error above and run Install-Programs.bat again.
echo.
pause
exit /b 1

:finished
echo.
echo Installer finished. You can run this file again at any time.
echo.
pause
exit /b 0
