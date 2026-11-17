"""
Official Facebook Page Reels Publisher for Gaming Uncut.
Uses Meta Graph API Video Reels session protocol with resumable chunk upload.
"""

import requests
import logging
from pathlib import Path
from typing import Dict, Any
from backend.app.security.vault import vault
from backend.app.publishers.quota_tracker import FacebookRateLimitError

logger = logging.getLogger("gaming_uncut.facebook")
API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"

class FacebookPublishClient:
    def _get_auth(self) -> Dict[str, str]:
        token = vault.get_secret("meta", "page_access_token")
        page_id = vault.get_secret("meta", "page_id")
        if not token:
            raise ValueError("Meta Page Access Token is not set in vault.")
        return {"token": token, "page_id": page_id or "me"}

    def publish_reel(self, video_path: Path, description: str) -> Dict[str, Any]:
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        auth = self._get_auth()
        token = auth["token"]
        page_id = auth["page_id"]
        file_size = video_path.stat().st_size

        # 1. Start upload session
        init_res = requests.post(
            f"{GRAPH_URL}/{page_id}/video_reels",
            data={"upload_phase": "start", "access_token": token},
            timeout=30
        ).json()

        if "video_id" not in init_res or "upload_url" not in init_res:
            err = init_res.get("error", {}).get("message", str(init_res))
            code = init_res.get("error", {}).get("code")
            if code in (32, 4, 17, 613) or "rate limit" in str(err).lower():
                raise FacebookRateLimitError(f"Facebook Graph API rate limit reached (code {code}): {err}")
            logger.error(f"Facebook Reel start error: {err}")
            raise RuntimeError(f"Facebook Reel session start failed: {err}")

        video_id = init_res["video_id"]
        upload_url = init_res["upload_url"]

        # 2. Upload binary
        headers = {
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream"
        }
        with open(video_path, "rb") as f:
            chunk_res = requests.post(upload_url, headers=headers, data=f, timeout=300)
            if chunk_res.status_code not in (200, 201):
                raise RuntimeError(f"Facebook binary transfer failed: {chunk_res.text}")

        # 3. Finish & publish
        finish_res = requests.post(
            f"{GRAPH_URL}/{page_id}/video_reels",
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description,
                "access_token": token
            },
            timeout=30
        ).json()

        if not finish_res.get("success"):
            err = finish_res.get("error", {}).get("message", str(finish_res))
            code = finish_res.get("error", {}).get("code")
            if code in (32, 4, 17, 613) or "rate limit" in str(err).lower():
                raise FacebookRateLimitError(f"Facebook Graph API publish rate limit reached (code {code}): {err}")
            raise RuntimeError(f"Facebook Reel finish failed: {err}")

        post_url = f"https://www.facebook.com/reel/{video_id}"
        logger.info(f"Published Facebook Reel: {post_url}")

        return {
            "platform": "facebook",
            "post_id": video_id,
            "url": post_url
        }

facebook_client = FacebookPublishClient()
