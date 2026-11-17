"""
Official Instagram Graph API Reels Publisher for Gaming Uncut.
Uses Meta Graph API direct resumable upload protocol (rupload.facebook.com).
Handles container creation, binary streaming, status polling, and media_publish.
"""

import time
import requests
import logging
from pathlib import Path
from typing import Dict, Any
from backend.app.security.vault import vault
from backend.app.publishers.quota_tracker import InstagramRateLimitError

logger = logging.getLogger("gaming_uncut.instagram")
API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"

class InstagramPublishClient:
    def _get_auth(self) -> Dict[str, str]:
        token = vault.get_secret("meta", "page_access_token")
        ig_id = vault.get_secret("meta", "instagram_account_id")

        if not token:
            raise ValueError("Meta Page Access Token is not set in vault.")
        if not ig_id:
            raise ValueError("Instagram Account ID is not set in vault.")

        return {"token": token, "ig_id": ig_id}

    def publish_reel(self, video_path: Path, caption: str) -> Dict[str, Any]:
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        auth = self._get_auth()
        token = auth["token"]
        ig_id = auth["ig_id"]
        file_size = video_path.stat().st_size

        # 1. Initialize resumable container
        init_res = requests.post(
            f"{GRAPH_URL}/{ig_id}/media",
            data={
                "media_type": "REELS",
                "caption": caption,
                "upload_type": "resumable",
                "access_token": token
            },
            timeout=30
        ).json()

        if "id" not in init_res or "uri" not in init_res:
            err = init_res.get("error", {}).get("message", str(init_res))
            code = init_res.get("error", {}).get("code")
            if code in (32, 4, 9, 17) or "rate limit" in str(err).lower() or "calls to this api" in str(err).lower():
                raise InstagramRateLimitError(f"Instagram Graph API publishing limit reached (code {code}): {err}")
            logger.error(f"Instagram init container error: {err}")
            raise RuntimeError(f"Instagram container creation failed: {err}")

        container_id = init_res["id"]
        upload_url = init_res["uri"]

        # 2. Binary stream to rupload endpoint
        headers = {
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream"
        }
        with open(video_path, "rb") as f:
            stream_res = requests.post(upload_url, headers=headers, data=f, timeout=300)
            if stream_res.status_code not in (200, 201):
                raise RuntimeError(f"Instagram binary upload failed ({stream_res.status_code}): {stream_res.text}")

        # 3. Poll processing status
        status_url = f"{GRAPH_URL}/{container_id}"
        logger.info(f"Polling Instagram container {container_id} processing...")
        max_checks = 40
        for _ in range(max_checks):
            st = requests.get(
                status_url,
                params={"fields": "status_code,status", "access_token": token},
                timeout=15
            ).json()
            code = st.get("status_code")
            if code == "FINISHED":
                break
            elif code == "ERROR":
                raise RuntimeError(f"Instagram processing failed: {st}")
            time.sleep(3)

        # 4. Final publish
        pub_res = requests.post(
            f"{GRAPH_URL}/{ig_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=30
        ).json()

        if "id" not in pub_res:
            err = pub_res.get("error", {}).get("message", str(pub_res))
            err_code = pub_res.get("error", {}).get("code")
            if err_code in (32, 4, 9, 17) or "rate limit" in str(err).lower() or "limit" in str(err).lower():
                raise InstagramRateLimitError(f"Instagram media_publish rate limit reached (code {err_code}): {err}")
            raise RuntimeError(f"Instagram media_publish failed: {err}")

        media_id = pub_res["id"]
        post_url = f"https://www.instagram.com/reel/{media_id}"
        logger.info(f"Published Instagram Reel: {post_url}")

        return {
            "platform": "instagram",
            "post_id": media_id,
            "url": post_url
        }

instagram_client = InstagramPublishClient()
