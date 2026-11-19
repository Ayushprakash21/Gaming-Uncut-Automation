"""
Official YouTube Shorts Publishing Client via Google Data API v3.
Handles OAuth 2.0 Desktop authorization, token persistence in vault,
channel verification via channels.list, and resumable chunked video uploads with quota monitoring.
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable
from datetime import datetime

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from backend.app.security.vault import vault
from backend.app.publishers.quota_tracker import YouTubeQuotaExceededError, YouTubeChannelUploadLimitError

logger = logging.getLogger("gaming_uncut.youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly"
]
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]


class YouTubeAuthError(Exception):
    """Base exception for YouTube authentication and channel verification errors."""
    pass


class YouTubeShortsValidationError(Exception):
    """Raised when a video fails the mandatory YouTube Shorts technical profile (1080x1920 9:16 <= 180s)."""
    pass


class YouTubePublishClient:
    def __init__(self):
        self._service = None

    def get_credentials(self) -> Optional[Credentials]:
        """
        Retrieves credentials from vault and automatically refreshes access token if expired.
        Raises YouTubeAuthError if token is revoked or refresh fails.
        """
        token_info = vault.get_json("google", "oauth_tokens")
        if not token_info:
            return None

        try:
            creds = Credentials.from_authorized_user_info(token_info, SCOPES)
        except Exception as e:
            logger.warning(f"Failed to deserialize Google OAuth token: {e}")
            return None

        if creds and not creds.valid:
            if creds.expired and creds.refresh_token:
                try:
                    logger.info("Refreshing expired Google OAuth access token...")
                    creds.refresh(Request())
                    # Update vault with refreshed token
                    token_dict = json.loads(creds.to_json())
                    vault.save_json("google", "oauth_tokens", token_dict)
                    logger.info("Google OAuth token successfully refreshed.")
                except RefreshError as e:
                    logger.error(f"Google OAuth refresh token was revoked or expired: {e}")
                    # Clear invalid tokens from vault
                    vault.delete_secret("google", "oauth_tokens")
                    vault.delete_secret("google", "verified_channel")
                    raise YouTubeAuthError(
                        "YouTube authorization was revoked or expired. Please click 'Connect YouTube Account' to reconnect."
                    )
                except Exception as e:
                    logger.error(f"Unexpected error refreshing Google OAuth token: {e}")
                    raise YouTubeAuthError(f"Failed to refresh YouTube authorization: {e}")
            else:
                return None

        return creds

    def verify_channel(self, creds: Optional[Credentials] = None) -> Dict[str, Any]:
        """
        Calls YouTube Data API channels.list(part='snippet,contentDetails,statistics', mine=True)
        to verify that the user possesses a valid, active YouTube channel.
        Saves verified channel metadata into the secure vault.
        """
        if creds is None:
            creds = self.get_credentials()

        if not creds or not creds.valid:
            raise YouTubeAuthError("Not authorized. No valid Google OAuth credentials found.")

        try:
            service = build("youtube", "v3", credentials=creds)
            res = service.channels().list(
                part="snippet,contentDetails,statistics",
                mine=True
            ).execute()

            items = res.get("items", [])
            if not items:
                raise YouTubeAuthError(
                    "No YouTube channel was found for this Google account. "
                    "Please create a YouTube channel on youtube.com, then try connecting again."
                )

            item = items[0]
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})

            channel_info = {
                "channel_id": item["id"],
                "title": snippet.get("title", "YouTube Channel"),
                "custom_url": snippet.get("customUrl", ""),
                "description": snippet.get("description", "")[:200],
                "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                "subscriber_count": statistics.get("subscriberCount", "0"),
                "video_count": statistics.get("videoCount", "0"),
                "verified_at": datetime.utcnow().isoformat()
            }

            vault.save_json("google", "verified_channel", channel_info)
            logger.info(f"Verified YouTube Channel: {channel_info['title']} ({channel_info['channel_id']})")
            return channel_info

        except HttpError as e:
            logger.error(f"YouTube channels.list API error: {e}")
            raise YouTubeAuthError(f"YouTube channel verification failed: {e.reason or str(e)}")
        except Exception as e:
            if isinstance(e, YouTubeAuthError):
                raise
            logger.error(f"Unexpected error during channel verification: {e}")
            raise YouTubeAuthError(f"Could not verify YouTube channel: {e}")

    def connect_account(self, open_browser: bool = True, timeout: int = 180) -> Dict[str, Any]:
        """
        Starts Google Desktop OAuth 2.0 InstalledAppFlow on localhost via oauth_manager.
        Returns immediately with authorization URL and live session state.
        """
        from backend.app.publishers.youtube.oauth_manager import oauth_manager
        return oauth_manager.start_authorization(open_browser=open_browser, timeout_seconds=timeout)

    def disconnect_account(self) -> Dict[str, Any]:
        """
        Wipes OAuth tokens and verified channel information from vault.
        Resets YouTube connection status to Not Connected.
        """
        from backend.app.publishers.youtube.oauth_manager import oauth_manager
        oauth_manager.cancel_authorization()
        vault.delete_secret("google", "oauth_tokens")
        vault.delete_secret("google", "verified_channel")
        self._service = None
        logger.info("YouTube account successfully disconnected and tokens cleared from vault.")
        return {
            "success": True,
            "status": "disconnected",
            "message": "YouTube account has been disconnected. Client secrets remain saved."
        }

    def test_connection(self) -> Dict[str, Any]:
        """
        Validates existing tokens by making an actual call to YouTube Data API channels.list.
        """
        creds = self.get_credentials()
        if not creds:
            return {
                "success": False,
                "connected": False,
                "authenticated": False,
                "error": "No YouTube authorization tokens found. Please connect your YouTube account."
            }

        try:
            channel_info = self.verify_channel(creds)
            return {
                "success": True,
                "connected": True,
                "authenticated": True,
                "channel": channel_info,
                "message": f"Connection verified for channel: {channel_info['title']}"
            }
        except YouTubeAuthError as e:
            return {
                "success": False,
                "connected": False,
                "authenticated": False,
                "error": str(e)
            }

    def get_service(self) -> Any:
        """Returns authenticated YouTube Data API v3 service or raises YouTubeAuthError."""
        creds = self.get_credentials()
        if not creds or not creds.valid:
            raise YouTubeAuthError(
                "YouTube account is not connected or authorization expired. "
                "Please connect your YouTube account in Settings before publishing."
            )
        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    def upload_short(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy_status: str = "public",
        on_video_id_created: Optional[Callable[[str], None]] = None
    ) -> Dict[str, Any]:
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        # Mandatory Pre-Upload Validation for YouTube Shorts
        from backend.app.video.validator import validate_shorts_profile
        is_valid, validation_msg, probe_info = validate_shorts_profile(video_path)
        if not is_valid:
            logger.error(f"YouTube Shorts Pre-Upload Validation Failed: {validation_msg}")
            raise YouTubeShortsValidationError(f"Cannot upload to YouTube Shorts: {validation_msg}")

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
                    # Parse error details for quota vs channel limits
                    try:
                        err_content = json.loads(e.content.decode("utf-8")) if isinstance(e.content, bytes) else json.loads(str(e.content))
                        errors_list = err_content.get("error", {}).get("errors", [])
                        reasons = [item.get("reason", "") for item in errors_list]
                        error_msg = err_content.get("error", {}).get("message", "")

                        if any(r in ("quotaExceeded", "dailyLimitExceeded") for r in reasons) or "quota" in error_msg.lower():
                            logger.warning(f"YouTube Project API Quota Exceeded (10,000 unit bucket): {error_msg}")
                            raise YouTubeQuotaExceededError(f"YouTube API Project Quota Exceeded: {error_msg}")

                        if any(r == "uploadLimitExceeded" for r in reasons) or "upload limit" in error_msg.lower() or "exceeded the number" in error_msg.lower():
                            logger.warning(f"YouTube Channel-Level Upload Limit Reached: {error_msg}")
                            raise YouTubeChannelUploadLimitError(f"YouTube Channel-Level Upload Limit Reached: {error_msg}")

                    except (YouTubeQuotaExceededError, YouTubeChannelUploadLimitError):
                        raise
                    except Exception:
                        pass

                    logger.error(f"YouTube API Fatal Error ({e.resp.status}): {e.content}")
                    raise
            except Exception as e:
                retry += 1
                if retry > max_retries:
                    raise
                time.sleep(2 ** retry)

        video_id = response.get("id")
        if on_video_id_created and video_id:
            try:
                on_video_id_created(video_id)
            except Exception as e:
                logger.debug(f"YouTube on_video_id_created notice: {e}")
        shorts_url = f"https://www.youtube.com/shorts/{video_id}"
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"Published YouTube Short: {shorts_url}")

        # Post-upload verification through YouTube Data API
        upload_status = "uploaded"
        try:
            verify_res = service.videos().list(
                part="snippet,contentDetails,status",
                id=video_id
            ).execute()
            if verify_res.get("items"):
                v_item = verify_res["items"][0]
                upload_status = v_item.get("status", {}).get("uploadStatus", "uploaded")
        except Exception as e:
            logger.debug(f"Post-upload verification notice: {e}")

        return {
            "platform": "youtube",
            "post_id": video_id,
            "url": shorts_url,
            "watch_url": watch_url,
            "is_short": True,
            "dimensions": f"{probe_info['width']}x{probe_info['height']}",
            "duration": probe_info["duration"],
            "aspect_ratio": "9:16",
            "upload_status": upload_status,
            "quota_units": 1600
        }

youtube_client = YouTubePublishClient()
