"""
Comprehensive Test Suite for Configurable Daily Targets & Quota Enforcement.
Tests:
1. Configurable limits retrieval & update (/api/settings/limits)
2. Default target of 15 clips/day (no hardcoded 6 or 12)
3. Separation of video generation limits from platform publishing limits
4. YouTube videos.insert quota bucket (1,600 units) vs Channel upload restrictions
5. Instagram & Facebook rate limits (Meta Graph API codes)
6. Automatic job queuing for next permitted window (scheduled_next_window)
7. Separate tracking of generated, queued, published, and failed counts in /api/overview
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    SystemSettings, PlatformQuotaState, SourceVideo, GeneratedClip,
    PublishingJob, PlatformUpload
)
from backend.app.publishers.quota_tracker import (
    get_or_create_settings, get_or_create_quota_state, can_publish,
    record_upload_success, record_platform_restriction, calculate_next_window,
    YouTubeQuotaExceededError, YouTubeChannelUploadLimitError,
    InstagramRateLimitError, FacebookRateLimitError
)
from backend.app.publishers.manager import publishing_manager

client = TestClient(app)

@pytest.fixture(scope="module")
def db():
    db_session = SessionLocal()
    yield db_session
    db_session.close()


def test_default_limits_and_endpoints(db):
    """Verify default target is 15 and limits can be queried and updated via API."""
    # Ensure initialized/reset to default 15
    client.post("/api/settings/limits", json={
        "daily_generation_target": 15,
        "daily_publishing_target": 15,
        "youtube_daily_limit": 15,
        "instagram_daily_limit": 25,
        "facebook_daily_limit": 25
    })

    res = client.get("/api/settings/limits")
    assert res.status_code == 200
    data = res.json()
    assert data["daily_generation_target"] == 15
    assert data["daily_publishing_target"] == 15
    assert data["youtube_daily_limit"] == 15
    assert data["instagram_daily_limit"] == 25
    assert data["facebook_daily_limit"] == 25

    # Update limits
    update_payload = {
        "daily_generation_target": 20,
        "daily_publishing_target": 18,
        "youtube_daily_limit": 10,
        "instagram_daily_limit": 30,
        "facebook_daily_limit": 30
    }
    update_res = client.post("/api/settings/limits", json=update_payload)
    assert update_res.status_code == 200
    updated_data = update_res.json()["settings"]
    assert updated_data["daily_generation_target"] == 20
    assert updated_data["daily_publishing_target"] == 18
    assert updated_data["youtube_daily_limit"] == 10

    # Reset back to default 15
    client.post("/api/settings/limits", json={
        "daily_generation_target": 15,
        "daily_publishing_target": 15,
        "youtube_daily_limit": 15,
        "instagram_daily_limit": 25,
        "facebook_daily_limit": 25
    })


def test_separation_of_generation_and_publishing_limits(db):
    """Verify generation target and publishing target are tracked independently."""
    settings = get_or_create_settings(db)
    settings.daily_generation_target = 25
    settings.daily_publishing_target = 10
    db.commit()

    res = client.get("/api/overview")
    assert res.status_code == 200
    kpis = res.json()["kpis"]
    assert kpis["daily_generation_target"] == 25
    assert kpis["daily_publishing_target"] == 10
    assert kpis["daily_target"] == 10  # publishing target backward compatible


def test_youtube_quota_units_and_success_tracking(db):
    """Verify videos.insert quota tracking: 1,600 units per video."""
    state = get_or_create_quota_state(db, "youtube")
    initial_units = state.quota_units_used
    initial_published = state.published_today

    record_upload_success(db, "youtube", quota_units=1600)
    db.refresh(state)

    assert state.published_today == initial_published + 1
    assert state.quota_units_used == initial_units + 1600


def test_youtube_api_quota_exceeded_vs_channel_limit(db):
    """
    Distinguish project-level videos.insert quota (10,000 limit) from
    channel-level upload restrictions (uploadLimitExceeded).
    """
    # 1. Test API Quota Exceeded (resets 00:00 PST / 08:00 UTC)
    state = get_or_create_quota_state(db, "youtube")
    next_window = calculate_next_window("youtube", "api_quota_exceeded")
    # Verify next window is in future (e.g. next 08:00 UTC)
    assert next_window > datetime.utcnow()

    record_platform_restriction(db, "youtube", "api_quota_exceeded", "Daily videos.insert quota limit reached")
    db.refresh(state)

    assert state.api_quota_exceeded is True
    assert state.channel_limit_reached is False
    allowed, reason, retry_time = can_publish(db, "youtube")
    assert allowed is False
    assert "paused until" in reason

    # 2. Test Channel Upload Limit Exceeded (rolling 24h)
    state.api_quota_exceeded = False
    state.pause_until = None
    db.commit()

    record_platform_restriction(db, "youtube", "channel_limit_reached", "The user has exceeded the number of videos they may upload.")
    db.refresh(state)

    assert state.channel_limit_reached is True
    allowed, reason, retry_time = can_publish(db, "youtube")
    assert allowed is False
    assert "paused until" in reason

    # Clear restriction for subsequent tests
    state.channel_limit_reached = False
    state.pause_until = None
    db.commit()


def test_meta_rate_limit_handling(db):
    """Verify Instagram and Facebook rate limits pause publishing and set next window."""
    # Instagram
    ig_state = get_or_create_quota_state(db, "instagram")
    record_platform_restriction(db, "instagram", "rate_limit", "(#32) Page request limit reached")
    db.refresh(ig_state)

    assert ig_state.rate_limited is True
    allowed, reason, _ = can_publish(db, "instagram")
    assert allowed is False
    assert "paused until" in reason

    # Reset Instagram
    ig_state.rate_limited = False
    ig_state.pause_until = None
    db.commit()

    # Facebook
    fb_state = get_or_create_quota_state(db, "facebook")
    record_platform_restriction(db, "facebook", "rate_limit", "(#613) Rate limit exceeded")
    db.refresh(fb_state)

    assert fb_state.rate_limited is True
    allowed, reason, _ = can_publish(db, "facebook")
    assert allowed is False
    assert "paused until" in reason

    # Reset Facebook
    fb_state.rate_limited = False
    fb_state.pause_until = None
    db.commit()


def test_automatic_rescheduling_on_quota(db):
    """
    Verify that when a platform restriction or user limit is reached,
    jobs are not permanently failed; they transition to 'scheduled_next_window'
    with scheduled_time set to the reopen window.
    """
    from backend.app.models.schema import ProcessingJob, ClipMetadata
    
    # Create test source video, processing job, and clip
    unique_ts = int(time.time() * 1000)
    source = SourceVideo(
        title=f"Test Footage {unique_ts}",
        url=f"https://youtube.com/watch?v=limit_test_{unique_ts}",
        local_path=f"data/raw/test_{unique_ts}.mp4",
        duration=60.0,
        status="processed"
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    pjob = ProcessingJob(
        source_video_id=source.id,
        job_type="full_pipeline",
        status="completed",
        progress_pct=100
    )
    db.add(pjob)
    db.commit()
    db.refresh(pjob)

    clip_file = ROOT_DIR / "data" / "temporary" / f"test_{unique_ts}.mp4"
    clip_file.parent.mkdir(parents=True, exist_ok=True)
    clip_file.write_bytes(b"dummy_video_bytes")

    clip = GeneratedClip(
        job_id=pjob.id,
        output_path=str(clip_file),
        duration_sec=60.0,
        quality_score=85.0,
        validation_status="passed"
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)

    metadata = ClipMetadata(
        clip_id=clip.id,
        title=f"Epic Moment {unique_ts}",
        topic="FPS",
        description="Highlights",
        hashtags="#gaming #shorts"
    )
    db.add(metadata)
    db.commit()

    job = PublishingJob(
        clip_id=clip.id,
        scheduled_time=datetime.utcnow(),
        status="pending"
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    upload = PlatformUpload(
        publishing_job_id=job.id,
        platform="youtube",
        status="pending"
    )
    db.add(upload)
    db.commit()

    # Artificially set YouTube quota exceeded
    yt_state = get_or_create_quota_state(db, "youtube")
    yt_state.api_quota_exceeded = True
    yt_state.pause_until = datetime.utcnow() + timedelta(hours=4)
    db.commit()

    # Dispatch via publishing manager
    publishing_manager.execute_job(job.id)
    db.refresh(job)

    # Job should be scheduled for next window, NOT failed
    assert job.status == "scheduled_next_window"
    assert job.scheduled_time is not None
    assert job.scheduled_time >= datetime.utcnow()

    # Reset YouTube quota state
    yt_state.api_quota_exceeded = False
    yt_state.pause_until = None
    db.commit()

    # Reset YouTube quota state
    yt_state.api_quota_exceeded = False
    yt_state.pause_until = None
    db.commit()


def test_dashboard_kpis_breakdown(db):
    """
    Verify that /api/overview returns separate counts for:
    - today_generated
    - today_queued
    - today_published
    - today_failed
    """
    res = client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()

    kpis = data["kpis"]
    assert "today_generated" in kpis
    assert "today_queued" in kpis
    assert "today_published" in kpis
    assert "today_failed" in kpis
    assert "daily_generation_target" in kpis
    assert "daily_publishing_target" in kpis

    quotas = data.get("quotas", {})
    assert "youtube" in quotas
    assert "instagram" in quotas
    assert "facebook" in quotas
    assert quotas["youtube"]["quota_units_bucket_limit"] == 10000
