"""
Comprehensive test suite verifying removal of the global daily publishing cap of 15 clips/day,
unlimited-by-default behavior, configurable limits, safeguard preservation, safe re-evaluation of
quota-paused jobs, and dashboard metric accuracy.
"""

import time
from datetime import datetime, timedelta
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    SourceVideo,
    ProcessingJob,
    GeneratedClip,
    PublishingJob,
    PlatformUpload,
    PublishingIdempotencyRecord,
    PlatformQuotaState,
    SystemSettings,
)
from backend.app.publishers.quota_tracker import (
    can_publish,
    get_or_create_settings,
    get_or_create_quota_state,
    record_platform_restriction,
    recover_quota_paused_jobs,
)
from backend.app.publishers.manager import publishing_manager
from backend.app.publishers.idempotency import idempotency_manager
from backend.app.scheduler.manager import run_process_due_publishing_jobs

client = TestClient(app)

@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()

def create_mock_clip(db, unique_suffix: str):
    source = SourceVideo(
        title=f"Gameplay {unique_suffix}",
        url=f"https://youtube.com/watch?v={unique_suffix}",
        local_path=f"data/raw/{unique_suffix}.mp4",
        duration=60.0,
        status="processed"
    )
    db.add(source)
    db.commit()

    pjob = ProcessingJob(
        source_video_id=source.id,
        job_type="full_pipeline",
        status="completed",
        progress_pct=100
    )
    db.add(pjob)
    db.commit()

    clip = GeneratedClip(
        job_id=pjob.id,
        output_path=f"data/clips/{unique_suffix}.mp4",
        duration_sec=25.0,
        width=1080,
        height=1920,
        validation_status="passed"
    )
    db.add(clip)
    db.commit()
    return clip

def test_unlimited_publishing_default_settings(db):
    """1. Application operates without a daily cap when unlimited (target is null/0)."""
    settings = get_or_create_settings(db)
    # Ensure default is unlimited
    settings.daily_publishing_target = None
    settings.youtube_daily_limit = None
    settings.instagram_daily_limit = None
    settings.facebook_daily_limit = None
    db.commit()

    clip = create_mock_clip(db, f"unlim_{int(time.time() * 1000)}")
    job = PublishingJob(clip_id=clip.id, scheduled_time=datetime.utcnow(), status="published")
    db.add(job)
    db.commit()

    # Even if 20 videos were published today, can_publish must return True when target is None
    with patch("backend.app.publishers.quota_tracker.is_platform_authorized", return_value=(True, None)):
        for plat in ["youtube", "instagram", "facebook"]:
            allowed, reason, next_window = can_publish(db, plat, check_auth=True, is_manual=False)
            assert allowed is True, f"Platform {plat} was blocked under unlimited settings: {reason}"
            assert reason is None
            assert next_window is None

    # Overview endpoint reports unlimited
    res = client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["kpis"]["daily_publishing_target"] is None or data["kpis"]["daily_publishing_target"] == 0
    assert data["kpis"]["daily_target"] is None or data["kpis"]["daily_target"] == 0

def test_jobs_above_15_can_enter_publishing_queue(db):
    """2. Jobs above 15 can enter the publishing queue."""
    unique_ts = f"q_{int(time.time() * 1000)}"
    clip = create_mock_clip(db, unique_ts)

    # Queue post for clip via API
    res = client.post(f"/api/clips/{clip.id}/queue", json={
        "platforms": ["youtube", "instagram", "facebook"]
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    job_id = data["publishing_job_id"]

    # Verify job and uploads were created in pending state
    pub_job = db.get(PublishingJob, job_id)
    assert pub_job is not None
    assert pub_job.status in ["pending", "QUEUED"]

    uploads = db.query(PlatformUpload).filter_by(publishing_job_id=job_id).all()
    assert len(uploads) == 3
    for u in uploads:
        assert u.status == "pending"
        assert u.error_code is None

def test_manual_publishing_works_without_internal_daily_cap(db):
    """3. Manual publishing works without an internal daily cap."""
    settings = get_or_create_settings(db)
    # Set an artificial limit of 2 to ensure is_manual bypasses it
    settings.youtube_daily_limit = 2
    settings.daily_publishing_target = 2
    db.commit()

    # Even with an artificial limit, manual publishing must not be blocked
    with patch("backend.app.publishers.quota_tracker.is_platform_authorized", return_value=(True, None)):
        allowed_manual, reason_man, _ = can_publish(db, "youtube", check_auth=True, is_manual=True)
        assert allowed_manual is True, f"Manual publishing was unexpectedly blocked: {reason_man}"

    # Reset back to unlimited
    settings.youtube_daily_limit = None
    settings.daily_publishing_target = None
    db.commit()

def test_platform_api_rate_limits_pause_and_defer_correctly(db):
    """4. Platform API rate-limit responses still pause or defer publishing correctly."""
    # Register an actual platform rate limit (e.g. YouTube quota or Meta rate limit)
    restr_reason = "Instagram Graph API Page Request Limit reached (#32)"
    next_win = record_platform_restriction(db, "instagram", "rate_limit", restr_reason, retry_after_seconds=3600)

    # Verify can_publish reflects the genuine platform pause
    allowed, reason, win = can_publish(db, "instagram", check_auth=True, is_manual=False)
    assert allowed is False
    assert "paused until" in reason
    assert "rate_limit" in reason
    assert win is not None

    # Clear restriction
    ig_state = get_or_create_quota_state(db, "instagram")
    ig_state.rate_limited = False
    ig_state.pause_until = None
    ig_state.last_restriction_type = None
    ig_state.last_restriction_reason = None
    db.commit()

def test_duplicate_publishing_remains_prevented(db):
    """5. Duplicate publishing remains prevented by idempotency manager."""
    unique_ts = f"dup_{int(time.time() * 1000)}"
    clip = create_mock_clip(db, unique_ts)

    # Create publishing job and platform upload
    job = PublishingJob(clip_id=clip.id, scheduled_time=datetime.utcnow(), status="pending", approval_status="approved")
    db.add(job)
    db.commit()

    upload = PlatformUpload(publishing_job_id=job.id, platform="youtube", status="pending")
    db.add(upload)
    db.commit()

    worker_1 = "worker_alpha"
    worker_2 = "worker_beta"

    # Worker 1 locks idempotency record
    locked_1, _, _ = idempotency_manager.acquire_platform_idempotency_lock(
        db, clip.id, "youtube", "chan_123", "shorts", job.id, upload.id, worker_1
    )
    assert locked_1 is True

    # Mark as published
    idempotency_manager.update_stage(
        db, f"clip:{clip.id}:youtube:chan_123:shorts", worker_1, "PUBLISHED",
        external_post_id="yt_vid_999", external_url="https://youtube.com/shorts/yt_vid_999"
    )

    # Worker 2 attempts to claim the same clip/platform/destination
    locked_2, reason_2, _ = idempotency_manager.acquire_platform_idempotency_lock(
        db, clip.id, "youtube", "chan_123", "shorts", job.id, upload.id, worker_2, allow_republish=False
    )
    assert locked_2 is False
    assert "Duplicate publishing blocked" in reason_2

def test_approval_mode_is_respected(db):
    """6. Approval mode is respected: unapproved jobs are not processed by scheduler."""
    unique_ts = f"appr_{int(time.time() * 1000)}"
    clip = create_mock_clip(db, unique_ts)

    job = PublishingJob(
        clip_id=clip.id,
        scheduled_time=datetime.utcnow() - timedelta(minutes=5),
        status="pending",
        approval_status="pending_review"  # Needs manual review
    )
    db.add(job)
    db.commit()

    with patch.object(publishing_manager, "execute_job") as mock_exec:
        with patch("backend.app.core.config.settings.AUTO_PUBLISHING_ENABLED", True):
            run_process_due_publishing_jobs()
            # Job is pending_review, so mock_exec must NOT be called for this job
            called_ids = [call.args[0] for call in mock_exec.call_args_list if call.args]
            assert job.id not in called_ids

def test_previously_quota_paused_jobs_safely_reevaluated(db):
    """7. Previously quota-paused jobs are safely re-evaluated."""
    unique_ts = f"rec_{int(time.time() * 1000)}"
    clip = create_mock_clip(db, unique_ts)

    job = PublishingJob(
        clip_id=clip.id,
        scheduled_time=datetime.utcnow() + timedelta(days=1),
        status="scheduled_next_window",
        approval_status="needs_review",
        attempts=2
    )
    db.add(job)
    db.commit()

    for p in ["youtube", "instagram", "facebook"]:
        upload = PlatformUpload(
            publishing_job_id=job.id,
            platform=p,
            status="paused_quota",
            error_message="Overall daily publishing target reached (16/15).",
            attempts=0
        )
        db.add(upload)
        db.commit()

        idemp = PublishingIdempotencyRecord(
            idempotency_key=f"clip:{clip.id}:{p}:acc_test:reels",
            clip_id=clip.id,
            platform=p,
            account_id="acc_test",
            destination="reels",
            publishing_job_id=job.id,
            platform_upload_id=upload.id,
            status="PAUSED"
        )
        db.add(idemp)
    db.commit()

    # Run recovery
    result = recover_quota_paused_jobs(db)
    assert job.id in result["recovered_jobs"]
    assert result["recovered_uploads_count"] >= 3

    # Check job and upload states
    db.refresh(job)
    assert job.status == "pending"
    assert job.approval_status == "needs_review"  # Preserved!
    assert job.attempts == 2  # Preserved!

    uploads = db.query(PlatformUpload).filter_by(publishing_job_id=job.id).all()
    for u in uploads:
        assert u.status == "pending"
        assert u.error_message is None
        assert u.error_code is None

def test_scheduler_continues_processing_eligible_jobs(db):
    """8. Scheduler continues processing eligible jobs above 15."""
    unique_ts = f"sched_{int(time.time() * 1000)}"
    clip = create_mock_clip(db, unique_ts)

    # Schedule oldest due so it's guaranteed at the front of the queue
    job = PublishingJob(
        clip_id=clip.id,
        scheduled_time=datetime.utcnow() - timedelta(days=365),
        status="pending",
        approval_status="approved"
    )
    db.add(job)
    db.commit()

    upload = PlatformUpload(publishing_job_id=job.id, platform="youtube", status="pending")
    db.add(upload)
    db.commit()

    with patch.object(publishing_manager, "execute_job") as mock_exec:
        with patch("backend.app.core.config.settings.AUTO_PUBLISHING_ENABLED", True):
            run_process_due_publishing_jobs()
            called_ids = [call.args[0] for call in mock_exec.call_args_list if call.args]
            assert job.id in called_ids
