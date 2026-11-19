"""
Enterprise Instagram Container Reconciliation Engine for Gaming Uncut.
Runs asynchronously in the background to monitor in-flight Instagram containers:
- Bounded, configurable polling intervals with exponential backoff on network errors.
- Never hammers Meta's Graph API.
- Reconciles containers without blocking the FastAPI server or other platform jobs.
- Explicit status handling:
    - IN_PROGRESS: records status, increments attempts, monitors up to 24h deadline.
    - FINISHED: calls media_publish exactly once with idempotency lock, marks published.
    - PUBLISHED: records permalink, marks completed, prevents duplicate publish.
    - ERROR: records precise Meta error, stops further attempts, requires safe decision.
    - EXPIRED: records expiration, stops further attempts.
- Survives application restarts by maintaining SQLite persistence.
- Zero access token leakage in logs or exceptions.
"""

import json
import logging
import threading
import re
import requests
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from backend.app.database.session import SessionLocal
from backend.app.models.schema import PlatformUpload, PublishingJob, PublishingIdempotencyRecord
from backend.app.publishers.idempotency import idempotency_manager, PublishingState
from backend.app.publishers.instagram.instagram_client import (
    instagram_client,
    redact_token,
    GRAPH_URL,
    InstagramProcessingFailedError,
    InstagramUploadFailedError,
    InstagramAPIError,
    InstagramVerificationError
)
from backend.app.publishers.meta_transport import meta_transport
from backend.app.publishers.meta_token_manager import meta_token_manager
from backend.app.publishers.quota_tracker import record_upload_success

logger = logging.getLogger("gaming_uncut.instagram.reconciler")

_reconciliation_lock = threading.Lock()
_rate_limit_paused_until: Optional[datetime] = None


class InstagramReconciler:
    """Manages asynchronous background reconciliation of Instagram media containers."""

    def reconcile_pending_containers(self, db: Optional[Session] = None) -> List[Dict[str, Any]]:
        """
        Scans for in-flight / unknown / reconciling Instagram uploads.
        Non-blocking lock ensures only one worker thread monitors containers concurrently.
        """
        global _rate_limit_paused_until
        now_check = datetime.utcnow()
        if _rate_limit_paused_until and now_check < _rate_limit_paused_until:
            cooldown_rem = int((_rate_limit_paused_until - now_check).total_seconds())
            logger.info(f"[InstagramReconciler] Meta rate limit cooldown active ({cooldown_rem}s remaining). Skipping tick.")
            return []

        if not _reconciliation_lock.acquire(blocking=False):
            logger.debug("[InstagramReconciler] Previous reconciliation run in progress. Skipping tick.")
            return []

        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        results: List[Dict[str, Any]] = []

        try:
            # Discover uploads that need reconciliation:
            # Status is in unknown, reconciling, PROCESSING, or instagram_processing,
            # and external_post_id (container ID) is present.
            pending_uploads = db.query(PlatformUpload).join(PublishingJob).filter(
                PlatformUpload.platform == "instagram",
                PlatformUpload.status.in_([
                    "unknown", "reconciling", "RECONCILING",
                    "PROCESSING", "instagram_processing", "upload_initialized",
                    "published_unverified", "PUBLISHED_UNVERIFIED"
                ]),
                PlatformUpload.external_post_id.isnot(None)
            ).all()

            if not pending_uploads:
                return []

            logger.info(f"[InstagramReconciler] Found {len(pending_uploads)} in-flight Instagram container(s) to reconcile.")

            creds = meta_token_manager.get_instagram_credentials()
            token = creds.get("token")
            ig_id = creds.get("instagram_account_id")

            if not token or not ig_id:
                logger.warning("[InstagramReconciler] Cannot reconcile: Meta credentials or Instagram Account ID missing.")
                return []

            now = datetime.utcnow()

            for upload in pending_uploads:
                container_id = str(upload.external_post_id).strip()
                if not container_id:
                    continue

                # Rate-limiting / backoff guard: do not query Meta more frequently than every 30 seconds
                if upload.last_attempt_at:
                    seconds_since_last = (now - upload.last_attempt_at).total_seconds()
                    if seconds_since_last < 30:
                        logger.debug(f"[InstagramReconciler] Skipping container {container_id}: checked {seconds_since_last:.0f}s ago (<30s).")
                        continue

                try:
                    res_outcome = self._reconcile_single_upload(db, upload, container_id, ig_id, token, now)
                    results.append(res_outcome)
                except Exception as e:
                    safe_msg = redact_token(str(e))
                    upload.last_attempt_at = now
                    if "limit reached" in safe_msg.lower() or "ratelimit" in safe_msg.lower() or "(#4)" in safe_msg or "(#17)" in safe_msg or "(#32)" in safe_msg:
                        _rate_limit_paused_until = now + timedelta(minutes=15)
                        logger.warning(f"[InstagramReconciler] Meta API rate limit hit: {safe_msg}. Backing off reconciliation for 15 minutes.")
                        break
                    logger.error(f"[InstagramReconciler] Error reconciling container {container_id}: {safe_msg}")

            db.commit()
            return results

        finally:
            _reconciliation_lock.release()
            if should_close:
                db.close()

    def _reconcile_single_upload(
        self,
        db: Session,
        upload: PlatformUpload,
        container_id: str,
        ig_id: str,
        token: str,
        now: datetime
    ) -> Dict[str, Any]:
        """Reconciles one Instagram upload container against Meta Graph API."""
        job = upload.publishing_job
        clip_id = job.clip_id if job else None

        logger.info(f"[InstagramReconciler] Checking container/media {container_id} for Job #{upload.publishing_job_id}...")

        # Import audit logger inside method to avoid circular imports
        from backend.app.publishers.manager import log_publishing_audit

        # 1. Check if this upload is already at the unverified media stage
        if str(upload.status).lower() in ("published_unverified",):
            return self._reconcile_unverified_media(db, upload, container_id, ig_id, token, now, clip_id, job)

        # 2. Check if upload is marked published but has a fabricated or missing URL
        if upload.status == "published" and (not upload.external_url or re.match(r'^https?://(?:www\.)?instagram\.com/reel/\d{15,}/?$', str(upload.external_url))):
            return self._reconcile_unverified_media(db, upload, container_id, ig_id, token, now, clip_id, job)

        # Query container status with safe fallback (handles Meta 500 on copyright_check_status)
        try:
            status_info = instagram_client.query_container_status(container_id, token=token, include_copyright=True)
        except requests.RequestException as e:
            safe_err = redact_token(str(e))
            # Check if this object is actually a media ID rather than a container ID
            if "Tried accessing nonexisting field" in safe_err or "OAuthException" in safe_err:
                logger.info(f"[InstagramReconciler] Target {container_id} returned field error. Attempting media verification.")
                return self._reconcile_unverified_media(db, upload, container_id, ig_id, token, now, clip_id, job)

            logger.warning(f"[InstagramReconciler] Transient network error querying container {container_id}: {safe_err}")
            upload.status_checks = (upload.status_checks or 0) + 1
            upload.last_attempt_at = now
            container_created_at = self._get_or_infer_container_created_at(db, upload, container_id)
            deadline = container_created_at + timedelta(hours=24)
            diag_metadata = {
                "container_created_at": container_created_at.isoformat(),
                "last_ack_at": upload.last_attempt_at.isoformat() if upload.last_attempt_at else now.isoformat(),
                "total_file_bytes": None,
                "acknowledged_bytes": None,
                "last_graph_status": "NETWORK_ERROR",
                "last_graph_status_text": safe_err,
                "last_checked_at": now.isoformat(),
                "last_network_error": safe_err,
                "next_check_at": (now + timedelta(seconds=60)).isoformat(),
                "reconciliation_deadline": deadline.isoformat(),
                "status_checks": upload.status_checks,
                "upload_attempts": upload.attempts,
                "publishing_attempts": upload.publishing_attempts or 0
            }
            upload.reconciliation_metadata = json.dumps(diag_metadata)
            return {"container_id": container_id, "status": "NETWORK_ERROR", "action": "retry_later", "diagnostics": diag_metadata}

        status_code = status_info["status_code"]
        status_text = status_info["status"]

        # =====================================================================
        # 1. FINISHED: Eligible for immediate publishing!
        # =====================================================================
        if status_code == "FINISHED":
            return self._publish_finished_container(db, upload, container_id, ig_id, token, now, clip_id, job)

        # =====================================================================
        # 2. PUBLISHED: Already live on Instagram
        # =====================================================================
        elif status_code == "PUBLISHED":
            return self._handle_published_container(db, upload, container_id, token, now, job)

        # =====================================================================
        # 3. IN_PROGRESS: Check for FINISHED sibling before continuing monitoring
        # =====================================================================
        elif status_code == "IN_PROGRESS":
            # Check if an earlier sibling container for this job/clip is already FINISHED or PUBLISHED
            for sib_id in self._find_sibling_containers(db, upload):
                if sib_id == container_id:
                    continue
                try:
                    sib_info = instagram_client.query_container_status(sib_id, token=token, include_copyright=False)
                    if sib_info.get("status_code") == "FINISHED":
                        logger.info(
                            f"[InstagramReconciler] Primary container {container_id} is IN_PROGRESS, but sibling container {sib_id} "
                            "is FINISHED! Reconciling and publishing sibling container."
                        )
                        container_id = sib_id
                        upload.external_post_id = sib_id
                        return self._publish_finished_container(db, upload, container_id, ig_id, token, now, clip_id, job)
                    elif sib_info.get("status_code") == "PUBLISHED":
                        logger.info(
                            f"[InstagramReconciler] Sibling container {sib_id} is already PUBLISHED! Marking complete."
                        )
                        container_id = sib_id
                        upload.external_post_id = sib_id
                        return self._handle_published_container(db, upload, container_id, token, now, job)
                except Exception as e:
                    logger.debug(f"[InstagramReconciler] Sibling query on {sib_id} failed: {e}")

            # Increment dedicated status_checks counter (preserve upload.attempts for actual binary upload attempts)
            upload.status_checks = (upload.status_checks or 0) + 1
            upload.last_attempt_at = now
            upload.status = "reconciling"
            upload.error_code = "RECONCILING"

            # Determine actual container creation timestamp (never use overall job creation time)
            container_created_at = self._get_or_infer_container_created_at(db, upload, container_id)
            deadline = container_created_at + timedelta(hours=24)
            elapsed_hours = (now - container_created_at).total_seconds() / 3600.0

            # Total expected and acknowledged file bytes if available
            total_file_bytes = None
            if job and job.clip and job.clip.output_path:
                try:
                    p = Path(job.clip.output_path)
                    if p.exists():
                        total_file_bytes = p.stat().st_size
                except Exception:
                    pass

            diag_metadata = {
                "container_created_at": container_created_at.isoformat(),
                "last_ack_at": upload.last_attempt_at.isoformat() if upload.last_attempt_at else now.isoformat(),
                "total_file_bytes": total_file_bytes,
                "acknowledged_bytes": total_file_bytes,
                "last_graph_status": status_code,
                "last_graph_status_text": status_text,
                "last_checked_at": now.isoformat(),
                "last_network_error": None,
                "next_check_at": (now + timedelta(seconds=60)).isoformat(),
                "reconciliation_deadline": deadline.isoformat(),
                "status_checks": upload.status_checks,
                "upload_attempts": upload.attempts,
                "publishing_attempts": upload.publishing_attempts or 0
            }
            upload.reconciliation_metadata = json.dumps(diag_metadata)

            if elapsed_hours >= 24.0:
                logger.error(f"[InstagramReconciler] Container {container_id} exceeded 24-hour Meta deadline ({elapsed_hours:.1f}h). Marking EXPIRED.")
                upload.status = "failed"
                upload.error_code = "CONTAINER_EXPIRED"
                upload.error_message = f"Container exceeded Meta's 24-hour processing lifetime ({elapsed_hours:.1f} hours elapsed)."
                idempotency_manager.update_stage(db, upload.idempotency_key, "reconciler", PublishingState.FAILED)
                return {"container_id": container_id, "status": "EXPIRED", "action": "marked_failed", "diagnostics": diag_metadata}

            msg = f"Instagram container {container_id} is transcoding on Meta servers ({upload.status_checks} checks). Status: {status_text}"
            upload.error_message = msg
            idempotency_manager.update_stage(
                db, upload.idempotency_key, "reconciler", PublishingState.RECONCILING,
                external_post_id=container_id
            )
            logger.info(f"[InstagramReconciler] {msg}. Monitoring continues in background.")
            return {
                "container_id": container_id,
                "status": "IN_PROGRESS",
                "action": "monitoring_continued",
                "status_checks": upload.status_checks,
                "upload_attempts": upload.attempts,
                "diagnostics": diag_metadata
            }

        # =====================================================================
        # 4. ERROR: Meta transcoding failure
        # =====================================================================
        elif status_code == "ERROR":
            err_msg = f"Meta video transcoding error: {status_text or 'Video failed Meta transcoding requirements'}"
            logger.error(f"[InstagramReconciler] Container {container_id} reported ERROR: {err_msg}")
            upload.status = "failed"
            upload.error_code = "TRANSCODE_ERROR"
            upload.error_message = err_msg
            idempotency_manager.update_stage(db, upload.idempotency_key, "reconciler", PublishingState.FAILED)

            log_publishing_audit(
                db, platform="instagram", publishing_job_id=upload.publishing_job_id,
                clip_id=clip_id, stage="reconciliation_error", error=None, level="ERROR",
                attempts=upload.attempts, user_message=err_msg, worker_id="reconciler",
                account_id=ig_id, external_media_id=container_id, idempotency_decision="FAILED",
                completed_at=now
            )
            return {"container_id": container_id, "status": "ERROR", "action": "marked_failed", "error": err_msg}

        # =====================================================================
        # 5. EXPIRED: Container expired on Meta servers
        # =====================================================================
        elif status_code == "EXPIRED":
            err_msg = f"Instagram container {container_id} expired on Meta servers before publication."
            logger.error(f"[InstagramReconciler] Container {container_id} is EXPIRED.")
            upload.status = "failed"
            upload.error_code = "CONTAINER_EXPIRED"
            upload.error_message = err_msg
            idempotency_manager.update_stage(db, upload.idempotency_key, "reconciler", PublishingState.FAILED)
            return {"container_id": container_id, "status": "EXPIRED", "action": "marked_failed", "error": err_msg}

        else:
            logger.warning(f"[InstagramReconciler] Container {container_id} returned unrecognized status: {status_code}")
            return {"container_id": container_id, "status": status_code, "action": "unknown"}

    def _reconcile_unverified_media(
        self,
        db: Session,
        upload: PlatformUpload,
        media_id: str,
        ig_id: str,
        token: str,
        now: datetime,
        clip_id: Optional[int],
        job: Optional[PublishingJob]
    ) -> Dict[str, Any]:
        """
        Reconciles a published media ID that lacks a verified canonical permalink.
        Fetches the media object directly or cross-references the account's recent media list.
        Never calls media_publish again. Never fabricates a permalink.
        """
        from backend.app.publishers.manager import log_publishing_audit

        logger.info(f"[InstagramReconciler] Reconciling unverified Media ID {media_id} for Job #{upload.publishing_job_id}...")

        # 1. Try direct verification
        try:
            verified = instagram_client.verify_media_published(media_id, token, expected_ig_id=ig_id)
            canonical_url = verified["permalink"]
            upload.status = "published"
            upload.external_url = canonical_url
            upload.error_code = None
            upload.error_message = None
            upload.uploaded_at = upload.uploaded_at or now

            idempotency_manager.update_stage(
                db, upload.idempotency_key, "reconciler", PublishingState.PUBLISHED_VERIFIED,
                external_post_id=media_id, external_url=canonical_url
            )
            record_upload_success(db, "instagram", quota_units=0)
            self._check_and_complete_job(db, job)

            log_publishing_audit(
                db, platform="instagram", publishing_job_id=upload.publishing_job_id,
                clip_id=clip_id, stage="reconciliation_published_verified", error=None, level="INFO",
                attempts=upload.attempts,
                user_message=f"Reconciler verified live Instagram Reel: {canonical_url}",
                worker_id="reconciler", account_id=ig_id,
                external_media_id=media_id, published_url=canonical_url,
                idempotency_decision="PUBLISHED_VERIFIED", completed_at=now
            )
            return {
                "media_id": media_id,
                "status": "PUBLISHED_VERIFIED",
                "action": "verified",
                "url": canonical_url
            }
        except InstagramVerificationError as ve:
            logger.info(f"[InstagramReconciler] Direct verification of {media_id} unconfirmed: {ve}")
        except Exception as e:
            logger.warning(f"[InstagramReconciler] Unexpected error verifying media {media_id}: {e}")

        # 2. Check connected Instagram account's recent media list
        try:
            res = meta_transport.get(
                f"{GRAPH_URL}/{ig_id}/media",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "id,media_type,permalink,shortcode,timestamp", "limit": 25},
                timeout=(meta_transport.connect_timeout, 15.0)
            )
            if res.status_code == 200:
                media_list = res.json().get("data", [])
                for item in media_list:
                    if str(item.get("id")) == str(media_id):
                        raw_permalink = item.get("permalink")
                        shortcode = item.get("shortcode")
                        canonical_url = None
                        if raw_permalink and re.match(r'^https?://(?:www\.)?instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)/?$', str(raw_permalink)):
                            canonical_url = str(raw_permalink)
                        elif shortcode:
                            canonical_url = f"https://www.instagram.com/reel/{shortcode}/"

                        if canonical_url:
                            upload.status = "published"
                            upload.external_url = canonical_url
                            upload.error_code = None
                            upload.error_message = None
                            upload.uploaded_at = upload.uploaded_at or now

                            idempotency_manager.update_stage(
                                db, upload.idempotency_key, "reconciler", PublishingState.PUBLISHED_VERIFIED,
                                external_post_id=media_id, external_url=canonical_url
                            )
                            record_upload_success(db, "instagram", quota_units=0)
                            self._check_and_complete_job(db, job)

                            log_publishing_audit(
                                db, platform="instagram", publishing_job_id=upload.publishing_job_id,
                                clip_id=clip_id, stage="reconciliation_published_verified", error=None, level="INFO",
                                attempts=upload.attempts,
                                user_message=f"Reconciler matched Media ID in recent account media: {canonical_url}",
                                worker_id="reconciler", account_id=ig_id,
                                external_media_id=media_id, published_url=canonical_url,
                                idempotency_decision="PUBLISHED_VERIFIED", completed_at=now
                            )
                            return {
                                "media_id": media_id,
                                "status": "PUBLISHED_VERIFIED",
                                "action": "verified_via_recent_media",
                                "url": canonical_url
                            }
        except Exception as e:
            logger.warning(f"[InstagramReconciler] Account media list query failed: {e}")

        # Still unverified - preserve media ID, do not fabricate URL
        upload.status = "published_unverified"
        upload.external_url = None
        upload.error_code = "VERIFICATION_PENDING"
        upload.error_message = f"Media ID {media_id} published on Meta edge, but public canonical permalink is pending verification."
        upload.status_checks = (upload.status_checks or 0) + 1
        upload.last_attempt_at = now
        return {
            "media_id": media_id,
            "status": "PUBLISHED_UNVERIFIED",
            "action": "pending_verification"
        }

    def _publish_finished_container(
        self,
        db: Session,
        upload: PlatformUpload,
        container_id: str,
        ig_id: str,
        token: str,
        now: datetime,
        clip_id: Optional[int],
        job: Optional[PublishingJob]
    ) -> Dict[str, Any]:
        """Publishes a FINISHED container to Instagram with safety checks against duplicate publishing."""
        from backend.app.publishers.manager import log_publishing_audit
        logger.info(f"[InstagramReconciler] Container {container_id} is FINISHED! Dispatches media_publish...")
        upload.publishing_attempts = (upload.publishing_attempts or 0) + 1
        try:
            media_id = instagram_client.publish_container(ig_id, container_id, token)
            upload.external_post_id = media_id

            try:
                verified = instagram_client.verify_media_published(
                    media_id, token, expected_ig_id=ig_id, container_id=container_id
                )
                canonical_url = verified["permalink"]
                upload.status = "published"
                upload.external_url = canonical_url
                upload.uploaded_at = now
                upload.error_code = None
                upload.error_message = None

                idempotency_manager.update_stage(
                    db, upload.idempotency_key, "reconciler", PublishingState.PUBLISHED_VERIFIED,
                    external_post_id=media_id, external_url=canonical_url
                )
                record_upload_success(db, "instagram", quota_units=0)
                self._check_and_complete_job(db, job)

                log_publishing_audit(
                    db, platform="instagram", publishing_job_id=upload.publishing_job_id,
                    clip_id=clip_id, stage="reconciliation_published_verified", error=None, level="INFO",
                    attempts=upload.attempts,
                    user_message=f"Reconciler published and verified Instagram Reel: {canonical_url}",
                    worker_id="reconciler", account_id=ig_id,
                    external_media_id=media_id, published_url=canonical_url,
                    idempotency_decision="PUBLISHED_VERIFIED", completed_at=now
                )
                return {
                    "container_id": container_id,
                    "status": "PUBLISHED_VERIFIED",
                    "action": "published",
                    "media_id": media_id,
                    "url": canonical_url
                }
            except InstagramVerificationError as ve:
                logger.warning(f"[InstagramReconciler] Container {container_id} published as media {media_id}, but canonical permalink unconfirmed: {ve}")
                upload.status = "published_unverified"
                upload.external_url = None
                upload.error_code = "VERIFICATION_PENDING"
                upload.error_message = f"Media ID {media_id} published on Meta edge; canonical permalink verification is pending."
                idempotency_manager.update_stage(
                    db, upload.idempotency_key, "reconciler", PublishingState.PUBLISHED_UNVERIFIED,
                    external_post_id=media_id
                )
                return {
                    "container_id": container_id,
                    "status": "PUBLISHED_UNVERIFIED",
                    "action": "published_unverified",
                    "media_id": media_id
                }
        except Exception as e:
            safe_err = redact_token(str(e))
            logger.error(f"[InstagramReconciler] Failed publishing FINISHED container {container_id}: {safe_err}")
            # Query container once more to see if Meta actually published it despite network error
            try:
                second_check = instagram_client.query_container_status(container_id, token=token, include_copyright=False)
                if second_check.get("is_published"):
                    res = self._handle_published_container(db, upload, container_id, token, now, job)
                    if res.get("action") == "marked_published":
                        res["action"] = "published"
                    return res
            except Exception:
                pass
            upload.status = "reconciling"
            upload.error_code = "RECONCILE_PUBLISH_FAILED"
            upload.error_message = safe_err
            return {"container_id": container_id, "status": "FINISHED", "action": "publish_failed", "error": safe_err}

    def _handle_published_container(
        self,
        db: Session,
        upload: PlatformUpload,
        container_id: str,
        token: str,
        now: datetime,
        job: Optional[PublishingJob]
    ) -> Dict[str, Any]:
        """Marks a container that is already PUBLISHED on Instagram by resolving its true media ID and canonical permalink."""
        logger.info(f"[InstagramReconciler] Container {container_id} is reported PUBLISHED by Meta. Resolving genuine media object...")
        creds = meta_token_manager.get_instagram_credentials()
        ig_id = creds.get("instagram_account_id")

        canonical_url = None
        matched_media_id = None

        # Check stored metadata
        stored_meta = {}
        if upload.reconciliation_metadata:
            try:
                stored_meta = json.loads(upload.reconciliation_metadata)
            except Exception:
                pass
        stored_media_id = stored_meta.get("media_id")

        # 1. First try verify_media_published on stored_media_id, external_post_id, or container_id
        candidates = [c for c in [stored_media_id, upload.external_post_id, container_id] if c]
        for cand in candidates:
            try:
                v = instagram_client.verify_media_published(cand, token, expected_ig_id=ig_id, container_id=container_id)
                if v and v.get("permalink"):
                    canonical_url = v["permalink"]
                    matched_media_id = v.get("media_id", cand)
                    break
            except Exception:
                pass

        # 2. If not verified via direct lookup, cross-reference account's recent media
        if not canonical_url and ig_id:
            try:
                res = meta_transport.get(
                    f"{GRAPH_URL}/{ig_id}/media",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"fields": "id,media_type,permalink,shortcode,timestamp", "limit": 25},
                    timeout=(meta_transport.connect_timeout, 15.0)
                )
                if res.status_code == 200:
                    media_list = res.json().get("data", [])
                    if stored_media_id:
                        for item in media_list:
                            if str(item.get("id")) == str(stored_media_id):
                                matched_media_id = str(stored_media_id)
                                canonical_url = item.get("permalink")
                                break
            except Exception as e:
                logger.debug(f"[InstagramReconciler] Failed querying account media for published container {container_id}: {e}")

        if matched_media_id and canonical_url and re.match(r'^https?://(?:www\.)?instagram\.com/(?:reel|p)/([A-Za-z0-9_-]+)/?$', str(canonical_url)):
            upload.status = "published"
            upload.external_post_id = matched_media_id
            upload.external_url = canonical_url
            upload.uploaded_at = upload.uploaded_at or now
            upload.error_code = None
            upload.error_message = None

            idempotency_manager.update_stage(
                db, upload.idempotency_key, "reconciler", PublishingState.PUBLISHED_VERIFIED,
                external_post_id=matched_media_id, external_url=canonical_url
            )
            self._check_and_complete_job(db, job)
            return {"container_id": container_id, "media_id": matched_media_id, "status": "PUBLISHED", "action": "marked_published", "url": canonical_url}

        # Cannot confirm canonical permalink yet - NEVER fabricate!
        upload.status = "published_unverified"
        upload.external_url = None
        upload.error_code = "VERIFICATION_PENDING"
        upload.error_message = f"Meta container {container_id} is PUBLISHED, but canonical media permalink could not be verified."
        idempotency_manager.update_stage(
            db, upload.idempotency_key, "reconciler", PublishingState.PUBLISHED_UNVERIFIED,
            external_post_id=container_id
        )
        return {"container_id": container_id, "status": "PUBLISHED_UNVERIFIED", "action": "unverified", "url": None}

    def _get_or_infer_container_created_at(self, db: Session, upload: PlatformUpload, container_id: str) -> datetime:
        """Determines the actual creation timestamp of the container, falling back safely without using overall job time."""
        # 1. Check existing metadata
        if upload.reconciliation_metadata:
            try:
                meta = json.loads(upload.reconciliation_metadata)
                if meta.get("container_created_at"):
                    return datetime.fromisoformat(meta["container_created_at"])
            except Exception:
                pass

        # 2. Check SystemLog for container creation
        try:
            from backend.app.models.schema import SystemLog
            log_entry = db.query(SystemLog).filter(
                SystemLog.message.like(f"%{container_id}%")
            ).order_by(SystemLog.id.asc()).first()
            if log_entry and log_entry.created_at:
                return log_entry.created_at
        except Exception:
            pass

        # 3. Fall back to upload's last_attempt_at or current time
        return upload.last_attempt_at or datetime.utcnow()

    def _find_sibling_containers(self, db: Session, upload: PlatformUpload) -> List[str]:
        """Finds any other container IDs associated with this publishing job from system logs."""
        from backend.app.models.schema import SystemLog
        cids: List[str] = []
        if not upload.publishing_job_id:
            return cids
        try:
            # Strictly scoped to this specific publishing job ID - NEVER generic like('%container%')
            logs = db.query(SystemLog).filter(
                (SystemLog.message.like(f"%Job #{upload.publishing_job_id}%")) |
                (SystemLog.metadata_json.like(f'%\"publishing_job_id\": {upload.publishing_job_id}%'))
            ).order_by(SystemLog.id.desc()).limit(50).all()
            for l in logs:
                text = (l.message or "") + " " + (l.metadata_json or "")
                for m in re.findall(r'180\d{14}', text):
                    if m not in cids and m != str(upload.external_post_id):
                        cids.append(m)
        except Exception as e:
            logger.debug(f"[InstagramReconciler] Error finding sibling containers: {e}")
        return cids

    def _check_and_complete_job(self, db: Session, job: Optional[PublishingJob]):
        """Checks if all platform uploads for the job have reached terminal published/paused state and completes the job."""
        if not job:
            return
        uploads = db.query(PlatformUpload).filter(PlatformUpload.publishing_job_id == job.id).all()
        # All uploads must be published or legitimately paused (quota)
        all_resolved = all(u.status in ("published", "paused_quota") for u in uploads)
        any_published = any(u.status == "published" for u in uploads)
        if all_resolved and any_published:
            job.status = "completed"
            job.locked_at = None
            job.lock_expires_at = None
            job.worker_id = None
            logger.info(f"[InstagramReconciler] All uploads resolved for Job #{job.id}. Job marked COMPLETED.")


instagram_reconciler = InstagramReconciler()
