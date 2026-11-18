@echo off
setlocal enabledelayedexpansion
title Gaming Uncut Automation — NO FILTER. JUST GAMEPLAY.
cd /d "%~dp0"

echo ==================================================================
echo                  GAMING UNCUT AUTOMATION
echo                NO FILTER. JUST GAMEPLAY.
echo ==================================================================
echo.

rem ------------------------------------------------------------------
rem 1. Detect & Activate Python Environment
rem ------------------------------------------------------------------
set "PYTHON_EXE="
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
    set "PYTHON_EXE=python"
    echo [*] Activated local virtual environment [.venv]
) else if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    echo [*] Using local virtual environment [.venv]
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        echo [*] Virtual environment [.venv] not found.
        echo [*] Initializing new Python virtual environment in .venv...
        python -m venv .venv
        if exist ".venv\Scripts\activate.bat" (
            call ".venv\Scripts\activate.bat"
            set "PYTHON_EXE=python"
            echo [*] Installing requirements into .venv...
            python -m pip install --upgrade pip
            python -m pip install -r requirements.txt
        ) else (
            set "PYTHON_EXE=python"
            echo [*] Using system Python
        )
    ) else (
        echo [X] ERROR: Python 3.10+ was not found on your system or in .venv!
        echo     Please install Python 3.10 or later and ensure it is added to PATH.
        echo.
        pause
        exit /b 1
    )
)

rem Verify core python packages
"%PYTHON_EXE%" -c "import fastapi, uvicorn, sqlalchemy" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [*] Core Python packages missing. Installing dependencies from requirements.txt...
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo [!] Warning: pip install encountered issues. Attempting to continue...
    )
)

rem ------------------------------------------------------------------
rem 2. Pre-flight Check & Auto-Discovery: FFmpeg
rem ------------------------------------------------------------------
where ffmpeg >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [*] FFmpeg detected on PATH.
) else (
    echo [*] FFmpeg not found on default PATH. Searching local packages...
    set "FFMPEG_DIR="
    for /f "tokens=*" %%F in ('powershell -NoProfile -Command "(Get-ChildItem -Path $env:LOCALAPPDATA\Microsoft\WinGet\Packages -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1).DirectoryName"') do (
        set "FFMPEG_DIR=%%F"
    )
    if not defined FFMPEG_DIR (
        if exist "C:\ffmpeg\bin\ffmpeg.exe" set "FFMPEG_DIR=C:\ffmpeg\bin"
    )
    if defined FFMPEG_DIR (
        set "PATH=!FFMPEG_DIR!;%PATH%"
        echo [*] Auto-discovered FFmpeg: !FFMPEG_DIR!
    ) else (
        echo [*] WARNING: FFmpeg was not found in PATH or standard directories!
        echo     FFmpeg is required for 1080x1920 video rendering and audio analysis.
    )
)

rem ------------------------------------------------------------------
rem 3. Pre-flight Check: Node.js and npm
rem ------------------------------------------------------------------
where node >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [*] Node.js detected on PATH.
) else (
    echo [*] Note: Node.js not found in PATH [optional if frontend build exists].
)

rem ------------------------------------------------------------------
rem 4. Ensure required data directories exist
rem ------------------------------------------------------------------
if not exist "data\input" mkdir "data\input"
if not exist "data\output" mkdir "data\output"
if not exist "data\processing" mkdir "data\processing"
if not exist "data\thumbnails" mkdir "data\thumbnails"
if not exist "data\temporary" mkdir "data\temporary"
if not exist "logs" mkdir "logs"
if not exist ".vault" mkdir ".vault"

rem ------------------------------------------------------------------
rem 5. Check frontend production build
rem ------------------------------------------------------------------
set "FORCE_BUILD=0"
if "%1"=="--build" set "FORCE_BUILD=1"
if "%1"=="-b" set "FORCE_BUILD=1"
if "%1"=="--rebuild" set "FORCE_BUILD=1"

if "!FORCE_BUILD!"=="1" (
    echo [*] Force rebuild requested. Rebuilding React SPA...
    where npm >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        if not exist "frontend\node_modules" call npm install --prefix frontend
        call npm run build --prefix frontend
    )
) else (
    if not exist "frontend\dist\index.html" (
        echo [*] Frontend production build missing. Building React SPA...
        where npm >nul 2>nul
        if %ERRORLEVEL% equ 0 (
            if not exist "frontend\node_modules" (
                echo [*] Installing frontend npm packages...
                call npm install --prefix frontend
            )
            echo [*] Compiling frontend bundle...
            call npm run build --prefix frontend
        ) else (
            echo [*] Warning: npm not found. Standalone UI will be served as fallback at /ui
        )
    )
)

rem ------------------------------------------------------------------
rem 6. Port 8765 Conflict Resolution & Safe Recycling
rem ------------------------------------------------------------------
netstat -ano | findstr ":8765 " | findstr "LISTENING" >nul 2>nul
if %ERRORLEVEL% equ 0 (
    echo [*] Server port 8765 is currently in use.
    if "%1"=="--keep" (
        echo [*] Keeping existing server. Opening dashboard...
        start http://localhost:8765
        exit /b 0
    )
    if "%1"=="-k" (
        echo [*] Keeping existing server. Opening dashboard...
        start http://localhost:8765
        exit /b 0
    )
    echo [*] Recycling port 8765 to guarantee fresh server launch...
    for /f %%p in ('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"') do (
        set "EXISTING_PID=%%p"
        if defined EXISTING_PID (
            echo [*] Terminating previous server process [PID: !EXISTING_PID!]...
            taskkill /F /T /PID !EXISTING_PID! >nul 2>nul
        )
    )
    rem Wait for socket release
    for /l %%w in (1,1,6) do (
        netstat -ano | findstr ":8765 " | findstr "LISTENING" >nul 2>nul
        if !ERRORLEVEL! equ 0 (
            ping 127.0.0.1 -n 2 >nul 2>&1
        )
    )
)

rem ------------------------------------------------------------------
rem 7. Network / IP Binding & Server Execution
rem ------------------------------------------------------------------
if not defined ENABLE_LAN_ACCESS set "ENABLE_LAN_ACCESS=1"
if not defined HOST set "HOST=0.0.0.0"

echo.
echo ==================================================================
echo   Starting Gaming Uncut Automation server...
echo   - Localhost URL:   http://localhost:8765
echo   - Standalone UI:   http://localhost:8765/ui
echo   - REST API Docs:   http://localhost:8765/docs
echo ==================================================================
echo.

"%PYTHON_EXE%" run.py
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo ==================================================================
if !EXIT_CODE! neq 0 (
    echo [X] Server terminated with error code !EXIT_CODE!.
    echo     Check logs\app.log for detailed tracebacks.
) else (
    echo [*] Gaming Uncut Automation server has stopped cleanly.
)
echo ==================================================================
pause
