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
from backend.app.publishers.idempotency import (
    idempotency_manager, PublishingState, build_idempotency_key, generate_worker_id
)
from backend.app.security.vault import vault
from backend.app.publishers.meta_token_manager import meta_token_manager
from backend.app.publishers.youtube.youtube_client import youtube_client, YouTubeShortsValidationError
from backend.app.publishers.instagram.instagram_client import (
    instagram_client, InstagramAPIError, InstagramTokenExpiredError,
    InstagramPermissionError, InstagramConfigError, InstagramVideoUrlError,
    InstagramProcessingFailedError, InstagramProcessingTimeoutError,
    InstagramUploadFailedError, InstagramValidationError, InstagramRateLimitError as ClientIGRateLimitError
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
    user_message: Optional[str] = None,
    worker_id: Optional[str] = None,
    account_id: Optional[str] = None,
    idempotency_decision: Optional[str] = None,
    external_media_id: Optional[str] = None,
    published_url: Optional[str] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None
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
        "worker_id": worker_id,
        "account_id": str(account_id) if account_id else None,
        "idempotency_decision": idempotency_decision,
        "external_media_id": str(external_media_id) if external_media_id else None,
        "published_url": published_url,
        "started_at": started_at.isoformat() if started_at else None,
        "completed_at": completed_at.isoformat() if completed_at else None,
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
    def execute_job(
        self,
        publishing_job_id: int,
        allow_republish: bool = False,
        worker_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a publishing job across YouTube Shorts, Instagram Reels, and Facebook Reels.
        Guarantees strict single-execution semantics and zero duplicate publications via:
        1. Atomic worker check-and-set claim with unexpired lease detection.
        2. Persistent database-level idempotency key: (clip_id, platform, account_id, destination).
        3. Pre-flight remote platform reconciliation before retrying.
        4. Immediate persistence of external post/container IDs upon platform allocation.
        5. UNKNOWN state classification on network timeouts to prevent blind retries.
        """
        active_worker_id = worker_id or generate_worker_id()
        started_at = datetime.utcnow()

        with SessionLocal() as db:
            # 1. Atomic Job Claim
            claimed, reject_reason, job = idempotency_manager.claim_job_atomically(
                db=db,
                job_id=publishing_job_id,
                worker_id=active_worker_id,
                lease_seconds=300,
                allow_republish=allow_republish
            )

            if not claimed:
                logger.warning(f"Publishing Job #{publishing_job_id} claim rejected: {reject_reason}")
                log_publishing_audit(
                    db, platform="system", publishing_job_id=publishing_job_id,
                    clip_id=job.clip_id if job else None, stage="job_claim", error=None,
                    level="WARNING", error_code="CLAIM_REJECTED", user_message=reject_reason,
                    worker_id=active_worker_id, idempotency_decision="CLAIM_REJECTED",
                    started_at=started_at, completed_at=datetime.utcnow()
                )
                db.commit()
                return {
                    "status": "blocked_by_idempotency",
                    "job_id": publishing_job_id,
                    "reason": reject_reason
                }

            clip = db.query(GeneratedClip).filter_by(id=job.clip_id).first()
            if not clip:
                idempotency_manager.release_job(db, job.id, PublishingState.FAILED)
                raise ValueError(f"Clip for job #{publishing_job_id} not found.")

            meta = db.query(ClipMetadata).filter_by(clip_id=clip.id).first()
            video_path = Path(clip.output_path)

            # Check file presence
            if not video_path.exists():
                idempotency_manager.release_job(db, job.id, PublishingState.FAILED)
                log_publishing_audit(
                    db, platform="system", publishing_job_id=job.id, clip_id=clip.id,
                    stage="pre_publish_validation", error=None, level="ERROR",
                    attempts=job.attempts + 1, error_code="FILE_NOT_FOUND",
                    user_message=f"Clip video file missing on disk: {video_path}",
                    worker_id=active_worker_id, idempotency_decision="FILE_MISSING",
                    started_at=started_at, completed_at=datetime.utcnow()
                )
                db.commit()
                raise FileNotFoundError(f"Clip video file missing: {video_path}")

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
            if yt_upload:
                yt_channel = vault.get_json("google", "verified_channel", {}) or {}
                yt_account_id = yt_channel.get("id") or "channel_default"
                yt_dest = "shorts"
                yt_idemp_key = build_idempotency_key(clip.id, "youtube", yt_account_id, yt_dest)
                yt_upload.account_id = yt_account_id
                yt_upload.destination = yt_dest
                yt_upload.idempotency_key = yt_idemp_key
                db.commit()

                # Step 1A: Pre-flight Remote Reconciliation
                reconciled = idempotency_manager.reconcile_platform_publication(
                    db, "youtube", clip.id, yt_account_id, yt_dest,
                    candidate_external_id=yt_upload.external_post_id,
                    candidate_title=yt_title
                )
                if reconciled and reconciled.get("published"):
                    yt_upload.status = "published"
                    yt_upload.external_post_id = reconciled["post_id"]
                    yt_upload.external_url = reconciled["url"]
                    yt_upload.uploaded_at = yt_upload.uploaded_at or datetime.utcnow()
                    idempotency_manager.update_stage(
                        db, yt_idemp_key, active_worker_id, PublishingState.PUBLISHED,
                        external_post_id=reconciled["post_id"], external_url=reconciled["url"]
                    )
                    results["youtube"] = reconciled
                    log_publishing_audit(
                        db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                        stage="reconciliation", error=None, level="INFO", attempts=yt_upload.attempts,
                        user_message=f"Reconciled existing YouTube publication: {reconciled['url']}",
                        worker_id=active_worker_id, account_id=yt_account_id,
                        external_media_id=reconciled["post_id"], published_url=reconciled["url"],
                        idempotency_decision="RECONCILED", started_at=started_at, completed_at=datetime.utcnow()
                    )
                    db.commit()
                elif yt_upload.status != "published":
                    # Step 1B: Acquire Platform Idempotency Lock
                    locked, lock_reason, idemp_rec = idempotency_manager.acquire_platform_idempotency_lock(
                        db, clip.id, "youtube", yt_account_id, yt_dest,
                        job.id, yt_upload.id, active_worker_id, allow_republish=allow_republish
                    )
                    if not locked:
                        log_publishing_audit(
                            db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                            stage="idempotency_gate", error=None, level="WARNING", attempts=yt_upload.attempts,
                            error_code="IDEMPOTENCY_BLOCKED", user_message=lock_reason,
                            worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="BLOCKED",
                            started_at=started_at, completed_at=datetime.utcnow()
                        )
                    else:
                        yt_stage = "authorization_check"
                        allowed, pause_reason, next_win = can_publish(db, "youtube")
                        if not allowed:
                            if next_win:
                                yt_upload.status = "paused_quota"
                                rescheduled_windows.append(next_win)
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=yt_stage, error=None, level="WARNING", attempts=yt_upload.attempts,
                                    error_code="PAUSED_QUOTA", user_message=pause_reason,
                                    worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="PAUSED_QUOTA"
                                )
                            else:
                                yt_upload.status = "paused_unauthorized"
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=yt_stage, error=None, level="WARNING", attempts=yt_upload.attempts,
                                    error_code="PAUSED_UNAUTHORIZED", user_message=pause_reason,
                                    worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="PAUSED_UNAUTHORIZED"
                                )
                            yt_upload.error_message = pause_reason
                            logger.info(f"Safely skipping YouTube for Job #{job.id}: {pause_reason}")
                        else:
                            try:
                                yt_stage = "uploading"
                                yt_upload.status = "uploading"
                                yt_upload.attempts += 1
                                yt_upload.last_attempt_at = datetime.utcnow()
                                yt_upload.error_code = None
                                yt_upload.error_message = None
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.UPLOADING)
                                db.commit()

                                def yt_on_video_id(vid: str):
                                    yt_upload.external_post_id = str(vid)
                                    idempotency_manager.update_stage(
                                        db, yt_idemp_key, active_worker_id, PublishingState.PROCESSING, external_post_id=str(vid)
                                    )
                                    db.commit()

                                res = youtube_client.upload_short(
                                    video_path, yt_title, yt_desc, hashtags,
                                    on_video_id_created=yt_on_video_id
                                )
                                yt_upload.status = "published"
                                yt_upload.external_post_id = res["post_id"]
                                yt_upload.external_url = res["url"]
                                yt_upload.uploaded_at = datetime.utcnow()
                                idempotency_manager.update_stage(
                                    db, yt_idemp_key, active_worker_id, PublishingState.PUBLISHED,
                                    external_post_id=res["post_id"], external_url=res["url"]
                                )
                                record_upload_success(db, "youtube", quota_units=1600)
                                results["youtube"] = res
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage="published", error=None, level="INFO", attempts=yt_upload.attempts,
                                    user_message=f"YouTube Short published successfully: {res['url']}",
                                    worker_id=active_worker_id, account_id=yt_account_id,
                                    external_media_id=res["post_id"], published_url=res["url"],
                                    idempotency_decision="PUBLISHED", started_at=started_at, completed_at=datetime.utcnow()
                                )
                            except YouTubeQuotaExceededError as e:
                                next_win = record_platform_restriction(db, "youtube", "api_quota_exceeded", str(e))
                                yt_upload.status = "paused_quota"
                                msg = "Paused: YouTube Project API Quota Exceeded (10,000 unit bucket). Rescheduled for next window."
                                yt_upload.error_message = msg
                                rescheduled_windows.append(next_win)
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=yt_stage, error=e, level="WARNING", attempts=yt_upload.attempts,
                                    error_code="QUOTA_EXCEEDED", user_message=msg,
                                    worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="PAUSED_QUOTA"
                                )
                            except YouTubeChannelUploadLimitError as e:
                                next_win = record_platform_restriction(db, "youtube", "channel_limit_reached", str(e))
                                yt_upload.status = "paused_quota"
                                msg = "Paused: YouTube Channel Daily Upload Limit reached. Rescheduled for next window."
                                yt_upload.error_message = msg
                                rescheduled_windows.append(next_win)
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=yt_stage, error=e, level="WARNING", attempts=yt_upload.attempts,
                                    error_code="CHANNEL_LIMIT_REACHED", user_message=msg,
                                    worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="PAUSED_QUOTA"
                                )
                            except YouTubeShortsValidationError as e:
                                yt_upload.status = "failed"
                                yt_upload.error_code = "SHORTS_PROFILE_VIOLATION"
                                yt_upload.error_message = str(e)
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"YouTube Shorts validation failed for Job #{job.id}: {e}")
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage="pre_upload_validation", error=e, level="ERROR", attempts=yt_upload.attempts,
                                    error_code="SHORTS_PROFILE_VIOLATION", user_message=str(e),
                                    worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="FAILED_PERMANENT"
                                )
                            except requests.Timeout as e:
                                yt_upload.status = "unknown"
                                yt_upload.error_code = "NETWORK_TIMEOUT"
                                msg = f"YouTube network request timed out: {e}. Marked UNKNOWN to prevent blind duplicate retry."
                                yt_upload.error_message = msg
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.UNKNOWN)
                                logger.warning(msg)
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=yt_stage, error=e, level="WARNING", attempts=yt_upload.attempts,
                                    error_code="NETWORK_TIMEOUT", user_message=msg,
                                    worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="MARKED_UNKNOWN"
                                )
                            except Exception as e:
                                yt_upload.status = "failed"
                                yt_upload.error_message = str(e)
                                idempotency_manager.update_stage(db, yt_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"YouTube upload failed: {e}")
                                log_publishing_audit(
                                    db, platform="youtube", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=yt_stage, error=e, level="ERROR", attempts=yt_upload.attempts,
                                    error_code=str(getattr(e, "code", "YOUTUBE_UPLOAD_ERROR")), user_message=str(e),
                                    worker_id=active_worker_id, account_id=yt_account_id, idempotency_decision="FAILED"
                                )
                db.commit()

            # ==========================================
            # 2. Instagram Reels
            # ==========================================
            ig_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="instagram").first()
            if ig_upload:
                ig_creds = meta_token_manager.get_instagram_credentials() or {}
                ig_account_id = ig_creds.get("instagram_account_id") or "instagram_default"
                ig_dest = "reels"
                ig_idemp_key = build_idempotency_key(clip.id, "instagram", ig_account_id, ig_dest)
                ig_upload.account_id = ig_account_id
                ig_upload.destination = ig_dest
                ig_upload.idempotency_key = ig_idemp_key
                db.commit()

                # Step 2A: Pre-flight Remote Reconciliation
                reconciled = idempotency_manager.reconcile_platform_publication(
                    db, "instagram", clip.id, ig_account_id, ig_dest,
                    candidate_external_id=ig_upload.external_post_id
                )
                if reconciled and reconciled.get("published"):
                    ig_upload.status = "published"
                    ig_upload.external_post_id = reconciled["post_id"]
                    ig_upload.external_url = reconciled["url"]
                    ig_upload.uploaded_at = ig_upload.uploaded_at or datetime.utcnow()
                    idempotency_manager.update_stage(
                        db, ig_idemp_key, active_worker_id, PublishingState.PUBLISHED,
                        external_post_id=reconciled["post_id"], external_url=reconciled["url"]
                    )
                    results["instagram"] = reconciled
                    log_publishing_audit(
                        db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                        stage="reconciliation", error=None, level="INFO", attempts=ig_upload.attempts,
                        user_message=f"Reconciled existing Instagram publication: {reconciled['url']}",
                        worker_id=active_worker_id, account_id=ig_account_id,
                        external_media_id=reconciled["post_id"], published_url=reconciled["url"],
                        idempotency_decision="RECONCILED", started_at=started_at, completed_at=datetime.utcnow()
                    )
                    db.commit()
                elif ig_upload.status != "published":
                    # Step 2B: Acquire Platform Idempotency Lock
                    locked, lock_reason, idemp_rec = idempotency_manager.acquire_platform_idempotency_lock(
                        db, clip.id, "instagram", ig_account_id, ig_dest,
                        job.id, ig_upload.id, active_worker_id, allow_republish=allow_republish
                    )
                    if not locked:
                        log_publishing_audit(
                            db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                            stage="idempotency_gate", error=None, level="WARNING", attempts=ig_upload.attempts,
                            error_code="IDEMPOTENCY_BLOCKED", user_message=lock_reason,
                            worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="BLOCKED",
                            started_at=started_at, completed_at=datetime.utcnow()
                        )
                    else:
                        ig_stage = ["authorization_check"]
                        allowed, pause_reason, next_win = can_publish(db, "instagram")
                        if not allowed:
                            if next_win:
                                ig_upload.status = "paused_quota"
                                rescheduled_windows.append(next_win)
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=None, level="WARNING", attempts=ig_upload.attempts,
                                    error_code="PAUSED_QUOTA", user_message=pause_reason,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="PAUSED_QUOTA"
                                )
                            else:
                                ig_upload.status = "paused_unauthorized"
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=None, level="WARNING", attempts=ig_upload.attempts,
                                    error_code="PAUSED_UNAUTHORIZED", user_message=pause_reason,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="PAUSED_UNAUTHORIZED"
                                )
                            ig_upload.error_message = pause_reason
                            logger.info(f"Safely skipping Instagram for Job #{job.id}: {pause_reason}")
                        else:
                            try:
                                ig_stage[0] = "upload_initialized"
                                ig_upload.status = "upload_initialized"
                                ig_upload.attempts += 1
                                ig_upload.last_attempt_at = datetime.utcnow()
                                ig_upload.error_code = None
                                ig_upload.error_message = None
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.UPLOADING)
                                db.commit()

                                def ig_stage_callback(stage: str):
                                    ig_stage[0] = stage
                                    ig_upload.status = stage
                                    db.commit()

                                def ig_on_container(cid: str):
                                    ig_upload.external_post_id = str(cid)
                                    idempotency_manager.update_stage(
                                        db, ig_idemp_key, active_worker_id, PublishingState.PROCESSING, external_post_id=str(cid)
                                    )
                                    db.commit()

                                res = instagram_client.publish_reel(
                                    video_path,
                                    ig_caption,
                                    stage_callback=ig_stage_callback,
                                    on_container_created=ig_on_container
                                )
                                ig_upload.status = "published"
                                ig_upload.error_code = None
                                ig_upload.error_message = None
                                ig_upload.external_post_id = res["post_id"]
                                ig_upload.external_url = res["url"]
                                ig_upload.uploaded_at = datetime.utcnow()
                                idempotency_manager.update_stage(
                                    db, ig_idemp_key, active_worker_id, PublishingState.PUBLISHED,
                                    external_post_id=res["post_id"], external_url=res["url"]
                                )
                                record_upload_success(db, "instagram", quota_units=0)
                                results["instagram"] = res
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage="published", error=None, level="INFO", attempts=ig_upload.attempts,
                                    user_message=f"Instagram Reel published successfully: {res['url']}",
                                    worker_id=active_worker_id, account_id=ig_account_id,
                                    external_media_id=res["post_id"], published_url=res["url"],
                                    idempotency_decision="PUBLISHED", started_at=started_at, completed_at=datetime.utcnow()
                                )
                            except (InstagramRateLimitError, ClientIGRateLimitError) as e:
                                code = str(getattr(e, "code", "613"))
                                next_win = record_platform_restriction(db, "instagram", "rate_limited", str(e))
                                ig_upload.status = "paused_quota"
                                ig_upload.error_code = code
                                msg = getattr(e, "user_message", f"Paused: Instagram publishing rate limit ({code}). Rescheduled for next window.")
                                ig_upload.error_message = msg
                                rescheduled_windows.append(next_win)
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="WARNING", attempts=ig_upload.attempts,
                                    error_code=code, user_message=msg,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="PAUSED_QUOTA"
                                )
                            except InstagramTokenExpiredError as e:
                                ig_upload.status = "failed"
                                ig_upload.error_code = str(e.code or "190")
                                ig_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Instagram upload paused due to expired token: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                                    error_code=str(e.code or "190"), user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="FAILED_PERMANENT"
                                )
                                meta_token_manager.mark_auth_expired("instagram", str(e.code or "190"), str(getattr(e, "subcode", 463)), e.user_message)
                            except InstagramPermissionError as e:
                                ig_upload.status = "failed"
                                ig_upload.error_code = str(e.code or "200")
                                ig_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Instagram upload failed due to missing permissions: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                                    error_code=str(e.code or "200"), user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="FAILED_PERMANENT"
                                )
                            except InstagramConfigError as e:
                                ig_upload.status = "failed"
                                ig_upload.error_code = str(e.code or "CONFIG_ERROR")
                                ig_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Instagram upload failed due to configuration: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                                    error_code=str(e.code or "CONFIG_ERROR"), user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="FAILED_PERMANENT"
                                )
                            except InstagramValidationError as e:
                                ig_upload.status = "failed"
                                ig_upload.error_code = "VALIDATION_FAILED"
                                ig_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Instagram pre-upload validation failed: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                                    error_code="VALIDATION_FAILED", user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="FAILED_VALIDATION",
                                    started_at=started_at, completed_at=datetime.utcnow()
                                )
                            except InstagramVideoUrlError as e:
                                ig_upload.status = "failed"
                                ig_upload.error_code = "INACCESSIBLE_URL"
                                ig_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Instagram upload failed due to inaccessible video URL: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                                    error_code="INACCESSIBLE_URL", user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="FAILED"
                                )
                            except (InstagramProcessingFailedError, InstagramProcessingTimeoutError) as e:
                                code = "PROCESSING_TIMEOUT" if isinstance(e, InstagramProcessingTimeoutError) else "PROCESSING_FAILED"
                                ig_upload.status = "unknown" if isinstance(e, InstagramProcessingTimeoutError) else "failed"
                                ig_upload.error_code = code
                                ig_upload.error_message = e.user_message
                                new_idemp_state = PublishingState.UNKNOWN if isinstance(e, InstagramProcessingTimeoutError) else PublishingState.FAILED
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, new_idemp_state)
                                logger.error(f"Instagram container processing notice: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage="instagram_processing", error=e, level="WARNING" if isinstance(e, InstagramProcessingTimeoutError) else "ERROR",
                                    attempts=ig_upload.attempts, error_code=code, user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=ig_account_id,
                                    idempotency_decision="MARKED_UNKNOWN" if isinstance(e, InstagramProcessingTimeoutError) else "FAILED"
                                )
                            except InstagramUploadFailedError as e:
                                ig_upload.status = "failed"
                                ig_upload.error_code = "BINARY_TRANSFER_FAILED"
                                ig_upload.error_message = getattr(e, "user_message", str(e))
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Instagram binary upload failed: {e}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                                    error_code="BINARY_TRANSFER_FAILED", user_message=getattr(e, "user_message", str(e)),
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="FAILED"
                                )
                            except requests.Timeout as e:
                                ig_upload.status = "unknown"
                                ig_upload.error_code = "NETWORK_TIMEOUT"
                                msg = f"Instagram network request timed out: {e}. Marked UNKNOWN to prevent blind duplicate retry."
                                ig_upload.error_message = msg
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.UNKNOWN)
                                logger.warning(msg)
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="WARNING", attempts=ig_upload.attempts,
                                    error_code="NETWORK_TIMEOUT", user_message=msg,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="MARKED_UNKNOWN"
                                )
                            except Exception as e:
                                raw_code = getattr(e, "code", None)
                                code = str(raw_code) if raw_code is not None else "UPLOAD_FAILED"
                                msg = getattr(e, "user_message", str(e))
                                ig_upload.status = "failed"
                                ig_upload.error_code = code
                                ig_upload.error_message = msg
                                idempotency_manager.update_stage(db, ig_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Instagram upload failed: {e}")
                                log_publishing_audit(
                                    db, platform="instagram", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=ig_stage[0], error=e, level="ERROR", attempts=ig_upload.attempts,
                                    error_code=code, user_message=msg,
                                    worker_id=active_worker_id, account_id=ig_account_id, idempotency_decision="FAILED"
                                )
                db.commit()

            # ==========================================
            # 3. Facebook Reels
            # ==========================================
            fb_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="facebook").first()
            if fb_upload:
                fb_creds = meta_token_manager.get_facebook_credentials() or {}
                fb_account_id = fb_creds.get("page_id") or "facebook_default"
                fb_dest = "reels"
                fb_idemp_key = build_idempotency_key(clip.id, "facebook", fb_account_id, fb_dest)
                fb_upload.account_id = fb_account_id
                fb_upload.destination = fb_dest
                fb_upload.idempotency_key = fb_idemp_key
                db.commit()

                # Step 3A: Pre-flight Remote Reconciliation
                reconciled = idempotency_manager.reconcile_platform_publication(
                    db, "facebook", clip.id, fb_account_id, fb_dest,
                    candidate_external_id=fb_upload.external_post_id
                )
                if reconciled and reconciled.get("published"):
                    fb_upload.status = "published"
                    fb_upload.external_post_id = reconciled["post_id"]
                    fb_upload.external_url = reconciled["url"]
                    fb_upload.uploaded_at = fb_upload.uploaded_at or datetime.utcnow()
                    idempotency_manager.update_stage(
                        db, fb_idemp_key, active_worker_id, PublishingState.PUBLISHED,
                        external_post_id=reconciled["post_id"], external_url=reconciled["url"]
                    )
                    results["facebook"] = reconciled
                    log_publishing_audit(
                        db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                        stage="reconciliation", error=None, level="INFO", attempts=fb_upload.attempts,
                        user_message=f"Reconciled existing Facebook publication: {reconciled['url']}",
                        worker_id=active_worker_id, account_id=fb_account_id,
                        external_media_id=reconciled["post_id"], published_url=reconciled["url"],
                        idempotency_decision="RECONCILED", started_at=started_at, completed_at=datetime.utcnow()
                    )
                    db.commit()
                elif fb_upload.status != "published":
                    # Step 3B: Acquire Platform Idempotency Lock
                    locked, lock_reason, idemp_rec = idempotency_manager.acquire_platform_idempotency_lock(
                        db, clip.id, "facebook", fb_account_id, fb_dest,
                        job.id, fb_upload.id, active_worker_id, allow_republish=allow_republish
                    )
                    if not locked:
                        log_publishing_audit(
                            db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                            stage="idempotency_gate", error=None, level="WARNING", attempts=fb_upload.attempts,
                            error_code="IDEMPOTENCY_BLOCKED", user_message=lock_reason,
                            worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="BLOCKED",
                            started_at=started_at, completed_at=datetime.utcnow()
                        )
                    else:
                        fb_stage = ["authorization_check"]
                        allowed, pause_reason, next_win = can_publish(db, "facebook")
                        if not allowed:
                            if next_win:
                                fb_upload.status = "paused_quota"
                                rescheduled_windows.append(next_win)
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=None, level="WARNING", attempts=fb_upload.attempts,
                                    error_code="PAUSED_QUOTA", user_message=pause_reason,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="PAUSED_QUOTA"
                                )
                            else:
                                fb_upload.status = "paused_unauthorized"
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=None, level="WARNING", attempts=fb_upload.attempts,
                                    error_code="PAUSED_UNAUTHORIZED", user_message=pause_reason,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="PAUSED_UNAUTHORIZED"
                                )
                            fb_upload.error_message = pause_reason
                            logger.info(f"Safely skipping Facebook for Job #{job.id}: {pause_reason}")
                        else:
                            try:
                                fb_stage[0] = "page_verification"
                                fb_upload.status = "upload_initialized"
                                fb_upload.attempts += 1
                                fb_upload.last_attempt_at = datetime.utcnow()
                                fb_upload.error_code = None
                                fb_upload.error_message = None
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.UPLOADING)
                                db.commit()

                                def fb_stage_callback(stage: str):
                                    fb_stage[0] = stage
                                    fb_upload.status = stage
                                    db.commit()

                                def fb_on_video_id(vid: str):
                                    fb_upload.external_post_id = str(vid)
                                    idempotency_manager.update_stage(
                                        db, fb_idemp_key, active_worker_id, PublishingState.PROCESSING, external_post_id=str(vid)
                                    )
                                    db.commit()

                                res = facebook_client.publish_reel(
                                    video_path,
                                    fb_caption,
                                    stage_callback=fb_stage_callback,
                                    on_video_id_created=fb_on_video_id
                                )
                                fb_upload.status = "published"
                                fb_upload.error_code = None
                                fb_upload.error_message = None
                                fb_upload.external_post_id = res["post_id"]
                                fb_upload.external_url = res["url"]
                                fb_upload.uploaded_at = datetime.utcnow()
                                idempotency_manager.update_stage(
                                    db, fb_idemp_key, active_worker_id, PublishingState.PUBLISHED,
                                    external_post_id=res["post_id"], external_url=res["url"]
                                )
                                record_upload_success(db, "facebook", quota_units=0)
                                results["facebook"] = res
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage="published", error=None, level="INFO", attempts=fb_upload.attempts,
                                    user_message=f"Facebook Reel published successfully: {res['url']}",
                                    worker_id=active_worker_id, account_id=fb_account_id,
                                    external_media_id=res["post_id"], published_url=res["url"],
                                    idempotency_decision="PUBLISHED", started_at=started_at, completed_at=datetime.utcnow()
                                )
                            except (FacebookRateLimitError, ClientFBRateLimitError) as e:
                                code = str(getattr(e, "code", "613"))
                                next_win = record_platform_restriction(db, "facebook", "rate_limited", str(e))
                                fb_upload.status = "paused_quota"
                                fb_upload.error_code = code
                                msg = getattr(e, "user_message", f"Paused: Facebook publishing rate limit ({code}). Rescheduled for next window.")
                                fb_upload.error_message = msg
                                rescheduled_windows.append(next_win)
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.PAUSED)
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=e, level="WARNING", attempts=fb_upload.attempts,
                                    error_code=code, user_message=msg,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="PAUSED_QUOTA"
                                )
                            except FacebookTokenExpiredError as e:
                                fb_upload.status = "failed"
                                fb_upload.error_code = str(e.code or "190")
                                fb_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Facebook upload paused due to expired token: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                                    error_code=str(e.code or "190"), user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="FAILED_PERMANENT"
                                )
                                meta_token_manager.mark_auth_expired("facebook", str(e.code or "190"), str(getattr(e, "subcode", 463)), e.user_message)
                            except FacebookPermissionError as e:
                                fb_upload.status = "failed"
                                fb_upload.error_code = str(e.code or "200")
                                fb_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Facebook upload failed due to permissions: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                                    error_code=str(e.code or "200"), user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="FAILED_PERMANENT"
                                )
                            except FacebookConfigError as e:
                                fb_upload.status = "failed"
                                fb_upload.error_code = str(e.code or "CONFIG_ERROR")
                                fb_upload.error_message = e.user_message
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Facebook upload failed due to configuration: {e.user_message}")
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage="page_verification" if fb_stage[0] in ("authorization_check", "upload_initialized") else fb_stage[0],
                                    error=e, level="ERROR", attempts=fb_upload.attempts,
                                    error_code=str(e.code or "CONFIG_ERROR"), user_message=e.user_message,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="FAILED_PERMANENT"
                                )
                            except FacebookUploadFailedError as e:
                                fb_upload.status = "failed"
                                fb_upload.error_code = "BINARY_TRANSFER_FAILED"
                                fb_upload.error_message = getattr(e, "user_message", str(e))
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Facebook binary upload failed: {e}")
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                                    error_code="BINARY_TRANSFER_FAILED", user_message=getattr(e, "user_message", str(e)),
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="FAILED"
                                )
                            except requests.Timeout as e:
                                fb_upload.status = "unknown"
                                fb_upload.error_code = "NETWORK_TIMEOUT"
                                msg = f"Facebook network request timed out: {e}. Marked UNKNOWN to prevent blind duplicate retry."
                                fb_upload.error_message = msg
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.UNKNOWN)
                                logger.warning(msg)
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=e, level="WARNING", attempts=fb_upload.attempts,
                                    error_code="NETWORK_TIMEOUT", user_message=msg,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="MARKED_UNKNOWN"
                                )
                            except Exception as e:
                                code = str(getattr(e, "code", "UPLOAD_FAILED"))
                                msg = getattr(e, "user_message", str(e))
                                fb_upload.status = "failed"
                                fb_upload.error_code = code
                                fb_upload.error_message = msg
                                idempotency_manager.update_stage(db, fb_idemp_key, active_worker_id, PublishingState.FAILED)
                                logger.error(f"Facebook upload failed: {e}")
                                log_publishing_audit(
                                    db, platform="facebook", publishing_job_id=job.id, clip_id=clip.id,
                                    stage=fb_stage[0], error=e, level="ERROR", attempts=fb_upload.attempts,
                                    error_code=code, user_message=msg,
                                    worker_id=active_worker_id, account_id=fb_account_id, idempotency_decision="FAILED"
                                )
                db.commit()

            # Evaluate overall job status & release worker lease
            all_uploads = db.query(PlatformUpload).filter_by(publishing_job_id=job.id).all()
            if all(u.status == "published" for u in all_uploads):
                final_status = "published"
            elif any(u.status == "unknown" for u in all_uploads):
                final_status = "unknown"
            elif any(u.status == "published" for u in all_uploads):
                final_status = "partially_published"
                if any(u.status == "paused_quota" for u in all_uploads) and rescheduled_windows:
                    job.scheduled_time = min(rescheduled_windows)
            elif any(u.status == "paused_quota" for u in all_uploads) and rescheduled_windows:
                final_status = "scheduled_next_window"
                job.scheduled_time = min(rescheduled_windows)
                logger.info(f"Job #{job.id} automatically scheduled for next permitted window: {job.scheduled_time}")
            elif any(u.status == "paused_unauthorized" for u in all_uploads):
                final_status = "paused_unauthorized"
                logger.info(f"Publishing Job #{job.id} safely paused: Platform authorization required.")
            else:
                final_status = "failed"

            idempotency_manager.release_job(db, job.id, final_status)
            db.commit()
            return results

publishing_manager = PublishingManager()
