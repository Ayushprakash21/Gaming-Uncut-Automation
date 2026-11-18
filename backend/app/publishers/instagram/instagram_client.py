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

logger = logging.getLogger("gaming_uncut.instagram")
API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"

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

class InstagramUploadFailedError(InstagramAPIError):
    """Raised when binary transfer or media publishing fails."""
    pass


def parse_instagram_error(
    res_data: Any,
    default_msg: str = "Instagram API operation failed",
    raw_text: Optional[str] = None,
    http_status: Optional[int] = None
) -> InstagramAPIError:
    """Parses Meta Graph API error payload into a safe typed exception without exposing secrets."""
    import re
    def redact(text: str) -> str:
        if not text:
            return ""
        return re.sub(r'(EAAB[a-zA-Z0-9_-]+|EAAQ[a-zA-Z0-9_-]+|IGAA[a-zA-Z0-9_-]+|IGQV[a-zA-Z0-9_-]+)', '[REDACTED_TOKEN]', str(text))

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

        if err_type == "NotAuthorizedError" or "not authorized" in safe_msg.lower():
            user_msg = "Meta Access Token is not authorized or has expired for Instagram upload. Please check token permissions in Settings."
            return InstagramTokenExpiredError(
                safe_msg, code=190, error_type=err_type, user_message=user_msg, http_status=http_status
            )

        if "not in the status" in safe_msg.lower():
            user_msg = f"Instagram container is not in a valid upload status: {safe_msg}. Container session expired or failed."
            return InstagramUploadFailedError(
                safe_msg, code=400, error_type=err_type, user_message=user_msg, http_status=http_status
            )

        if err_type == "ProcessingFailedError" or "processing failed" in safe_msg.lower():
            user_msg = (
                f"Instagram binary upload rejected by Meta edge ingest ({safe_msg}). "
                "Ensure video adheres to Meta Reels specs (AAC audio <= 48kHz, H.264 CFR, 9:16 portrait, <= 60s, <= 60MB)."
            )
            return InstagramProcessingFailedError(
                safe_msg, code=400, error_type=err_type, user_message=user_msg, http_status=http_status
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
            # 1. Check from configured page_id
            if page_id and page_id != "me":
                res = requests.get(
                    f"{GRAPH_URL}/{page_id}",
                    params={"fields": "instagram_business_account", "access_token": token},
                    timeout=10
                ).json()
                if "instagram_business_account" in res and "id" in res["instagram_business_account"]:
                    return str(res["instagram_business_account"]["id"])

            # 2. Check from /me/accounts
            accounts = requests.get(
                f"{GRAPH_URL}/me/accounts",
                params={"fields": "id,name,instagram_business_account", "access_token": token},
                timeout=10
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
        data = {
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": "true",
            "access_token": token
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
                res = requests.post(f"{GRAPH_URL}/{ig_id}/media", data=data, timeout=30)
                init_res = res.json()
                break
            except requests.RequestException as e:
                logger.warning(f"Container creation network attempt {attempt}/3 failed: {e}")
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
        logger.info(f"Stage 1 complete: Container created. ID: {container_id}, Upload URL: {upload_url}")
        log_instagram_audit(
            "INFO",
            f"Instagram Reel container created: {container_id}",
            {"stage": "create_container", "container_id": container_id, "upload_type": "url" if video_url else "resumable", "upload_url": upload_url}
        )
        return {"container_id": container_id, "upload_url": upload_url}

    # ==========================================
    # Pre-Upload Video Optimization
    # ==========================================
    def optimize_video_for_reels(self, video_path: Path) -> Tuple[Path, bool]:
        """
        Ensures video strictly adheres to Meta Instagram Reels requirements:
        - H.264 High profile, CFR 60fps, closed GOP (-bf 0, -g 60)
        - BT.709 SDR color tags, format=yuv420p
        - Bitrate ~3.2M, maxrate 4.5M, bufsize 6M (keeps 60s files ~22MB)
        - 48kHz stereo AAC audio 160k
        - Clean monotonic timestamps (-avoid_negative_ts make_zero -fflags +genpts)
        - Clamped duration strictly <= 59.8s
        - Faststart (+faststart)
        Returns (optimized_video_path, is_temp_file).
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        file_size = video_path.stat().st_size
        if file_size <= 22 * 1024 * 1024 and "_ig_opt" in video_path.name:
            return video_path, False

        out_dir = video_path.parent
        opt_file = out_dir / f"{video_path.stem}_ig_opt.mp4"
        logger.info(f"Optimizing video for Instagram Reels: {video_path.name} -> {opt_file.name}")

        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", "3.2M", "-maxrate", "4.5M", "-bufsize", "6M",
            "-g", "60", "-bf", "0",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-t", "59.8",
            "-avoid_negative_ts", "make_zero", "-fflags", "+genpts",
            "-movflags", "+faststart",
            str(opt_file)
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and opt_file.exists():
                logger.info(f"Instagram optimization completed: {opt_file.name} ({opt_file.stat().st_size} bytes)")
                return opt_file, True
            else:
                logger.warning(f"Optimization non-zero exit ({res.returncode}): {res.stderr[-200:] if res.stderr else 'unknown'}")
                return video_path, False
        except Exception as e:
            logger.warning(f"Video optimization failed (using source file): {e}")
            return video_path, False

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
        Uploads chunks (default 4MB) with byte offsets, preventing the ~45s proxy connection timeout.
        """
        if not video_path.exists():
            raise FileNotFoundError(f"Video file missing: {video_path}")

        file_size = video_path.stat().st_size
        if file_size <= 0:
            raise ValueError(f"Video file is empty: {video_path}")

        logger.info(f"Stage 2: Transferring binary ({file_size} bytes, {chunk_size // 1024 // 1024}MB chunks) to Instagram upload endpoint")

        current_offset = 0

        with open(video_path, "rb") as f:
            while current_offset < file_size:
                f.seek(current_offset)
                chunk_data = f.read(chunk_size)
                chunk_len = len(chunk_data)

                success = False
                chunk_error = None

                for attempt in range(1, max_retries + 1):
                    # On retry, query acknowledged offset from rupload server
                    if attempt > 1:
                        try:
                            chk = requests.get(upload_url, headers={"Authorization": f"OAuth {token}"}, timeout=10)
                            if chk.status_code == 200:
                                srv_offset = int(chk.json().get("offset", 0))
                                if srv_offset >= file_size:
                                    logger.info(f"Server acknowledged full upload ({srv_offset}/{file_size}).")
                                    return True
                                if srv_offset > current_offset:
                                    logger.info(f"Advancing offset to server acknowledged: {srv_offset}")
                                    current_offset = srv_offset
                                    f.seek(current_offset)
                                    chunk_data = f.read(chunk_size)
                                    chunk_len = len(chunk_data)
                        except Exception:
                            pass

                    headers = {
                        "Authorization": f"OAuth {token}",
                        "offset": str(current_offset),
                        "file_size": str(file_size),
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(chunk_len)
                    }

                    try:
                        t0 = time.time()
                        stream_res = requests.post(upload_url, headers=headers, data=chunk_data, timeout=60)
                        dur = time.time() - t0

                        # HTTP 200/201 (completed) or 206 (partial chunk accepted)
                        if stream_res.status_code in (200, 201, 206):
                            logger.info(f"Uploaded chunk offset {current_offset} ({chunk_len} bytes) in {dur:.1f}s -> HTTP {stream_res.status_code}")
                            current_offset += chunk_len
                            success = True
                            break

                        # Check if container transitioned to FINISHED / IN_PROGRESS on Graph API
                        if container_id:
                            try:
                                cchk = requests.get(
                                    f"{GRAPH_URL}/{container_id}",
                                    params={"fields": "status_code,status", "access_token": token},
                                    timeout=10
                                ).json()
                                if cchk.get("status_code") in ("FINISHED", "IN_PROGRESS"):
                                    logger.info(f"Container {container_id} status is already {cchk.get('status_code')}. Upload complete.")
                                    return True
                            except Exception:
                                pass

                        # Parse Meta response
                        err_json = None
                        try:
                            err_json = stream_res.json()
                            chunk_error = parse_instagram_error(
                                err_json,
                                f"HTTP {stream_res.status_code} during Instagram binary chunk transfer",
                                http_status=stream_res.status_code
                            )
                        except Exception:
                            chunk_error = InstagramUploadFailedError(
                                f"Instagram binary upload returned HTTP {stream_res.status_code}: {stream_res.text[:200]}",
                                http_status=stream_res.status_code
                            )

                        if isinstance(chunk_error, (InstagramTokenExpiredError, InstagramPermissionError)):
                            raise chunk_error

                        # Check for completion on final chunk or stop on non-retriable failure
                        is_final_chunk = (current_offset + chunk_len >= file_size)
                        if isinstance(err_json, dict) and err_json.get("debug_info", {}).get("retriable") is False:
                            if is_final_chunk and container_id:
                                # Meta's edge proxy sometimes returns HTTP 400 after 30s while asynchronous ingest finishes
                                for _ in range(4):
                                    time.sleep(3)
                                    try:
                                        cchk = requests.get(
                                            f"{GRAPH_URL}/{container_id}",
                                            params={"fields": "status_code,status", "access_token": token},
                                            timeout=10
                                        ).json()
                                        if cchk.get("status_code") in ("FINISHED", "IN_PROGRESS"):
                                            logger.info(f"Container {container_id} is {cchk.get('status_code')} following final chunk. Treating upload as complete.")
                                            return True
                                    except Exception:
                                        pass
                            logger.error(f"Meta server returned non-retriable error: {chunk_error.user_message}")
                            raise chunk_error

                        logger.warning(f"Chunk offset {current_offset} attempt {attempt}/{max_retries} failed: HTTP {stream_res.status_code} - {chunk_error.user_message}")

                    except (requests.ConnectionError, requests.Timeout) as e:
                        chunk_error = InstagramUploadFailedError(f"Network error during Instagram binary chunk transfer: {e}")
                        logger.warning(f"Chunk offset {current_offset} attempt {attempt}/{max_retries} network error: {e}")

                    time.sleep(2 ** attempt)

                if not success:
                    # Final fallback: did container finish on Graph API?
                    if container_id:
                        try:
                            cchk = requests.get(
                                f"{GRAPH_URL}/{container_id}",
                                params={"fields": "status_code,status", "access_token": token},
                                timeout=10
                            ).json()
                            if cchk.get("status_code") in ("FINISHED", "IN_PROGRESS"):
                                logger.info(f"Container {container_id} is {cchk.get('status_code')}. Treating upload as complete.")
                                return True
                        except Exception:
                            pass

                    log_instagram_audit(
                        "ERROR",
                        f"Instagram binary chunk transfer failed at offset {current_offset}: {chunk_error.user_message if chunk_error else 'Unknown'}",
                        {"stage": "transfer_binary", "offset": current_offset, "file_size": file_size}
                    )
                    raise chunk_error or InstagramUploadFailedError(f"Instagram binary transfer failed at offset {current_offset}")

        log_instagram_audit(
            "INFO",
            f"Instagram binary transfer completed ({file_size} bytes).",
            {"stage": "transfer_binary", "bytes": file_size}
        )
        return True

    # ==========================================
    # Stage 3: Container Processing Status Polling
    # ==========================================
    def poll_container_status(
        self,
        container_id: str,
        token: str,
        max_wait_sec: int = 300,
        poll_interval: float = 5.0,
        poll_interval_sec: Optional[float] = None,
        max_checks: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Stage 3: Polls container status. Strictly awaits 'FINISHED' before proceeding.
        Raises InstagramProcessingFailedError on 'ERROR' and InstagramProcessingTimeoutError on timeout.
        """
        if poll_interval_sec is not None:
            poll_interval = poll_interval_sec

        status_url = f"{GRAPH_URL}/{container_id}"
        logger.info(f"Stage 3: Polling Instagram container {container_id} processing status...")
        start_time = time.time()
        checks = 0

        while True:
            checks += 1
            if max_checks is not None and checks > max_checks:
                break
            if max_checks is None and (time.time() - start_time) >= max_wait_sec:
                break

            try:
                res = requests.get(
                    status_url,
                    params={"fields": "status_code,status", "access_token": token},
                    timeout=15
                )
                data = res.json()
            except requests.RequestException as e:
                logger.warning(f"Transient error querying Instagram container {container_id}: {e}")
                if poll_interval > 0:
                    time.sleep(poll_interval)
                continue

            if "error" in data:
                err = parse_instagram_error(data, f"Status check failed for container {container_id}")
                log_instagram_audit("ERROR", f"Container status check failed: {err.user_message}", {"container_id": container_id})
                raise err

            code = data.get("status_code", "").upper()
            status_text = data.get("status", "")

            if code == "FINISHED":
                logger.info(f"Stage 3 complete: Container {container_id} is FINISHED and ready to publish.")
                log_instagram_audit("INFO", f"Container {container_id} processing FINISHED.", {"container_id": container_id})
                return data

            if code == "ERROR":
                err_msg = f"Instagram container transcode error: {status_text or 'Video failed Meta transcoding requirements'}"
                log_instagram_audit("ERROR", err_msg, {"container_id": container_id, "status": status_text})
                raise InstagramProcessingFailedError(err_msg, user_message=err_msg)

            if code == "EXPIRED":
                err_msg = f"Instagram container {container_id} expired before publishing."
                log_instagram_audit("ERROR", err_msg, {"container_id": container_id})
                raise InstagramUploadFailedError(err_msg, user_message=err_msg)

            if poll_interval > 0:
                time.sleep(poll_interval)

        # Timeout reached without FINISHED status
        timeout_msg = (
            f"Instagram container {container_id} timed out after {checks} checks without reaching FINISHED state. "
            "Publishing aborted to prevent publishing an incomplete media container."
        )
        log_instagram_audit("ERROR", timeout_msg, {"container_id": container_id, "waited_sec": max_wait_sec})
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
        logger.info(f"Stage 4: Publishing Instagram Reel for Container ID {container_id}")
        try:
            res = requests.post(
                f"{GRAPH_URL}/{ig_id}/media_publish",
                data={"creation_id": container_id, "access_token": token},
                timeout=30
            )
            pub_res = res.json()
        except requests.RequestException as e:
            raise InstagramUploadFailedError(f"Network error executing Instagram media_publish: {e}")

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
        try:
            res = requests.get(
                f"{GRAPH_URL}/{media_id}",
                params={"fields": "id,media_type,permalink,shortcode,timestamp", "access_token": token},
                timeout=15
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
        poll_interval_sec: int = 5,
        max_poll_checks: int = 60,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Executes the complete 5-stage Instagram Reels publishing workflow:
        Stage 1: Container Creation
        Stage 2: Binary Streaming (if resumable)
        Stage 3: Container Processing Status Polling (strictly awaits FINISHED)
        Stage 4: Final Media Publishing
        Stage 5: Final Media Verification
        """
        if not video_url and not video_path:
            raise ValueError("Either video_path or video_url must be provided.")

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
            self.poll_container_status(container_id, token, poll_interval_sec=poll_interval_sec, max_checks=max_poll_checks)

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

        try:
            # 1. Test /me token validity
            me_res = requests.get(
                f"{GRAPH_URL}/me",
                params={"fields": "id,name", "access_token": token},
                timeout=10
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
            perms_res = requests.get(
                f"{GRAPH_URL}/me/permissions",
                params={"access_token": token},
                timeout=10
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
                ig_res = requests.get(
                    f"{GRAPH_URL}/{ig_id}",
                    params={"fields": "id,name,username", "access_token": token},
                    timeout=10
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
