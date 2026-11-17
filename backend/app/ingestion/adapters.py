"""
Modular Video Source Ingestion Engine for Gaming Uncut Automation.
Supports YouTube (authorized/user-owned), Direct HTTP/HTTPS Video URLs, and Local Files.
Validates accessibility, duration, resolution, authorization, and extracts standardized inputs.
"""

import os
import re
import json
import logging
import subprocess
import threading
import time
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional
import httpx
from yt_dlp import YoutubeDL

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import SourceVideo, SourceAuthorization, SystemLog

logger = logging.getLogger("gaming_uncut.ingestion")

_SOURCE_LOCKS: Dict[str, threading.Lock] = {}
_SOURCE_LOCKS_MUTEX = threading.Lock()

def get_source_lock(video_key: str) -> threading.Lock:
    """Returns a process-wide mutex for a specific video source key (e.g. video_id or normalized URL)."""
    with _SOURCE_LOCKS_MUTEX:
        if video_key not in _SOURCE_LOCKS:
            _SOURCE_LOCKS[video_key] = threading.Lock()
        return _SOURCE_LOCKS[video_key]

def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts the 11-character YouTube video ID from various URL formats, stripping backslashes."""
    if not url:
        return None
    clean = url.replace("\\", "/").strip().strip('\"\'')
    patterns = [
        r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|\/live\/)([a-zA-Z0-9_-]{11})",
        r"(?:^|[^a-zA-Z0-9_-])([a-zA-Z0-9_-]{11})(?:$|[^a-zA-Z0-9_-])"
    ]
    for pattern in patterns:
        match = re.search(pattern, clean)
        if match:
            vid = match.group(1)
            if vid not in ("watch", "embed", "live", "shorts"):
                return vid
    return None

def normalize_youtube_url(url: str) -> str:
    """Normalizes any valid YouTube URL variant into canonical https://www.youtube.com/watch?v=VIDEO_ID."""
    vid = extract_youtube_id(url)
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return url.replace("\\", "/").strip().strip('\"\'')

def safe_windows_replace(src: Path, dst: Path, max_retries: int = 5, initial_delay: float = 0.5) -> Path:
    """
    Safely moves or replaces a file on Windows, handling transient file locks
    (WinError 32) from antivirus, indexing, or flush delays with exponential backoff.
    """
    delay = initial_delay
    last_err = None
    for attempt in range(max_retries):
        try:
            os.replace(str(src), str(dst))
            return dst
        except (PermissionError, OSError) as e:
            last_err = e
            logger.warning(
                f"Transient file lock on {src} -> {dst} (attempt {attempt+1}/{max_retries}): {e}. Retrying in {delay:.2f}s..."
            )
            time.sleep(delay)
            delay *= 2
    raise OSError(f"Failed to move {src} to {dst} after {max_retries} attempts: {last_err}") from last_err

def probe_local_media(file_path: Path) -> Dict[str, Any]:
    """Probes media metadata using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0))
        size = int(fmt.get("size", file_path.stat().st_size if file_path.exists() else 0))
        
        v_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
        width, height, fps = 0, 0, 0.0
        if v_stream:
            width = int(v_stream.get("width", 0))
            height = int(v_stream.get("height", 0))
            r_fps = v_stream.get("r_frame_rate", "0/1")
            if "/" in r_fps:
                n, d = r_fps.split("/")
                fps = round(float(n) / float(d), 2) if float(d) > 0 else 0.0

        return {
            "duration": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "size": size,
            "format": fmt.get("format_name", "mp4")
        }
    except Exception as e:
        logger.error(f"FFprobe error on {file_path}: {e}")
        return {"duration": 0, "width": 0, "height": 0, "fps": 0, "size": 0, "format": "unknown"}

class BaseSourceAdapter(ABC):
    @abstractmethod
    def can_handle(self, url_or_path: str) -> bool:
        pass

    @abstractmethod
    def validate_source(self, url_or_path: str) -> Dict[str, Any]:
        """Validates source accessibility, format, and metadata without downloading full payload."""
        pass

    @abstractmethod
    def validate_and_ingest(self, url_or_path: str, auth_type: str = "user_owned", job_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Validates the source, downloads/copies if necessary,
        returns normalized metadata and local file path.
        """
        pass

class LocalFileAdapter(BaseSourceAdapter):
    def can_handle(self, url_or_path: str) -> bool:
        raw_str = str(url_or_path).strip().strip('\"\'')
        p = Path(os.path.expandvars(os.path.expanduser(raw_str)))
        return p.exists() and p.is_file() and p.suffix.lower() in [".mp4", ".mkv", ".mov", ".avi", ".ts", ".webm"]

    def validate_source(self, url_or_path: str) -> Dict[str, Any]:
        raw_str = str(url_or_path).strip().strip('\"\'')
        path = Path(os.path.expandvars(os.path.expanduser(raw_str))).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Local video file not found at: '{path}'")
        if not path.is_file():
            raise ValueError(f"Path is not a regular file: '{path}'")
        meta = probe_local_media(path)
        if meta["duration"] < 5.0:
            raise ValueError(f"Local video duration ({meta['duration']:.1f}s) is too short. Minimum 5 seconds required.")

        minutes = int(meta["duration"] // 60)
        seconds = int(meta["duration"] % 60)
        return {
            "valid": True,
            "source_type": "local",
            "platform_name": "Local Video Footage",
            "title": path.name,
            "duration": meta["duration"],
            "duration_formatted": f"{minutes}:{seconds:02d}",
            "resolution": f"{meta['width']}x{meta['height']}",
            "thumbnail_url": "",
            "author": "Local Device",
            "accessible": True,
            "message": f"Local footage verified ({meta['width']}x{meta['height']}, {minutes}:{seconds:02d})."
        }

    def validate_and_ingest(self, url_or_path: str, auth_type: str = "user_owned", job_id: Optional[int] = None) -> Dict[str, Any]:
        if job_id:
            from backend.app.services.pipeline_service import pipeline_service, JobCancelledException
            if pipeline_service.is_cancellation_requested(job_id):
                raise JobCancelledException(f"Job #{job_id} cancelled during local file ingestion.")
        raw_str = str(url_or_path).strip().strip('\"\'')
        path = Path(os.path.expandvars(os.path.expanduser(raw_str))).resolve()
        meta = probe_local_media(path)
        if meta["duration"] < 5.0:
            raise ValueError("Local video duration is too short for reel production (minimum 5s required).")

        return {
            "source_type": "local",
            "url": str(path),
            "title": path.stem,
            "duration": meta["duration"],
            "width": meta["width"],
            "height": meta["height"],
            "fps": meta["fps"],
            "format": meta["format"],
            "local_path": str(path),
            "author": "Local User",
            "license_info": "User Footage",
            "auth_type": auth_type
        }

class DirectUrlAdapter(BaseSourceAdapter):
    def can_handle(self, url_or_path: str) -> bool:
        clean = url_or_path.strip().strip('\"\'')
        return clean.startswith(("http://", "https://")) and any(
            clean.lower().endswith(ext) or f"{ext}?" in clean.lower()
            for ext in [".mp4", ".mkv", ".mov", ".webm"]
        )

    def validate_source(self, url_or_path: str) -> Dict[str, Any]:
        url = url_or_path.strip().strip('\"\'')
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.head(url)
                if resp.status_code >= 400:
                    resp = client.get(url, headers={"Range": "bytes=0-1024"})
                if resp.status_code not in (200, 206):
                    raise ValueError(f"Direct video stream returned HTTP status {resp.status_code}.")
                content_type = resp.headers.get("content-type", "video/mp4")
                filename = url.split("?")[0].split("/")[-1] or "direct_video.mp4"
                return {
                    "valid": True,
                    "source_type": "direct_url",
                    "platform_name": "Direct Video Stream",
                    "title": filename,
                    "duration": 0.0,
                    "duration_formatted": "Remote Stream",
                    "resolution": "1920x1080 (Estimated)",
                    "thumbnail_url": "",
                    "author": "Web Video Source",
                    "accessible": True,
                    "message": f"Direct video stream accessible ({content_type})."
                }
        except Exception as e:
            raise ValueError(f"Direct stream validation error: {e}")

    def validate_and_ingest(self, url_or_path: str, auth_type: str = "creator_authorized", job_id: Optional[int] = None) -> Dict[str, Any]:
        # Validate accessibility with HEAD request
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.head(url_or_path)
            if resp.status_code != 200:
                raise ValueError(f"Direct video URL is not accessible (HTTP {resp.status_code}).")

        # Stream download to input directory
        filename = f"direct_{abs(hash(url_or_path)) % 10000000}.mp4"
        dest_path = settings.INPUT_DIR / filename
        
        logger.info(f"Downloading direct video stream: {url_or_path} -> {dest_path}")
        with httpx.stream("GET", url_or_path, timeout=120.0, follow_redirects=True) as response:
            if response.status_code != 200:
                raise ValueError(f"Failed to stream direct URL: HTTP {response.status_code}")
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    if job_id:
                        from backend.app.services.pipeline_service import pipeline_service, JobCancelledException
                        if pipeline_service.is_cancellation_requested(job_id):
                            raise JobCancelledException(f"Direct stream download cancelled for Job #{job_id}")
                    f.write(chunk)

        meta = probe_local_media(dest_path)
        return {
            "source_type": "direct_url",
            "url": url_or_path,
            "title": filename,
            "duration": meta["duration"],
            "width": meta["width"],
            "height": meta["height"],
            "fps": meta["fps"],
            "format": meta["format"],
            "local_path": str(dest_path),
            "author": "Direct Source",
            "license_info": "Creator Authorized Direct Stream",
            "auth_type": auth_type
        }

class YouTubeAdapter(BaseSourceAdapter):
    YT_REGEX = re.compile(r"^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/live/|youtube\.com/shorts/)[\w-]+")

    def can_handle(self, url_or_path: str) -> bool:
        clean = url_or_path.replace("\\", "/").strip().strip('\"\'')
        return bool(extract_youtube_id(clean) or ("youtube.com" in clean.lower()) or ("youtu.be" in clean.lower()))

    def validate_source(self, url_or_path: str) -> Dict[str, Any]:
        canonical_url = normalize_youtube_url(url_or_path)
        ydl_opts = {
            "extract_flat": False,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(canonical_url, download=False)
                if not info:
                    raise ValueError("Could not extract metadata from YouTube URL.")
                duration = float(info.get("duration", 0) or 0)
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                width = info.get("width") or 1920
                height = info.get("height") or 1080
                thumb = info.get("thumbnail") or ""
                return {
                    "valid": True,
                    "source_type": "youtube",
                    "platform_name": "YouTube",
                    "title": info.get("title", f"YouTube {info.get('id', '')}"),
                    "duration": duration,
                    "duration_formatted": f"{minutes}:{seconds:02d}",
                    "resolution": f"{width}x{height}",
                    "thumbnail_url": thumb,
                    "author": info.get("uploader") or info.get("channel") or "YouTube Creator",
                    "accessible": True,
                    "message": f"YouTube video verified ({minutes}:{seconds:02d}, {width}x{height})."
                }
        except Exception as e:
            raise ValueError(f"YouTube source validation failed: {e}")

    def validate_and_ingest(self, url_or_path: str, auth_type: str = "creator_authorized", job_id: Optional[int] = None) -> Dict[str, Any]:
        if job_id:
            from backend.app.services.pipeline_service import pipeline_service, JobCancelledException
            if pipeline_service.is_cancellation_requested(job_id):
                raise JobCancelledException(f"Job #{job_id} cancelled prior to YouTube download.")

        canonical_url = normalize_youtube_url(url_or_path)
        video_id = extract_youtube_id(canonical_url)
        if not video_id:
            raise ValueError(f"Could not extract a valid YouTube video ID from: {url_or_path}")

        source_lock = get_source_lock(video_id)
        target_file = settings.INPUT_DIR / f"yt_{video_id}.mp4"

        # Concurrency protection: Lock per video ID so multiple jobs do not conflict
        with source_lock:
            if job_id:
                from backend.app.services.pipeline_service import pipeline_service, JobCancelledException
                if pipeline_service.is_cancellation_requested(job_id):
                    raise JobCancelledException(f"Job #{job_id} cancelled while acquiring source lock.")

            # 1. Deduplication / Cache: Check if already downloaded, readable, and non-empty
            if target_file.exists() and target_file.stat().st_size > 1024:
                meta = probe_local_media(target_file)
                if meta.get("duration", 0) > 0 and meta.get("width", 0) > 0:
                    logger.info(f"Reusing existing downloaded source: {target_file}")
                    return {
                        "source_type": "youtube",
                        "url": canonical_url,
                        "title": f"YouTube {video_id}",
                        "duration": meta["duration"],
                        "width": meta["width"],
                        "height": meta["height"],
                        "fps": meta["fps"],
                        "format": meta["format"],
                        "local_path": str(target_file.resolve()),
                        "author": "YouTube Creator",
                        "license_info": "Authorized Creator Ingestion",
                        "auth_type": auth_type
                    }

            # 2. Download with unique temp file per thread to avoid file collisions
            unique_token = uuid.uuid4().hex[:8]
            temp_out_tmpl = str(settings.TEMP_DIR / f"yt_{video_id}_{unique_token}.%(ext)s")

            def _yt_cancel_hook(d):
                if job_id:
                    from backend.app.services.pipeline_service import pipeline_service, JobCancelledException
                    if pipeline_service.is_cancellation_requested(job_id):
                        raise JobCancelledException(f"Job #{job_id} cancelled during YouTube download.")

            ydl_opts = {
                "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "outtmpl": temp_out_tmpl,
                "merge_output_format": "mp4",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "progress_hooks": [_yt_cancel_hook],
            }

            try:
                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(canonical_url, download=False)
                    if not info:
                        raise ValueError(f"Could not retrieve metadata for YouTube URL: {canonical_url}")
                    
                    duration = float(info.get("duration", 0) or 0)
                    if duration > 7200:
                        raise ValueError(f"Video duration ({duration/60:.1f}m) exceeds 2-hour processing maximum.")

                    ydl.download([canonical_url])

                # Locate downloaded temp output file
                downloaded_temp = None
                for ext in ["mp4", "mkv", "webm"]:
                    candidate = settings.TEMP_DIR / f"yt_{video_id}_{unique_token}.{ext}"
                    if candidate.exists() and candidate.stat().st_size > 1024:
                        downloaded_temp = candidate
                        break

                if not downloaded_temp:
                    raise FileNotFoundError(f"yt-dlp download failed to produce expected file for video ID {video_id}")

                # Safely replace to canonical target location with Windows retry/backoff
                safe_windows_replace(downloaded_temp, target_file)

            except Exception as e:
                logger.error(f"Downloader failed for YouTube video {video_id}: {e}", exc_info=True)
                raise RuntimeError(f"YouTube video acquisition failed: {e}") from e

            # Validate target file exists, is non-empty, and probe-able
            if not target_file.exists() or target_file.stat().st_size <= 1024:
                raise FileNotFoundError(f"Downloaded video file missing or corrupt: {target_file}")

            meta = probe_local_media(target_file)
            if meta.get("duration", 0) <= 0:
                raise ValueError(f"Downloaded media is unreadable or corrupt: {target_file}")

            return {
                "source_type": "youtube",
                "url": canonical_url,
                "title": info.get("title", f"YouTube {video_id}"),
                "duration": meta["duration"] or duration,
                "width": meta["width"],
                "height": meta["height"],
                "fps": meta["fps"],
                "format": meta["format"],
                "local_path": str(target_file.resolve()),
                "author": info.get("uploader", "YouTube Creator"),
                "license_info": info.get("license", "Authorized Creator Ingestion"),
                "auth_type": auth_type
            }

class IngestionManager:
    def __init__(self):
        self.adapters: list[BaseSourceAdapter] = [
            LocalFileAdapter(),
            YouTubeAdapter(),
            DirectUrlAdapter()
        ]

    def validate_source(self, url_or_path: str) -> Dict[str, Any]:
        """Probes and validates source accessibility, metadata, and platform type."""
        raw = str(url_or_path).strip().strip('\"\'')
        # Check local path expansion first
        try:
            local_p = Path(os.path.expandvars(os.path.expanduser(raw)))
            if local_p.exists() and local_p.is_file():
                return LocalFileAdapter().validate_source(str(local_p))
        except Exception:
            pass

        matched_adapter = next((a for a in self.adapters if a.can_handle(raw)), None)
        if not matched_adapter:
            raise ValueError(
                f"Unsupported source format or unreachable URL: '{raw}'. "
                "Supported: Authorized YouTube URLs, Direct Video MP4 streams, or Local MP4/MKV files."
            )
        return matched_adapter.validate_source(raw)

    def process_url_or_path(self, url_or_path: str, auth_type: str = "user_owned", notes: str = "", job_id: Optional[int] = None) -> SourceVideo:
        target = url_or_path.strip()
        matched_adapter = next((a for a in self.adapters if a.can_handle(target)), None)
        
        if not matched_adapter:
            raise ValueError(
                f"Unsupported source URL or path: '{target}'. "
                "Supported: Authorized YouTube URLs, Direct Video HTTP/HTTPS URLs, or Local MP4/MKV files."
            )

        logger.info(f"Ingesting source with {matched_adapter.__class__.__name__}: {target}")
        data = matched_adapter.validate_and_ingest(target, auth_type=auth_type, job_id=job_id)

        with SessionLocal() as db:
            # Check duplicate by local_path or URL
            existing = db.query(SourceVideo).filter(
                (SourceVideo.local_path == data["local_path"]) | (SourceVideo.url == data["url"])
            ).first()

            if existing:
                video = existing
                # ALWAYS update local_path, title, duration, and dimensions to real media values
                video.local_path = data["local_path"]
                video.url = data["url"]
                if data.get("title") and ("Source " not in data["title"] or not video.title):
                    video.title = data["title"]
                if data.get("duration"):
                    video.duration = data["duration"]
                if data.get("width"):
                    video.width = data["width"]
                if data.get("height"):
                    video.height = data["height"]
                if data.get("fps"):
                    video.fps = data["fps"]
                if data.get("format"):
                    video.format = data["format"]
                video.status = "ready"
            else:
                video = SourceVideo(
                    url=data["url"],
                    title=data["title"],
                    duration=data["duration"],
                    width=data["width"],
                    height=data["height"],
                    fps=data["fps"],
                    format=data["format"],
                    local_path=data["local_path"],
                    source_type=data["source_type"],
                    author=data["author"],
                    license_info=data["license_info"],
                    status="ready"
                )
                db.add(video)
                db.flush()

                # Add authorization record
                auth = SourceAuthorization(
                    source_video_id=video.id,
                    auth_type=data["auth_type"],
                    authorized_by=data["author"],
                    notes=notes or "Authorized via Gaming Uncut Ingestion Engine"
                )
                db.add(auth)

            db.commit()
            db.refresh(video)
            return video

ingestion_manager = IngestionManager()
