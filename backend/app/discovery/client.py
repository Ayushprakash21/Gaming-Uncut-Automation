"""
Official YouTube Data API v3 Discovery Client for Gaming Uncut.
Performs manual and automated video discovery with quota management,
multi-game queries, duration parsing, channel allowlisting, and pagination.
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.security.vault import vault
from backend.app.publishers.quota_tracker import (
    get_or_create_quota_state,
    YouTubeQuotaExceededError
)
from backend.app.models.schema import SystemLog, PlatformQuotaState

logger = logging.getLogger("gaming_uncut.discovery.client")

def parse_iso8601_duration(duration_str: str) -> float:
    """Parses ISO 8601 duration (e.g. PT1H15M33S or PT4M20S) into total seconds."""
    if not duration_str:
        return 0.0
    pattern = r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?"
    match = re.match(pattern, duration_str)
    if not match:
        return 0.0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return float(hours * 3600 + minutes * 60 + seconds)

def format_duration(seconds: float) -> str:
    """Formats seconds into MM:SS or HH:MM:SS string."""
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"

class YouTubeDiscoveryConfigError(Exception):
    """Raised when YouTube credentials or API keys are missing or invalid."""
    pass

class YouTubeDiscoveryError(Exception):
    """Base exception for YouTube Data API discovery failures."""
    pass

class YouTubeDiscoveryClient:
    def __init__(self):
        self._cached_service = None

    def get_service(self, db: Optional[Session] = None):
        """
        Builds authenticated YouTube Data API v3 client using either:
        1. OAuth credentials from vault
        2. Google API Key from vault / env
        """
        # 1. Try Google OAuth credentials
        from backend.app.publishers.youtube.youtube_client import YouTubePublishClient
        yt_pub = YouTubePublishClient()
        creds = yt_pub.get_credentials()
        if creds and creds.valid:
            return build("youtube", "v3", credentials=creds, cache_discovery=False)

        # 2. Try API Key from vault or environment
        api_key = (
            vault.get_secret("google", "api_key")
            or os.getenv("YOUTUBE_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or vault.get_gemini_api_key()
            or os.getenv("GEMINI_API_KEY")
        )
        if api_key:
            return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

        raise YouTubeDiscoveryConfigError(
            "YouTube API access not configured. Connect your YouTube account in Platform Connections "
            "or provide a Google / YouTube API Key in settings."
        )

    def _check_and_charge_quota(self, units: int, db: Session):
        """Verifies daily quota has not been exceeded and charges used units."""
        quota_state = get_or_create_quota_state(db, "youtube")
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        if quota_state.api_quota_exceeded and quota_state.pause_until and quota_state.pause_until > now:
            raise YouTubeQuotaExceededError(
                f"YouTube API quota exhausted. Discovery requests paused until {quota_state.pause_until.isoformat()} UTC."
            )

        quota_state.quota_units_used += units
        quota_state.last_updated_at = now
        db.commit()

    def search_videos(
        self,
        query: str,
        game_title: Optional[str] = None,
        channel_id: Optional[str] = None,
        published_after: Optional[datetime] = None,
        duration_filter: Optional[str] = None,  # "any", "short", "medium", "long"
        max_results: int = 15,
        page_token: Optional[str] = None,
        db: Optional[Session] = None
    ) -> Dict[str, Any]:
        """
        Executes YouTube Data API v3 search().list + videos().list to find gaming footage
        with accurate durations, view counts, and metadata.
        Cost: 100 units for search + 1 unit for video details = 101 units.
        """
        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            # 1. Quota Pre-check (101 units)
            self._check_and_charge_quota(101, db)

            service = self.get_service(db)

            # Build search query
            query_parts = []
            if game_title and game_title.strip():
                query_parts.append(f'"{game_title.strip()}"')
            if query and query.strip():
                query_parts.append(query.strip())
            full_query = " ".join(query_parts) if query_parts else "gameplay"

            search_kwargs: Dict[str, Any] = {
                "q": full_query,
                "part": "snippet",
                "type": "video",
                "videoCategoryId": "20",  # YouTube Gaming Category
                "maxResults": min(max(max_results, 1), 50),
            }

            if channel_id and channel_id.strip():
                search_kwargs["channelId"] = channel_id.strip()

            if published_after:
                search_kwargs["publishedAfter"] = (
                    published_after.replace(tzinfo=timezone.utc).isoformat()
                    if published_after.tzinfo is None
                    else published_after.isoformat()
                )

            # Map duration filter:
            # "short" (<4m), "medium" (4-20m), "long" (>20m), "any"
            if duration_filter and duration_filter.lower() in ["short", "medium", "long"]:
                search_kwargs["videoDuration"] = duration_filter.lower()

            if page_token:
                search_kwargs["pageToken"] = page_token

            # Execute search
            try:
                search_response = service.search().list(**search_kwargs).execute()
            except HttpError as e:
                self._handle_http_error(e, db)

            items = search_response.get("items", [])
            next_page_token = search_response.get("nextPageToken")
            prev_page_token = search_response.get("prevPageToken")
            page_info = search_response.get("pageInfo", {})
            total_results = page_info.get("totalResults", len(items))

            video_ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]

            if not video_ids:
                return {
                    "items": [],
                    "next_page_token": next_page_token,
                    "prev_page_token": prev_page_token,
                    "total_results": 0,
                    "quota_used": 100
                }

            # 2. Fetch video details (duration and stats)
            details_map = self._get_video_details(service, video_ids, db)

            # Format standardized output
            results: List[Dict[str, Any]] = []
            for item in items:
                vid = item.get("id", {}).get("videoId")
                if not vid:
                    continue
                snippet = item.get("snippet", {})
                detail = details_map.get(vid, {})

                pub_time = None
                pub_raw = snippet.get("publishedAt")
                if pub_raw:
                    try:
                        pub_time = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                    except Exception:
                        pass

                duration_sec = detail.get("duration_sec", 0.0)
                results.append({
                    "youtube_video_id": vid,
                    "title": snippet.get("title", ""),
                    "channel_id": snippet.get("channelId", ""),
                    "channel_title": snippet.get("channelTitle", ""),
                    "published_at": pub_time,
                    "duration_sec": duration_sec,
                    "duration_formatted": format_duration(duration_sec),
                    "view_count": detail.get("view_count", 0),
                    "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url")
                        or snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                    "youtube_url": f"https://www.youtube.com/watch?v={vid}",
                    "game_title": game_title or "Unknown Game",
                    "description": snippet.get("description", "")
                })

            return {
                "items": results,
                "next_page_token": next_page_token,
                "prev_page_token": prev_page_token,
                "total_results": total_results,
                "quota_used": 101
            }

        finally:
            if close_db:
                db.close()

    def _get_video_details(
        self,
        service: Any,
        video_ids: List[str],
        db: Session
    ) -> Dict[str, Dict[str, Any]]:
        """Calls videos().list for detailed contentDetails and statistics."""
        if not video_ids:
            return {}

        details_map = {}
        try:
            chunks = [video_ids[i:i + 50] for i in range(0, len(video_ids), 50)]
            for chunk in chunks:
                resp = service.videos().list(
                    part="contentDetails,statistics",
                    id=",".join(chunk)
                ).execute()

                for v in resp.get("items", []):
                    vid = v.get("id")
                    content_details = v.get("contentDetails", {})
                    statistics = v.get("statistics", {})

                    dur_str = content_details.get("duration", "")
                    duration_sec = parse_iso8601_duration(dur_str)
                    view_count = int(statistics.get("viewCount", 0))

                    details_map[vid] = {
                        "duration_sec": duration_sec,
                        "view_count": view_count
                    }
        except HttpError as e:
            logger.warning(f"Failed to fetch detailed video statistics: {e}")

        return details_map

    def _handle_http_error(self, e: HttpError, db: Session):
        """Extracts exact error code and triggers quota pausing if limit is reached."""
        status = e.resp.status
        content_str = e.content.decode("utf-8", errors="ignore") if isinstance(e.content, bytes) else str(e.content)

        if status == 403 and ("quotaExceeded" in content_str or "dailyLimitExceeded" in content_str):
            quota_state = get_or_create_quota_state(db, "youtube")
            now = datetime.now(timezone.utc)
            tomorrow_midnight = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=None
            )
            quota_state.api_quota_exceeded = True
            quota_state.pause_until = tomorrow_midnight
            quota_state.last_restriction_type = "api_quota_exceeded"
            quota_state.last_restriction_reason = "YouTube Data API v3 daily quota exceeded during discovery scan."
            quota_state.last_updated_at = now.replace(tzinfo=None)
            db.commit()

            # Audit log
            log = SystemLog(
                module="youtube_discovery",
                level="WARNING",
                message="YouTube Data API daily quota limit exceeded. Discovery scheduled scans paused until tomorrow 00:00 UTC.",
                metadata_json=json.dumps({"http_status": 403, "reason": "quotaExceeded", "pause_until": tomorrow_midnight.isoformat()}),
                created_at=now.replace(tzinfo=None)
            )
            db.add(log)
            db.commit()

            raise YouTubeQuotaExceededError(
                f"YouTube Data API v3 daily quota exceeded. Discovery paused until {tomorrow_midnight.isoformat()} UTC."
            )

        err_msg = f"YouTube Data API error (HTTP {status}): {content_str[:300]}"
        logger.error(err_msg)
        raise YouTubeDiscoveryError(err_msg) from e

youtube_discovery_client = YouTubeDiscoveryClient()
