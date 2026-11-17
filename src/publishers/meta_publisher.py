"""
Official Meta Graph API Publisher for Instagram Reels and Facebook Reels.
Uses direct binary resumable uploads (rupload.facebook.com) eliminating third-party hosting needs.
Handles container creation, binary transfer, processing status polling, and final publication.
"""

import os
import time
import requests
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from src.core.security import vault
from src.core.database import log_event

logger = logging.getLogger("gaming_uncut.meta")

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
RUPLOAD_BASE_URL = f"https://rupload.facebook.com/video-upload/{GRAPH_API_VERSION}"

class MetaPublisher:
    def __init__(self):
        pass

    def _get_credentials(self) -> Dict[str, str]:
        page_token = vault.get_secret("meta_page_access_token")
        ig_user_id = vault.get_secret("meta_instagram_account_id")
        page_id = vault.get_secret("meta_page_id")

        if not page_token:
            raise ValueError("Meta Page Access Token is not configured in the vault.")

        return {
            "access_token": page_token,
            "ig_user_id": ig_user_id or "",
            "page_id": page_id or ""
        }

    # ==========================================
    # INSTAGRAM REELS (Direct Resumable Upload)
    # ==========================================
    def upload_instagram_reel(self, video_path: Path, caption: str) -> Dict[str, Any]:
        """
        Publishes a Reel to Instagram using the official Graph API direct resumable upload protocol.
        Step 1: Create Reel Media Container with upload_type=resumable.
        Step 2: Stream binary video bytes to the returned upload_url.
        Step 3: Poll status until FINISHED.
        Step 4: Execute media_publish.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        creds = self._get_credentials()
        ig_user_id = creds["ig_user_id"]
        access_token = creds["access_token"]

        if not ig_user_id:
            raise ValueError("Instagram Account ID is not configured. Link an IG professional account.")

        file_size = video_path.stat().st_size

        # Step 1: Initialize Resumable Container
        init_url = f"{GRAPH_BASE_URL}/{ig_user_id}/media"
        init_payload = {
            "media_type": "REELS",
            "caption": caption,
            "upload_type": "resumable",
            "access_token": access_token
        }
        res = requests.post(init_url, data=init_payload, timeout=30)
        init_data = res.json()
        if "id" not in init_data or "uri" not in init_data:
            err = init_data.get("error", {}).get("message", res.text)
            log_event("instagram", "ERROR", f"Failed to initialize IG Reel container: {err}")
            raise RuntimeError(f"Instagram container creation failed: {err}")

        container_id = init_data["id"]
        upload_url = init_data["uri"]

        # Step 2: Stream Binary File Chunks to rupload
        headers = {
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream"
        }

        logger.info(f"Uploading IG Reel binary stream ({file_size / (1024*1024):.2f} MB)...")
        with open(video_path, "rb") as f:
            upload_res = requests.post(upload_url, headers=headers, data=f, timeout=300)
            if upload_res.status_code not in (200, 201):
                raise RuntimeError(f"Instagram binary chunk upload failed ({upload_res.status_code}): {upload_res.text}")

        # Step 3: Poll processing status
        status_url = f"{GRAPH_BASE_URL}/{container_id}"
        logger.info("Polling Instagram video processing status...")
        max_checks = 40
        for _ in range(max_checks):
            status_res = requests.get(
                status_url,
                params={"fields": "status_code,status", "access_token": access_token},
                timeout=15
            ).json()
            code = status_res.get("status_code")
            if code == "FINISHED":
                break
            elif code == "ERROR":
                raise RuntimeError(f"Instagram video encoding error: {status_res}")
            time.sleep(3)

        # Step 4: Final Publish
        publish_url = f"{GRAPH_BASE_URL}/{ig_user_id}/media_publish"
        pub_res = requests.post(publish_url, data={"creation_id": container_id, "access_token": access_token}, timeout=30).json()
        if "id" not in pub_res:
            err = pub_res.get("error", {}).get("message", str(pub_res))
            raise RuntimeError(f"Instagram media_publish failed: {err}")

        media_id = pub_res["id"]
        post_url = f"https://www.instagram.com/reel/{media_id}"
        log_event("instagram", "INFO", f"Published Instagram Reel successfully: {media_id}", {"url": post_url})

        return {
            "platform": "instagram_reels",
            "media_id": media_id,
            "url": post_url
        }

    # ==========================================
    # FACEBOOK PAGE REELS (Resumable Upload)
    # ==========================================
    def upload_facebook_reel(self, video_path: Path, description: str) -> Dict[str, Any]:
        """
        Publishes a Reel to a Facebook Page using official Video Reels API with chunked resumable upload.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        creds = self._get_credentials()
        page_id = creds["page_id"]
        access_token = creds["access_token"]

        if not page_id:
            raise ValueError("Facebook Page ID is not configured.")

        file_size = video_path.stat().st_size

        # Step 1: Start upload session
        init_url = f"{GRAPH_BASE_URL}/{page_id}/video_reels"
        init_res = requests.post(init_url, data={"upload_phase": "start", "access_token": access_token}, timeout=30).json()
        if "video_id" not in init_res or "upload_url" not in init_res:
            err = init_res.get("error", {}).get("message", str(init_res))
            log_event("facebook", "ERROR", f"Failed to start FB Reel session: {err}")
            raise RuntimeError(f"Facebook Reel session start failed: {err}")

        video_id = init_res["video_id"]
        upload_url = init_res["upload_url"]

        # Step 2: Binary transfer
        headers = {
            "Authorization": f"OAuth {access_token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream"
        }
        with open(video_path, "rb") as f:
            chunk_res = requests.post(upload_url, headers=headers, data=f, timeout=300)
            if chunk_res.status_code not in (200, 201):
                raise RuntimeError(f"Facebook Reel binary upload failed: {chunk_res.text}")

        # Step 3: Finish & publish
        finish_res = requests.post(
            init_url,
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": "PUBLISHED",
                "description": description,
                "access_token": access_token
            },
            timeout=30
        ).json()

        if not finish_res.get("success"):
            err = finish_res.get("error", {}).get("message", str(finish_res))
            raise RuntimeError(f"Facebook Reel finish phase failed: {err}")

        post_url = f"https://www.facebook.com/reel/{video_id}"
        log_event("facebook", "INFO", f"Published Facebook Reel successfully: {video_id}", {"url": post_url})

        return {
            "platform": "facebook_reels",
            "video_id": video_id,
            "url": post_url
        }
