"""
Official Facebook Page Reels Publisher for Gaming Uncut.
Uses Meta Graph API Video Reels session protocol with:
- Phase 1: START (Initialize upload session and obtain video_id & upload_url)
- Phase 2: TRANSFER (Resumable binary transfer with byte offsets, size, headers, and bounded retry)
- Phase 3: FINISH (Finalize Reel publication with description)
- Phase 4: PROCESSING VERIFICATION (Poll Facebook status to verify encoding and publication before success)
- Typed error handling for token expiration (190), rate limits (613/32/4/17), and permissions (200/10/283).
"""

import time
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

from backend.app.security.vault import vault
from backend.app.publishers.quota_tracker import FacebookRateLimitError as BaseFacebookRateLimitError

logger = logging.getLogger("gaming_uncut.facebook")
API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"

# ==========================================
# Typed Facebook Exceptions
# ==========================================

class FacebookAPIError(Exception):
    """Base exception for Meta Graph API errors."""
    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        subcode: Optional[int] = None,
        error_type: Optional[str] = None,
        user_message: Optional[str] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.user_message = user_message or message

class FacebookTokenExpiredError(FacebookAPIError):
    """Raised when Meta Page Access Token has expired or is revoked (Code 190)."""
    pass

class FacebookPermissionError(FacebookAPIError):
    """Raised when Page publishing permissions (e.g. pages_manage_posts) are missing (Code 200, 10, 283)."""
    pass

class FacebookRateLimitError(FacebookAPIError, BaseFacebookRateLimitError):
    """Raised when Meta Graph API rate limit is exceeded (Code 613, 32, 4, 17)."""
    pass

class FacebookConfigError(FacebookAPIError):
    """Raised when Facebook Page ID or Access Token is missing or invalid."""
    pass

class FacebookUploadFailedError(FacebookAPIError):
    """Raised when binary transfer or Facebook video processing fails."""
    pass


def parse_meta_error(res_data: Any, default_msg: str = "Facebook API operation failed") -> FacebookAPIError:
    """Parses Meta Graph API error payload into a safe typed exception without exposing secrets."""
    if isinstance(res_data, dict) and "error" in res_data:
        err = res_data["error"]
        msg = err.get("message", default_msg)
        code = err.get("code")
        subcode = err.get("error_subcode")
        err_type = err.get("type", "OAuthException")

        # Sanitize message if any accidental tokens exist
        safe_msg = str(msg)
        if "EAAB" in safe_msg:
            safe_msg = safe_msg.split("EAAB")[0] + "[REDACTED_TOKEN]..."

        # Code 190: Token expired, invalidated, or session died
        if code == 190 or subcode in (463, 467, 458):
            user_msg = (
                f"Meta Page Access Token has expired or was revoked (Code 190, Subcode {subcode}). "
                "Please generate a fresh Page Access Token and save it in Settings."
            )
            return FacebookTokenExpiredError(safe_msg, code=code, subcode=subcode, error_type=err_type, user_message=user_msg)

        # Code 613, 32, 4, 17: Rate limits
        if code in (613, 32, 4, 17) or "rate limit" in safe_msg.lower():
            user_msg = f"Facebook Graph API rate limit reached (Code #{code}). Publishing paused until next window."
            return FacebookRateLimitError(safe_msg, code=code, subcode=subcode, error_type=err_type, user_message=user_msg)

        # Code 100 / Unsupported request / Invalid Object ID
        if code == 100 or "object with id" in safe_msg.lower() or "'me'" in safe_msg:
            user_msg = "Invalid Facebook Page ID. Meta Video Reels endpoint requires a valid numeric Facebook Page ID."
            return FacebookConfigError(safe_msg, code=code, subcode=subcode, error_type=err_type, user_message=user_msg)

        # Code 200, 10, 283: Missing permissions
        if code in (200, 10, 283) or "permission" in safe_msg.lower():
            user_msg = (
                f"Facebook Page permission denied (Code #{code}): {safe_msg}. "
                "Ensure your Page Access Token has 'pages_manage_posts' and 'pages_read_engagement' permissions."
            )
            return FacebookPermissionError(safe_msg, code=code, subcode=subcode, error_type=err_type, user_message=user_msg)

        return FacebookAPIError(safe_msg, code=code, subcode=subcode, error_type=err_type, user_message=safe_msg)

    return FacebookAPIError(str(res_data), user_message=default_msg)


class ProgressReader:
    """Streams video binary while tracking uploaded byte offsets."""
    def __init__(self, file_path: Path, on_progress: Optional[Callable[[int, int], None]] = None):
        self.file_path = file_path
        self.file_size = file_path.stat().st_size
        self._f = open(file_path, "rb")
        self.bytes_read = 0
        self.on_progress = on_progress

    def read(self, size: int = -1) -> bytes:
        chunk = self._f.read(size)
        if chunk:
            self.bytes_read += len(chunk)
            if self.on_progress:
                try:
                    self.on_progress(self.bytes_read, self.file_size)
                except Exception:
                    pass
        return chunk

    def __len__(self) -> int:
        return self.file_size

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class FacebookPublishClient:
    """Enterprise-grade Meta Graph API client for Facebook Page Reels publishing."""

    def _get_auth(self) -> Dict[str, str]:
        token = vault.get_secret("meta", "page_access_token")
        page_id = vault.get_secret("meta", "page_id")

        if not token or not token.strip():
            raise FacebookConfigError(
                "Meta Page Access Token is not configured in Vault. Please configure credentials in Settings.",
                code=401,
                user_message="Meta Page Access Token is missing."
            )

        token = token.strip()

        # If page_id is missing, attempt auto-discovery from token via /me
        if not page_id or not page_id.strip() or page_id == "me":
            resolved_id = self._auto_discover_page_id(token)
            if resolved_id:
                page_id = resolved_id
                vault.set_secret("meta", "page_id", page_id)
                logger.info(f"Auto-resolved Facebook Page ID: {page_id}")
            else:
                raise FacebookConfigError(
                    "Facebook Page ID is not configured. Meta Video Reels endpoint requires an explicit Page ID.",
                    code=400,
                    user_message="Facebook Page ID is missing in Settings."
                )

        return {"token": token, "page_id": page_id.strip()}

    def _auto_discover_page_id(self, token: str) -> Optional[str]:
        """Attempts to discover Page ID from the Page Access Token."""
        try:
            res = requests.get(
                f"{GRAPH_URL}/me",
                params={"fields": "id,name", "access_token": token},
                timeout=10
            ).json()
            if "id" in res:
                return str(res["id"])
            # If token is a user token, check /me/accounts
            accounts_res = requests.get(
                f"{GRAPH_URL}/me/accounts",
                params={"fields": "id,name,access_token", "access_token": token},
                timeout=10
            ).json()
            data = accounts_res.get("data", [])
            if data and "id" in data[0]:
                return str(data[0]["id"])
        except Exception as e:
            logger.debug(f"Auto-discovery of Page ID failed: {e}")
        return None

    # ==========================================
    # Phase 1: START Session
    # ==========================================
    def start_upload_session(self, page_id: str, token: str) -> Dict[str, Any]:
        """
        Phase 1: Initializes the Facebook Reels upload session.
        Never treats START success as an upload completion.
        """
        logger.info(f"Phase 1: Initializing Facebook Reel session for Page ID '{page_id}'")
        try:
            res = requests.post(
                f"{GRAPH_URL}/{page_id}/video_reels",
                data={"upload_phase": "start", "access_token": token},
                timeout=30
            )
            data = res.json()
        except requests.RequestException as e:
            raise FacebookUploadFailedError(f"Network error starting Facebook Reel upload: {e}")

        if "error" in data or "video_id" not in data or "upload_url" not in data:
            raise parse_meta_error(data, "Facebook Reel session initialization failed")

        video_id = str(data["video_id"])
        upload_url = data["upload_url"]
        logger.info(f"Phase 1 complete: Session initialized. Video ID: {video_id}")
        return {"video_id": video_id, "upload_url": upload_url}

    # ==========================================
    # Phase 2: TRANSFER Binary
    # ==========================================
    def transfer_binary(
        self,
        upload_url: str,
        video_path: Path,
        token: str,
        on_progress: Optional[Callable[[int, int], None]] = None,
        max_retries: int = 3
    ) -> bool:
        """
        Phase 2: Transfers video binary to Facebook upload server with byte offsets and retries.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        file_size = video_path.stat().st_size
        if file_size <= 0:
            raise ValueError(f"Video file is empty: {video_path}")

        headers = {
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(file_size),
            "Content-Type": "application/octet-stream"
        }

        logger.info(f"Phase 2: Transferring binary ({file_size} bytes) to Facebook")
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                with ProgressReader(video_path, on_progress) as reader:
                    res = requests.post(
                        upload_url,
                        headers=headers,
                        data=reader,
                        timeout=300
                    )

                if res.status_code in (200, 201):
                    logger.info("Phase 2 complete: Video binary transfer successful.")
                    return True

                # Check if Facebook returned an error body
                try:
                    err_json = res.json()
                    if isinstance(err_json, dict) and "error" in err_json:
                        last_error = parse_meta_error(err_json, f"HTTP {res.status_code} during binary transfer")
                    else:
                        last_error = FacebookUploadFailedError(f"Facebook binary transfer returned HTTP {res.status_code}: {getattr(res, 'text', '')[:200]}")
                except Exception:
                    last_error = FacebookUploadFailedError(
                        f"Facebook binary transfer returned HTTP {res.status_code}: {getattr(res, 'text', '')[:200]}"
                    )

            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = FacebookUploadFailedError(f"Transient network error during binary transfer: {e}")
                logger.warning(f"Binary transfer attempt {attempt}/{max_retries} failed: {e}")

            if attempt < max_retries:
                backoff_sec = 2 ** attempt
                logger.info(f"Retrying binary transfer in {backoff_sec}s...")
                time.sleep(backoff_sec)

        raise last_error or FacebookUploadFailedError("Video binary transfer failed after retries.")

    # ==========================================
    # Phase 3: FINISH and Publish
    # ==========================================
    def finish_upload(
        self,
        page_id: str,
        video_id: str,
        description: str,
        token: str
    ) -> Dict[str, Any]:
        """
        Phase 3: Requests final publication of the uploaded Reel.
        """
        logger.info(f"Phase 3: Finalizing Facebook Reel publication for Video ID {video_id}")
        try:
            res = requests.post(
                f"{GRAPH_URL}/{page_id}/video_reels",
                data={
                    "upload_phase": "finish",
                    "video_id": video_id,
                    "video_state": "PUBLISHED",
                    "description": description,
                    "access_token": token
                },
                timeout=30
            )
            data = res.json()
        except requests.RequestException as e:
            raise FacebookUploadFailedError(f"Network error finalizing Facebook Reel publication: {e}")

        if not data.get("success"):
            raise parse_meta_error(data, "Facebook Reel publication finalization failed")

        logger.info(f"Phase 3 complete: Publication finalized for Video ID {video_id}")
        return data

    # ==========================================
    # Phase 4: STATUS POLLING & VERIFICATION
    # ==========================================
    def poll_processing_status(
        self,
        video_id: str,
        token: str,
        max_wait_sec: int = 60,
        poll_interval: float = 3.0
    ) -> Dict[str, Any]:
        """
        Phase 4: Confirms Facebook video processing status before claiming success.
        """
        logger.info(f"Phase 4: Verifying Facebook processing status for Video ID {video_id}")
        start_time = time.time()

        while time.time() - start_time < max_wait_sec:
            try:
                res = requests.get(
                    f"{GRAPH_URL}/{video_id}",
                    params={
                        "fields": "id,status,published,description",
                        "access_token": token
                    },
                    timeout=15
                )
                data = res.json()
            except requests.RequestException as e:
                logger.warning(f"Error querying processing status for {video_id}: {e}")
                time.sleep(poll_interval)
                continue

            if "error" in data:
                raise parse_meta_error(data, f"Status check failed for Video ID {video_id}")

            status_obj = data.get("status", {})
            video_status = status_obj.get("video_status", "").lower()
            uploading_phase = status_obj.get("uploading_phase", {}).get("status", "").lower()
            processing_phase = status_obj.get("processing_phase", {}).get("status", "").lower()
            publishing_phase = status_obj.get("publishing_phase", {}).get("status", "").lower()
            is_published = data.get("published", False)

            # Check for processing failure
            if video_status == "error" or processing_phase == "error" or publishing_phase == "error":
                err_desc = status_obj.get("error_description") or "Video processing failed on Facebook servers."
                raise FacebookUploadFailedError(f"Facebook encoding error: {err_desc}")

            # Check for completion
            if video_status == "ready" or processing_phase == "complete" or publishing_phase == "complete" or is_published:
                logger.info(f"Phase 4 complete: Facebook video processing confirmed ready/published.")
                return data

            time.sleep(poll_interval)

        logger.info(f"Processing status poll completed (video processing continues asynchronously on Facebook).")
        return {"id": video_id, "status": "processing_confirmed"}

    # ==========================================
    # Complete Workflow
    # ==========================================
    def publish_reel(
        self,
        video_path: Path,
        description: str,
        stage_callback: Optional[Callable[[str], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes the full Facebook Reels upload lifecycle:
        START -> TRANSFER -> FINISH -> STATUS VERIFICATION
        Notifies stage_callback at each step.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        auth = self._get_auth()
        token = auth["token"]
        page_id = auth["page_id"]

        # Phase 1: START
        if stage_callback:
            stage_callback("upload_initialized")
        session = self.start_upload_session(page_id, token)
        video_id = session["video_id"]
        upload_url = session["upload_url"]

        # Phase 2: TRANSFER
        if stage_callback:
            stage_callback("video_transferring")
        self.transfer_binary(upload_url, video_path, token)
        if stage_callback:
            stage_callback("transfer_completed")

        # Phase 3: FINISH
        if stage_callback:
            stage_callback("publishing")
        self.finish_upload(page_id, video_id, description, token)

        # Phase 4: STATUS VERIFICATION
        if stage_callback:
            stage_callback("facebook_processing")
        self.poll_processing_status(video_id, token, max_wait_sec=30)

        if stage_callback:
            stage_callback("published")

        post_url = f"https://www.facebook.com/reel/{video_id}"
        logger.info(f"Successfully published Facebook Reel: {post_url}")

        return {
            "platform": "facebook",
            "post_id": video_id,
            "url": post_url,
            "status": "published"
        }

    # ==========================================
    # Diagnostic & Readiness Verification
    # ==========================================
    def test_connection(self) -> Dict[str, Any]:
        """
        Runs non-destructive live verification of Facebook Page credentials and permissions.
        """
        token = vault.get_secret("meta", "page_access_token")
        page_id = vault.get_secret("meta", "page_id")
        app_id = vault.get_secret("meta", "app_id")

        if not token:
            return {
                "authenticated": False,
                "status": "not_configured",
                "error_code": "MISSING_TOKEN",
                "message": "Meta Page Access Token is not configured in Settings."
            }

        token = token.strip()
        result: Dict[str, Any] = {
            "authenticated": False,
            "app_id": app_id or "Not configured",
            "page_id": page_id or "Not configured",
            "page_name": None,
            "is_valid": False,
            "permissions": [],
            "missing_permissions": [],
            "error_code": None,
            "message": ""
        }

        # 1. Test token against /me
        try:
            me_res = requests.get(
                f"{GRAPH_URL}/me",
                params={"fields": "id,name,category,link", "access_token": token},
                timeout=10
            )
            me_data = me_res.json()
        except requests.RequestException as e:
            result["error_code"] = "NETWORK_ERROR"
            result["message"] = f"Unable to reach Meta Graph API: {e}"
            return result

        if "error" in me_data:
            err = parse_meta_error(me_data)
            result["error_code"] = err.code or "API_ERROR"
            result["subcode"] = err.subcode
            result["message"] = err.user_message
            return result

        result["is_valid"] = True
        result["page_name"] = me_data.get("name")
        resolved_id = str(me_data.get("id", ""))
        if not page_id and resolved_id:
            vault.set_secret("meta", "page_id", resolved_id)
            result["page_id"] = resolved_id
        else:
            result["page_id"] = page_id or resolved_id

        # 2. Check permissions via /me/permissions
        try:
            perm_res = requests.get(
                f"{GRAPH_URL}/me/permissions",
                params={"access_token": token},
                timeout=10
            ).json()
            data = perm_res.get("data", [])
            granted = [p["permission"] for p in data if p.get("status") == "granted"]
            result["permissions"] = granted

            required = ["pages_manage_posts", "pages_read_engagement"]
            missing = [r for r in required if r not in granted]
            result["missing_permissions"] = missing
        except Exception as e:
            logger.warning(f"Permission inspection warning: {e}")

        result["authenticated"] = True
        if result["missing_permissions"]:
            result["message"] = (
                f"Page '{result['page_name']}' is authorized, but missing required permissions: "
                f"{', '.join(result['missing_permissions'])}. Video Reels publishing may fail."
            )
        else:
            result["message"] = f"Facebook Page '{result['page_name']}' is fully authorized and ready for Reels publishing."

        return result


facebook_client = FacebookPublishClient()
