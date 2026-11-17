"""
Official YouTube Shorts Publishing Client via Google Data API v3.
Handles OAuth 2.0 Desktop authorization, token persistence in vault,
and resumable chunked video uploads with quota monitoring.
"""

import json
import time
import httplib2
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from backend.app.security.vault import vault

logger = logging.getLogger("gaming_uncut.youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

class YouTubePublishClient:
    def __init__(self):
        self._service = None

    def get_service(self) -> Any:
        token_info = vault.get_json("google", "oauth_tokens")
        client_config = vault.get_json("google", "client_config")

        if not client_config:
            raise ValueError("Google Client secrets not found in vault. Please import client_secrets.json first.")

        creds = None
        if token_info:
            creds = Credentials.from_authorized_user_info(token_info, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Google OAuth access token...")
                creds.refresh(Request())
                vault.save_json("google", "oauth_tokens", json.loads(creds.to_json()))
            else:
                logger.info("Initiating local Google OAuth authorization flow on localhost...")
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
                vault.save_json("google", "oauth_tokens", json.loads(creds.to_json()))

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def upload_short(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str = "public"
    ) -> Dict[str, Any]:
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        service = self.get_service()

        # Enforce #Shorts in title and description
        final_title = title if "#Shorts" in title else f"{title[:90]} #Shorts"
        final_desc = description if "#Shorts" in description else f"{description}\n\n#Shorts #GamingUncut"

        body = {
            "snippet": {
                "title": final_title[:100],
                "description": final_desc[:5000],
                "tags": tags,
                "categoryId": "20"  # Gaming Category
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=4 * 1024 * 1024,
            resumable=True
        )

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        retry = 0
        max_retries = 8

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"YouTube upload progress: {progress}%")
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    retry += 1
                    if retry > max_retries:
                        raise
                    time.sleep(2 ** retry)
                else:
                    logger.error(f"YouTube API Fatal Error ({e.resp.status}): {e.content}")
                    raise
            except Exception as e:
                retry += 1
                if retry > max_retries:
                    raise
                time.sleep(2 ** retry)

        video_id = response.get("id")
        video_url = f"https://www.youtube.com/shorts/{video_id}"
        logger.info(f"Published YouTube Short: {video_url}")

        return {
            "platform": "youtube",
            "post_id": video_id,
            "url": video_url,
            "quota_units": 1600
        }

youtube_client = YouTubePublishClient()
