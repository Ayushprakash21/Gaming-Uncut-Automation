import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class AppSettings(BaseSettings):
    # App Identity
    APP_NAME: str = "Gaming Uncut Automation"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    
    # Storage Paths
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    RENDERS_DIR: Path = BASE_DIR / "renders"
    TEMP_DIR: Path = BASE_DIR / "temp"
    LOGS_DIR: Path = BASE_DIR / "logs"
    ASSETS_DIR: Path = BASE_DIR / "assets"
    
    # Ingestion / OBS Watcher
    WATCH_FOLDER: Path = BASE_DIR / "data" / "watch_input"
    SUPPORTED_EXTENSIONS: list[str] = [".mp4", ".mkv", ".mov", ".avi", ".ts"]
    
    # Clip Settings
    TARGET_WIDTH: int = 1080
    TARGET_HEIGHT: int = 1920
    TARGET_FPS: int = 60
    TARGET_CLIP_DURATION: int = 58  # Seconds (under 60s for YouTube Shorts / Reels)
    MIN_CLIP_DURATION: int = 25
    MAX_CLIP_DURATION: int = 60
    USE_NVENC: bool = True
    
    # Workflow & Approval Mode
    # Options: "manual" (require human approval) | "auto" (auto-publish if quality score passes)
    WORKFLOW_MODE: str = "manual"
    MIN_QUALITY_SCORE: float = 75.0  # Out of 100
    DAILY_TARGET_CLIPS: int = 12
    
    # Branding
    WATERMARK_ENABLED: bool = True
    WATERMARK_TEXT: str = "GAMING UNCUT"
    
    # Server / UI
    HOST: str = "127.0.0.1"
    PORT: int = 8765
    
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = AppSettings()

# Ensure critical runtime directories exist
for p in [settings.DATA_DIR, settings.RENDERS_DIR, settings.TEMP_DIR, settings.LOGS_DIR, settings.ASSETS_DIR, settings.WATCH_FOLDER]:
    p.mkdir(parents=True, exist_ok=True)
