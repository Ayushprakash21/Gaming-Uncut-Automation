"""
Content Fingerprinting and Analysis Result Caching for Gaming Uncut.
Prevents duplicate Gemini API calls for identical video footage or repeat requests.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from backend.app.core.config import settings

logger = logging.getLogger("gaming_uncut.analysis_cache")

CACHE_FILE = settings.TEMP_DIR / "ai_analysis_cache.json"
_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}


def compute_video_fingerprint(video_path: Path) -> str:
    """
    Computes a deterministic SHA256 fingerprint for a video file
    combining file size, head bytes, and tail bytes.
    """
    if not video_path.exists():
        return hashlib.sha256(str(video_path).encode("utf-8")).hexdigest()

    h = hashlib.sha256()
    file_size = video_path.stat().st_size
    h.update(str(file_size).encode("utf-8"))

    try:
        with open(video_path, "rb") as f:
            # Read first 64KB
            head = f.read(65536)
            h.update(head)
            # Read last 64KB if file is large enough
            if file_size > 131072:
                f.seek(-65536, 2)
                tail = f.read(65536)
                h.update(tail)
    except Exception as e:
        logger.warning(f"Error reading video bytes for fingerprint: {e}")
        h.update(video_path.name.encode("utf-8"))

    return h.hexdigest()


def get_cached_analysis(fingerprint: str) -> Optional[Dict[str, Any]]:
    """Retrieves cached analysis result if available."""
    if fingerprint in _MEMORY_CACHE:
        return _MEMORY_CACHE[fingerprint]

    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if fingerprint in data:
                _MEMORY_CACHE[fingerprint] = data[fingerprint]
                return data[fingerprint]
        except Exception as e:
            logger.warning(f"Failed to read analysis cache file: {e}")

    return None


def store_cached_analysis(fingerprint: str, analysis: Dict[str, Any]):
    """Stores analysis result in memory and on disk."""
    _MEMORY_CACHE[fingerprint] = analysis

    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        disk_data = {}
        if CACHE_FILE.exists():
            try:
                disk_data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except Exception:
                disk_data = {}

        disk_data[fingerprint] = analysis
        # Keep cache capped at latest 100 entries
        if len(disk_data) > 100:
            keys = list(disk_data.keys())
            disk_data = {k: disk_data[k] for k in keys[-100:]}

        CACHE_FILE.write_text(json.dumps(disk_data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to write analysis cache to disk: {e}")
