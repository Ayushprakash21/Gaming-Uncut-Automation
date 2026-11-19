"""
Enterprise-grade Instagram Graph API Reels Publisher for Gaming Uncut.
Implements the complete 5-stage publishing lifecycle:
- Stage 1: Media Container Creation (Resumable or Direct URL with accessibility validation)
- Stage 2: Video Binary Streaming to rupload.facebook.com with byte offsets and retry backoff
- Stage 3: Container Processing Status Polling (strictly awaits FINISHED; handles ERROR & timeouts)
- Stage 4: Media Publishing (media_publish with creation_id)
- Stage 5: Final Media Verification (queries media object to verify permalink and publication)
- Typed exceptions for Token Expiration (Code 190), Permissions (Code 200), Rate Limits (Code 32/4/9/17/613),
  and Inaccessible Video URLs.
- Structured Audit Logging to SystemLog.
"""

import time
import json
import logging
import subprocess
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Callable, Union, Tuple
import socket
from urllib.parse import urlparse
import urllib3.util.connection as urllib_conn
try:
    urllib_conn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

from backend.app.security.vault import vault
from backend.app.publishers.quota_tracker import InstagramRateLimitError as BaseInstagramRateLimitError
from backend.app.database.session import SessionLocal
from backend.app.models.schema import SystemLog
from backend.app.publishers.meta_transport import meta_transport, redact_meta_secrets, MetaNetworkTimeoutError

logger = logging.getLogger("gaming_uncut.instagram")
API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"

# ==========================================
# Single Source of Truth: Polling Configuration
# ==========================================
DEFAULT_INITIAL_POLL_WAIT_SEC: float = 15.0
DEFAULT_POLL_INTERVAL_SEC: float = 60.0
DEFAULT_MAX_POLL_CHECKS: int = 5
DEFAULT_MAX_MONITORING_WINDOW_SEC: int = 300

# ==========================================
# Typed Instagram Exceptions
# ==========================================

class InstagramAPIError(Exception):
    """Base exception for Instagram Graph API errors."""
    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        subcode: Optional[int] = None,
        error_type: Optional[str] = None,
        user_message: Optional[str] = None,
        fbtrace_id: Optional[str] = None,
        http_status: Optional[int] = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.user_message = user_message or message
        self.fbtrace_id = fbtrace_id
        self.http_status = http_status

class InstagramTokenExpiredError(InstagramAPIError):
    """Raised when Meta token has expired or is revoked (Code 190)."""
    pass

class InstagramPermissionError(InstagramAPIError):
    """Raised when Instagram publishing permissions are missing (Code 200, 10, 283)."""
    pass

class InstagramRateLimitError(InstagramAPIError, BaseInstagramRateLimitError):
    """Raised when Meta rate limit is exceeded (Code 32, 4, 9, 17, 613)."""
    pass

class InstagramConfigError(InstagramAPIError):
    """Raised when Instagram Professional Account ID or Token is missing or invalid."""
    pass

class InstagramVideoUrlError(InstagramAPIError):
    """Raised when video URL is not publicly accessible to Meta servers."""
    pass

class InstagramProcessingFailedError(InstagramAPIError):
    """Raised when Meta video transcode/container processing enters ERROR state."""
    pass

class InstagramProcessingTimeoutError(InstagramAPIError):
    """Raised when container processing times out waiting for FINISHED status."""
    pass

class InstagramContainerProcessingInProgress(InstagramProcessingTimeoutError):
    """
    Raised when an Instagram container is confirmed actively transcoding / processing
    on Meta servers, but has not yet reached FINISHED state within the inline monitoring window.
    This is NOT a failure; the container should be reconciled in the background.
    """
    def __init__(self, container_id: str, status_data: Dict[str, Any], checks: int = 0, waited_sec: float = 0.0):
        status_text = status_data.get("status", "Media is still being processed.")
        status_code = status_data.get("status_code", "IN_PROGRESS")
        msg = f"Instagram container {container_id} timed out waiting for completion: status is {status_code} ({status_text}) after {checks} checks ({waited_sec:.1f}s)."
        super().__init__(msg, code=None, subcode=None, error_type="ProcessingInProgress", user_message=msg)
        self.container_id = container_id
        self.status_data = status_data
        self.checks = checks
        self.waited_sec = waited_sec

class InstagramUploadFailedError(InstagramAPIError):
    """Raised when binary transfer or media publishing fails."""
    pass

class InstagramValidationError(InstagramAPIError):
    """Raised when video asset fails Meta Instagram Reels pre-upload validation."""
    pass

class InstagramEncodingRejectionError(InstagramProcessingFailedError):
    """Raised when Meta edge ingest rejects video due to encoding or container incompatibility."""
    pass

class InstagramBinaryTransferError(InstagramUploadFailedError):
    """Raised when binary chunk transfer or server acknowledgement fails."""
    pass

class InstagramNetworkTimeoutError(InstagramAPIError):
    """Raised when network timeout occurs during Instagram operations."""
    pass

class InstagramInaccessibleMediaError(InstagramVideoUrlError):
    """Raised when media file or URL is not accessible to Meta servers."""
    pass


def redact_token(text: Any) -> str:
    """Masks Meta and Google access tokens, secrets, and auth headers from text and log statements."""
    if not text:
        return ""
    import re
    s = str(text)
    s = re.sub(r'(EAAB[a-zA-Z0-9_\-\.]+)|(EAAQ[a-zA-Z0-9_\-\.]+)|(IGAA[a-zA-Z0-9_\-\.]+)|(IGQV[a-zA-Z0-9_\-\.]+)', '[REDACTED_TOKEN]', s)
    s = re.sub(r'access_token=[^&\s"\']+', 'access_token=[REDACTED_TOKEN]', s)
    s = re.sub(r'Authorization:\s*(Bearer|OAuth)\s+[^\s"\']+', 'Authorization: \\1 [REDACTED_TOKEN]', s, flags=re.IGNORECASE)
    return s


def parse_instagram_error(
    res_data: Any,
    default_msg: str = "Instagram API operation failed",
    raw_text: Optional[str] = None,
    http_status: Optional[int] = None
) -> InstagramAPIError:
    """Parses Meta Graph API error payload into a safe typed exception without exposing secrets."""
    redact = redact_token

    # Handle Meta rupload debug_info payload format:
    # {"debug_info": {"retriable": false, "type": "ProcessingFailedError", "message": "..."}}
    if isinstance(res_data, dict) and "debug_info" in res_data:
        debug_info = res_data["debug_info"]
        msg = debug_info.get("message", default_msg)
        err_type = debug_info.get("type", "ProcessingFailedError")
        retriable = debug_info.get("retriable", False)

        # Handle nested JSON message strings like '{"success":false,"error":{"message":"The ig container is not in the status..."}}'
        if isinstance(msg, str) and msg.strip().startswith("{") and msg.strip().endswith("}"):
            try:
                nested = json.loads(msg)
                if isinstance(nested, dict):
                    if "error" in nested and isinstance(nested["error"], dict):
                        msg = nested["error"].get("message", msg)
                    elif "message" in nested:
                        msg = nested.get("message", msg)
            except Exception:
                pass

        safe_msg = redact(msg)

        if err_type == "NotAuthorizedError" or "not authorized" in safe_msg.lower() or "oauth" in safe_msg.lower():
            user_msg = "Meta Access Token is not authorized or has expired for Instagram upload. Please check token permissions in Settings."
            return InstagramTokenExpiredError(
                safe_msg, code=190, error_type=err_type, user_message=user_msg, http_status=http_status
            )

        if "not in the status" in safe_msg.lower() or "session expired" in safe_msg.lower():
            user_msg = f"Instagram container is not in a valid upload status: {safe_msg}. Container session expired or failed."
            return InstagramUploadFailedError(
                safe_msg, code=400, error_type=err_type, user_message=user_msg, http_status=http_status
            )

        if err_type == "ProcessingFailedError" or "processing failed" in safe_msg.lower() or "encoding" in safe_msg.lower():
            user_msg = (
                f"Instagram binary upload rejected by Meta edge ingest ({safe_msg}). "
                "Ensure video adheres to Meta Reels specs (H.264 High 4.1 CFR 30FPS, AAC-LC <= 48kHz, 1080x1920 9:16, <= 60s, <= 60MB)."
            )
            return InstagramEncodingRejectionError(
                safe_msg, code=400, error_type=err_type, user_message=user_msg, http_status=http_status
            )

        if "offset" in safe_msg.lower() or "chunk" in safe_msg.lower() or "transfer" in safe_msg.lower():
            return InstagramBinaryTransferError(
                safe_msg, code=400, error_type=err_type, user_message=safe_msg, http_status=http_status
            )

        return InstagramUploadFailedError(
            safe_msg, code=400, error_type=err_type, user_message=safe_msg, http_status=http_status
        )

    if isinstance(res_data, dict) and "error" in res_data:
        err = res_data["error"]
        msg = err.get("message", default_msg)
        code = err.get("code")
        subcode = err.get("error_subcode")
        err_type = err.get("type", "OAuthException")
        fbtrace_id = err.get("fbtrace_id")

        safe_msg = redact(msg)

        # Code 190: Token expired, invalidated, or session died
        if code == 190 or subcode in (463, 467, 458):
            user_msg = (
                f"Meta Access Token has expired or was revoked (Code 190, Subcode {subcode}). "
                "Please generate a fresh Page Access Token with Instagram permissions and save it in Settings."
            )
            return InstagramTokenExpiredError(
                safe_msg, code=code, subcode=subcode, error_type=err_type,
                user_message=user_msg, fbtrace_id=fbtrace_id, http_status=http_status
            )

        # Code 32, 4, 9, 17, 613: Rate limits
        if code in (32, 4, 9, 17, 613) or "rate limit" in safe_msg.lower() or "calls to this api" in safe_msg.lower():
            user_msg = f"Instagram Graph API rate limit reached (Code #{code}). Publishing paused until next window."
            return InstagramRateLimitError(
                safe_msg, code=code, subcode=subcode, error_type=err_type,
                user_message=user_msg, fbtrace_id=fbtrace_id, http_status=http_status
            )

        # Code 200, 10, 283: Missing permissions
        if code in (200, 10, 283) or "permission" in safe_msg.lower():
            user_msg = (
                f"Instagram permission denied (Code #{code}): {safe_msg}. "
                "Ensure your token has 'instagram_content_publish', 'pages_show_list', and 'pages_read_engagement' permissions."
            )
            return InstagramPermissionError(
                safe_msg, code=code, subcode=subcode, error_type=err_type,
                user_message=user_msg, fbtrace_id=fbtrace_id, http_status=http_status
            )

        # Code 9007 / Subcode 2207027: Media not ready for publishing
        if code == 9007 or subcode == 2207027 or "media is not ready" in safe_msg.lower():
            user_msg = f"Instagram media container is not ready for publishing (Code #{code}, Subcode #{subcode}): {safe_msg}"
            return InstagramProcessingFailedError(
                safe_msg, code=code, subcode=subcode, error_type=err_type,
                user_message=user_msg, fbtrace_id=fbtrace_id, http_status=http_status
            )

        # Code 100 / Unsupported request / Invalid Object ID
        if code == 100 and ("object with id" in safe_msg.lower() or "unsupported post request" in safe_msg.lower()):
            user_msg = "Invalid Instagram Account ID. Ensure you have configured the Instagram Professional (Creator/Business) Account ID."
            return InstagramConfigError(
                safe_msg, code=code, subcode=subcode, error_type=err_type,
                user_message=user_msg, fbtrace_id=fbtrace_id, http_status=http_status
            )

        # Video URL accessibility error
        if "url could not be reached" in safe_msg.lower() or "failed to download" in safe_msg.lower() or "url is not accessible" in safe_msg.lower():
            user_msg = f"Instagram rejected video URL as inaccessible to Meta servers: {safe_msg}"
            return InstagramVideoUrlError(
                safe_msg, code=code, subcode=subcode, error_type=err_type,
                user_message=user_msg, fbtrace_id=fbtrace_id, http_status=http_status
            )

        return InstagramAPIError(
            safe_msg, code=code, subcode=subcode, error_type=err_type,
            user_message=safe_msg, fbtrace_id=fbtrace_id, http_status=http_status
        )

    fallback_text = redact(raw_text if raw_text is not None else str(res_data))
    return InstagramAPIError(fallback_text, user_message=default_msg, http_status=http_status)


def is_public_url(url: str) -> bool:
    """Validates that a URL is a public web address accessible to Meta's servers."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # Disallow loopback and private network ranges
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return False
        if host.startswith(("192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.30.", "172.31.")):
            return False
        return True
    except Exception:
        return False


def log_instagram_audit(level: str, message: str, metadata: Optional[Dict[str, Any]] = None):
    """Writes structured diagnostic log entry to SystemLog."""
    try:
        with SessionLocal() as db:
            meta_json = json.dumps(metadata) if metadata else None
            db.add(SystemLog(
                module="instagram_publisher",
                level=level,
                message=message,
                metadata_json=meta_json
            ))
            db.commit()
    except Exception as e:
        logger.warning(f"Failed to record Instagram audit log: {e}")


# ==========================================
# Instagram Publishing Client
# ==========================================

class InstagramPublishClient:
    """Enterprise-grade Meta Graph API client for Instagram Professional Reels publishing."""

    def _get_auth(self) -> Dict[str, str]:
        token = vault.get_secret("meta", "system_user_token") or vault.get_secret("meta", "page_access_token")
        ig_id = vault.get_secret("meta", "instagram_account_id")

        if not token or not token.strip():
            raise InstagramConfigError(
                "Meta Access Token is not set in vault. Please configure credentials in Settings.",
                code=401,
                user_message="Meta Access Token is missing."
            )

        token = token.strip()

        # If instagram_account_id is missing, attempt auto-discovery from token & linked page
        if not ig_id or not ig_id.strip():
            resolved_id = self._auto_discover_instagram_account_id(token)
            if resolved_id:
                ig_id = resolved_id
                vault.set_secret("meta", "instagram_account_id", ig_id)
                logger.info(f"Auto-resolved Instagram Professional Account ID: {ig_id}")
            else:
                raise InstagramConfigError(
                    "Instagram Account ID is not configured in Settings and could not be auto-discovered. "
                    "Ensure your Facebook Page is linked to an Instagram Professional Account.",
                    code=400,
                    user_message="Instagram Account ID is missing in Settings."
                )

        return {"token": token, "ig_id": ig_id.strip()}

    def _auto_discover_instagram_account_id(self, token: str) -> Optional[str]:
        """Discovers the linked Instagram Business/Creator Account ID from the linked Facebook Page."""
        try:
            page_id = vault.get_secret("meta", "page_id")
            headers = {"Authorization": f"Bearer {token}"}
            # 1. Check from configured page_id
            if page_id and page_id != "me":
                res = meta_transport.get(
                    f"{GRAPH_URL}/{page_id}",
                    headers=headers,
                    params={"fields": "instagram_business_account"},
                    timeout=(meta_transport.connect_timeout, 10.0)
                ).json()
                if "instagram_business_account" in res and "id" in res["instagram_business_account"]:
                    return str(res["instagram_business_account"]["id"])

            # 2. Check from /me/accounts
            accounts = meta_transport.get(
                f"{GRAPH_URL}/me/accounts",
                headers=headers,
                params={"fields": "id,name,instagram_business_account"},
                timeout=(meta_transport.connect_timeout, 10.0)
            ).json().get("data", [])

            for p in accounts:
                if "instagram_business_account" in p and "id" in p["instagram_business_account"]:
                    return str(p["instagram_business_account"]["id"])

        except Exception as e:
            logger.debug(f"Auto-discovery of Instagram account failed: {e}")
        return None

    # ==========================================
    # Stage 1: Container Creation
    # ==========================================
    def create_media_container(
        self,
        ig_id: str,
        token: str,
        caption: str,
        video_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Stage 1: Creates an Instagram Reel media container.
        Supports:
        - Resumable direct upload (when video_url is None)
        - Direct public video URL ingestion (when video_url is a public address)
        """
        headers = {"Authorization": f"Bearer {token}"}
        data = {
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": "true"
        }

        if video_url:
            if not is_public_url(video_url):
                raise InstagramVideoUrlError(
                    f"Video URL '{video_url}' is not a publicly accessible HTTP/HTTPS URL. "
                    "Meta requires a public URL or resumable binary transfer.",
                    code=400,
                    user_message="Video URL is not accessible to Meta servers (must be public HTTP/HTTPS, not localhost/private IP)."
                )
            data["video_url"] = video_url
            logger.info(f"Stage 1: Initializing Instagram Reel container via public URL for Account ID '{ig_id}'")
        else:
            data["upload_type"] = "resumable"
            logger.info(f"Stage 1: Initializing Instagram Reel container via Resumable Protocol for Account ID '{ig_id}'")

        init_res = None
        for attempt in range(1, 4):
            try:
                res = meta_transport.post(
                    f"{GRAPH_URL}/{ig_id}/media",
                    headers=headers,
                    data=data,
                    timeout=(meta_transport.connect_timeout, 30.0)
                )
                init_res = res.json()
                break
            except requests.RequestException as e:
                safe_err = redact_token(str(e))
                logger.warning(f"Container creation network attempt {attempt}/3 failed: {safe_err}")
                if attempt == 3:
                    raise InstagramUploadFailedError(f"Network error creating Instagram container: {e}")
                time.sleep(2 ** attempt)

        if "id" not in init_res:
            err = parse_instagram_error(init_res, "Instagram container creation failed")
            log_instagram_audit(
                "ERROR",
                f"Instagram container creation failed: {err.user_message}",
                {"stage": "create_container", "error_code": err.code, "subcode": err.subcode, "raw_error": init_res.get("error")}
            )
            raise err

        container_id = str(init_res["id"])
        upload_url = init_res.get("uri")
        if not upload_url and not video_url:
            upload_url = f"https://rupload.facebook.com/ig-api-upload/{API_VERSION}/{container_id}"
        logger.info(f"Stage 1 complete: Container created. ID: {container_id}, Upload type: {'url' if video_url else 'resumable'}")
        log_instagram_audit(
            "INFO",
            f"Instagram Reel container created: {container_id}",
            {"stage": "create_container", "container_id": container_id, "upload_type": "url" if video_url else "resumable"}
        )
        return {"container_id": container_id, "upload_url": upload_url}

    # ==========================================
    # Pre-Upload Video Normalization & Validation
    # ==========================================
    def optimize_video_for_reels(self, video_path: Path) -> Tuple[Path, bool]:
        """
        Validates and normalizes video asset to strictly satisfy Meta Reels specifications:
        - 1080x1920 9:16 portrait
        - H.264 High profile, 30 FPS constant frame rate (CFR), closed GOP
        - AAC stereo audio 48kHz (generates synthetic silent audio if source has none)
        - Faststart (+faststart moov atom placed before mdat)
        - Clamped duration between 3.0s and 60.0s
        - Dynamic bitrate capping file size safely <= 55MB (under 60MB limit)
        Returns (optimized_video_path, is_temp_file).
        """
        from backend.app.publishers.instagram.validator import instagram_video_validator
        from backend.app.publishers.instagram.normalizer import instagram_video_normalizer, InstagramNormalizationError

        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        # Check if already strictly compliant across all mandatory properties
        val = instagram_video_validator.validate(video_path)
        is_compliant = (
            val.is_valid
            and val.details.get("resolution") == "1080x1920"
            and val.details.get("is_constant_frame_rate") is True
            and val.details.get("video_codec") in ("h264", "avc1")
            and val.details.get("pixel_format") == "yuv420p"
            and val.details.get("audio_present") is True
            and val.details.get("audio_codec") == "aac"
            and val.details.get("faststart_valid") is True
            and 3.0 <= val.details.get("duration_sec", 0.0) <= 60.00
            and val.details.get("file_size_bytes", 0) <= 60 * 1000 * 1000
        )
        if is_compliant:
            logger.info(f"Video {video_path.name} strictly satisfies all Meta Reels specifications. Using directly without re-encoding.")
            return video_path, False

        logger.info(
            f"Video {video_path.name} requires normalization for Instagram Reels: "
            f"Reasons: {val.errors + val.warnings}"
        )
        try:
            normalized = instagram_video_normalizer.normalize(video_path, force_render=True)
        except Exception as e:
            logger.error(f"Normalization failed for {video_path.name}: {e}")
            raise InstagramValidationError(
                f"Video normalization failed: {e}",
                code=400,
                error_type="NormalizationError",
                user_message=f"Video normalization failed: {e}"
            )

        # Validate final output
        final_val = instagram_video_validator.validate(normalized)
        if not final_val.is_valid:
            logger.error(f"Normalized video failed validation: {final_val.errors}")
            raise InstagramValidationError(
                f"Video normalization failed to satisfy Meta Reels specifications: {final_val.summary()}",
                code=400,
                error_type="ValidationError",
                user_message=final_val.summary()
            )

        logger.info(
            f"Instagram video successfully normalized and validated: {normalized.name} "
            f"({final_val.details.get('file_size_mb')} MB, {final_val.details.get('duration_sec')}s)"
        )
        return normalized, True

    # ==========================================
    # Stage 2: Video Binary Transfer (Chunked Resumable)
    # ==========================================
    def transfer_binary(
        self,
        upload_url: str,
        video_path: Path,
        token: str,
        max_retries: int = 3,
        chunk_size: int = 4 * 1024 * 1024,
        container_id: Optional[str] = None
    ) -> bool:
        """
        Stage 2: Streams video binary to Meta's rupload endpoint using chunked resumable protocol.
        Uploads chunks with exact byte offsets. Strictly verifies server acknowledgement.
        Never treats Graph API container status as a substitute for binary upload confirmation.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        file_size = video_path.stat().st_size
        if file_size <= 0:
            raise ValueError(f"Video file is empty: {video_path}")

        logger.info(f"Stage 2: Transferring binary ({file_size} bytes, {chunk_size // 1024 // 1024}MB chunks) to Instagram upload endpoint")

        current_offset = 0

        # Helper to query rupload server acknowledged offset
        def get_server_offset() -> Optional[int]:
            try:
                chk = meta_transport.get(
                    upload_url,
                    headers={"Authorization": f"OAuth {token}"},
                    timeout=(meta_transport.connect_timeout, 15.0),
                    max_retries=2
                )
                if chk.status_code == 200:
                    data = chk.json()
                    if isinstance(data, dict) and "offset" in data:
                        return int(data["offset"])
            except Exception as ex:
                logger.debug(f"Failed to query rupload offset: {redact_token(str(ex))}")
            return None

        # Check initial offset if resuming
        initial_srv_offset = get_server_offset()
        if initial_srv_offset is not None and 0 < initial_srv_offset < file_size:
            logger.info(f"Resuming upload from rupload acknowledged offset: {initial_srv_offset}/{file_size}")
            current_offset = initial_srv_offset
        elif initial_srv_offset is not None and initial_srv_offset >= file_size:
            logger.info(f"Rupload server already has full file ({initial_srv_offset}/{file_size} bytes).")
            return True

        with open(video_path, "rb") as f:
            while current_offset < file_size:
                f.seek(current_offset)
                chunk_data = f.read(chunk_size)
                chunk_len = len(chunk_data)

                success = False
                last_error = None

                for attempt in range(1, max_retries + 1):
                    headers = {
                        "Authorization": f"OAuth {token}",
                        "offset": str(current_offset),
                        "file_size": str(file_size),
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(chunk_len)
                    }

                    try:
                        t0 = time.time()
                        stream_res = meta_transport.post(
                            upload_url,
                            headers=headers,
                            data=chunk_data,
                            timeout=(meta_transport.connect_timeout, 60.0)
                        )
                        dur = time.time() - t0

                        # HTTP 200/201 (completed) or 206 (partial chunk accepted)
                        if stream_res.status_code in (200, 201, 206):
                            res_offset = None
                            try:
                                r_json = stream_res.json()
                                if isinstance(r_json, dict) and "offset" in r_json:
                                    res_offset = int(r_json["offset"])
                            except Exception:
                                pass

                            if res_offset is not None and res_offset > current_offset:
                                current_offset = res_offset
                            else:
                                current_offset += chunk_len

                            logger.info(
                                f"Uploaded chunk offset {current_offset - chunk_len} ({chunk_len} bytes) "
                                f"in {dur:.1f}s -> HTTP {stream_res.status_code} (new offset: {current_offset}/{file_size})"
                            )
                            success = True
                            break

                        # Parse Meta error response
                        err_json = None
                        try:
                            err_json = stream_res.json()
                            last_error = parse_instagram_error(
                                err_json,
                                f"HTTP {stream_res.status_code} during Instagram binary chunk transfer",
                                http_status=stream_res.status_code
                            )
                        except Exception:
                            last_error = InstagramUploadFailedError(
                                f"Instagram binary upload returned HTTP {stream_res.status_code}: {stream_res.text[:200]}",
                                http_status=stream_res.status_code
                            )

                        is_non_retriable = False
                        if err_json and isinstance(err_json, dict):
                            debug_info = err_json.get("debug_info", {})
                            if debug_info.get("retriable") is False:
                                is_non_retriable = True

                        if is_non_retriable or isinstance(last_error, (InstagramTokenExpiredError, InstagramPermissionError, InstagramProcessingFailedError, InstagramEncodingRejectionError)):
                            diag = {
                                "stage": "transfer_binary",
                                "container_id": container_id,
                                "expected_file_size": file_size,
                                "actual_transferred_bytes": current_offset,
                                "acknowledged_offset": res_offset if 'res_offset' in locals() else None,
                                "http_status": stream_res.status_code,
                                "meta_debug_info": err_json.get("debug_info") if err_json and isinstance(err_json, dict) else None,
                                "error_type": type(last_error).__name__,
                                "timestamp": datetime.utcnow().isoformat()
                            }
                            log_instagram_audit(
                                "ERROR",
                                f"Non-retriable Meta error during binary transfer: {last_error.user_message}",
                                diag
                            )
                            raise last_error

                        logger.warning(
                            f"Chunk offset {current_offset} attempt {attempt}/{max_retries} failed: "
                            f"HTTP {stream_res.status_code} - {last_error.user_message}"
                        )

                    except (requests.ConnectionError, requests.Timeout) as e:
                        safe_e = redact_token(str(e))
                        last_error = InstagramUploadFailedError(f"Network error during Instagram binary chunk transfer: {safe_e}")
                        logger.warning(f"Chunk offset {current_offset} attempt {attempt}/{max_retries} network error: {safe_e}")

                    # On failure or retry, query rupload server to find out what was actually acknowledged
                    srv_offset = get_server_offset()
                    if srv_offset is not None:
                        if srv_offset >= file_size:
                            logger.info(f"Server verified full file receipt ({srv_offset}/{file_size} bytes).")
                            current_offset = srv_offset
                            success = True
                            break
                        elif srv_offset > current_offset:
                            logger.info(f"Advancing offset to server acknowledged: {srv_offset} (was {current_offset})")
                            current_offset = srv_offset
                            f.seek(current_offset)
                            chunk_data = f.read(chunk_size)
                            chunk_len = len(chunk_data)

                    time.sleep(2 ** attempt)

                if not success:
                    # Final verification with rupload server before giving up
                    srv_offset = get_server_offset()
                    if srv_offset is not None and srv_offset >= file_size:
                        logger.info(f"Server confirmed complete receipt ({srv_offset}/{file_size} bytes).")
                        current_offset = srv_offset
                        break

                    log_instagram_audit(
                        "ERROR",
                        f"Instagram binary chunk transfer failed at offset {current_offset}/{file_size}: {last_error.user_message if last_error else 'Unknown'}",
                        {"stage": "transfer_binary", "offset": current_offset, "file_size": file_size}
                    )
                    raise last_error or InstagramUploadFailedError(
                        f"Instagram binary transfer failed at offset {current_offset}/{file_size}"
                    )

        # Final sanity check: acknowledged bytes must equal or exceed file size
        if current_offset < file_size:
            srv_offset = get_server_offset()
            if srv_offset is None or srv_offset < file_size:
                raise InstagramUploadFailedError(
                    f"Binary upload terminated prematurely: transferred {current_offset} of {file_size} bytes. "
                    "Server did not acknowledge full file."
                )

        log_instagram_audit(
            "INFO",
            f"Instagram binary transfer completed ({file_size} bytes).",
            {"stage": "transfer_binary", "bytes": file_size}
        )
        return True

    # ==========================================
    # Stage 3: Container Processing Status Polling & Querying
    # ==========================================
    def query_container_status(
        self,
        container_id: str,
        token: Optional[str] = None,
        include_copyright: bool = True
    ) -> Dict[str, Any]:
        """
        Queries the current status of an Instagram media container using Meta Graph API.
        Guarantees zero access token leakage in logs/exceptions.
        Explicitly handles all statuses: IN_PROGRESS, FINISHED, PUBLISHED, ERROR, EXPIRED.
        """
        if not token:
            auth = self._get_auth()
            token = auth["token"]

        headers = {"Authorization": f"Bearer {token}"}
        status_url = f"{GRAPH_URL}/{container_id}"

        # 1. First attempt full fields query if include_copyright is True
        data = None
        copyright_status = None
        if include_copyright:
            try:
                res = meta_transport.get(
                    status_url,
                    headers=headers,
                    params={"fields": "id,status_code,status,copyright_check_status"},
                    timeout=(meta_transport.connect_timeout, 15.0),
                    max_retries=2
                )
                if res.status_code == 200:
                    data = res.json()
                    copyright_status = data.get("copyright_check_status")
                elif res.status_code == 500:
                    # Meta Graph API returns HTTP 500 Code 1 OAuthException when querying copyright_check_status
                    # on un-evaluated or in-progress containers. Safely fall back to core status fields.
                    logger.debug(f"Container {container_id} copyright_check_status query returned HTTP 500. Falling back to status_code,status.")
            except requests.RequestException as e:
                safe_err = redact_token(str(e))
                logger.warning(f"Transient network error querying container {container_id} full fields: {safe_err}")

        # 2. Fallback or primary query for status_code,status
        if data is None or "status_code" not in data:
            try:
                res = meta_transport.get(
                    status_url,
                    headers=headers,
                    params={"fields": "id,status_code,status"},
                    timeout=(meta_transport.connect_timeout, 20.0),
                    max_retries=3
                )
                data = res.json()
            except (requests.Timeout, requests.ConnectionError, MetaNetworkTimeoutError) as e:
                safe_err = redact_token(str(e))
                logger.warning(f"Transient timeout querying container {container_id} status: {safe_err}")
                raise InstagramNetworkTimeoutError(f"Network error querying container {container_id}: {safe_err}")
            except requests.RequestException as e:
                safe_err = redact_token(str(e))
                logger.warning(f"Transient error querying container {container_id} status: {safe_err}")
                raise InstagramAPIError(f"Network error querying container {container_id}: {safe_err}")

        if "error" in data:
            err = parse_instagram_error(data, f"Status check failed for container {container_id}")
            raise err

        status_code = str(data.get("status_code", "")).upper()
        status_text = data.get("status", "")

        return {
            "id": str(data.get("id", container_id)),
            "status_code": status_code,
            "status": status_text,
            "copyright_check_status": copyright_status,
            "is_in_progress": status_code == "IN_PROGRESS",
            "is_finished": status_code == "FINISHED",
            "is_published": status_code == "PUBLISHED",
            "is_error": status_code == "ERROR",
            "is_expired": status_code == "EXPIRED",
            "raw_response": data
        }

    def poll_container_status(
        self,
        container_id: str,
        token: str,
        max_wait_sec: int = 300,
        poll_interval: float = 60.0,
        poll_interval_sec: Optional[float] = None,
        max_checks: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Stage 3: Polls container status following Meta's official Graph API guidance:
        - Poll approximately once per minute (default 60s).
        - Maximum initial inline window: five minutes (300s, ~5 checks).
        - Strictly awaits FINISHED before returning success.
        - If still IN_PROGRESS when initial 5-minute deadline is reached, moves to RECONCILING
          and raises InstagramContainerProcessingInProgress so background reconciliation continues
          without blocking the server or throwing a premature upload failure.
        - Raises InstagramProcessingFailedError on ERROR and InstagramUploadFailedError on EXPIRED.
        """
        if poll_interval_sec is not None:
            poll_interval = poll_interval_sec

        logger.info(f"Stage 3: Controlled Meta polling for Instagram container {container_id} (interval={poll_interval}s, max_inline_wait={max_wait_sec}s)...")
        start_time = time.time()
        checks = 0
        last_status_data = None

        # Initial wait before first probe (skip if in fast unit test mode)
        if poll_interval > 5.0 and max_checks is None:
            time.sleep(15.0)

        while True:
            checks += 1
            if max_checks is not None and checks > max_checks:
                break
            if max_checks is None and (time.time() - start_time) >= max_wait_sec:
                break

            try:
                status_info = self.query_container_status(container_id, token=token, include_copyright=False)
                last_status_data = status_info
            except (requests.RequestException, InstagramNetworkTimeoutError) as e:
                safe_err = redact_token(str(e))
                logger.warning(f"Transient error querying Instagram container {container_id}: {safe_err}")
                if poll_interval > 0:
                    time.sleep(poll_interval)
                continue

            code = status_info["status_code"]
            status_text = status_info["status"]

            if code == "FINISHED":
                logger.info(f"Stage 3 complete: Container {container_id} is FINISHED and ready to publish.")
                log_instagram_audit("INFO", f"Container {container_id} processing FINISHED.", {"container_id": container_id, "checks": checks})
                return status_info["raw_response"]

            if code == "PUBLISHED":
                logger.info(f"Stage 3: Container {container_id} is already PUBLISHED on Instagram.")
                return status_info["raw_response"]

            if code == "ERROR":
                err_msg = f"Instagram container transcode error: {status_text or 'Video failed Meta transcoding requirements'}"
                log_instagram_audit("ERROR", err_msg, {"container_id": container_id, "status": status_text, "checks": checks})
                raise InstagramProcessingFailedError(err_msg, user_message=err_msg)

            if code == "EXPIRED":
                err_msg = f"Instagram container {container_id} expired before publishing."
                log_instagram_audit("ERROR", err_msg, {"container_id": container_id, "checks": checks})
                raise InstagramUploadFailedError(err_msg, user_message=err_msg)

            if code == "IN_PROGRESS":
                logger.info(f"Container {container_id} status check #{checks}: IN_PROGRESS ({status_text}). Awaiting next check in {poll_interval}s...")

            if poll_interval > 0:
                time.sleep(poll_interval)

        # Inline window completed without reaching terminal status
        waited = time.time() - start_time
        if last_status_data and last_status_data["is_in_progress"]:
            logger.info(f"Container {container_id} is confirmed IN_PROGRESS after {checks} inline checks ({waited:.1f}s). Handing off to background reconciliation.")
            raise InstagramContainerProcessingInProgress(container_id, last_status_data, checks=checks, waited_sec=waited)

        # Timeout reached without FINISHED status
        timeout_msg = (
            f"Instagram container {container_id} timed out after {checks} checks ({waited:.1f}s) without reaching FINISHED state. "
            "Publishing aborted to prevent publishing an incomplete media container."
        )
        log_instagram_audit("ERROR", timeout_msg, {"container_id": container_id, "waited_sec": waited, "checks": checks})
        raise InstagramProcessingTimeoutError(timeout_msg, user_message=timeout_msg)

    # ==========================================
    # Stage 4: Media Publishing
    # ==========================================
    def publish_container(
        self,
        ig_id: str,
        container_id: str,
        token: str
    ) -> str:
        """
        Stage 4: Dispatches media_publish for a verified FINISHED container.
        Returns the published media_id.
        """
        # Pre-flight check: ensure container is not IN_PROGRESS
        status_info = self.query_container_status(container_id, token=token, include_copyright=False)
        if status_info["status_code"] == "IN_PROGRESS":
            raise InstagramProcessingFailedError(
                f"Cannot publish container {container_id}: container is still IN_PROGRESS.",
                user_message=f"Container {container_id} is still transcoding on Meta servers."
            )
        if status_info["status_code"] == "PUBLISHED":
            logger.info(f"Container {container_id} is already PUBLISHED. Returning existing container ID.")
            return container_id

        logger.info(f"Stage 4: Publishing Instagram Reel for Container ID {container_id}")
        headers = {"Authorization": f"Bearer {token}"}
        pub_res = None
        for attempt in range(1, 3):
            try:
                res = meta_transport.post(
                    f"{GRAPH_URL}/{ig_id}/media_publish",
                    headers=headers,
                    data={"creation_id": container_id},
                    timeout=(meta_transport.connect_timeout, 45.0)
                )
                pub_res = res.json()
                break
            except requests.RequestException as e:
                safe_err = redact_token(str(e))
                logger.warning(f"media_publish attempt {attempt}/2 failed: {safe_err}")
                if attempt == 2:
                    raise InstagramUploadFailedError(f"Network error executing Instagram media_publish: {safe_err}")
                time.sleep(3)

        if "id" not in pub_res:
            err = parse_instagram_error(pub_res, "Instagram media_publish failed")
            log_instagram_audit(
                "ERROR",
                f"Instagram media_publish failed: {err.user_message}",
                {"container_id": container_id, "error_code": err.code, "subcode": err.subcode, "raw_error": pub_res.get("error")}
            )
            raise err

        media_id = str(pub_res["id"])
        logger.info(f"Stage 4 complete: Instagram media published successfully. Media ID: {media_id}")
        log_instagram_audit(
            "INFO",
            f"Instagram Reel media published: {media_id}",
            {"container_id": container_id, "media_id": media_id}
        )
        return media_id

    # ==========================================
    # Stage 5: Final Media Verification
    # ==========================================
    def verify_media_published(
        self,
        media_id: str,
        token: str
    ) -> Dict[str, Any]:
        """
        Stage 5: Confirms the published media exists on Instagram and extracts canonical permalink.
        """
        logger.info(f"Stage 5: Verifying published Instagram media object for Media ID {media_id}")
        headers = {"Authorization": f"Bearer {token}"}
        try:
            res = meta_transport.get(
                f"{GRAPH_URL}/{media_id}",
                headers=headers,
                params={"fields": "id,media_type,permalink,shortcode,timestamp"},
                timeout=(meta_transport.connect_timeout, 15.0)
            )
            data = res.json()
            if "id" in data:
                permalink = data.get("permalink") or f"https://www.instagram.com/reel/{media_id}"
                logger.info(f"Stage 5 complete: Verified published media. Permalink: {permalink}")
                log_instagram_audit(
                    "INFO",
                    f"Instagram media verified live: {permalink}",
                    {"media_id": media_id, "permalink": permalink, "shortcode": data.get("shortcode")}
                )
                return {
                    "media_id": media_id,
                    "permalink": permalink,
                    "shortcode": data.get("shortcode")
                }
        except Exception as e:
            logger.warning(f"Non-fatal warning verifying Instagram permalink for {media_id}: {e}")

        fallback_url = f"https://www.instagram.com/reel/{media_id}"
        return {"media_id": media_id, "permalink": fallback_url, "shortcode": None}

    # ==========================================
    # Full Publishing Workflow
    # ==========================================
    def publish_reel(
        self,
        video_path: Optional[Union[str, Path]] = None,
        caption: str = "",
        video_url: Optional[str] = None,
        stage_callback: Optional[Callable[[str], None]] = None,
        on_container_created: Optional[Callable[[str], None]] = None,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        max_poll_checks: int = DEFAULT_MAX_POLL_CHECKS,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Executes the complete 5-stage Instagram Reels publishing workflow:
        Stage 1: Container Creation
        Stage 2: Binary Streaming (if resumable)
        Stage 3: Container Processing Status Polling (strictly awaits FINISHED, bounded 5-min window)
        Stage 4: Final Media Publishing
        Stage 5: Final Media Verification
        """
        if not video_url and not video_path:
            raise ValueError("Either video_path or video_url must be provided.")

        # Guard against accidental caller overrides using legacy values (5s/60 checks)
        eff_interval = float(poll_interval_sec)
        eff_checks = int(max_poll_checks)
        if eff_interval >= 1.0:
            eff_interval = max(DEFAULT_POLL_INTERVAL_SEC, eff_interval)
            eff_checks = min(DEFAULT_MAX_POLL_CHECKS, eff_checks) if eff_checks > 1 else eff_checks

        vpath = None
        upload_path = None
        is_temp = False
        if video_path is not None:
            vpath = Path(video_path)
            if not vpath.exists():
                raise FileNotFoundError(f"Video file missing: {vpath}")
            # Pre-upload optimization for Reels specs and fast edge upload
            upload_path, is_temp = self.optimize_video_for_reels(vpath)

        auth = self._get_auth()
        token = auth["token"]
        ig_id = auth["ig_id"]

        try:
            # Stage 1: Container Creation
            if stage_callback:
                stage_callback("upload_initialized")
            container_data = self.create_media_container(ig_id, token, caption, video_url=video_url)
            container_id = container_data["container_id"]
            upload_url = container_data.get("upload_url")

            if on_container_created:
                try:
                    on_container_created(container_id)
                except Exception as e:
                    logger.debug(f"on_container_created callback notice: {e}")

            # Stage 2: Binary Streaming (if resumable)
            if upload_url:
                if not upload_path:
                    raise ValueError("Resumable upload initialized but no local video_path was provided for binary streaming.")
                if stage_callback:
                    stage_callback("video_transferring")
                self.transfer_binary(upload_url, upload_path, token, max_retries=max_retries, container_id=container_id)
                if stage_callback:
                    stage_callback("transfer_completed")

            # Stage 3: Container Processing Polling
            if stage_callback:
                stage_callback("instagram_processing")
            self.poll_container_status(
                container_id,
                token,
                max_wait_sec=DEFAULT_MAX_MONITORING_WINDOW_SEC,
                poll_interval=eff_interval,
                max_checks=eff_checks
            )

            # Stage 4: Media Publishing
            if stage_callback:
                stage_callback("publishing")
            media_id = self.publish_container(ig_id, container_id, token)

            # Stage 5: Final Media Verification
            verified = self.verify_media_published(media_id, token)

            if stage_callback:
                stage_callback("published")

            return {
                "platform": "instagram",
                "post_id": media_id,
                "url": verified["permalink"],
                "container_id": container_id,
                "shortcode": verified.get("shortcode"),
                "published_at": datetime.utcnow().isoformat()
            }
        finally:
            if is_temp and upload_path and upload_path.exists():
                try:
                    upload_path.unlink()
                    logger.debug(f"Cleaned up temporary Instagram Reel copy: {upload_path.name}")
                except Exception as e:
                    logger.debug(f"Failed to remove temp file {upload_path}: {e}")

    # ==========================================
    # Diagnostic / Test Connection
    # ==========================================
    def test_connection(self) -> Dict[str, Any]:
        """
        Performs non-publishing diagnostic audit of Instagram connection:
        1. Token validity and expiration check
        2. Instagram Professional Account ID resolution and name
        3. Permission inspection (instagram_content_publish, pages_show_list, etc.)
        """
        token = vault.get_secret("meta", "system_user_token") or vault.get_secret("meta", "page_access_token")
        ig_id = vault.get_secret("meta", "instagram_account_id")

        if not token or not token.strip():
            return {
                "configured": False,
                "authenticated": False,
                "message": "Meta Access Token is not set in vault.",
                "permissions": [],
                "missing_permissions": ["instagram_content_publish", "pages_show_list"]
            }

        token = token.strip()
        required_perms = ["instagram_content_publish", "pages_show_list"]

        headers = {"Authorization": f"Bearer {token}"}
        try:
            # 1. Test /me token validity
            me_res = meta_transport.get(
                f"{GRAPH_URL}/me",
                headers=headers,
                params={"fields": "id,name"},
                timeout=(meta_transport.connect_timeout, 10.0)
            ).json()

            if "error" in me_res:
                err = parse_instagram_error(me_res, "Token validation failed")
                log_instagram_audit("WARNING", f"Instagram connection diagnostic failed: {err.user_message}", {"error_code": err.code, "subcode": err.subcode})
                return {
                    "configured": True,
                    "authenticated": False,
                    "error_code": err.code,
                    "subcode": err.subcode,
                    "message": err.user_message,
                    "permissions": [],
                    "missing_permissions": required_perms
                }

            # 2. Check permissions
            perms_res = meta_transport.get(
                f"{GRAPH_URL}/me/permissions",
                headers=headers,
                timeout=(meta_transport.connect_timeout, 10.0)
            ).json()

            granted = [
                p.get("permission")
                for p in perms_res.get("data", [])
                if p.get("status") == "granted"
            ]
            missing = [p for p in required_perms if p not in granted]

            # 3. Resolve / verify Instagram account
            if not ig_id:
                ig_id = self._auto_discover_instagram_account_id(token)

            ig_name = None
            ig_username = None
            if ig_id:
                ig_res = meta_transport.get(
                    f"{GRAPH_URL}/{ig_id}",
                    headers=headers,
                    params={"fields": "id,name,username"},
                    timeout=(meta_transport.connect_timeout, 10.0)
                ).json()
                if "id" in ig_res:
                    ig_name = ig_res.get("name")
                    ig_username = ig_res.get("username")

            is_ready = len(missing) == 0 and (ig_id is not None)

            log_instagram_audit(
                "INFO" if is_ready else "WARNING",
                f"Instagram connection diagnostic: {'Ready' if is_ready else 'Action required'} (Account: @{ig_username or ig_id or 'none'})",
                {"ig_id": ig_id, "username": ig_username, "granted": granted, "missing": missing}
            )

            return {
                "configured": True,
                "authenticated": True,
                "account_id": ig_id,
                "instagram_account_id": ig_id,
                "username": ig_username,
                "account_username": ig_username,
                "account_name": ig_name,
                "name": ig_name,
                "permissions": granted,
                "missing_permissions": missing,
                "message": (
                    f"Connected to Instagram Professional Account: @{ig_username}"
                    if is_ready else
                    f"Permissions or Account ID missing: {missing}"
                )
            }

        except Exception as e:
            return {
                "configured": True,
                "authenticated": False,
                "message": f"Connection diagnostic failed: {e}",
                "permissions": [],
                "missing_permissions": required_perms
            }


instagram_client = InstagramPublishClient()
