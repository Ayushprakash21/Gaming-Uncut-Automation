"""
Unit & Integration Tests for Instagram Reels Publishing Engine.
Tests:
1. Complete 5-phase lifecycle:
   - Container Creation (resumable session)
   - Binary Transfer (rupload streaming)
   - Container Status Polling (strictly awaits FINISHED)
   - Media Publishing
   - Media Verification (permalink & shortcode confirmation)
2. Prevention of premature publishing (never publish when status is IN_PROGRESS or ERROR).
3. Inaccessible URL validation (rejects localhost / 127.0.0.1 / private IPs before calling Meta).
4. Token expiration (Code 190, Subcode 463) handling and safe error reporting.
5. Permission errors (Code 200, 10) handling.
6. Rate limit handling (Code 613, 32, 4, 17) and automatic quota pause window.
7. Granular upload status transitions in PublishingManager:
   upload_initialized -> video_transferring -> transfer_completed -> instagram_processing -> publishing -> published.
8. Diagnostic connection verification endpoints (/settings/instagram/test and /settings/instagram/status).
9. Structured Audit Logging in SystemLog (module="instagram_publisher") with token redaction.
"""

import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.publishers.instagram.instagram_client import (
    InstagramPublishClient,
    InstagramAPIError,
    InstagramTokenExpiredError,
    InstagramPermissionError,
    InstagramRateLimitError,
    InstagramConfigError,
    InstagramVideoUrlError,
    InstagramProcessingFailedError,
    InstagramProcessingTimeoutError,
    InstagramUploadFailedError,
    parse_instagram_error,
    is_public_url
)
from backend.app.publishers.manager import publishing_manager
from backend.app.publishers.quota_tracker import can_publish, get_or_create_quota_state
from backend.app.models.schema import SourceVideo, ProcessingJob, GeneratedClip, PublishingJob, PlatformUpload, SystemLog
from backend.app.database.session import SessionLocal
from backend.app.security.vault import vault

client = TestClient(app)

@pytest.fixture
def dummy_video(tmp_path):
    video = tmp_path / "test_reel.mp4"
    video.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 4096)
    return video


# ============================================================
# 1. Error Parsing & Redaction Tests
# ============================================================

def test_parse_instagram_error_token_expired():
    payload = {
        "error": {
            "message": "Error validating access token: Session has expired on Tuesday, 17-Nov-26 13:00:00 PST. The current time is Tuesday, 17-Nov-26 14:46:32 PST.",
            "type": "OAuthException",
            "code": 190,
            "error_subcode": 463
        }
    }
    err = parse_instagram_error(payload)
    assert isinstance(err, InstagramTokenExpiredError)
    assert err.code == 190
    assert err.subcode == 463
    assert "expired" in err.user_message.lower()


def test_parse_instagram_error_rate_limit():
    for code in [613, 32, 4, 17]:
        payload = {
            "error": {
                "message": f"(#{code}) User request limit reached",
                "type": "OAuthException",
                "code": code
            }
        }
        err = parse_instagram_error(payload)
        assert isinstance(err, InstagramRateLimitError)
        assert err.code == code
        assert "rate limit" in err.user_message.lower()


def test_parse_instagram_error_permission_denied():
    payload = {
        "error": {
            "message": "(#200) Requires instagram_content_publish permission to manage the object",
            "type": "OAuthException",
            "code": 200
        }
    }
    err = parse_instagram_error(payload)
    assert isinstance(err, InstagramPermissionError)
    assert err.code == 200
    assert "instagram_content_publish" in err.user_message


def test_parse_instagram_error_media_not_ready():
    payload = {
        "error": {
            "message": "The media is not ready for publishing, please wait for a moment",
            "type": "OAuthException",
            "code": 9007,
            "error_subcode": 2207027
        }
    }
    err = parse_instagram_error(payload)
    assert isinstance(err, InstagramProcessingFailedError)
    assert err.code == 9007
    assert err.subcode == 2207027
    assert "not ready" in err.user_message.lower()


def test_parse_instagram_error_invalid_account_id():
    payload = {
        "error": {
            "message": "Unsupported post request. Object with ID 'me' does not exist",
            "type": "GraphMethodException",
            "code": 100
        }
    }
    err = parse_instagram_error(payload)
    assert isinstance(err, InstagramConfigError)
    assert "Account ID" in err.user_message


def test_parse_instagram_error_redaction():
    secret_token = "EAAB1234567890abcdef"
    payload = {
        "error": {
            "message": f"Invalid OAuth token {secret_token} supplied in query.",
            "type": "OAuthException",
            "code": 190
        }
    }
    err = parse_instagram_error(payload, raw_text=f"Raw text with {secret_token}")
    assert secret_token not in err.user_message
    assert secret_token not in str(err)
    assert "[REDACTED_TOKEN]" in str(err)


def test_parse_instagram_error_ruploader_processing_failed():
    """Validates that rupload ProcessingFailedError payloads are parsed with actionable specs guidance."""
    payload = {
        "debug_info": {
            "retriable": False,
            "type": "ProcessingFailedError",
            "message": "Request processing failed"
        }
    }
    err = parse_instagram_error(payload, http_status=400)
    assert isinstance(err, InstagramProcessingFailedError)
    assert err.code == 400
    assert "rejected by Meta edge ingest" in err.user_message
    assert "48kHz" in err.user_message


def test_parse_instagram_error_ruploader_nested_container_invalid():
    """Validates that nested error JSON strings in debug_info.message are extracted and parsed."""
    payload = {
        "debug_info": {
            "retriable": False,
            "type": "ProcessingFailedError",
            "message": '{"success":false,"error":{"message":"The ig container is not in the status to upload a video"}}'
        }
    }
    err = parse_instagram_error(payload, http_status=400)
    assert isinstance(err, InstagramUploadFailedError)
    assert "not in a valid upload status" in err.user_message
    assert "The ig container is not in the status" in err.user_message


def test_parse_instagram_error_ruploader_not_authorized():
    """Validates that ruploader NotAuthorizedError is typed as InstagramTokenExpiredError."""
    payload = {
        "debug_info": {
            "retriable": False,
            "type": "NotAuthorizedError",
            "message": "User not authorized to perform this request"
        }
    }
    err = parse_instagram_error(payload, http_status=400)
    assert isinstance(err, InstagramTokenExpiredError)
    assert err.code == 190
    assert "Meta Access Token is not authorized or has expired" in err.user_message


# ============================================================
# 2. URL Accessibility Validation Tests
# ============================================================

def test_url_accessibility_checker():
    # Public URLs
    assert is_public_url("https://cdn.gaminguncut.com/videos/clip1.mp4") is True
    assert is_public_url("http://media.s3.amazonaws.com/reel.mp4") is True

    # Inaccessible local/private URLs
    assert is_public_url("http://localhost:8000/video.mp4") is False
    assert is_public_url("http://127.0.0.1:5000/video.mp4") is False
    assert is_public_url("http://192.168.1.55/video.mp4") is False
    assert is_public_url("file:///C:/videos/clip.mp4") is False


def test_inaccessible_url_raises_instagram_video_url_error():
    ig = InstagramPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    with pytest.raises(InstagramVideoUrlError) as exc_info:
        ig.publish_reel(video_url="http://localhost:8765/media/clip.mp4", caption="Test")
    assert "accessible" in str(exc_info.value).lower()
    assert exc_info.value.code in (400, "INACCESSIBLE_URL")


# ============================================================
# 3. Lifecycle & Client Execution Tests
# ============================================================

def test_instagram_client_complete_lifecycle_resumable_binary(dummy_video):
    """
    Verifies full 5-stage lifecycle:
    1. Container creation (resumable)
    2. Resumable binary upload
    3. Container polling (awaits FINISHED)
    4. Media publishing
    5. Media verification (returns verified permalink & shortcode)
    """
    ig = InstagramPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_VALID_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    stages_recorded = []

    def mock_post(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        # Stage 1: Container creation
        if "/media" in url and "media_publish" not in url:
            mock_res.json.return_value = {
                "id": "ig_container_12345",
                "uri": "https://rupload.facebook.com/ig-upload/v21.0/ig_container_12345"
            }
        # Stage 2: Resumable binary upload
        elif "rupload.facebook.com" in url:
            mock_res.status_code = 200
            mock_res.json.return_value = {"success": True}
        # Stage 4: Media publish
        elif "/media_publish" in url:
            mock_res.json.return_value = {"id": "ig_media_99999"}
        else:
            mock_res.json.return_value = {"success": True}
        return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        # Stage 3: Container status polling
        if "ig_container_12345" in url:
            mock_res.json.return_value = {
                "id": "ig_container_12345",
                "status_code": "FINISHED",
                "status": "Container processing completed."
            }
        # Stage 5: Media verification
        elif "ig_media_99999" in url:
            mock_res.json.return_value = {
                "id": "ig_media_99999",
                "permalink": "https://www.instagram.com/reel/C8XYZ123abc/",
                "shortcode": "C8XYZ123abc",
                "media_type": "VIDEO"
            }
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
        result = ig.publish_reel(
            video_path=dummy_video,
            caption="Epic Clutch Moment #GamingUncut",
            stage_callback=lambda s: stages_recorded.append(s)
        )

    assert result["platform"] == "instagram"
    assert result["post_id"] == "ig_media_99999"
    assert result["url"] == "https://www.instagram.com/reel/C8XYZ123abc/"
    assert result["container_id"] == "ig_container_12345"

    # Verify all 5 lifecycle stages were visited in order
    assert stages_recorded == [
        "upload_initialized",
        "video_transferring",
        "transfer_completed",
        "instagram_processing",
        "publishing",
        "published"
    ]


def test_instagram_client_complete_lifecycle_public_url():
    """Verifies that public HTTP video_url skips binary transfer and completes stages 1, 3, 4, 5."""
    ig = InstagramPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_VALID_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    stages_recorded = []

    def mock_post(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        if "/media" in url and "media_publish" not in url:
            mock_res.json.return_value = {"id": "ig_container_public"}
        elif "/media_publish" in url:
            mock_res.json.return_value = {"id": "ig_media_public"}
        return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        if "ig_container_public" in url:
            mock_res.json.return_value = {"id": "ig_container_public", "status_code": "FINISHED"}
        elif "ig_media_public" in url:
            mock_res.json.return_value = {
                "id": "ig_media_public",
                "permalink": "https://www.instagram.com/reel/CPublicReel/"
            }
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
        result = ig.publish_reel(
            video_url="https://cdn.gaminguncut.com/reels/clip100.mp4",
            caption="Public URL Reel",
            stage_callback=lambda s: stages_recorded.append(s)
        )

    assert result["post_id"] == "ig_media_public"
    assert result["url"] == "https://www.instagram.com/reel/CPublicReel/"
    assert "video_transferring" not in stages_recorded
    assert stages_recorded == [
        "upload_initialized",
        "instagram_processing",
        "publishing",
        "published"
    ]


# ============================================================
# 4. Premature Publishing Protection Tests
# ============================================================

def test_container_error_status_never_publishes(dummy_video):
    """If Meta transcode fails during container processing (status_code=ERROR), media_publish must NEVER be called."""
    ig = InstagramPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_VALID_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    publish_called = False

    def mock_post(url, *args, **kwargs):
        nonlocal publish_called
        if "/media_publish" in url:
            publish_called = True
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "id": "ig_container_corrupt",
            "uri": "https://rupload.facebook.com/ig-upload/v21.0/ig_container_corrupt"
        }
        return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "id": "ig_container_corrupt",
            "status_code": "ERROR",
            "status": "Transcode failed: Unsupported audio sample rate."
        }
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
        with pytest.raises(InstagramProcessingFailedError) as exc_info:
            ig.publish_reel(video_path=dummy_video, caption="Corrupt audio reel")

    assert "Unsupported audio sample rate" in str(exc_info.value)
    assert publish_called is False, "CRITICAL: media_publish was called even though container status was ERROR!"


def test_container_timeout_never_publishes(dummy_video):
    """If container processing times out (stays IN_PROGRESS), media_publish must NEVER be called."""
    ig = InstagramPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_VALID_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    publish_called = False

    def mock_post(url, *args, **kwargs):
        nonlocal publish_called
        if "/media_publish" in url:
            publish_called = True
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "id": "ig_container_slow",
            "uri": "https://rupload.facebook.com/ig-upload/v21.0/ig_container_slow"
        }
        return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "id": "ig_container_slow",
            "status_code": "IN_PROGRESS",
            "status": "Still transcoding..."
        }
        return mock_res

    # Use small max_checks and zero sleep for test speed
    with patch("requests.post", side_effect=mock_post), \
         patch("requests.get", side_effect=mock_get), \
         patch("time.sleep", return_value=None):
        with pytest.raises(InstagramProcessingTimeoutError) as exc_info:
            ig.publish_reel(
                video_path=dummy_video,
                caption="Slow reel",
                poll_interval_sec=0,
                max_poll_checks=3
            )

    assert "timed out" in str(exc_info.value).lower()
    assert publish_called is False, "CRITICAL: media_publish was called before container reached FINISHED!"


def test_binary_transfer_failure_never_publishes(dummy_video):
    """If binary transfer fails, media_publish must NEVER be called."""
    ig = InstagramPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_VALID_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    publish_called = False

    def mock_post(url, *args, **kwargs):
        nonlocal publish_called
        if "/media_publish" in url:
            publish_called = True
        mock_res = MagicMock()
        if "/media" in url and "media_publish" not in url:
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "id": "ig_container_neterr",
                "uri": "https://rupload.facebook.com/ig-upload/v21.0/ig_container_neterr"
            }
            return mock_res
        elif "rupload.facebook.com" in url:
            mock_res.status_code = 502
            mock_res.text = "Bad Gateway"
            mock_res.json.side_effect = ValueError("Invalid JSON")
            return mock_res
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("time.sleep", return_value=None):
        with pytest.raises(InstagramUploadFailedError) as exc_info:
            ig.publish_reel(video_path=dummy_video, caption="Upload fail test", max_retries=1)

    assert "binary upload returned http 502" in str(exc_info.value).lower() or "transfer failed" in str(exc_info.value).lower()
    assert publish_called is False


def test_transfer_binary_fails_fast_on_non_retriable_rejection(dummy_video):
    """If Meta returns non-retriable: false (e.g. ProcessingFailedError), transfer_binary must stop retrying immediately."""
    ig = InstagramPublishClient()
    token = "MOCK_VALID_TOKEN"
    upload_url = "https://rupload.facebook.com/ig-api-upload/v21.0/test_container"

    post_count = 0

    def mock_post(url, *args, **kwargs):
        nonlocal post_count
        post_count += 1
        mock_res = MagicMock()
        mock_res.status_code = 400
        mock_res.json.return_value = {
            "debug_info": {
                "retriable": False,
                "type": "ProcessingFailedError",
                "message": "Request processing failed"
            }
        }
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("time.sleep", return_value=None):
        with pytest.raises(InstagramProcessingFailedError):
            ig.transfer_binary(upload_url, dummy_video, token, max_retries=3)

    assert post_count == 1, f"Expected exactly 1 POST attempt on non-retriable failure, got {post_count}"


def test_transfer_binary_resumes_from_acknowledged_offset(dummy_video):
    """If initial POST fails, retry queries GET upload_url and resumes from acknowledged byte offset."""
    ig = InstagramPublishClient()
    token = "MOCK_VALID_TOKEN"
    upload_url = "https://rupload.facebook.com/ig-api-upload/v21.0/test_container"

    post_headers_seen = []
    attempt = 0

    def mock_post(url, *args, **kwargs):
        nonlocal attempt
        attempt += 1
        headers = kwargs.get("headers", {})
        post_headers_seen.append(dict(headers))
        mock_res = MagicMock()
        if attempt == 1:
            mock_res.status_code = 500
            mock_res.text = "Internal Server Error"
            mock_res.json.side_effect = ValueError("Invalid JSON")
            return mock_res
        else:
            mock_res.status_code = 200
            mock_res.json.return_value = {"success": True}
            return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {"offset": 2048}
        return mock_res

    with patch("requests.post", side_effect=mock_post), \
         patch("requests.get", side_effect=mock_get), \
         patch("time.sleep", return_value=None):
        success = ig.transfer_binary(upload_url, dummy_video, token, max_retries=2)

    assert success is True
    assert len(post_headers_seen) == 2
    assert post_headers_seen[0]["offset"] == "0"
    assert post_headers_seen[1]["offset"] == "2048"


# ============================================================
# 5. Manager Status & Rate-Limit Handling Tests
# ============================================================

def test_publishing_manager_instagram_expired_token_handling(dummy_video, monkeypatch):
    """Expired Meta token must fail the upload record with code 190 and user-friendly message."""
    vault.set_secret("meta", "page_access_token", "EXPIRED_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    from backend.app.publishers.instagram.instagram_client import instagram_client
    monkeypatch.setattr(
        instagram_client,
        "publish_reel",
        InstagramPublishClient.publish_reel.__get__(instagram_client, InstagramPublishClient)
    )

    with SessionLocal() as db:
        src = SourceVideo(url="https://youtube.com/watch?v=igtest01", title="IG Test Expired", duration=60.0, local_path=str(dummy_video))
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

        pj = PublishingJob(clip_id=clip.id, scheduled_time=datetime.utcnow(), status="pending", approval_status="approved")
        db.add(pj)
        db.commit()
        db.refresh(pj)

        upload = PlatformUpload(publishing_job_id=pj.id, platform="instagram", status="pending")
        db.add(upload)
        db.commit()

        # Mock container creation returning 190 token expired
        def mock_post(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "error": {
                    "message": "Error validating access token: Session has expired on Tuesday, 17-Nov-26 13:00:00 PST.",
                    "type": "OAuthException",
                    "code": 190,
                    "error_subcode": 463
                }
            }
            return mock_res

        with patch("requests.post", side_effect=mock_post):
            publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "failed"
        assert upload.error_code == "190"
        assert "expired" in upload.error_message.lower()


def test_publishing_manager_instagram_rate_limit_handling(dummy_video, monkeypatch):
    """Instagram rate limit (#613) must set status to paused_quota and schedule next window."""
    vault.set_secret("meta", "page_access_token", "VALID_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    from backend.app.publishers.instagram.instagram_client import instagram_client
    monkeypatch.setattr(
        instagram_client,
        "publish_reel",
        InstagramPublishClient.publish_reel.__get__(instagram_client, InstagramPublishClient)
    )

    with SessionLocal() as db:
        # Reset quota state
        ig_state = get_or_create_quota_state(db, "instagram")
        ig_state.rate_limited = False
        ig_state.pause_until = None
        db.commit()

        src = SourceVideo(url="https://youtube.com/watch?v=igtest02", title="IG Test Rate Limit", duration=60.0, local_path=str(dummy_video))
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

        pj = PublishingJob(clip_id=clip.id, scheduled_time=datetime.utcnow(), status="pending", approval_status="approved")
        db.add(pj)
        db.commit()
        db.refresh(pj)

        upload = PlatformUpload(publishing_job_id=pj.id, platform="instagram", status="pending")
        db.add(upload)
        db.commit()

        # Mock container creation returning #613 Rate Limit
        def mock_post(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "error": {
                    "message": "(#613) Rate limit exceeded for Instagram Professional API",
                    "type": "OAuthException",
                    "code": 613
                }
            }
            return mock_res

        with patch("requests.post", side_effect=mock_post):
            publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "paused_quota"
        assert upload.error_code == "613"

        # Verify platform quota state recorded rate limit
        db.refresh(ig_state)
        assert ig_state.rate_limited is True
        assert ig_state.pause_until is not None

        # Verify can_publish is paused
        allowed, reason, _ = can_publish(db, "instagram")
        assert allowed is False
        assert "paused until" in reason


def test_publishing_manager_instagram_successful_publication(dummy_video, monkeypatch):
    """End-to-end PublishingManager successful Instagram Reel publication."""
    vault.set_secret("meta", "page_access_token", "MOCK_VALID_PAGE_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841400000000000")

    from backend.app.publishers.instagram.instagram_client import instagram_client
    monkeypatch.setattr(
        instagram_client,
        "publish_reel",
        InstagramPublishClient.publish_reel.__get__(instagram_client, InstagramPublishClient)
    )

    with SessionLocal() as db:
        ig_state = get_or_create_quota_state(db, "instagram")
        ig_state.rate_limited = False
        ig_state.pause_until = None
        db.commit()

        src = SourceVideo(url="https://youtube.com/watch?v=igtest_success", title="IG Success Test", duration=60.0, local_path=str(dummy_video))
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

        pj = PublishingJob(clip_id=clip.id, scheduled_time=datetime.utcnow(), status="pending", approval_status="approved")
        db.add(pj)
        db.commit()
        db.refresh(pj)

        upload = PlatformUpload(publishing_job_id=pj.id, platform="instagram", status="pending")
        db.add(upload)
        db.commit()

        def mock_post(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 200
            if "/media" in url and "media_publish" not in url:
                mock_res.json.return_value = {
                    "id": "ig_cnt_success",
                    "uri": "https://rupload.facebook.com/ig-upload/v21.0/ig_cnt_success"
                }
            elif "rupload.facebook.com" in url:
                mock_res.json.return_value = {"success": True}
            elif "/media_publish" in url:
                mock_res.json.return_value = {"id": "ig_pub_998877"}
            return mock_res

        def mock_get(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 200
            if "ig_cnt_success" in url:
                mock_res.json.return_value = {
                    "id": "ig_cnt_success",
                    "status_code": "FINISHED",
                    "status": "Ready"
                }
            elif "ig_pub_998877" in url:
                mock_res.json.return_value = {
                    "id": "ig_pub_998877",
                    "permalink": "https://www.instagram.com/reel/CSuccess123/",
                    "shortcode": "CSuccess123"
                }
            return mock_res

        with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
            results = publishing_manager.execute_job(pj.id)

        assert "instagram" in results
        assert results["instagram"]["post_id"] == "ig_pub_998877"
        assert results["instagram"]["url"] == "https://www.instagram.com/reel/CSuccess123/"

        db.refresh(upload)
        assert upload.status == "published"
        assert upload.external_post_id == "ig_pub_998877"
        assert upload.external_url == "https://www.instagram.com/reel/CSuccess123/"
        assert upload.error_message is None

        db.refresh(pj)
        assert pj.status == "published"


# ============================================================
# 6. Diagnostic & Settings Endpoint Tests
# ============================================================

def test_instagram_diagnostic_endpoint_success():
    """Verify GET /api/settings/instagram/status returns account and permission details."""
    vault.set_secret("meta", "page_access_token", "MOCK_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841448213228145")

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        if "/me/permissions" in url:
            mock_res.json.return_value = {
                "data": [
                    {"permission": "instagram_content_publish", "status": "granted"},
                    {"permission": "pages_show_list", "status": "granted"},
                    {"permission": "instagram_basic", "status": "granted"}
                ]
            }
        elif "17841448213228145" in url:
            mock_res.json.return_value = {
                "id": "17841448213228145",
                "username": "gaminguncutofficial",
                "name": "Gaming Uncut Official"
            }
        return mock_res

    with patch("requests.get", side_effect=mock_get):
        res = client.get("/api/settings/instagram/status")
        assert res.status_code == 200
        data = res.json()
        assert data["authenticated"] is True
        assert data["account_id"] == "17841448213228145"
        assert data["account_username"] == "gaminguncutofficial"
        assert "instagram_content_publish" in data["permissions"]
        assert len(data["missing_permissions"]) == 0


def test_instagram_diagnostic_endpoint_expired_token():
    """Verify POST /api/settings/instagram/test captures expired token gracefully."""
    vault.set_secret("meta", "page_access_token", "EXPIRED_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841448213228145")

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 400
        mock_res.json.return_value = {
            "error": {
                "message": "Error validating access token: Session has expired on Tuesday, 17-Nov-26 13:00:00 PST.",
                "type": "OAuthException",
                "code": 190,
                "error_subcode": 463
            }
        }
        return mock_res

    with patch("requests.get", side_effect=mock_get):
        res = client.post("/api/settings/instagram/test")
        assert res.status_code == 200
        data = res.json()
        assert data["authenticated"] is False
        assert data["error_code"] == 190
        assert data["subcode"] == 463
        assert "expired" in data["message"].lower()


def test_instagram_diagnostic_endpoint_missing_token():
    """Verify diagnostic returns unconfigured if token is empty."""
    vault.set_secret("meta", "page_access_token", "")

    res = client.get("/api/settings/instagram/status")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is False
    assert data["authenticated"] is False
    assert "not set" in data["message"].lower()


# ============================================================
# 7. Structured Audit Logging Verification Tests
# ============================================================

def test_instagram_audit_logging(dummy_video):
    """Verify that Instagram publishing operations persist structured JSON logs in SystemLog."""
    ig = InstagramPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_AUDIT_TOKEN")
    vault.set_secret("meta", "instagram_account_id", "17841448213228145")

    def mock_post(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        if "/media" in url and "media_publish" not in url:
            mock_res.json.return_value = {
                "id": "ig_audit_container",
                "uri": "https://rupload.facebook.com/ig-upload/v21.0/ig_audit_container"
            }
        elif "rupload.facebook.com" in url:
            mock_res.json.return_value = {"success": True}
        elif "/media_publish" in url:
            mock_res.json.return_value = {"id": "ig_audit_media"}
        return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        if "ig_audit_container" in url:
            mock_res.json.return_value = {"id": "ig_audit_container", "status_code": "FINISHED"}
        elif "ig_audit_media" in url:
            mock_res.json.return_value = {
                "id": "ig_audit_media",
                "permalink": "https://www.instagram.com/reel/CAudit123/"
            }
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
        ig.publish_reel(video_path=dummy_video, caption="Audit log test reel")

    with SessionLocal() as db:
        logs = db.query(SystemLog).filter(SystemLog.module == "instagram_publisher").order_by(SystemLog.id.desc()).limit(10).all()
        assert len(logs) > 0

        messages = [log.message for log in logs]
        assert any("container" in m.lower() for m in messages) or any("finished" in m.lower() for m in messages) or any("published" in m.lower() for m in messages)

        # Check token redaction in logs
        for log in logs:
            assert "MOCK_AUDIT_TOKEN" not in (log.message or "")
            assert "MOCK_AUDIT_TOKEN" not in (log.metadata_json or "")
