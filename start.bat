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
        echo [!] ERROR: Python 3.10+ was not found on your system or in .venv!
        echo     Please install Python (3.10 or later) and ensure it is added to PATH.
        pause
        exit /b 1
    )
)

:: 2. Pre-flight Check: FFmpeg
where ffmpeg >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [*] FFmpeg detected on PATH.
) else (
    echo [!] WARNING: FFmpeg was not found in your system PATH!
    echo     FFmpeg is required for 1080x1920 video rendering, smart cropping,
    echo     and caption burning. Download FFmpeg from https://ffmpeg.org/ and add
    echo     the bin folder to your Windows system PATH.
)

:: 3. Pre-flight Check: Node.js & npm
where node >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [*] Node.js detected on PATH.
) else (
    echo [*] Note: Node.js not found in PATH (optional if frontend build exists).
)

:: 4. Ensure required directories exist
if not exist "data\input" mkdir "data\input"
if not exist "data\output" mkdir "data\output"
if not exist "data\processing" mkdir "data\processing"
if not exist "data\thumbnails" mkdir "data\thumbnails"
if not exist "data\temporary" mkdir "data\temporary"
if not exist "logs" mkdir "logs"
if not exist ".vault" mkdir ".vault"

:: 5. Check frontend production build
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

:: 6. Prevent Duplicate Servers: Check if port 8765 is already listening
netstat -ano | findstr ":8765 " | findstr "LISTENING" >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo.
    echo ==================================================================
    echo [!] Gaming Uncut Automation is ALREADY RUNNING on port 8765!
    echo [*] Opening existing dashboard: http://localhost:8765
    echo [*] To stop the running server, run Gaming_Uncut_Stop.bat
    echo ==================================================================
    start http://localhost:8765
    timeout /t 5 >nul
    exit /b 0
)

:: 7. Network / IP Binding
if not defined ENABLE_LAN_ACCESS set "ENABLE_LAN_ACCESS=1"
if not defined HOST set "HOST=0.0.0.0"

echo [*] Network IP mode ENABLED (Binding to 0.0.0.0:8765)
echo.
echo ==================================================================
echo   Starting Gaming Uncut Automation server...
echo   - Localhost URL:   http://localhost:8765
echo   - Network IP URL:  http://192.168.29.111:8765
echo   - Standalone UI:   http://192.168.29.111:8765/ui
echo   - REST API Docs:   http://192.168.29.111:8765/docs
echo ==================================================================
echo.

"%PYTHON_EXE%" run.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Server exited with error code %ERRORLEVEL%.
    pause
)
