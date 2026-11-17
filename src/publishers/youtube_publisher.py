"""
Official YouTube Data API v3 Publisher for YouTube Shorts.
Handles OAuth 2.0 authorization, token refreshing, and resumable chunked video uploads.
Tracks daily quota consumption (1,600 units per video.insert).
"""

import os
import sys
import json
import time
import httplib2
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from src.core.security import vault
from src.core.database import log_event

logger = logging.getLogger("gaming_uncut.youtube")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError)
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

class YouTubePublisher:
    def __init__(self):
        self._service = None

    def get_authenticated_service(self) -> Any:
        """
        Retrieves or refreshes YouTube Data API v3 service using vault-stored credentials.
        Never prompts in terminal without user trigger.
        """
        token_info = vault.get_json_secret("google_oauth_tokens")
        client_config = vault.get_json_secret("google_client_config")

        if not client_config:
            raise ValueError("Google Client configuration missing from vault. Please import client_secrets.json first.")

        creds = None
        if token_info:
            creds = Credentials.from_authorized_user_info(token_info, SCOPES)

        # Refresh or initiate flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired Google OAuth access token...")
                creds.refresh(Request())
                vault.save_json_secret("google_oauth_tokens", json.loads(creds.to_json()))
            else:
                logger.info("Initiating local Google OAuth Desktop authorization flow...")
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                # Opens browser for user consent on localhost port
                creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
                vault.save_json_secret("google_oauth_tokens", json.loads(creds.to_json()))

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def upload_short(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str = "public",
        category_id: str = "20"  # 20 = Gaming
    ) -> Dict[str, Any]:
        """
        Executes official resumable chunked upload to YouTube Data API v3.
        Appends #Shorts to title and description to guarantee vertical Shorts categorization.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        youtube = self.get_authenticated_service()

        # Format title/description for Shorts
        final_title = title if "#Shorts" in title else f"{title} #Shorts"
        final_desc = description if "#Shorts" in description else f"{description}\n\n#Shorts #GamingUncut"

        body = {
            "snippet": {
                "title": final_title[:100],
                "description": final_desc[:5000],
                "tags": tags,
                "categoryId": category_id
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False
            }
        }

        # Resumable chunked upload: 4MB chunks
        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=4 * 1024 * 1024,
            resumable=True
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        error = None
        retry = 0
        max_retries = 10

        logger.info(f"Initiating YouTube Shorts upload for: {video_path.name}")

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"YouTube upload progress: {progress}%")
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    error = f"A retriable HTTP error {e.resp.status} occurred:\n{e.content}"
                else:
                    # Non-retriable (e.g. Quota Exceeded 403)
                    log_event("youtube", "ERROR", f"YouTube API Error {e.resp.status}: {e.content}")
                    raise
            except RETRIABLE_EXCEPTIONS as e:
                error = f"A retriable network error occurred: {e}"

            if error:
                logger.warning(error)
                retry += 1
                if retry > max_retries:
                    raise IOError("Max retries exceeded uploading video chunk to YouTube.")
                time.sleep(2 ** retry)
                error = None

        video_id = response.get("id")
        video_url = f"https://www.youtube.com/shorts/{video_id}"
        
        log_event("youtube", "INFO", f"Published YouTube Short successfully: {video_id}", {"url": video_url})
        return {
            "platform": "youtube_shorts",
            "video_id": video_id,
            "url": video_url,
            "quota_units_consumed": 1600
        }
