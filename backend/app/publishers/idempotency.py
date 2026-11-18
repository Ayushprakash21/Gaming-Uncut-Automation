"""
Publishing Idempotency and State Machine Engine for Gaming Uncut.
Guarantees strict single-execution semantics across YouTube Shorts, Instagram Reels, and Facebook Reels.
Prevents duplicate publishing across concurrent workers, retries, restart recoveries, and double-clicks.
"""

import logging
import uuid
import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.app.models.schema import (
    PublishingJob, PlatformUpload, PublishingIdempotencyRecord, SystemLog
)

logger = logging.getLogger("gaming_uncut.idempotency")


class PublishingState:
    QUEUED = "QUEUED"
    CLAIMED = "CLAIMED"
    CONTAINER_CREATED = "CONTAINER_CREATED"
    UPLOADING = "UPLOADING"
    PROCESSING = "PROCESSING"
    FINISHED = "FINISHED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    PAUSED = "PAUSED"

    ALL_STATES = {
        QUEUED, CLAIMED, CONTAINER_CREATED, UPLOADING, PROCESSING,
        FINISHED, PUBLISHING, PUBLISHED, FAILED, ERROR, EXPIRED,
        UNKNOWN, RECONCILING, PAUSED
    }

    # Normalize status values to strict state machine states
    NORMALIZATION_MAP = {
        "pending": QUEUED,
        "queued": QUEUED,
        "QUEUED": QUEUED,
        "claimed": CLAIMED,
        "CLAIMED": CLAIMED,
        "container_created": CONTAINER_CREATED,
        "CONTAINER_CREATED": CONTAINER_CREATED,
        "upload_initialized": CONTAINER_CREATED,
        "video_transferring": UPLOADING,
        "transfer_completed": UPLOADING,
        "uploading": UPLOADING,
        "UPLOADING": UPLOADING,
        "instagram_processing": PROCESSING,
        "facebook_processing": PROCESSING,
        "processing": PROCESSING,
        "PROCESSING": PROCESSING,
        "finished": FINISHED,
        "FINISHED": FINISHED,
        "publishing": PUBLISHING,
        "PUBLISHING": PUBLISHING,
        "published": PUBLISHED,
        "PUBLISHED": PUBLISHED,
        "partially_published": QUEUED,
        "failed": FAILED,
        "FAILED": FAILED,
        "error": ERROR,
        "ERROR": ERROR,
        "expired": EXPIRED,
        "EXPIRED": EXPIRED,
        "unknown": UNKNOWN,
        "UNKNOWN": UNKNOWN,
        "ambiguous": UNKNOWN,
        "reconciling": RECONCILING,
        "RECONCILING": RECONCILING,
        "paused_quota": PAUSED,
        "paused_unauthorized": PAUSED,
        "paused": PAUSED,
        "PAUSED": PAUSED,
        "scheduled_next_window": PAUSED
    }

    @classmethod
    def normalize(cls, state: Optional[str]) -> str:
        if not state:
            return cls.QUEUED
        s = state.strip().lower()
        return cls.NORMALIZATION_MAP.get(s, state.upper())


class DuplicatePublishingError(Exception):
    """Raised when an attempt is made to publish an already published clip without explicit republish consent."""
    pass


class ConcurrentPublishingLockError(Exception):
    """Raised when another worker currently holds an active, unexpired lease on the publishing operation."""
    pass


class InvalidStateTransitionError(Exception):
    """Raised when a state transition violates the strict publishing state machine rules."""
    pass


# Valid directed transitions
VALID_TRANSITIONS = {
    PublishingState.QUEUED: {PublishingState.CLAIMED, PublishingState.PAUSED, PublishingState.FAILED},
    PublishingState.CLAIMED: {PublishingState.UPLOADING, PublishingState.PAUSED, PublishingState.FAILED, PublishingState.UNKNOWN},
    PublishingState.UPLOADING: {PublishingState.PROCESSING, PublishingState.PUBLISHED, PublishingState.FAILED, PublishingState.UNKNOWN, PublishingState.PAUSED},
    PublishingState.PROCESSING: {PublishingState.PUBLISHED, PublishingState.FAILED, PublishingState.UNKNOWN, PublishingState.PAUSED},
    PublishingState.UNKNOWN: {PublishingState.PUBLISHED, PublishingState.FAILED, PublishingState.QUEUED},
    PublishingState.FAILED: {PublishingState.QUEUED, PublishingState.CLAIMED},
    PublishingState.PAUSED: {PublishingState.QUEUED, PublishingState.CLAIMED},
    PublishingState.PUBLISHED: set()  # Terminal state: cannot transition without explicit republish override
}


def can_transition(from_state: str, to_state: str, allow_republish: bool = False) -> bool:
    """Validates if transition from from_state to to_state is valid."""
    norm_from = PublishingState.normalize(from_state)
    norm_to = PublishingState.normalize(to_state)

    if norm_from == norm_to:
        return True

    if norm_from == PublishingState.PUBLISHED and allow_republish:
        return norm_to in (PublishingState.QUEUED, PublishingState.CLAIMED)

    allowed_targets = VALID_TRANSITIONS.get(norm_from, set())
    return norm_to in allowed_targets


def build_idempotency_key(clip_id: int, platform: str, account_id: str, destination: str = "reels") -> str:
    """
    Constructs the canonical unique publishing identity key.
    Enforces format: clip:{clip_id}:{platform}:{account_id}:{destination}
    """
    plat = platform.lower().strip()
    dest = destination.lower().strip()
    acct = str(account_id).lower().strip() if account_id else "default"
    return f"clip:{clip_id}:{plat}:{acct}:{dest}"


def generate_worker_id(prefix: str = "worker") -> str:
    """Generates a unique worker ID incorporating process ID and UUID slice."""
    return f"{prefix}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class IdempotencyManager:
    """
    Enterprise-grade database-level idempotency and distributed lease coordinator.
    Ensures that parallel processes, scheduler ticks, or network retries never publish duplicates.
    """

    @staticmethod
    def claim_job_atomically(
        db: Session,
        job_id: int,
        worker_id: str,
        lease_seconds: int = 300,
        allow_republish: bool = False
    ) -> Tuple[bool, Optional[str], Optional[PublishingJob]]:
        """
        Atomically acquires a worker lease on a PublishingJob using database-level update synchronization.
        Returns: (success: bool, rejection_reason: Optional[str], job: Optional[PublishingJob])
        """
        now = datetime.utcnow()
        lock_expires = now + timedelta(seconds=lease_seconds)

        job = db.query(PublishingJob).filter_by(id=job_id).first()
        if not job:
            return False, f"Job #{job_id} does not exist.", None

        current_norm = PublishingState.normalize(job.status)

        # 1. Terminal State Check: Cannot claim an already PUBLISHED job unless allow_republish is explicitly True
        if current_norm == PublishingState.PUBLISHED and not (allow_republish or job.allow_republish):
            return False, f"Job #{job_id} is already PUBLISHED. Duplicate publishing prevented by idempotency gate.", job

        # 2. In-flight Lease Check: Is another worker actively running with an unexpired lease?
        if current_norm in (PublishingState.CLAIMED, PublishingState.UPLOADING, PublishingState.PROCESSING):
            if job.worker_id and job.worker_id != worker_id and job.lock_expires_at and job.lock_expires_at > now:
                remaining_sec = int((job.lock_expires_at - now).total_seconds())
                return False, f"Job #{job_id} is actively held by worker '{job.worker_id}' (lease expires in {remaining_sec}s).", job

        # 3. Atomic check-and-set claim via SQL UPDATE
        try:
            update_stmt = text(
                """
                UPDATE publishing_jobs
                SET status = 'CLAIMED',
                    worker_id = :worker_id,
                    locked_at = :now,
                    lock_expires_at = :lock_expires
                WHERE id = :job_id
                  AND (
                    worker_id = :worker_id
                    OR worker_id IS NULL
                    OR lock_expires_at IS NULL
                    OR lock_expires_at < :now
                    OR status NOT IN ('CLAIMED', 'UPLOADING', 'PROCESSING')
                  )
                """
            )
            res = db.execute(update_stmt, {
                "worker_id": worker_id,
                "now": now,
                "lock_expires": lock_expires,
                "job_id": job_id
            })
            db.commit()

            if res.rowcount == 0:
                # Concurrent worker won the race
                db.refresh(job)
                return False, f"Job #{job_id} was claimed concurrently by worker '{job.worker_id}'.", job

            db.refresh(job)
            logger.info(f"Worker '{worker_id}' successfully claimed PublishingJob #{job_id} (lease: {lease_seconds}s).")
            return True, None, job

        except Exception as e:
            db.rollback()
            logger.error(f"Atomic claim failed on Job #{job_id}: {e}")
            return False, f"Database transaction error claiming job #{job_id}: {e}", job

    @staticmethod
    def acquire_platform_idempotency_lock(
        db: Session,
        clip_id: int,
        platform: str,
        account_id: str,
        destination: str = "reels",
        job_id: Optional[int] = None,
        upload_id: Optional[int] = None,
        worker_id: str = "default_worker",
        lease_seconds: int = 300,
        allow_republish: bool = False
    ) -> Tuple[bool, Optional[str], Optional[PublishingIdempotencyRecord]]:
        """
        Acquires or validates the persistent platform-level idempotency record.
        Strictly prevents publishing the exact same clip twice to the same platform and account.
        """
        key = build_idempotency_key(clip_id, platform, account_id, destination)
        now = datetime.utcnow()
        lock_expires = now + timedelta(seconds=lease_seconds)

        record = db.query(PublishingIdempotencyRecord).filter_by(idempotency_key=key).first()

        if record:
            norm_status = PublishingState.normalize(record.status)

            # Rule 1: Clip already published to this platform & account
            if norm_status == PublishingState.PUBLISHED:
                if not allow_republish:
                    msg = (
                        f"Clip #{clip_id} was already published to {platform.capitalize()} "
                        f"(Account: {account_id}, Destination: {destination}). "
                        f"Existing Post ID: '{record.external_post_id}', URL: {record.external_url}. "
                        "Duplicate publishing blocked by idempotency engine."
                    )
                    logger.warning(msg)
                    return False, msg, record

            # Rule 2: Active lease held by another worker
            if norm_status in (PublishingState.CLAIMED, PublishingState.UPLOADING, PublishingState.PROCESSING):
                if record.worker_id and record.worker_id != worker_id and record.lock_expires_at and record.lock_expires_at > now:
                    remaining_sec = int((record.lock_expires_at - now).total_seconds())
                    msg = (
                        f"{platform.capitalize()} upload for Clip #{clip_id} is in-flight by worker "
                        f"'{record.worker_id}' (lease active for {remaining_sec}s). Aborting duplicate dispatch."
                    )
                    logger.warning(msg)
                    return False, msg, record

            # Refresh or take over expired lease / retry
            record.status = PublishingState.CLAIMED
            record.worker_id = worker_id
            record.locked_at = now
            record.lock_expires_at = lock_expires
            record.publishing_job_id = job_id
            if upload_id:
                record.platform_upload_id = upload_id
            record.updated_at = now
            db.commit()
            db.refresh(record)
            return True, None, record

        # Create new idempotency record
        try:
            new_record = PublishingIdempotencyRecord(
                idempotency_key=key,
                clip_id=clip_id,
                platform=platform.lower(),
                account_id=str(account_id),
                destination=destination.lower(),
                publishing_job_id=job_id,
                platform_upload_id=upload_id,
                status=PublishingState.CLAIMED,
                worker_id=worker_id,
                locked_at=now,
                lock_expires_at=lock_expires,
                created_at=now,
                updated_at=now
            )
            db.add(new_record)
            db.commit()
            db.refresh(new_record)
            return True, None, new_record
        except Exception as e:
            db.rollback()
            # In case another worker inserted concurrently, check again
            existing = db.query(PublishingIdempotencyRecord).filter_by(idempotency_key=key).first()
            if existing:
                return False, f"Concurrent lock collision for key {key}: {e}", existing
            return False, f"Failed to persist idempotency lock {key}: {e}", None

    @staticmethod
    def update_stage(
        db: Session,
        idempotency_key: str,
        worker_id: str,
        new_state: str,
        external_post_id: Optional[str] = None,
        external_url: Optional[str] = None,
        extend_lease_seconds: int = 300
    ) -> bool:
        """
        Updates the state machine stage and immediately persists external media IDs / URLs.
        """
        record = db.query(PublishingIdempotencyRecord).filter_by(idempotency_key=idempotency_key).first()
        if not record:
            return False

        norm_state = PublishingState.normalize(new_state)
        now = datetime.utcnow()

        record.status = norm_state
        if external_post_id:
            record.external_post_id = str(external_post_id)
        if external_url:
            record.external_url = str(external_url)

        if norm_state == PublishingState.PUBLISHED:
            # Release worker lock upon completion
            record.worker_id = None
            record.lock_expires_at = None
        else:
            # Heartbeat lease extension for ongoing stages
            record.lock_expires_at = now + timedelta(seconds=extend_lease_seconds)

        record.updated_at = now
        db.commit()
        return True

    @staticmethod
    def release_job(db: Session, job_id: int, final_status: str) -> None:
        """Releases the job lease and sets the final overall status."""
        job = db.query(PublishingJob).filter_by(id=job_id).first()
        if job:
            norm = PublishingState.normalize(final_status)
            job.status = "published" if norm == PublishingState.PUBLISHED else ("failed" if norm == PublishingState.FAILED else norm)
            job.worker_id = None
            job.locked_at = None
            job.lock_expires_at = None
            db.commit()

    @staticmethod
    def reconcile_platform_publication(
        db: Session,
        platform: str,
        clip_id: int,
        account_id: str,
        destination: str = "reels",
        candidate_external_id: Optional[str] = None,
        candidate_title: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Reconciles ambiguous or UNKNOWN states against official platform APIs before permitting retries.
        Returns verified publication details if the video was already published remotely, preventing duplicate uploads.
        """
        plat = platform.lower().strip()
        key = build_idempotency_key(clip_id, plat, account_id, destination)
        record = db.query(PublishingIdempotencyRecord).filter_by(idempotency_key=key).first()

        # If locally already recorded as published
        if record and PublishingState.normalize(record.status) == PublishingState.PUBLISHED:
            return {
                "published": True,
                "platform": plat,
                "post_id": record.external_post_id,
                "url": record.external_url,
                "reconciled_via": "local_idempotency_record"
            }

        target_id = candidate_external_id or (record.external_post_id if record else None)

        if plat == "facebook":
            if target_id:
                try:
                    import requests
                    from backend.app.publishers.meta_token_manager import meta_token_manager
                    creds = meta_token_manager.get_facebook_credentials()
                    token = creds.get("token")
                    if token:
                        res = requests.get(
                            f"https://graph.facebook.com/v21.0/{target_id}",
                            headers={"Authorization": f"Bearer {token}"},
                            params={"fields": "id,status,published,permalink_url"},
                            timeout=10
                        )
                        if res.status_code == 200:
                            data = res.json()
                            status_obj = data.get("status", {})
                            is_ready = status_obj.get("video_status") == "ready" or data.get("published") is True
                            if is_ready or data.get("permalink_url"):
                                permalink = data.get("permalink_url") or f"https://www.facebook.com/reel/{target_id}"
                                logger.info(f"Reconciled Facebook publication for target #{target_id}: {permalink}")
                                return {
                                    "published": True,
                                    "platform": "facebook",
                                    "post_id": target_id,
                                    "url": permalink,
                                    "reconciled_via": "facebook_graph_api"
                                }
                except Exception as e:
                    logger.debug(f"Facebook reconciliation check notice: {e}")

        elif plat == "instagram":
            try:
                import requests
                from backend.app.publishers.meta_token_manager import meta_token_manager
                creds = meta_token_manager.get_instagram_credentials()
                token = creds.get("token")
                ig_id = creds.get("instagram_account_id")

                if token and target_id:
                    headers = {"Authorization": f"Bearer {token}"}
                    # 1. Check if target_id is an already published media object
                    try:
                        res = requests.get(
                            f"https://graph.facebook.com/v21.0/{target_id}",
                            headers=headers,
                            params={"fields": "id,permalink,media_type,shortcode"},
                            timeout=10
                        )
                        if res.status_code == 200 and "permalink" in res.json():
                            m_data = res.json()
                            logger.info(f"Reconciled Instagram published media for #{target_id}: {m_data['permalink']}")
                            return {
                                "published": True,
                                "platform": "instagram",
                                "post_id": target_id,
                                "url": m_data["permalink"],
                                "reconciled_via": "instagram_media_lookup"
                            }
                    except Exception as e:
                        logger.debug(f"Instagram media lookup notice: {e}")

                    # 2. Check if target_id is an Instagram container
                    try:
                        c_res = requests.get(
                            f"https://graph.facebook.com/v21.0/{target_id}",
                            headers=headers,
                            params={"fields": "status_code,status"},
                            timeout=10
                        )
                        if c_res.status_code == 200:
                            c_data = c_res.json()
                            status_code = str(c_data.get("status_code", "")).upper()
                            if status_code == "FINISHED":
                                return {
                                    "published": False,
                                    "ready_to_publish_container": True,
                                    "container_id": target_id,
                                    "platform": "instagram",
                                    "reconciled_via": "instagram_finished_container"
                                }
                            elif status_code == "IN_PROGRESS":
                                return {
                                    "published": False,
                                    "in_progress_container": True,
                                    "container_id": target_id,
                                    "status_text": c_data.get("status", "Media is still being processed."),
                                    "platform": "instagram",
                                    "reconciled_via": "instagram_in_progress_container"
                                }
                            elif status_code == "PUBLISHED":
                                return {
                                    "published": True,
                                    "platform": "instagram",
                                    "post_id": target_id,
                                    "url": f"https://www.instagram.com/reel/{target_id}",
                                    "reconciled_via": "instagram_container_published"
                                }
                            elif status_code == "ERROR":
                                return {
                                    "published": False,
                                    "error_container": True,
                                    "container_id": target_id,
                                    "error_message": c_data.get("status", "Video failed Meta transcoding requirements"),
                                    "platform": "instagram",
                                    "reconciled_via": "instagram_error_container"
                                }
                            elif status_code == "EXPIRED":
                                return {
                                    "published": False,
                                    "expired_container": True,
                                    "container_id": target_id,
                                    "platform": "instagram",
                                    "reconciled_via": "instagram_expired_container"
                                }
                    except Exception as e:
                        logger.debug(f"Instagram container lookup notice: {e}")
            except Exception as e:
                logger.debug(f"Instagram reconciliation check notice: {e}")

        elif plat == "youtube":
            if target_id:
                try:
                    from backend.app.publishers.youtube.youtube_client import youtube_client
                    service = youtube_client.get_service()
                    res = service.videos().list(part="snippet,status", id=target_id).execute()
                    if res.get("items"):
                        shorts_url = f"https://www.youtube.com/shorts/{target_id}"
                        logger.info(f"Reconciled YouTube publication for #{target_id}: {shorts_url}")
                        return {
                            "published": True,
                            "platform": "youtube",
                            "post_id": target_id,
                            "url": shorts_url,
                            "reconciled_via": "youtube_data_api"
                        }
                except Exception as e:
                    logger.debug(f"YouTube reconciliation check notice: {e}")

        return None


idempotency_manager = IdempotencyManager()
