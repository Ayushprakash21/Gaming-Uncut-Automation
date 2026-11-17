"""
Database module for Gaming Uncut Automation.
Uses SQLite with Write-Ahead Logging (WAL) for concurrency and crash-resistance.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from src.core.config import settings

logger = logging.getLogger("gaming_uncut.db")

DB_PATH = settings.DATA_DIR / "gaming_uncut.db"

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Initializes schema if not already present."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Raw Footage Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS raw_footage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                filesize INTEGER NOT NULL,
                duration REAL DEFAULT 0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                fps REAL DEFAULT 0,
                status TEXT DEFAULT 'pending', -- pending, analyzing, processed, failed
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Detected Moments Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS detected_moments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                footage_id INTEGER NOT NULL,
                start_sec REAL NOT NULL,
                end_sec REAL NOT NULL,
                duration_sec REAL NOT NULL,
                score REAL NOT NULL, -- 0 to 100
                detection_type TEXT NOT NULL, -- audio_peak, speech_hype, multimodal, manual
                summary TEXT,
                transcript TEXT,
                suggested_title TEXT,
                suggested_hashtags TEXT,
                status TEXT DEFAULT 'detected', -- detected, rendering, rendered, rejected
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (footage_id) REFERENCES raw_footage(id) ON DELETE CASCADE
            )
        """)
        
        # Rendered Clips Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rendered_clips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                moment_id INTEGER NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                thumbnail_path TEXT,
                duration_sec REAL NOT NULL,
                width INTEGER DEFAULT 1080,
                height INTEGER DEFAULT 1920,
                layout_template TEXT NOT NULL, -- split_screen, blurred_bg, center_crop
                title TEXT NOT NULL,
                description TEXT,
                hashtags TEXT,
                approval_status TEXT DEFAULT 'pending', -- pending, approved, rejected
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (moment_id) REFERENCES detected_moments(id) ON DELETE CASCADE
            )
        """)
        
        # Publishing Queue Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS publishing_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clip_id INTEGER NOT NULL,
                platform TEXT NOT NULL, -- youtube_shorts, instagram_reels, facebook_reels
                scheduled_time TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'queued', -- queued, publishing, published, failed, paused
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 5,
                last_error TEXT,
                external_post_id TEXT,
                external_url TEXT,
                published_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (clip_id) REFERENCES rendered_clips(id) ON DELETE CASCADE
            )
        """)
        
        # System & Audit Logs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Channel & Post Analytics Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS post_analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                external_post_id TEXT NOT NULL,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (queue_id) REFERENCES publishing_queue(id) ON DELETE CASCADE
            )
        """)

        conn.commit()
        logger.info("Database initialized successfully with WAL mode.")

def log_event(module: str, level: str, message: str, meta: Optional[dict] = None):
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO system_logs (module, level, message, metadata_json) VALUES (?, ?, ?, ?)",
                (module, level.upper(), message, json.dumps(meta) if meta else None)
            )
            conn.commit()
    except Exception as e:
        print(f"Log Error: {e}")
