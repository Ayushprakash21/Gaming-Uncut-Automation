"""
Automated Footage Ingestion & File Watcher for Gaming Uncut.
Monitors the OBS/Shadowplay directory, waits for file write completion (debounce),
probes video metadata (resolution, duration, fps), and registers to database.
"""

import os
import time
import json
import subprocess
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from src.core.config import settings
from src.core.database import get_connection, log_event

logger = logging.getLogger("gaming_uncut.ingestion")

def probe_video(filepath: Path) -> dict:
    """Probes video metadata using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(filepath)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        
        format_info = data.get("format", {})
        duration = float(format_info.get("duration", 0))
        size = int(format_info.get("size", filepath.stat().st_size))
        
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        width = 0
        height = 0
        fps = 0.0
        
        if video_stream:
            width = int(video_stream.get("width", 0))
            height = int(video_stream.get("height", 0))
            r_frame_rate = video_stream.get("r_frame_rate", "0/1")
            if "/" in r_frame_rate:
                num, den = r_frame_rate.split("/")
                fps = round(float(num) / float(den), 2) if float(den) > 0 else 0.0
                
        return {
            "duration": duration,
            "size": size,
            "width": width,
            "height": height,
            "fps": fps
        }
    except Exception as e:
        logger.error(f"Failed to probe {filepath}: {e}")
        return {"duration": 0, "size": filepath.stat().st_size, "width": 0, "height": 0, "fps": 0}

def wait_for_file_settled(filepath: Path, timeout_sec: int = 120, check_interval: float = 2.0) -> bool:
    """Ensures file is fully written and no longer locked by OBS/recording software."""
    last_size = -1
    elapsed = 0.0
    while elapsed < timeout_sec:
        try:
            curr_size = filepath.stat().st_size
            if curr_size > 0 and curr_size == last_size:
                # File size is stable, verify read access
                with open(filepath, "rb") as f:
                    f.read(1024)
                return True
            last_size = curr_size
        except (PermissionError, FileNotFoundError):
            pass
        time.sleep(check_interval)
        elapsed += check_interval
    return False

def register_footage(filepath: Path) -> int:
    """Registers new video in the database and triggers analysis."""
    meta = probe_video(filepath)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO raw_footage (filename, filepath, filesize, duration, width, height, fps, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(filepath) DO UPDATE SET
                filesize=excluded.filesize,
                duration=excluded.duration
            RETURNING id
            """,
            (filepath.name, str(filepath), meta["size"], meta["duration"], meta["width"], meta["height"], meta["fps"])
        )
        row = cursor.fetchone()
        conn.commit()
        footage_id = row["id"]
        
    log_event("ingestion", "INFO", f"Registered new raw footage: {filepath.name} ({meta['duration']:.1f}s)", meta)
    return footage_id

class VideoFolderHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() in settings.SUPPORTED_EXTENSIONS:
            logger.info(f"Detected new video write: {path.name}. Waiting for file to finish writing...")
            if wait_for_file_settled(path):
                logger.info(f"File {path.name} is complete and ready.")
                register_footage(path)

class IngestionWatcher:
    def __init__(self, watch_dir: Path = settings.WATCH_FOLDER):
        self.watch_dir = watch_dir
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.observer = Observer()

    def start(self):
        handler = VideoFolderHandler()
        self.observer.schedule(handler, str(self.watch_dir), recursive=False)
        self.observer.start()
        logger.info(f"Ingestion watcher started on: {self.watch_dir}")

    def stop(self):
        self.observer.stop()
        self.observer.join()
