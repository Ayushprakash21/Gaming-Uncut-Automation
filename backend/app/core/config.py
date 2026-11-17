import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent

class Settings(BaseSettings):
    # App Information
    APP_NAME: str = "Gaming Uncut Automation"
    BRAND_NAME: str = "Gaming Uncut"
    TAGLINE: str = "NO FILTER. JUST GAMEPLAY."
    VERSION: str = "2.0.0"
    DEBUG: bool = False
    
    # Base Directories
    ROOT_DIR: Path = ROOT_DIR
    BACKEND_DIR: Path = ROOT_DIR / "backend"
    DATA_DIR: Path = ROOT_DIR / "data"
    INPUT_DIR: Path = ROOT_DIR / "data" / "input"
    PROCESSING_DIR: Path = ROOT_DIR / "data" / "processing"
    OUTPUT_DIR: Path = ROOT_DIR / "data" / "output"
    PUBLISHED_DIR: Path = ROOT_DIR / "data" / "published"
    FAILED_DIR: Path = ROOT_DIR / "data" / "failed"
    THUMBNAILS_DIR: Path = ROOT_DIR / "data" / "thumbnails"
    TEMP_DIR: Path = ROOT_DIR / "data" / "temporary"
    LOGS_DIR: Path = ROOT_DIR / "logs"
    ASSETS_DIR: Path = ROOT_DIR / "assets"
    VAULT_DIR: Path = ROOT_DIR / ".vault"
    
    # Server / Binding (Localhost only)
    HOST: str = "127.0.0.1"
    PORT: int = 8765
    
    # Video Specifications
    TARGET_WIDTH: int = 1080
    TARGET_HEIGHT: int = 1920
    TARGET_FPS: int = 60
    TARGET_DURATION: int = 60  # Exactly 60-second Reels / Shorts
    MIN_DURATION: int = 25
    MAX_DURATION: int = 60
    USE_NVENC: bool = True
    
    # Quality & Automation Defaults
    DEFAULT_PUBLISH_MODE: str = "auto"  # "auto" (quality-gated auto-publish) | "approval" (manual approval)
    MIN_QUALITY_SCORE: float = 75.0
    DAILY_TARGET_CLIPS: int = 12
    MAX_CONCURRENT_RENDERS: int = 1
    
    # AI Engine Provider
    AI_PROVIDER: str = "gemini"  # "gemini" or local fallback
    GEMINI_MODEL: str = "gemini-2.5-flash"
    
    # Platform APIs
    YOUTUBE_MAX_DAILY_UPLOADS: int = 6  # standard free quota threshold
    INSTAGRAM_DAILY_LIMIT: int = 50
    FACEBOOK_DAILY_LIMIT: int = 50
    
    # Database
    DATABASE_URL: str = f"sqlite:///{ROOT_DIR / 'data' / 'gaming_uncut_v2.db'}"

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# Ensure runtime directories exist
for folder in [
    settings.DATA_DIR,
    settings.INPUT_DIR,
    settings.PROCESSING_DIR,
    settings.OUTPUT_DIR,
    settings.PUBLISHED_DIR,
    settings.FAILED_DIR,
    settings.THUMBNAILS_DIR,
    settings.TEMP_DIR,
    settings.LOGS_DIR,
    settings.VAULT_DIR,
    settings.ASSETS_DIR / "branding",
    settings.ASSETS_DIR / "fonts",
    settings.ASSETS_DIR / "templates"
]:
    folder.mkdir(parents=True, exist_ok=True)
