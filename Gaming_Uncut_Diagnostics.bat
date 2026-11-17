@echo off
setlocal enabledelayedexpansion
title Gaming Uncut Automation — System & Security Diagnostics
cd /d "%~dp0"

set PYTHON_EXE=
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        set "PYTHON_EXE=python"
    ) else (
        echo [!] Python not found on PATH or in .venv.
        pause
        exit /b 1
    )
)

"%PYTHON_EXE%" backend\scripts\diagnostics.py
echo.
pause
