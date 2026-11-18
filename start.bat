@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
title Gaming Uncut Automation — NO FILTER. JUST GAMEPLAY.
cd /d "%~dp0"

echo ==================================================================
echo                  GAMING UNCUT AUTOMATION
echo                NO FILTER. JUST GAMEPLAY.
echo ==================================================================
echo.

rem ------------------------------------------------------------------
rem 0. Parse Command-Line Arguments
rem ------------------------------------------------------------------
set "FORCE_BUILD=0"
set "FORCE_RESTART=0"
set "KEEP_EXISTING=0"

:parse_args
if "%~1"=="" goto :done_args
if /i "%~1"=="--build" set "FORCE_BUILD=1"
if /i "%~1"=="-b" set "FORCE_BUILD=1"
if /i "%~1"=="--rebuild" set "FORCE_BUILD=1"
if /i "%~1"=="--restart" set "FORCE_RESTART=1"
if /i "%~1"=="-r" set "FORCE_RESTART=1"
if /i "%~1"=="--force" set "FORCE_RESTART=1"
if /i "%~1"=="--keep" set "KEEP_EXISTING=1"
if /i "%~1"=="-k" set "KEEP_EXISTING=1"
if /i "%~1"=="--help" goto :show_help
if /i "%~1"=="-h" goto :show_help
shift
goto :parse_args

:show_help
echo Usage: start.bat [options]
echo.
echo Options:
echo   --restart, -r, --force   Force restart even if server is already running
echo   --build, -b, --rebuild   Force rebuild the React frontend SPA
echo   --keep, -k               Keep existing running server without restart
echo   --help, -h               Show this help message
echo.
exit /b 0

:done_args

rem ------------------------------------------------------------------
rem 1. Detect & Activate Python Environment
rem ------------------------------------------------------------------
set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
    if exist "%~dp0.venv\Scripts\activate.bat" (
        call "%~dp0.venv\Scripts\activate.bat"
    )
    echo [*] Using local virtual environment [.venv]
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        echo [*] Virtual environment [.venv] not found.
        echo [*] Initializing new Python virtual environment in .venv...
        python -m venv .venv
        if exist ".venv\Scripts\python.exe" (
            set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
            call ".venv\Scripts\activate.bat"
            echo [*] Installing requirements into .venv...
            "%PYTHON_EXE%" -m pip install --upgrade pip
            "%PYTHON_EXE%" -m pip install -r requirements.txt
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
"%PYTHON_EXE%" -c "import fastapi, uvicorn, sqlalchemy, psutil" >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [*] Core Python packages missing. Installing dependencies from requirements.txt...
    "%PYTHON_EXE%" -m pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo [!] Warning: pip install encountered issues. Attempting to continue...
    )
)

rem ------------------------------------------------------------------
rem 2. Pre-flight Check & Auto-Discovery: FFmpeg and FFprobe
rem ------------------------------------------------------------------
where.exe ffmpeg >nul 2>nul
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
where.exe node >nul 2>nul
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
if "!FORCE_BUILD!"=="1" (
    echo [*] Force rebuild requested. Rebuilding React SPA...
    where.exe npm >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        if not exist "frontend\node_modules" call npm install --prefix frontend
        call npm run build --prefix frontend
    )
) else (
    if not exist "frontend\dist\index.html" (
        echo [*] Frontend production build missing. Building React SPA...
        where.exe npm >nul 2>nul
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
rem 6. Port 8765 Conflict Resolution & Smart Server Lifecycle
rem ------------------------------------------------------------------
netstat -ano | findstr ":8765 " | findstr "LISTENING" >nul 2>nul
if %ERRORLEVEL% equ 0 (
    if "!FORCE_RESTART!"=="1" (
        echo [*] Force restart requested. Recycling port 8765...
        goto :kill_existing
    )
    if "!KEEP_EXISTING!"=="1" (
        echo [*] Keeping existing server. Opening dashboard...
        start http://localhost:8765
        exit /b 0
    )

    rem Test whether existing server is healthy and responsive
    curl.exe -f -s -m 2 http://127.0.0.1:8765/api/health >nul 2>nul
    if not errorlevel 1 (
        echo.
        echo ==================================================================
        echo [*] Gaming Uncut Automation is ALREADY RUNNING and healthy.
        echo [*] Dashboard URL:  http://localhost:8765
        echo [*] Standalone UI:  http://localhost:8765/ui
        echo [*] REST API Docs:  http://localhost:8765/docs
        echo [*] [To force restart, run: start.bat --restart]
        echo ==================================================================
        start http://localhost:8765
        exit /b 0
    )

    echo [*] Port 8765 is in use but unresponsive. Recycling port...

:kill_existing
    for /f %%p in ('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"') do (
        set "EXISTING_PID=%%p"
        if defined EXISTING_PID (
            echo [*] Terminating previous server process [PID: !EXISTING_PID!]...
            taskkill /F /T /PID !EXISTING_PID! >nul 2>nul
        )
    )
    rem Wait for socket release
    for /l %%w in (1,1,10) do (
        netstat -ano | findstr ":8765 " | findstr "LISTENING" >nul 2>nul
        if errorlevel 1 goto :port_freed
        ping 127.0.0.1 -n 2 >nul 2>&1
    )
:port_freed
    echo [*] Port 8765 is free.
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
