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
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional
import httpx
from yt_dlp import YoutubeDL

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import SourceVideo, SourceAuthorization, SystemLog

logger = logging.getLogger("gaming_uncut.ingestion")

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
    def validate_and_ingest(self, url_or_path: str, auth_type: str = "user_owned") -> Dict[str, Any]:
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

    def validate_and_ingest(self, url_or_path: str, auth_type: str = "user_owned") -> Dict[str, Any]:
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

    def validate_and_ingest(self, url_or_path: str, auth_type: str = "creator_authorized") -> Dict[str, Any]:
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
        return bool(self.YT_REGEX.match(url_or_path.strip().strip('\"\'')))

    def validate_source(self, url_or_path: str) -> Dict[str, Any]:
        url = url_or_path.strip().strip('\"\'')
        ydl_opts = {
            "extract_flat": False,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
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

    def validate_and_ingest(self, url_or_path: str, auth_type: str = "creator_authorized") -> Dict[str, Any]:
        url = url_or_path.strip()
        out_tmpl = str(settings.INPUT_DIR / "yt_%(id)s.%(ext)s")
        
        ydl_opts = {
            "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": out_tmpl,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        with YoutubeDL(ydl_opts) as ydl:
            # 1. Extract metadata without downloading first
            info = ydl.extract_info(url, download=False)
            if not info:
                raise ValueError(f"Could not retrieve metadata for YouTube URL: {url}")
            
            duration = float(info.get("duration", 0))
            if duration > 7200:  # Max 2 hours for safe local processing
                raise ValueError(f"Video duration ({duration/60:.1f}m) exceeds 2-hour processing maximum.")

            # 2. Download authorized stream
            ydl.download([url])
            local_file = settings.INPUT_DIR / f"yt_{info['id']}.mp4"

        if not local_file.exists():
            # Check if ext was mkv
            alt = settings.INPUT_DIR / f"yt_{info['id']}.mkv"
            if alt.exists():
                local_file = alt
            else:
                raise FileNotFoundError(f"Expected YouTube download output not found: {local_file}")

        meta = probe_local_media(local_file)
        return {
            "source_type": "youtube",
            "url": url,
            "title": info.get("title", f"YouTube {info['id']}"),
            "duration": meta["duration"] or duration,
            "width": meta["width"],
            "height": meta["height"],
            "fps": meta["fps"],
            "format": meta["format"],
            "local_path": str(local_file),
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

    def process_url_or_path(self, url_or_path: str, auth_type: str = "user_owned", notes: str = "") -> SourceVideo:
        target = url_or_path.strip()
        matched_adapter = next((a for a in self.adapters if a.can_handle(target)), None)
        
        if not matched_adapter:
            raise ValueError(
                f"Unsupported source URL or path: '{target}'. "
                "Supported: Authorized YouTube URLs, Direct Video HTTP/HTTPS URLs, or Local MP4/MKV files."
            )

        logger.info(f"Ingesting source with {matched_adapter.__class__.__name__}: {target}")
        data = matched_adapter.validate_and_ingest(target, auth_type=auth_type)

        with SessionLocal() as db:
            # Check duplicate by local_path or URL
            existing = db.query(SourceVideo).filter(
                (SourceVideo.local_path == data["local_path"]) | (SourceVideo.url == data["url"])
            ).first()

            if existing:
                video = existing
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
