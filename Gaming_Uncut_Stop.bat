@echo off
setlocal enabledelayedexpansion
title Gaming Uncut Automation — Stop Server
cd /d "%~dp0"

echo ==================================================================
echo                  GAMING UNCUT AUTOMATION
echo                     Stop Server Script
echo ==================================================================
echo.

set FOUND=0
for /f %%p in ('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique"') do (
    set TARGET_PID=%%p
    if defined TARGET_PID (
        set FOUND=1
        echo [*] Found active server listening on port 8765 with PID: !TARGET_PID!
        for /f "tokens=*" %%n in ('powershell -NoProfile -Command "(Get-Process -Id !TARGET_PID! -ErrorAction SilentlyContinue).ProcessName"') do (
            echo [*] Process Name: %%n
        )
        echo [*] Terminating process !TARGET_PID!...
        taskkill /F /PID !TARGET_PID! >nul 2>nul
        if %ERRORLEVEL% equ 0 (
            echo [v] Gaming Uncut Automation server stopped successfully.
        ) else (
            echo [!] Failed to terminate process !TARGET_PID!. You may need administrator rights.
        )
    )
)

if "%FOUND%"=="0" (
    echo [*] No running Gaming Uncut Automation server was detected on port 8765.
)

echo.
ping 127.0.0.1 -n 3 >nul 2>&1
