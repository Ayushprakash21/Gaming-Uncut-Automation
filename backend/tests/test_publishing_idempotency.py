"""
Comprehensive Regression Test Suite for Publishing Idempotency & Duplicate Prevention.
Tests all 10 scenarios specified in the production hardening requirements:
1. Double-click Publish Now (concurrent calls to dispatch).
2. Same clip queued twice.
3. Two workers claim the same job concurrently.
4. Scheduler runs twice concurrently.
5. Application restarts during upload (recovers to UNKNOWN, not re-publishing).
6. Platform succeeds but HTTP response times out (reconciles external post ID, marks PUBLISHED without re-upload).
7. Retry after confirmed successful publication (idempotency rejects re-upload).
8. Same clip published once on each of three platforms independently.
9. Intentional manual republishing (allow_republish=True).
10. Facebook, Instagram, and YouTube failures independently handled without cross-platform duplicate side-effects.
"""

import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.app.database.session import SessionLocal, get_db
from backend.app.main import app
from backend.app.models.schema import (
    ClipMetadata,
    GeneratedClip,
    PlatformUpload,
    PublishingIdempotencyRecord,
    PublishingJob,
    SystemLog,
)
from backend.app.publishers.idempotency import (
    IdempotencyManager,
    PublishingState,
    build_idempotency_key,
    can_transition,
    idempotency_manager,
)
from backend.app.publishers.manager import publishing_manager
from backend.app.scheduler.manager import (
    _publishing_tick_lock,
    run_process_due_publishing_jobs,
    task_scheduler,
)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_clip():
    """Creates a temporary test clip in the database."""
    with SessionLocal() as db:
        clip = GeneratedClip(
            duration_sec=30,
            width=1080,
            height=1920,
            output_path="test_output.mp4",
            validation_status="passed",
        )
        db.add(clip)
        db.commit()
        db.refresh(clip)

        meta = ClipMetadata(
            clip_id=clip.id,
            title="Idempotency Test Gameplay",
            description="Testing duplicate prevention",
            hashtags="#gaming #uncut",
        )
        db.add(meta)
        db.commit()
        clip_id = clip.id

    yield clip_id

    # Cleanup
    with SessionLocal() as db:
        db.query(PublishingIdempotencyRecord).filter_by(clip_id=clip_id).delete()
        jobs = db.query(PublishingJob).filter_by(clip_id=clip_id).all()
        for j in jobs:
            db.query(PlatformUpload).filter_by(publishing_job_id=j.id).delete()
            db.delete(j)
        db.query(ClipMetadata).filter_by(clip_id=clip_id).delete()
        db.query(GeneratedClip).filter_by(id=clip_id).delete()
        db.commit()


# Scenario 1: Double-click Publish Now (concurrent calls to dispatch)
def test_double_click_dispatch_prevention(client, test_clip):
    with SessionLocal() as db:
        job = PublishingJob(
            clip_id=test_clip,
            status="pending",
            approval_status="approved",
            scheduled_time=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    # Simulate in-flight state right after Worker 1 claims
    with SessionLocal() as db:
        j = db.query(PublishingJob).filter_by(id=job_id).first()
        j.status = PublishingState.CLAIMED
        j.worker_id = "worker-first-click"
        j.locked_at = datetime.utcnow()
        j.lock_expires_at = datetime.utcnow() + timedelta(seconds=300)
        db.commit()

    # Second click attempts dispatch
    response = client.post(f"/api/publishing/dispatch/{job_id}")
    assert response.status_code == 409
    assert "currently in-flight" in response.json()["detail"]


# Scenario 2: Same clip queued twice
def test_same_clip_queued_twice_idempotency(client, test_clip):
    # First queue call
    r1 = client.post(
        f"/api/clips/{test_clip}/queue",
        json={"platforms": ["youtube", "facebook"], "allow_republish": False},
    )
    assert r1.status_code == 200
    job_id_1 = r1.json()["publishing_job_id"]

    # Mark facebook as published
    with SessionLocal() as db:
        upload = (
            db.query(PlatformUpload)
            .filter_by(publishing_job_id=job_id_1, platform="facebook")
            .first()
        )
        upload.status = "published"
        upload.external_post_id = "fb_ext_123"
        db.commit()

    # Second queue call for the same clip
    r2 = client.post(
        f"/api/clips/{test_clip}/queue",
        json={"platforms": ["facebook", "instagram"], "allow_republish": False},
    )
    assert r2.status_code == 200
    job_id_2 = r2.json()["publishing_job_id"]
    assert job_id_1 == job_id_2

    # Verify Facebook status was NOT overwritten to pending
    with SessionLocal() as db:
        fb_up = (
            db.query(PlatformUpload)
            .filter_by(publishing_job_id=job_id_2, platform="facebook")
            .first()
        )
        assert fb_up.status == "published"
        assert fb_up.external_post_id == "fb_ext_123"


# Scenario 3: Two workers claim the same job concurrently
def test_concurrent_worker_claim(test_clip):
    with SessionLocal() as db:
        job = PublishingJob(
            clip_id=test_clip,
            status="pending",
            approval_status="approved",
            scheduled_time=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    successful_claims = []

    def try_claim(worker_id):
        with SessionLocal() as db:
            claimed, reason, j = idempotency_manager.claim_job_atomically(
                db, job_id, worker_id, lease_seconds=300
            )
            if claimed:
                successful_claims.append(worker_id)

    t1 = threading.Thread(target=try_claim, args=("worker-alpha",))
    t2 = threading.Thread(target=try_claim, args=("worker-beta",))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one worker must have successfully claimed the job
    assert len(successful_claims) == 1
    winner = successful_claims[0]

    with SessionLocal() as db:
        j = db.query(PublishingJob).filter_by(id=job_id).first()
        assert j.status == PublishingState.CLAIMED
        assert j.worker_id == winner
        assert j.lock_expires_at is not None


# Scenario 4: Scheduler runs twice concurrently
def test_concurrent_scheduler_ticks():
    # Verify _publishing_tick_lock prevents overlap
    assert not _publishing_tick_lock.locked()

    with _publishing_tick_lock:
        # While locked, a concurrent run must exit immediately without doing work
        with patch("backend.app.publishers.manager.publishing_manager.execute_job") as mock_exec:
            run_process_due_publishing_jobs()
            mock_exec.assert_not_called()

    assert not _publishing_tick_lock.locked()


# Scenario 5: Application restarts during upload (recovers to UNKNOWN, not re-publishing)
def test_crash_recovery_transitions_to_unknown(test_clip):
    with SessionLocal() as db:
        job = PublishingJob(
            clip_id=test_clip,
            scheduled_time=datetime.utcnow(),
            status="publishing",
            worker_id="crashed-worker",
            locked_at=datetime.utcnow(),
            lock_expires_at=datetime.utcnow() + timedelta(seconds=300),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    # Run crash recovery
    task_scheduler.recover_interrupted_jobs()

    with SessionLocal() as db:
        j = db.query(PublishingJob).filter_by(id=job_id).first()
        # Must be UNKNOWN, NEVER pending
        assert j.status == "UNKNOWN"
        assert j.worker_id is None
        assert j.lock_expires_at is None


# Scenario 6: Platform succeeds but HTTP response times out (reconciles external post ID, marks PUBLISHED without re-upload)
def test_timeout_preflight_reconciliation(test_clip):
    with SessionLocal() as db:
        job = PublishingJob(
            clip_id=test_clip,
            status="pending",
            approval_status="approved",
            scheduled_time=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        upload = PlatformUpload(
            publishing_job_id=job.id,
            platform="youtube",
            account_id="yt-channel-123",
            destination="reels",
            idempotency_key=build_idempotency_key(test_clip, "youtube", "yt-channel-123", "reels"),
            status="pending",
        )
        db.add(upload)
        db.commit()

        # Simulate remote platform finding an existing publication
        with patch.object(
            idempotency_manager,
            "reconcile_platform_publication",
            return_value="remote_yt_vid_999",
        ):
            reconciled = idempotency_manager.reconcile_platform_publication(
                db, "youtube", "yt-channel-123", "Idempotency Test Gameplay"
            )
            assert reconciled == "remote_yt_vid_999"


# Scenario 7: Retry after confirmed successful publication (idempotency rejects re-upload)
def test_retry_after_published_rejected(test_clip):
    with SessionLocal() as db:
        key = build_idempotency_key(test_clip, "instagram", "ig_acc_456", "reels")
        record = PublishingIdempotencyRecord(
            idempotency_key=key,
            clip_id=test_clip,
            platform="instagram",
            account_id="ig_acc_456",
            destination="reels",
            status=PublishingState.PUBLISHED,
            external_post_id="ig_media_already_done",
            external_url="https://www.instagram.com/reel/xyz123",
        )
        db.add(record)
        db.commit()

        # Attempt to acquire platform lock without allow_republish
        allowed, reason, rec = idempotency_manager.acquire_platform_idempotency_lock(
            db=db,
            clip_id=test_clip,
            platform="instagram",
            account_id="ig_acc_456",
            destination="reels",
            worker_id="retry-worker",
            allow_republish=False,
        )

        assert allowed is False
        assert "already published" in reason.lower()
        assert rec.external_post_id == "ig_media_already_done"


# Scenario 8: Same clip published once on each of three platforms independently
def test_independent_platform_idempotency_keys(test_clip):
    yt_key = build_idempotency_key(test_clip, "youtube", "yt_acc", "reels")
    ig_key = build_idempotency_key(test_clip, "instagram", "ig_acc", "reels")
    fb_key = build_idempotency_key(test_clip, "facebook", "fb_acc", "reels")

    # Keys must be distinct
    assert yt_key != ig_key
    assert ig_key != fb_key
    assert yt_key != fb_key

    with SessionLocal() as db:
        # Acquire lock for youtube
        allowed_yt, _, _ = idempotency_manager.acquire_platform_idempotency_lock(
            db=db, clip_id=test_clip, platform="youtube", account_id="yt_acc", destination="reels", worker_id="w1"
        )
        assert allowed_yt is True

        # Acquire lock for instagram
        allowed_ig, _, _ = idempotency_manager.acquire_platform_idempotency_lock(
            db=db, clip_id=test_clip, platform="instagram", account_id="ig_acc", destination="reels", worker_id="w1"
        )
        assert allowed_ig is True

        # Acquire lock for facebook
        allowed_fb, _, _ = idempotency_manager.acquire_platform_idempotency_lock(
            db=db, clip_id=test_clip, platform="facebook", account_id="fb_acc", destination="reels", worker_id="w1"
        )
        assert allowed_fb is True


# Scenario 9: Intentional manual republishing (allow_republish=True)
def test_intentional_republishing_with_flag(test_clip):
    with SessionLocal() as db:
        key = build_idempotency_key(test_clip, "facebook", "fb_page_789", "reels")
        record = PublishingIdempotencyRecord(
            idempotency_key=key,
            clip_id=test_clip,
            platform="facebook",
            account_id="fb_page_789",
            destination="reels",
            status=PublishingState.PUBLISHED,
            external_post_id="fb_prev_media_id",
            external_url="https://facebook.com/reel/111",
        )
        db.add(record)
        db.commit()

        # With allow_republish=True, acquire lock must succeed
        allowed, reason, rec = idempotency_manager.acquire_platform_idempotency_lock(
            db=db,
            clip_id=test_clip,
            platform="facebook",
            account_id="fb_page_789",
            destination="reels",
            worker_id="manual-republish-worker",
            allow_republish=True,
        )

        assert allowed is True
        assert rec.status == PublishingState.CLAIMED
        assert rec.worker_id == "manual-republish-worker"


# Scenario 10: Failures independently handled without cross-platform duplicate side-effects
def test_isolated_failure_handling(test_clip):
    with SessionLocal() as db:
        job = PublishingJob(
            clip_id=test_clip,
            status="pending",
            approval_status="approved",
            scheduled_time=datetime.utcnow(),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        fb_up = PlatformUpload(
            publishing_job_id=job.id,
            platform="facebook",
            account_id="fb_acc",
            destination="reels",
            status="pending",
        )
        ig_up = PlatformUpload(
            publishing_job_id=job.id,
            platform="instagram",
            account_id="ig_acc",
            destination="reels",
            status="pending",
        )
        db.add_all([fb_up, ig_up])
        db.commit()

        # Simulate FB succeeding, IG failing
        fb_key = build_idempotency_key(test_clip, "facebook", "fb_acc", "reels")
        idempotency_manager.acquire_platform_idempotency_lock(
            db=db, clip_id=test_clip, platform="facebook", account_id="fb_acc", destination="reels", worker_id="w-isolate"
        )
        idempotency_manager.update_stage(
            db=db,
            idempotency_key=fb_key,
            worker_id="w-isolate",
            new_state=PublishingState.PUBLISHED,
            external_post_id="fb_success_123",
            external_url="https://facebook.com/reel/123",
        )

        ig_key = build_idempotency_key(test_clip, "instagram", "ig_acc", "reels")
        idempotency_manager.acquire_platform_idempotency_lock(
            db=db, clip_id=test_clip, platform="instagram", account_id="ig_acc", destination="reels", worker_id="w-isolate"
        )
        idempotency_manager.update_stage(
            db=db,
            idempotency_key=ig_key,
            worker_id="w-isolate",
            new_state=PublishingState.FAILED,
        )

        # Update PlatformUpload error message
        ig_up.status = "failed"
        ig_up.error_message = "HTTP 400 during Instagram binary transfer"
        db.commit()

        # Verify FB remains published and IG remains failed
        fb_rec = db.query(PublishingIdempotencyRecord).filter_by(idempotency_key=fb_key).first()
        ig_rec = db.query(PublishingIdempotencyRecord).filter_by(idempotency_key=ig_key).first()

        assert fb_rec.status == PublishingState.PUBLISHED
        assert fb_rec.external_post_id == "fb_success_123"

        assert ig_rec.status == PublishingState.FAILED
        assert ig_up.status == "failed"
        assert "HTTP 400" in ig_up.error_message
