@echo off
setlocal enabledelayedexpansion
title Gaming Uncut Automation — NO FILTER. JUST GAMEPLAY.
cd /d "%~dp0"

echo ==================================================================
echo                  GAMING UNCUT AUTOMATION
echo                NO FILTER. JUST GAMEPLAY.
echo ==================================================================
echo.

:: 1. Detect Python Environment
set PYTHON_EXE=
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    echo [*] Using local virtual environment (.venv)
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PYTHON_EXE=python"
        echo [*] Using system Python
    ) else (
        echo [!] ERROR: Python 3.12+ was not found on your system or in .venv!
        echo     Please install Python 3.12 and ensure it is added to PATH.
        pause
        exit /b 1
    )
)

:: 2. Ensure directories exist
if not exist "data\input" mkdir "data\input"
if not exist "data\output" mkdir "data\output"
if not exist "data\processing" mkdir "data\processing"
if not exist "data\thumbnails" mkdir "data\thumbnails"
if not exist "data\temporary" mkdir "data\temporary"
if not exist "logs" mkdir "logs"
if not exist ".vault" mkdir ".vault"

:: 3. Check frontend production build
if not exist "frontend\dist\index.html" (
    echo [*] Frontend production build missing. Building React SPA...
    where npm >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        call npm install --prefix frontend
        call npm run build --prefix frontend
    ) else (
        echo [!] Warning: npm not found. Standalone UI will be served as fallback at /ui
    )
)

:: 4. Detect Local IP Address for display
for /f "tokens=4" %%a in ('route print ^| findstr "\<0.0.0.0\>" 2^>nul') do (
    if not defined LOCAL_IP set "LOCAL_IP=%%a"
)
if not defined LOCAL_IP set "LOCAL_IP=127.0.0.1"

echo.
echo ==================================================================
echo   System is launching on all network interfaces:
echo   - Localhost URL:   http://localhost:8765
echo   - Network IP URL:  http://!LOCAL_IP!:8765
echo   - Standalone UI:   http://!LOCAL_IP!:8765/ui
echo   - REST API Docs:   http://!LOCAL_IP!:8765/docs
echo ==================================================================
echo.
echo Launching server and background scheduler...

"%PYTHON_EXE%" run.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Server exited with error code %ERRORLEVEL%.
    pause
)
