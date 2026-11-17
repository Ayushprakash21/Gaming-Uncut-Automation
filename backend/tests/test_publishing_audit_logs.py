"""
Regression & Integrity Tests for Centralized Publishing Audit Logging.
Proves that every publishing error, rate limit, and timeout across Instagram, Facebook,
and YouTube creates a structured, redacted SystemLog record visible in the Audit Logs API.
"""

import json
import pytest
import requests
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    SourceVideo, ProcessingJob, GeneratedClip, ClipMetadata,
    PublishingJob, PlatformUpload, SystemLog
)
from backend.app.publishers.manager import publishing_manager, redact_secrets
from backend.app.publishers.facebook.facebook_client import (
    facebook_client, FacebookTokenExpiredError, FacebookConfigError, FacebookPermissionError
)
from backend.app.publishers.instagram.instagram_client import (
    instagram_client, InstagramTokenExpiredError, InstagramRateLimitError, InstagramAPIError
)
from backend.app.security.vault import vault

client = TestClient(app)


@pytest.fixture
def dummy_video(tmp_path):
    video = tmp_path / "audit_test_reel.mp4"
    video.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 2048)
    return video


def create_test_publishing_job(db, dummy_video, platform="instagram"):
    """Helper to set up a clean DB record hierarchy for a single-platform publish job."""
    from backend.app.models.schema import PlatformQuotaState
    db.query(PlatformQuotaState).filter_by(platform=platform).delete()
    db.commit()

    src = SourceVideo(url=f"https://youtube.com/watch?v=audit_{platform}", title="Audit Test", duration=45.0, local_path=str(dummy_video))
    db.add(src)
    db.commit()
    db.refresh(src)

    job = ProcessingJob(source_video_id=src.id, status="completed")
    db.add(job)
    db.commit()
    db.refresh(job)

    clip = GeneratedClip(job_id=job.id, output_path=str(dummy_video), duration_sec=30.0)
    db.add(clip)
    db.commit()
    db.refresh(clip)

    meta = ClipMetadata(clip_id=clip.id, title="Audit Clip", description="Audit description", hashtags="#Shorts #Gaming")
    db.add(meta)
    db.commit()

    pj = PublishingJob(clip_id=clip.id, scheduled_time=datetime.utcnow(), status="pending", approval_status="approved")
    db.add(pj)
    db.commit()
    db.refresh(pj)

    upload = PlatformUpload(publishing_job_id=pj.id, platform=platform, status="pending")
    db.add(upload)
    db.commit()
    db.refresh(upload)

    return pj, upload, clip


# ============================================================
# 1. Instagram Connection Timeout Test
# ============================================================

def test_instagram_connection_timeout_creates_system_log(dummy_video, monkeypatch):
    """Proves that network timeouts during Instagram publishing persist a SystemLog record."""
    def mock_publish_timeout(*args, **kwargs):
        if kwargs.get("stage_callback"):
            kwargs["stage_callback"]("video_transferring")
        raise requests.Timeout("HTTPSConnectionPool(host='graph.facebook.com'): Read timed out. (read timeout=10)")

    monkeypatch.setattr(instagram_client, "publish_reel", mock_publish_timeout)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="instagram")
        publishing_manager.execute_job(pj.id)

        # Verify PlatformUpload state
        db.refresh(upload)
        assert upload.status == "failed"
        assert upload.error_code == "NETWORK_TIMEOUT"

        # Verify SystemLog record in DB
        log_entry = db.query(SystemLog).filter_by(module="instagram_publisher", level="ERROR").order_by(SystemLog.id.desc()).first()
        assert log_entry is not None
        assert f"Job #{pj.id}" in log_entry.message
        assert f"Clip #{clip.id}" in log_entry.message
        assert "stage 'video_transferring'" in log_entry.message
        assert "[Code: NETWORK_TIMEOUT]" in log_entry.message
        assert "timed out" in log_entry.message.lower()

        # Check metadata_json
        meta = json.loads(log_entry.metadata_json)
        assert meta["platform"] == "instagram"
        assert meta["publishing_job_id"] == pj.id
        assert meta["clip_id"] == clip.id
        assert meta["stage"] == "video_transferring"
        assert meta["error_code"] == "NETWORK_TIMEOUT"
        assert meta["exception_type"] == "Timeout"


# ============================================================
# 2. Facebook Page Verification Failure Test
# ============================================================

def test_facebook_page_verification_failure_creates_system_log(dummy_video, monkeypatch):
    """Proves that invalid or non-existent Facebook Page ID writes a SystemLog error record."""
    def mock_publish_page_error(*args, **kwargs):
        if kwargs.get("stage_callback"):
            kwargs["stage_callback"]("page_verification")
        raise FacebookConfigError(
            "Facebook Page ID '99999999999' could not be verified on Graph API (Code 100).",
            code=100,
            error_type="GraphMethodException",
            user_message="Facebook Page ID is invalid or cannot be verified.",
            fbtrace_id="fb_trace_page_invalid"
        )

    monkeypatch.setattr(facebook_client, "publish_reel", mock_publish_page_error)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="facebook")
        publishing_manager.execute_job(pj.id)

        # Verify PlatformUpload state
        db.refresh(upload)
        assert upload.status == "failed"

        # Verify SystemLog record in DB
        log_entry = db.query(SystemLog).filter_by(module="facebook_publisher", level="ERROR").order_by(SystemLog.id.desc()).first()
        assert log_entry is not None
        assert f"Job #{pj.id}" in log_entry.message
        assert "stage 'page_verification'" in log_entry.message
        assert "Facebook Page ID is invalid" in log_entry.message

        meta = json.loads(log_entry.metadata_json)
        assert meta["platform"] == "facebook"
        assert meta["publishing_job_id"] == pj.id
        assert meta["stage"] == "page_verification"
        assert meta["fbtrace_id"] == "fb_trace_page_invalid"
        assert meta["exception_type"] == "FacebookConfigError"


# ============================================================
# 3. Instagram Token Expiration Test
# ============================================================

def test_instagram_token_expiration_creates_system_log(dummy_video, monkeypatch):
    """Proves that expired Meta token during Instagram publishing creates an ERROR SystemLog record."""
    def mock_publish_expired(*args, **kwargs):
        if kwargs.get("stage_callback"):
            kwargs["stage_callback"]("upload_initialized")
        raise InstagramTokenExpiredError(
            "Error validating access token: Session has expired",
            code=190,
            subcode=463,
            error_type="OAuthException",
            user_message="Meta access token has expired (Code 190, Subcode 463).",
            fbtrace_id="fb_trace_ig_expired_token",
            http_status=400
        )

    monkeypatch.setattr(instagram_client, "publish_reel", mock_publish_expired)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="instagram")
        publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "failed"
        assert upload.error_code == "190"

        log_entry = db.query(SystemLog).filter_by(module="instagram_publisher", level="ERROR").order_by(SystemLog.id.desc()).first()
        assert log_entry is not None
        assert f"Job #{pj.id}" in log_entry.message
        assert "[Code: 190]" in log_entry.message
        assert "expired" in log_entry.message.lower()

        meta = json.loads(log_entry.metadata_json)
        assert meta["error_code"] == "190"
        assert meta["error_subcode"] == "463"
        assert meta["fbtrace_id"] == "fb_trace_ig_expired_token"
        assert meta["http_status"] == 400


# ============================================================
# 4. Facebook Token Expiration Test
# ============================================================

def test_facebook_token_expiration_creates_system_log(dummy_video, monkeypatch):
    """Proves that expired Page Access Token during Facebook publishing creates an ERROR SystemLog record."""
    def mock_publish_fb_expired(*args, **kwargs):
        if kwargs.get("stage_callback"):
            kwargs["stage_callback"]("publishing")
        raise FacebookTokenExpiredError(
            "Meta Page Access Token has expired or was revoked",
            code=190,
            subcode=467,
            error_type="OAuthException",
            user_message="Meta Page Access Token expired (Code 190, Subcode 467).",
            fbtrace_id="fb_trace_fb_expired_token",
            http_status=400
        )

    monkeypatch.setattr(facebook_client, "publish_reel", mock_publish_fb_expired)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="facebook")
        publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "failed"
        assert upload.error_code == "190"

        log_entry = db.query(SystemLog).filter_by(module="facebook_publisher", level="ERROR").order_by(SystemLog.id.desc()).first()
        assert log_entry is not None
        assert f"Job #{pj.id}" in log_entry.message
        assert "stage 'publishing'" in log_entry.message
        assert "[Code: 190]" in log_entry.message

        meta = json.loads(log_entry.metadata_json)
        assert meta["error_code"] == "190"
        assert meta["error_subcode"] == "467"
        assert meta["fbtrace_id"] == "fb_trace_fb_expired_token"


# ============================================================
# 5. Rate-Limit Pauses Test (WARNING Record)
# ============================================================

def test_rate_limit_pauses_create_warning_record(dummy_video, monkeypatch):
    """Proves that hitting rate limits creates a WARNING SystemLog record (not an ERROR) and pauses upload."""
    def mock_publish_rate_limit(*args, **kwargs):
        if kwargs.get("stage_callback"):
            kwargs["stage_callback"]("upload_initialized")
        raise InstagramRateLimitError(
            "User request limit reached",
            code=613,
            user_message="Paused: Instagram publishing rate limit (613). Rescheduled for next window.",
            fbtrace_id="fb_trace_rate_limit"
        )

    monkeypatch.setattr(instagram_client, "publish_reel", mock_publish_rate_limit)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="instagram")
        publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "paused_quota"
        assert upload.error_code == "613"

        log_entry = db.query(SystemLog).filter_by(module="instagram_publisher", level="WARNING").order_by(SystemLog.id.desc()).first()
        assert log_entry is not None
        assert f"Job #{pj.id}" in log_entry.message
        assert "attempt 1 warning" in log_entry.message
        assert "[Code: 613]" in log_entry.message
        assert "rate limit" in log_entry.message.lower()

        meta = json.loads(log_entry.metadata_json)
        assert meta["error_code"] == "613"
        assert meta["fbtrace_id"] == "fb_trace_rate_limit"


# ============================================================
# 6. Generic Publisher Exceptions Test (ERROR Record)
# ============================================================

def test_generic_publisher_exception_creates_error_record(dummy_video, monkeypatch):
    """Proves that unexpected exceptions (e.g. OS errors, corrupt video) write an ERROR SystemLog record."""
    def mock_publish_crash(*args, **kwargs):
        if kwargs.get("stage_callback"):
            kwargs["stage_callback"]("video_transferring")
        raise RuntimeError("Unexpected NVENC H.264 stream corruption during binary transfer")

    monkeypatch.setattr(facebook_client, "publish_reel", mock_publish_crash)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="facebook")
        publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "failed"

        log_entry = db.query(SystemLog).filter_by(module="facebook_publisher", level="ERROR").order_by(SystemLog.id.desc()).first()
        assert log_entry is not None
        assert f"Job #{pj.id}" in log_entry.message
        assert "stage 'video_transferring'" in log_entry.message
        assert "Unexpected NVENC H.264 stream corruption" in log_entry.message

        meta = json.loads(log_entry.metadata_json)
        assert meta["exception_type"] == "RuntimeError"
        assert meta["stage"] == "video_transferring"


# ============================================================
# 7. Successful Publishing Test (No Failure Record Created)
# ============================================================

def test_successful_publishing_does_not_create_failure_record(dummy_video, monkeypatch):
    """Proves that clean, successful uploads do NOT write ERROR or WARNING failure records."""
    def mock_clean_success(*args, **kwargs):
        if kwargs.get("stage_callback"):
            kwargs["stage_callback"]("published")
        return {
            "platform": "instagram",
            "post_id": "test_clean_success_123",
            "url": "https://instagram.com/reel/test_clean_success_123"
        }

    monkeypatch.setattr(instagram_client, "publish_reel", mock_clean_success)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="instagram")
        
        # Count existing failure logs before execution
        initial_error_count = db.query(SystemLog).filter(
            SystemLog.level.in_(["ERROR", "WARNING"]),
            SystemLog.message.like(f"%Job #{pj.id}%")
        ).count()

        publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "published"

        # Verify NO new failure logs were written for this job
        final_error_count = db.query(SystemLog).filter(
            SystemLog.level.in_(["ERROR", "WARNING"]),
            SystemLog.message.like(f"%Job #{pj.id}%")
        ).count()

        assert final_error_count == initial_error_count == 0


# ============================================================
# 8. Secret Redaction Test
# ============================================================

def test_secrets_are_redacted_from_persisted_logs(dummy_video, monkeypatch):
    """Proves that raw Meta tokens and client secrets never persist to SystemLog message or metadata_json."""
    raw_secret_token = "EAAB999888777666555444333222111"
    raw_app_secret = "my_super_secret_meta_app_key"

    def mock_publish_leaky_error(*args, **kwargs):
        raise InstagramAPIError(
            f"Failed graph call with access_token={raw_secret_token} and client_secret={raw_app_secret} and Bearer {raw_secret_token}",
            code=400,
            user_message=f"Meta rejected token {raw_secret_token} using client_secret={raw_app_secret}"
        )

    monkeypatch.setattr(instagram_client, "publish_reel", mock_publish_leaky_error)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="instagram")
        publishing_manager.execute_job(pj.id)

        log_entry = db.query(SystemLog).filter_by(module="instagram_publisher", level="ERROR").order_by(SystemLog.id.desc()).first()
        assert log_entry is not None

        # Verify raw tokens are NEVER present in message
        assert raw_secret_token not in log_entry.message
        assert raw_app_secret not in log_entry.message
        assert "[REDACTED_TOKEN]" in log_entry.message

        # Verify raw tokens are NEVER present in metadata_json
        assert raw_secret_token not in log_entry.metadata_json
        assert raw_app_secret not in log_entry.metadata_json

        # Test standalone redact_secrets function
        sanitized = redact_secrets(f"Header: Bearer {raw_secret_token}")
        assert raw_secret_token not in sanitized
        assert "Bearer [REDACTED_TOKEN]" in sanitized


# ============================================================
# 9. Logs API Retrieval & Filtering Test
# ============================================================

def test_logs_api_returns_newly_persisted_records(dummy_video, monkeypatch):
    """Proves that GET /api/logs returns the persisted publishing audit logs and supports level filtering."""
    def mock_publish_fb_fail(*args, **kwargs):
        raise FacebookPermissionError(
            "Missing permissions: pages_manage_posts required",
            code=200,
            user_message="Permission pages_manage_posts is required to publish Facebook Reels."
        )

    monkeypatch.setattr(facebook_client, "publish_reel", mock_publish_fb_fail)

    with SessionLocal() as db:
        pj, upload, clip = create_test_publishing_job(db, dummy_video, platform="facebook")
        publishing_manager.execute_job(pj.id)

    # 1. Fetch all logs via API
    res = client.get("/api/logs")
    assert res.status_code == 200
    logs = res.json()
    assert len(logs) > 0

    # Locate the newly written log
    matching_log = next((l for l in logs if f"Job #{pj.id}" in l["message"]), None)
    assert matching_log is not None
    assert matching_log["module"] == "facebook_publisher"
    assert matching_log["level"] == "ERROR"
    assert matching_log["metadata_json"] is not None
    assert "pages_manage_posts" in matching_log["message"]

    # 2. Test level filtering via query param
    error_res = client.get("/api/logs?level=ERROR")
    assert error_res.status_code == 200
    error_logs = error_res.json()
    assert all(l["level"] == "ERROR" for l in error_logs)
    assert any(f"Job #{pj.id}" in l["message"] for l in error_logs)
