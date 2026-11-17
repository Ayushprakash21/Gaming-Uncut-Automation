@echo off
title Gaming Uncut Automation - NO FILTER. JUST GAMEPLAY.
cd /d "%~dp0"

echo =======================================================
echo           GAMING UNCUT AUTOMATION
echo          NO FILTER. JUST GAMEPLAY.
echo =======================================================

if not exist ".venv\Scripts\python.exe" (
    echo Python virtual environment not found. Setting up...
    python -m venv .venv
    call .venv\Scripts\pip install -r requirements.txt
    call .venv\Scripts\pip install sqlalchemy yt-dlp google-genai opencv-python scenedetect
)

if not exist "frontend\dist\index.html" (
    echo Building React TypeScript frontend...
    call npm install --prefix frontend
    call npm run build --prefix frontend
)

echo Starting Gaming Uncut Automation System...
.venv\Scripts\python.exe run.py
pause
