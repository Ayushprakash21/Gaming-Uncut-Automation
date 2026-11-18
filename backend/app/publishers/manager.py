"""
Unified Multi-Platform Publishing Manager for Gaming Uncut Automation.
Coordinates YouTube Shorts, Instagram Reels, and Facebook Reels.
Manages retry policies, duplicate prevention, and status verification.
Guarantees structured audit logging to SystemLog for all publishing events and failures.
"""

import logging
import json
import re
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from backend.app.database.session import SessionLocal
from backend.app.models.schema import PublishingJob, PlatformUpload, GeneratedClip, ClipMetadata, SystemLog
from backend.app.publishers.youtube.youtube_client import youtube_client, YouTubeShortsValidationError
from backend.app.publishers.instagram.instagram_client import (
    instagram_client, InstagramAPIError, InstagramTokenExpiredError,
    InstagramPermissionError, InstagramConfigError, InstagramVideoUrlError,
    InstagramProcessingFailedError, InstagramProcessingTimeoutError,
    InstagramUploadFailedError, InstagramRateLimitError as ClientIGRateLimitError
)
from backend.app.publishers.facebook.facebook_client import (
    facebook_client, FacebookAPIError, FacebookTokenExpiredError,
    FacebookPermissionError, FacebookConfigError, FacebookUploadFailedError,
    FacebookRateLimitError as ClientFBRateLimitError
)
from backend.app.publishers.quota_tracker import (
    can_publish, record_upload_success, record_platform_restriction,
    YouTubeQuotaExceededError, YouTubeChannelUploadLimitError,
    InstagramRateLimitError, FacebookRateLimitError
)

logger = logging.getLogger("gaming_uncut.publish_manager")


def redact_secrets(text: Any) -> str:
    """Sanitizes sensitive Meta access tokens, Bearer headers, and secrets from text."""
    if text is None:
        return ""
    s = str(text)
    # Redact Meta tokens (EAAB, EAAQ, IGAA, IGQV, etc.)
    s = re.sub(r'(EAAB[a-zA-Z0-9_-]+|EAAQ[a-zA-Z0-9_-]+|IGAA[a-zA-Z0-9_-]+|IGQV[a-zA-Z0-9_-]+)', '[REDACTED_TOKEN]', s)
    # Redact Authorization: Bearer tokens
    s = re.sub(r'(Bearer\s+)[a-zA-Z0-9_\-\.]+', r'\1[REDACTED_TOKEN]', s, flags=re.IGNORECASE)
    # Redact Google API keys
    s = re.sub(r'AIza[a-zA-Z0-9_\-]{35}', '[REDACTED_API_KEY]', s)
    # Redact client secrets or passwords
    s = re.sub(r'(client_secret["\']?\s*[:=]\s*["\']?)[^"\'\s,]+', r'\1[REDACTED_SECRET]', s, flags=re.IGNORECASE)
    return s


def log_publishing_audit(
    db: Any,
    platform: str,
    publishing_job_id: Optional[int],
    clip_id: Optional[int],
    stage: str,
    error: Any,
    level: str = "ERROR",
    attempts: int = 1,
    error_code: Optional[str] = None,
    user_message: Optional[str] = None
) -> SystemLog:
    """
    Centralized database audit logger for publishing operations.
    Persists structured SystemLog entries for all publishing failures, pauses, and milestones.
    Guarantees that errors in the Publishing Queue are surfaced in the Audit Logs page.
    """
    module = f"{platform.lower()}_publisher" if platform != "system" else "publishing_manager"

    # Extract exception type
    exc_type = type(error).__name__ if error is not None else None

    # Resolve safe message
    raw_msg = user_message or getattr(error, "user_message", None) or (str(error) if error else "Publishing operation failed")
    safe_msg = redact_secrets(raw_msg)

    # Resolve error code
    resolved_code = error_code or getattr(error, "code", None)
    if resolved_code is None and error is not None:
        if isinstance(error, requests.Timeout):
            resolved_code = "NETWORK_TIMEOUT"
        elif isinstance(error, requests.ConnectionError):
            resolved_code = "CONNECTION_ERROR"
        elif isinstance(error, FileNotFoundError):
            resolved_code = "FILE_NOT_FOUND"
        else:
            resolved_code = getattr(error, "error_type", None) or "UPLOAD_FAILED"
    resolved_code_str = str(resolved_code) if resolved_code is not None else None

    # Resolve subcode, error_type, fbtrace_id, http_status
    subcode = getattr(error, "subcode", None)
    err_type = getattr(error, "error_type", None)
    fbtrace_id = getattr(error, "fbtrace_id", None)
    http_status = getattr(error, "http_status", None)

    code_label = f" [Code: {resolved_code_str}]" if resolved_code_str else ""
    summary_message = (
        f"[{platform.capitalize()}] Job #{publishing_job_id} (Clip #{clip_id}) "
        f"attempt {attempts} {level.lower()} at stage '{stage}'{code_label}: {safe_msg}"
    )

    metadata = {
        "platform": platform.lower(),
        "publishing_job_id": publishing_job_id,
        "clip_id": clip_id,
        "attempt": attempts,
        "stage": stage,
        "error_code": resolved_code_str,
        "error_subcode": str(subcode) if subcode is not None else None,
        "error_type": err_type,
        "http_status": http_status,
        "fbtrace_id": fbtrace_id,
        "exception_type": exc_type,
        "user_message": safe_msg,
        "timestamp": datetime.utcnow().isoformat()
    }
    meta_json = redact_secrets(json.dumps(metadata))

    # Safe Deduplication: Avoid writing duplicate record if logged within last 5 seconds
    try:
        recent_cutoff = datetime.utcnow() - timedelta(seconds=5)
        existing = db.query(SystemLog).filter(
            SystemLog.module == module,
            SystemLog.level == level.upper(),
            SystemLog.created_at >= recent_cutoff,
            SystemLog.message == summary_message
        ).first()
        if existing:
            return existing
    except Exception as e:
        logger.debug(f"Deduplication query warning: {e}")

    log_entry = SystemLog(
        module=module,
        level=level.upper(),
        message=summary_message,
        metadata_json=meta_json,
        created_at=datetime.utcnow()
    )
    db.add(log_entry)
    return log_entry


class PublishingManager:
    def execute_job(self, publishing_job_id: int) -> Dict[str, Any]:
        with SessionLocal() as db:
            job = db.query(PublishingJob).filter_by(id=publishing_job_id).first()
            if not job:
                raise ValueError(f"Publishing job #{publishing_job_id} not found.")

            clip = db.query(GeneratedClip).filter_by(id=job.clip_id).first()
            if not clip:
                raise ValueError(f"Clip for job #{publishing_job_id} not found.")

            meta = db.query(ClipMetadata).filter_by(clip_id=clip.id).first()
            video_path = Path(clip.output_path)
            
            # Check file presence
            if not video_path.exists():
                job.status = "failed"
                log_publishing_audit(
                    db, platform="system", publishing_job_id=job.id, clip_id=clip.id,
                    stage="pre_publish_validation", error=None, level="ERROR",
                    attempts=job.attempts + 1, error_code="FILE_NOT_FOUND",
                    user_message=f"Clip video file missing on disk: {video_path}"
                )
                db.commit()
                raise FileNotFoundError(f"Clip video file missing: {video_path}")

            # Concurrency Guard: Prevent race conditions or multiple workers uploading the same job simultaneously
            if job.status == "publishing":
                logger.warning(f"Publishing Job #{job.id} is already in 'publishing' status. Skipping duplicate dispatch.")
                return {"status": "already_in_progress", "job_id": job.id, "message": f"Job #{job.id} is already actively uploading."}

            job.status = "publishing"
            job.attempts += 1
            db.commit()

            title = meta.title if meta else "Raw Gameplay Sequence | Gaming Uncut"
            desc = meta.description if meta else "Raw unedited gameplay capture from Gaming Uncut."
            hashtags = [h.strip() for h in (meta.hashtags if meta else "#GamingUncut #Shorts #GameplayMoments").split() if h.startswith("#")]
            caption = f"{title}\n\n{desc}\n\n{' '.join(hashtags)}"

            yt_title = (meta.youtube_title if meta and meta.youtube_title else title)
            yt_desc = (meta.youtube_desc if meta and meta.youtube_desc else desc)
            ig_caption = (meta.instagram_caption if meta and meta.instagram_caption else caption)
            fb_caption = (meta.facebook_caption if meta and meta.facebook_caption else caption)

            results = {}
            rescheduled_windows = []

            # ==========================================
            # 1. YouTube Shorts
            # ==========================================
            yt_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="youtube").first()
            if yt_upload and yt_upload.status != "published":
                yt_stage = "authorization_check"
                allowed, pause_reason, next_win = can_publish(db, "youtube")
                if not allowed:
                    if next_win:
                        yt_upload.status = "paused_quota"
                        rescheduled_windows.append(next_win)
                        log_publishing_audit(
                            db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                            stage=yt_stage, error=None, level="WARNING", attempts=yt_upload.attempts,
                            error_code="PAUSED_QUOTA", user_message=pause_reason
                        )
                    else:
                        yt_upload.status = "paused_unauthorized"
                        log_publishing_audit(
                            db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                            stage=yt_stage, error=None, level="WARNING", attempts=yt_upload.attempts,
                            error_code="PAUSED_UNAUTHORIZED", user_message=pause_reason
                        )
                    yt_upload.error_message = pause_reason
                    logger.info(f"Safely skipping YouTube for Job #{job.id}: {pause_reason}")
                else:
                    try:
                        yt_stage = "uploading"
                        yt_upload.status = "uploading"
                        yt_upload.attempts += 1
                        yt_upload.error_code = None
                        yt_upload.error_message = None
                        db.commit()

                        res = youtube_client.upload_short(video_path, yt_title, yt_desc, hashtags)
                        yt_upload.status = "published"
                        yt_upload.external_post_id = res["post_id"]
                        yt_upload.external_url = res["url"]
                        yt_upload.uploaded_at = datetime.utcnow()
                        record_upload_success(db, "youtube", quota_units=1600)
                        results["youtube"] = res
                    except YouTubeQuotaExceededError as e:
                        next_win = record_platform_restriction(db, "youtube", "api_quota_exceeded", str(e))
                        yt_upload.status = "paused_quota"
                        msg = "Paused: YouTube Project API Quota Exceeded (10,000 unit bucket). Rescheduled for next window."
                        yt_upload.error_message = msg
                        rescheduled_windows.append(next_win)
                        log_publishing_audit(
                            db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                            stage=yt_stage, error=e, level="WARNING", attempts=yt_upload.attempts,
                            error_code="QUOTA_EXCEEDED", user_message=msg
                        )
                    except YouTubeChannelUploadLimitError as e:
                        next_win = record_platform_restriction(db, "youtube", "channel_limit_reached", str(e))
                        yt_upload.status = "paused_quota"
                        msg = "Paused: YouTube Channel Daily Upload Limit reached. Rescheduled for next window."
                        yt_upload.error_message = msg
                        rescheduled_windows.append(next_win)
                        log_publishing_audit(
                            db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                            stage=yt_stage, error=e, level="WARNING", attempts=yt_upload.attempts,
                            error_code="CHANNEL_LIMIT_REACHED", user_message=msg
                        )
                    except YouTubeShortsValidationError as e:
                        yt_upload.status = "failed"
                        yt_upload.error_code = "SHORTS_PROFILE_VIOLATION"
                        yt_upload.error_message = str(e)
                        logger.error(f"YouTube Shorts validation failed for Job #{job.id}: {e}")
                        log_publishing_audit(
                            db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                            stage="pre_upload_validation", error=e, level="ERROR", attempts=yt_upload.attempts,
                            error_code="SHORTS_PROFILE_VIOLATION", user_message=str(e)
                        )
                    except Exception as e:
                        yt_upload.status = "failed"
                        yt_upload.error_message = str(e)
                        logger.error(f"YouTube upload failed: {e}")
                        log_publishing_audit(
                            db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                            stage=yt_stage, error=e, level="ERROR", attempts=yt_upload.attempts,
                            error_code=str(getattr(e, "code", "YOUTUBE_UPLOAD_ERROR")), user_message=str(e)
                        )
                db.commit()

            # ==========================================
            # 2. Instagram Reels
            # ==========================================
            ig_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="instagram").first()
            if ig_upload and ig_upload.status != "published":
                ig_stage = ["authorization_check"]
                allowed, pause_reason, next_win = can_publish(db, "instagram")
                if not allowed:
                    if next_win:
                        ig_upload.status = "paused_quota"
                        rescheduled_windows.append(next_win)
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=None, level="WARNING", attempts=ig_upload.attempts,
                            error_code="PAUSED_QUOTA", user_message=pause_reason
                        )
                    else:
                        ig_upload.status = "paused_unauthorized"
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=None, level="WARNING", attempts=ig_upload.attempts,
                            error_code="PAUSED_UNAUTHORIZED", user_message=pause_reason
                        )
                    ig_upload.error_message = pause_reason
                    logger.info(f"Safely skipping Instagram for Job #{job.id}: {pause_reason}")
                else:
                    try:
                        ig_stage[0] = "upload_initialized"
                        ig_upload.status = "upload_initialized"
                        ig_upload.attempts += 1
                        ig_upload.error_code = None
                        ig_upload.error_message = None
                        db.commit()

                        def ig_stage_callback(stage: str):
                            ig_stage[0] = stage
                            ig_upload.status = stage
                            db.commit()

                        res = instagram_client.publish_reel(
                            video_path,
                            ig_caption,
                            stage_callback=ig_stage_callback
                        )
                        ig_upload.status = "published"
                        ig_upload.error_code = None
                        ig_upload.error_message = None
                        ig_upload.external_post_id = res["post_id"]
                        ig_upload.external_url = res["url"]
                        ig_upload.uploaded_at = datetime.utcnow()
                        record_upload_success(db, "instagram", quota_units=0)
                        results["instagram"] = res
                    except (InstagramRateLimitError, ClientIGRateLimitError) as e:
                        code = str(getattr(e, "code", "613"))
                        next_win = record_platform_restriction(db, "instagram", "rate_limited", str(e))
                        ig_upload.status = "paused_quota"
                        ig_upload.error_code = code
                        msg = getattr(e, "user_message", f"Paused: Instagram publishing rate limit ({code}). Rescheduled for next window.")
                        ig_upload.error_message = msg
                        rescheduled_windows.append(next_win)
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="WARNING", attempts=ig_upload.attempts,
                            error_code=code, user_message=msg
                        )
                    except InstagramTokenExpiredError as e:
                        ig_upload.status = "failed"
                        ig_upload.error_code = str(e.code or "190")
                        ig_upload.error_message = e.user_message
                        logger.error(f"Instagram upload paused due to expired token: {e.user_message}")
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code=str(e.code or "190"), user_message=e.user_message
                        )
                        from backend.app.publishers.meta_token_manager import meta_token_manager
                        meta_token_manager.mark_auth_expired("instagram", str(e.code or "190"), str(getattr(e, "subcode", 463)), e.user_message)
                    except InstagramPermissionError as e:
                        ig_upload.status = "failed"
                        ig_upload.error_code = str(e.code or "200")
                        ig_upload.error_message = e.user_message
                        logger.error(f"Instagram upload failed due to missing permissions: {e.user_message}")
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code=str(e.code or "200"), user_message=e.user_message
                        )
                    except InstagramConfigError as e:
                        ig_upload.status = "failed"
                        ig_upload.error_code = str(e.code or "CONFIG_ERROR")
                        ig_upload.error_message = e.user_message
                        logger.error(f"Instagram upload failed due to configuration: {e.user_message}")
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code=str(e.code or "CONFIG_ERROR"), user_message=e.user_message
                        )
                    except InstagramVideoUrlError as e:
                        ig_upload.status = "failed"
                        ig_upload.error_code = "INACCESSIBLE_URL"
                        ig_upload.error_message = e.user_message
                        logger.error(f"Instagram upload failed due to inaccessible video URL: {e.user_message}")
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code="INACCESSIBLE_URL", user_message=e.user_message
                        )
                    except (InstagramProcessingFailedError, InstagramProcessingTimeoutError) as e:
                        code = "PROCESSING_TIMEOUT" if isinstance(e, InstagramProcessingTimeoutError) else "PROCESSING_FAILED"
                        ig_upload.status = "failed"
                        ig_upload.error_code = code
                        ig_upload.error_message = e.user_message
                        logger.error(f"Instagram container processing failed: {e.user_message}")
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage="instagram_processing", error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code=code, user_message=e.user_message
                        )
                    except InstagramUploadFailedError as e:
                        ig_upload.status = "failed"
                        ig_upload.error_code = "BINARY_TRANSFER_FAILED"
                        ig_upload.error_message = getattr(e, "user_message", str(e))
                        logger.error(f"Instagram binary upload failed: {e}")
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code="BINARY_TRANSFER_FAILED", user_message=getattr(e, "user_message", str(e))
                        )
                    except requests.Timeout as e:
                        ig_upload.status = "failed"
                        ig_upload.error_code = "NETWORK_TIMEOUT"
                        msg = f"Instagram network request timed out: {e}"
                        ig_upload.error_message = msg
                        logger.error(msg)
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code="NETWORK_TIMEOUT", user_message=msg
                        )
                    except Exception as e:
                        raw_code = getattr(e, "code", None)
                        code = str(raw_code) if raw_code is not None else "UPLOAD_FAILED"
                        msg = getattr(e, "user_message", str(e))
                        ig_upload.status = "failed"
                        ig_upload.error_code = code
                        ig_upload.error_message = msg
                        logger.error(f"Instagram upload failed: {e}")
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                            error_code=code, user_message=msg
                        )
                db.commit()

            # ==========================================
            # 3. Facebook Reels
            # ==========================================
            fb_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="facebook").first()
            if fb_upload and fb_upload.status != "published":
                fb_stage = ["authorization_check"]
                allowed, pause_reason, next_win = can_publish(db, "facebook")
                if not allowed:
                    if next_win:
                        fb_upload.status = "paused_quota"
                        rescheduled_windows.append(next_win)
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=None, level="WARNING", attempts=fb_upload.attempts,
                            error_code="PAUSED_QUOTA", user_message=pause_reason
                        )
                    else:
                        fb_upload.status = "paused_unauthorized"
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=None, level="WARNING", attempts=fb_upload.attempts,
                            error_code="PAUSED_UNAUTHORIZED", user_message=pause_reason
                        )
                    fb_upload.error_message = pause_reason
                    logger.info(f"Safely skipping Facebook for Job #{job.id}: {pause_reason}")
                else:
                    try:
                        fb_stage[0] = "page_verification"
                        fb_upload.status = "upload_initialized"
                        fb_upload.attempts += 1
                        fb_upload.error_code = None
                        fb_upload.error_message = None
                        db.commit()

                        def fb_stage_callback(stage: str):
                            fb_stage[0] = stage
                            fb_upload.status = stage
                            db.commit()

                        res = facebook_client.publish_reel(
                            video_path,
                            fb_caption,
                            stage_callback=fb_stage_callback
                        )
                        fb_upload.status = "published"
                        fb_upload.error_code = None
                        fb_upload.error_message = None
                        fb_upload.external_post_id = res["post_id"]
                        fb_upload.external_url = res["url"]
                        fb_upload.uploaded_at = datetime.utcnow()
                        record_upload_success(db, "facebook", quota_units=0)
                        results["facebook"] = res
                    except (FacebookRateLimitError, ClientFBRateLimitError) as e:
                        code = str(getattr(e, "code", "613"))
                        next_win = record_platform_restriction(db, "facebook", "rate_limited", str(e))
                        fb_upload.status = "paused_quota"
                        fb_upload.error_code = code
                        msg = getattr(e, "user_message", f"Paused: Facebook publishing rate limit ({code}). Rescheduled for next window.")
                        fb_upload.error_message = msg
                        rescheduled_windows.append(next_win)
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=e, level="WARNING", attempts=fb_upload.attempts,
                            error_code=code, user_message=msg
                        )
                    except FacebookTokenExpiredError as e:
                        fb_upload.status = "failed"
                        fb_upload.error_code = str(e.code or "190")
                        fb_upload.error_message = e.user_message
                        logger.error(f"Facebook upload paused due to expired token: {e.user_message}")
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                            error_code=str(e.code or "190"), user_message=e.user_message
                        )
                        from backend.app.publishers.meta_token_manager import meta_token_manager
                        meta_token_manager.mark_auth_expired("facebook", str(e.code or "190"), str(getattr(e, "subcode", 463)), e.user_message)
                    except FacebookPermissionError as e:
                        fb_upload.status = "failed"
                        fb_upload.error_code = str(e.code or "200")
                        fb_upload.error_message = e.user_message
                        logger.error(f"Facebook upload failed due to permissions: {e.user_message}")
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                            error_code=str(e.code or "200"), user_message=e.user_message
                        )
                    except FacebookConfigError as e:
                        fb_upload.status = "failed"
                        fb_upload.error_code = str(e.code or "CONFIG_ERROR")
                        fb_upload.error_message = e.user_message
                        logger.error(f"Facebook upload failed due to configuration: {e.user_message}")
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage="page_verification" if fb_stage[0] in ("authorization_check", "upload_initialized") else fb_stage[0],
                            error=e, level="ERROR", attempts=fb_upload.attempts,
                            error_code=str(e.code or "CONFIG_ERROR"), user_message=e.user_message
                        )
                    except FacebookUploadFailedError as e:
                        fb_upload.status = "failed"
                        fb_upload.error_code = "BINARY_TRANSFER_FAILED"
                        fb_upload.error_message = getattr(e, "user_message", str(e))
                        logger.error(f"Facebook binary upload failed: {e}")
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                            error_code="BINARY_TRANSFER_FAILED", user_message=getattr(e, "user_message", str(e))
                        )
                    except requests.Timeout as e:
                        fb_upload.status = "failed"
                        fb_upload.error_code = "NETWORK_TIMEOUT"
                        msg = f"Facebook network request timed out: {e}"
                        fb_upload.error_message = msg
                        logger.error(msg)
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                            error_code="NETWORK_TIMEOUT", user_message=msg
                        )
                    except Exception as e:
                        code = str(getattr(e, "code", "UPLOAD_FAILED"))
                        msg = getattr(e, "user_message", str(e))
                        fb_upload.status = "failed"
                        fb_upload.error_code = code
                        fb_upload.error_message = msg
                        logger.error(f"Facebook upload failed: {e}")
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                            error_code=code, user_message=msg
                        )
                db.commit()

            # Evaluate overall job status & auto-rescheduling
            all_uploads = db.query(PlatformUpload).filter_by(publishing_job_id=job.id).all()
            if all(u.status == "published" for u in all_uploads):
                job.status = "published"
            elif any(u.status == "published" for u in all_uploads):
                job.status = "partially_published"
                if any(u.status == "paused_quota" for u in all_uploads) and rescheduled_windows:
                    job.scheduled_time = min(rescheduled_windows)
            elif any(u.status == "paused_quota" for u in all_uploads) and rescheduled_windows:
                job.status = "scheduled_next_window"
                job.scheduled_time = min(rescheduled_windows)
                logger.info(f"Job #{job.id} automatically scheduled for next permitted window: {job.scheduled_time}")
            elif any(u.status == "paused_unauthorized" for u in all_uploads):
                job.status = "paused_unauthorized"
                logger.info(f"Publishing Job #{job.id} safely paused: Platform authorization required.")
            else:
                job.status = "failed"

            db.commit()
            return results

publishing_manager = PublishingManager()
