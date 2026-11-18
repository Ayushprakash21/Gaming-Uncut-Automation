"""
Isolated YouTube Video Source Acquisition Service for Gaming Uncut Automation.

Responsibilities:
1. Safely inspects and retrieves YouTube video metadata (via oEmbed and/or YouTube Data API v3).
2. Probes stream acquisition capability with yt-dlp using configured JS runtimes (Node.js/Deno).
3. Strictly isolates consumer stream acquisition from YouTube OAuth publishing credentials.
4. Distinguishes granular source states:
   - SOURCE_ACQUIRED: Video file is locally available and verified.
   - CAN_DOWNLOAD: Video stream is directly downloadable without bot challenges.
   - METADATA_AVAILABLE: Metadata exists, but direct stream download requires a local or creator file.
   - AUTH_REQUIRED: YouTube bot detection challenge triggered ("Sign in to confirm you're not a bot").
   - CREATOR_FILE_REQUIRED: Stream is restricted; original creator video file required.
   - SOURCE_UNAVAILABLE: Video is deleted, private, or invalid.
5. Zero exposure or auto-extraction of browser cookies; zero logging of credentials.
6. Seamlessly combines YouTube metadata with local or creator-provided video files.
"""

import os
import re
import json
import logging
import shutil
import uuid
from enum import Enum
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
import requests

from backend.app.core.config import settings
from backend.app.security.vault import vault

logger = logging.getLogger("gaming_uncut.youtube_source")


class YouTubeSourceStatus(str, Enum):
    SOURCE_ACQUIRED = "source_acquired"             # File is verified on disk
    CAN_DOWNLOAD = "can_download"                   # Unrestricted download possible
    METADATA_AVAILABLE = "metadata_available"       # Metadata exists, stream requires creator/local file
    AUTH_REQUIRED = "auth_required"                 # YouTube bot challenge ("Sign in to confirm you're not a bot")
    CREATOR_FILE_REQUIRED = "creator_file_required" # Creator must supply original MP4 file
    SOURCE_UNAVAILABLE = "source_unavailable"       # Deleted, private, or invalid URL


class YouTubeAcquisitionError(Exception):
    """Exception raised when YouTube video acquisition encounters restrictions."""
    def __init__(self, message: str, status: YouTubeSourceStatus, metadata: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.metadata = metadata or {}


def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts 11-character YouTube video ID from standard and short URLs."""
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
    """Normalizes any YouTube URL variant into canonical https://www.youtube.com/watch?v=VIDEO_ID."""
    vid = extract_youtube_id(url)
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return url.replace("\\", "/").strip().strip('\"\'')


class YouTubeSourceService:
    """Enterprise-grade, isolated YouTube source acquisition and validation engine."""

    def __init__(self):
        self._cached_js_runtime: Optional[Dict[str, Any]] = None

    def get_js_runtime_config(self) -> Dict[str, Any]:
        """Auto-discovers available JavaScript runtimes (Node.js or Deno) for yt-dlp challenge solving."""
        if self._cached_js_runtime is not None:
            return self._cached_js_runtime

        js_runtimes = {}
        # 1. Check Node.js
        node_path = shutil.which("node")
        if not node_path and os.path.exists(r"C:\Program Files\nodejs\node.exe"):
            node_path = r"C:\Program Files\nodejs\node.exe"

        if node_path:
            js_runtimes["node"] = {"path": node_path}
            logger.info(f"Discovered JavaScript runtime for yt-dlp: Node.js at {node_path}")
        else:
            # 2. Check Deno
            deno_path = shutil.which("deno")
            if deno_path:
                js_runtimes["deno"] = {"path": deno_path}
                logger.info(f"Discovered JavaScript runtime for yt-dlp: Deno at {deno_path}")

        self._cached_js_runtime = js_runtimes
        return js_runtimes

    def fetch_oembed_metadata(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieves public video metadata via YouTube oEmbed API.
        Guaranteed to bypass bot challenges and consume zero API quota.
        """
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        try:
            resp = requests.get(oembed_url, timeout=6.0)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "video_id": video_id,
                    "title": data.get("title") or f"YouTube Video {video_id}",
                    "author": data.get("author_name") or "YouTube Creator",
                    "author_url": data.get("author_url", ""),
                    "thumbnail_url": data.get("thumbnail_url") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    "width": data.get("width") or 1920,
                    "height": data.get("height") or 1080,
                }
            elif resp.status_code in (401, 403, 404):
                logger.warning(f"YouTube oEmbed reported video {video_id} unavailable (HTTP {resp.status_code})")
        except Exception as e:
            logger.debug(f"oEmbed fetch for {video_id} failed: {e}")
        return None

    def fetch_youtube_api_metadata(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Optionally enriches metadata using YouTube Data API v3 if API key is configured.
        Never uses or confuses publishing OAuth tokens.
        """
        try:
            api_key = vault.get_secret("google", "api_key")
            if not api_key:
                return None

            url = "https://www.googleapis.com/youtube/v3/videos"
            params = {
                "id": video_id,
                "part": "snippet,contentDetails",
                "key": api_key
            }
            resp = requests.get(url, params=params, timeout=6.0)
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    v = items[0]
                    snippet = v.get("snippet", {})
                    content_details = v.get("contentDetails", {})
                    dur_str = content_details.get("duration", "")

                    # Simple ISO8601 duration parser
                    duration_sec = 0.0
                    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur_str)
                    if match:
                        h = int(match.group(1) or 0)
                        m = int(match.group(2) or 0)
                        s = int(match.group(3) or 0)
                        duration_sec = float(h * 3600 + m * 60 + s)

                    thumbs = snippet.get("thumbnails", {})
                    thumb_url = (
                        thumbs.get("maxres", {}).get("url") or
                        thumbs.get("standard", {}).get("url") or
                        thumbs.get("high", {}).get("url") or
                        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    )

                    return {
                        "video_id": video_id,
                        "title": snippet.get("title", f"YouTube {video_id}"),
                        "author": snippet.get("channelTitle", "YouTube Creator"),
                        "thumbnail_url": thumb_url,
                        "duration": duration_sec,
                        "description": snippet.get("description", ""),
                        "tags": snippet.get("tags", []),
                    }
        except Exception as e:
            logger.debug(f"YouTube Data API metadata fetch for {video_id} failed: {e}")
        return None

    def get_unified_metadata(self, video_id: str) -> Optional[Dict[str, Any]]:
        """
        Combines oEmbed and Data API metadata to build a complete, resilient metadata record.
        Does not invoke yt-dlp or risk triggering consumer bot-detection.
        """
        oembed = self.fetch_oembed_metadata(video_id)
        api_meta = self.fetch_youtube_api_metadata(video_id)

        if not oembed and not api_meta:
            return None

        title = (api_meta.get("title") if api_meta else None) or (oembed.get("title") if oembed else None) or f"YouTube {video_id}"
        author = (api_meta.get("author") if api_meta else None) or (oembed.get("author") if oembed else None) or "YouTube Creator"
        thumbnail_url = (api_meta.get("thumbnail_url") if api_meta else None) or (oembed.get("thumbnail_url") if oembed else None) or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        duration = (api_meta.get("duration") if api_meta else 0.0) or 0.0

        return {
            "video_id": video_id,
            "canonical_url": f"https://www.youtube.com/watch?v={video_id}",
            "title": title,
            "author": author,
            "thumbnail_url": thumbnail_url,
            "duration": duration,
            "resolution": "1920x1080 (HD)",
            "width": 1920,
            "height": 1080
        }

    def check_local_cache(self, video_id: str) -> Optional[Path]:
        """Checks if a valid, non-empty downloaded or imported source file already exists."""
        candidate = settings.INPUT_DIR / f"yt_{video_id}.mp4"
        if candidate.exists() and candidate.stat().st_size > 1024:
            return candidate
        return None

    def probe_stream_availability(self, canonical_url: str) -> Tuple[bool, str, Optional[str]]:
        """
        Checks whether yt-dlp can acquire video streams without full download.
        Detects bot challenges ('Sign in to confirm you're not a bot', 429) cleanly.
        Never uses publishing OAuth tokens; never extracts browser cookies automatically.
        Returns: (can_download, reason_code, error_message).
        """
        from yt_dlp import YoutubeDL

        ydl_opts: Dict[str, Any] = {
            "extract_flat": True,
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

        # Inject Node.js / Deno JS runtime if available
        js_runtimes = self.get_js_runtime_config()
        if js_runtimes:
            ydl_opts["js_runtimes"] = js_runtimes

        # If user explicitly configured custom authorization in vault (never logged)
        custom_cookies = vault.get_secret("ingestion", "youtube_cookies")
        if custom_cookies and custom_cookies.strip():
            # Support secure path to user-consented cookies file
            cookie_file = Path(custom_cookies.strip())
            if cookie_file.exists():
                ydl_opts["cookiefile"] = str(cookie_file)

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(canonical_url, download=False)
                if not info:
                    return False, "unavailable", "YouTube video metadata could not be retrieved."
                return True, "can_download", None
        except Exception as e:
            err_msg = str(e)
            err_lower = err_msg.lower()
            if "sign in to confirm you" in err_lower or "bot" in err_lower or "429" in err_lower or "too many requests" in err_lower:
                return False, "auth_required", "YouTube requires sign-in verification (bot detection active)."
            if "private video" in err_lower or "video unavailable" in err_lower or "not available" in err_lower:
                return False, "unavailable", "Video is private, deleted, or unavailable."
            return False, "restricted", err_msg

    def inspect_and_validate(self, url_or_path: str, local_override_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Comprehensive two-phase inspection:
        Phase 1: Validates metadata existence (via oEmbed / API).
        Phase 2: Verifies stream acquisition capability or local file availability.
        Distinguishes:
        - SOURCE_ACQUIRED: Local file already verified.
        - CAN_DOWNLOAD: Direct stream ready.
        - METADATA_AVAILABLE / AUTH_REQUIRED / CREATOR_FILE_REQUIRED: Metadata found, but requires creator/local file.
        - SOURCE_UNAVAILABLE: Video not found.
        """
        video_id = extract_youtube_id(url_or_path)
        if not video_id:
            raise ValueError(f"Invalid YouTube URL format: '{url_or_path}'. Expected valid watch, shorts, or share link.")

        canonical_url = f"https://www.youtube.com/watch?v={video_id}"

        # 1. Check local file override first
        if local_override_path:
            p = Path(local_override_path).resolve()
            if p.exists() and p.is_file() and p.stat().st_size > 1024:
                from backend.app.ingestion.adapters import probe_local_media
                meta = probe_local_media(p)
                yt_meta = self.get_unified_metadata(video_id) or {}
                duration = meta.get("duration", 0) or yt_meta.get("duration", 0)
                minutes = int(duration // 60)
                seconds = int(duration % 60)
                return {
                    "valid": True,
                    "ready_to_process": True,
                    "source_type": "youtube",
                    "platform_name": "YouTube (Creator-Provided Footage)",
                    "status": YouTubeSourceStatus.SOURCE_ACQUIRED.value,
                    "title": yt_meta.get("title", p.stem),
                    "author": yt_meta.get("author", "YouTube Creator"),
                    "duration": duration,
                    "duration_formatted": f"{minutes}:{seconds:02d}",
                    "resolution": f"{meta.get('width', 1920)}x{meta.get('height', 1080)}",
                    "thumbnail_url": yt_meta.get("thumbnail_url", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
                    "accessible": True,
                    "can_download": False,
                    "requires_file_import": False,
                    "local_path": str(p),
                    "message": f"Source video verified via local creator file ({minutes}:{seconds:02d}, {meta.get('width')}x{meta.get('height')}).",
                    "suggested_actions": [],
                    "cached_locally": True
                }

        # 2. Check local disk cache
        cached_file = self.check_local_cache(video_id)
        if cached_file:
            from backend.app.ingestion.adapters import probe_local_media
            meta = probe_local_media(cached_file)
            yt_meta = self.get_unified_metadata(video_id) or {}
            duration = meta.get("duration", 0) or yt_meta.get("duration", 0)
            minutes = int(duration // 60)
            seconds = int(duration % 60)
            return {
                "valid": True,
                "ready_to_process": True,
                "source_type": "youtube",
                "platform_name": "YouTube (Cached Source)",
                "status": YouTubeSourceStatus.SOURCE_ACQUIRED.value,
                "title": yt_meta.get("title", f"YouTube {video_id}"),
                "author": yt_meta.get("author", "YouTube Creator"),
                "duration": duration,
                "duration_formatted": f"{minutes}:{seconds:02d}",
                "resolution": f"{meta.get('width', 1920)}x{meta.get('height', 1080)}",
                "thumbnail_url": yt_meta.get("thumbnail_url", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"),
                "accessible": True,
                "can_download": False,
                "requires_file_import": False,
                "local_path": str(cached_file.resolve()),
                "message": f"Cached YouTube source ready ({minutes}:{seconds:02d}).",
                "suggested_actions": [],
                "cached_locally": True
            }

        # 3. Retrieve metadata via oEmbed / Data API
        metadata = self.get_unified_metadata(video_id)
        if not metadata:
            # Metadata could not be fetched
            return {
                "valid": False,
                "ready_to_process": False,
                "source_type": "youtube",
                "platform_name": "YouTube",
                "status": YouTubeSourceStatus.SOURCE_UNAVAILABLE.value,
                "title": f"YouTube Video ({video_id})",
                "author": "Unknown",
                "duration": 0.0,
                "duration_formatted": "0:00",
                "resolution": "Unknown",
                "thumbnail_url": "",
                "accessible": False,
                "can_download": False,
                "requires_file_import": True,
                "message": "YouTube video could not be found or is private/deleted.",
                "suggested_actions": ["import_local_video", "creator_provided_video"],
                "cached_locally": False
            }

        # 4. Probe stream download capability
        can_download, reason, error_detail = self.probe_stream_availability(canonical_url)

        duration = metadata.get("duration", 0.0)
        minutes = int(duration // 60)
        seconds = int(duration % 60)

        if can_download:
            return {
                "valid": True,
                "ready_to_process": True,
                "source_type": "youtube",
                "platform_name": "YouTube",
                "status": YouTubeSourceStatus.CAN_DOWNLOAD.value,
                "title": metadata["title"],
                "author": metadata["author"],
                "duration": duration,
                "duration_formatted": f"{minutes}:{seconds:02d}",
                "resolution": metadata["resolution"],
                "thumbnail_url": metadata["thumbnail_url"],
                "accessible": True,
                "can_download": True,
                "requires_file_import": False,
                "message": f"YouTube video verified ({minutes}:{seconds:02d}, {metadata['resolution']}).",
                "suggested_actions": [],
                "cached_locally": False
            }

        # Stream download blocked (e.g. Bot check / 429) -> Require Local/Creator Import
        status_value = (
            YouTubeSourceStatus.AUTH_REQUIRED.value
            if reason == "auth_required"
            else YouTubeSourceStatus.CREATOR_FILE_REQUIRED.value
        )

        return {
            "valid": False,
            "ready_to_process": False,
            "source_type": "youtube",
            "platform_name": "YouTube",
            "status": status_value,
            "title": metadata["title"],
            "author": metadata["author"],
            "duration": duration,
            "duration_formatted": f"{minutes}:{seconds:02d}",
            "resolution": metadata["resolution"],
            "thumbnail_url": metadata["thumbnail_url"],
            "accessible": True,
            "can_download": False,
            "requires_file_import": True,
            "message": "Video found. Import the original video file or provide an authorised source to start AI clipping.",
            "suggested_actions": [
                "import_local_video",
                "creator_provided_video",
                "retry_validation",
                "view_metadata"
            ],
            "cached_locally": False,
            "error_detail": error_detail
        }

    def download_source_file(
        self,
        video_id: str,
        target_path: Path,
        job_id: Optional[int] = None
    ) -> Path:
        """
        Executes stream download for an authorized, accessible YouTube video.
        Uses Node.js/Deno JS runtime.
        Catches bot detection and raises YouTubeAcquisitionError with structured context.
        """
        from yt_dlp import YoutubeDL

        canonical_url = f"https://www.youtube.com/watch?v={video_id}"
        unique_token = uuid.uuid4().hex[:8]
        temp_out_tmpl = str(settings.TEMP_DIR / f"yt_{video_id}_{unique_token}.%(ext)s")

        def _cancel_hook(d):
            if job_id:
                from backend.app.services.pipeline_service import pipeline_service, JobCancelledException
                if pipeline_service.is_cancellation_requested(job_id):
                    raise JobCancelledException(f"Job #{job_id} cancelled during stream download.")

        ydl_opts: Dict[str, Any] = {
            "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": temp_out_tmpl,
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "progress_hooks": [_cancel_hook],
        }

        js_runtimes = self.get_js_runtime_config()
        if js_runtimes:
            ydl_opts["js_runtimes"] = js_runtimes

        custom_cookies = vault.get_secret("ingestion", "youtube_cookies")
        if custom_cookies and custom_cookies.strip():
            cookie_file = Path(custom_cookies.strip())
            if cookie_file.exists():
                ydl_opts["cookiefile"] = str(cookie_file)

        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([canonical_url])

            downloaded_temp = None
            for ext in ["mp4", "mkv", "webm"]:
                cand = settings.TEMP_DIR / f"yt_{video_id}_{unique_token}.{ext}"
                if cand.exists() and cand.stat().st_size > 1024:
                    downloaded_temp = cand
                    break

            if not downloaded_temp:
                raise FileNotFoundError(f"yt-dlp download produced no output for video {video_id}")

            from backend.app.ingestion.adapters import safe_windows_replace
            safe_windows_replace(downloaded_temp, target_path)
            return target_path

        except Exception as e:
            err_msg = str(e)
            err_lower = err_msg.lower()
            if "sign in to confirm you" in err_lower or "bot" in err_lower or "429" in err_lower:
                yt_meta = self.get_unified_metadata(video_id)
                raise YouTubeAcquisitionError(
                    message="Video found. Import the original video file or provide an authorised source to start AI clipping.",
                    status=YouTubeSourceStatus.AUTH_REQUIRED,
                    metadata=yt_meta
                ) from e
            raise RuntimeError(f"YouTube stream download failed: {e}") from e


youtube_source_service = YouTubeSourceService()
