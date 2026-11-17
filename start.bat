@echo off
title Gaming Uncut Automation
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python virtual environment not found. Creating...
    python -m venv .venv
    call .venv\Scripts\pip install -r requirements.txt
)
echo Starting Gaming Uncut Automation Hub...
.venv\Scripts\python.exe run.py
pause
