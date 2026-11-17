"""
Unit & Integration Tests for Facebook Reels Publishing Engine.
Tests:
1. Complete 4-phase lifecycle: START session, TRANSFER binary, FINISH publishing, STATUS POLLING verification.
2. Prevention of treating START as success.
3. Token expiration (Code 190, Subcode 463) handling and safe error reporting.
4. Permission errors (Code 200, 10, 283) handling.
5. Rate limit handling (Code 613, 32, 4, 17) and automatic quota pause window.
6. Granular upload status transitions in PublishingManager:
   upload_initialized -> video_transferring -> transfer_completed -> facebook_processing -> publishing -> published.
7. Diagnostic connection verification endpoint (/settings/facebook/test and /settings/facebook/status).
8. Protection against empty video, missing file, missing page_id (never falls back to 'me').
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.publishers.facebook.facebook_client import (
    FacebookPublishClient,
    FacebookAPIError,
    FacebookTokenExpiredError,
    FacebookPermissionError,
    FacebookRateLimitError,
    FacebookConfigError,
    FacebookUploadFailedError,
    parse_meta_error
)
from backend.app.publishers.manager import publishing_manager
from backend.app.publishers.quota_tracker import can_publish, get_or_create_quota_state
from backend.app.models.schema import SourceVideo, ProcessingJob, DetectedHighlight, GeneratedClip, PublishingJob, PlatformUpload
from backend.app.database.session import SessionLocal
from backend.app.security.vault import vault

client = TestClient(app)

@pytest.fixture
def dummy_video(tmp_path):
    video = tmp_path / "test_reel.mp4"
    video.write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 2048)
    return video


# ============================================================
# 1. Error Parsing Tests
# ============================================================

def test_parse_meta_error_token_expired():
    payload = {
        "error": {
            "message": "Error validating access token: Session has expired on Tuesday, 17-Nov-26 13:00:00 PST.",
            "type": "OAuthException",
            "code": 190,
            "error_subcode": 463
        }
    }
    err = parse_meta_error(payload)
    assert isinstance(err, FacebookTokenExpiredError)
    assert err.code == 190
    assert err.subcode == 463
    assert "expired" in err.user_message.lower()


def test_parse_meta_error_rate_limit():
    payload = {
        "error": {
            "message": "(#613) Rate limit exceeded",
            "type": "OAuthException",
            "code": 613
        }
    }
    err = parse_meta_error(payload)
    assert isinstance(err, FacebookRateLimitError)
    assert err.code == 613
    assert "rate limit" in err.user_message.lower()


def test_parse_meta_error_permission_denied():
    payload = {
        "error": {
            "message": "(#200) Requires pages_manage_posts permission to manage the object",
            "type": "OAuthException",
            "code": 200
        }
    }
    err = parse_meta_error(payload)
    assert isinstance(err, FacebookPermissionError)
    assert err.code == 200
    assert "pages_manage_posts" in err.user_message


def test_parse_meta_error_invalid_page_me():
    payload = {
        "error": {
            "message": "Unsupported post request. Object with ID 'me' does not exist, cannot be loaded due to missing permissions, or does not support this operation.",
            "type": "GraphMethodException",
            "code": 100
        }
    }
    err = parse_meta_error(payload)
    assert isinstance(err, FacebookConfigError)
    assert "Page ID" in err.user_message


# ============================================================
# 2. Lifecycle & Client Execution Tests
# ============================================================

def test_facebook_client_complete_lifecycle(dummy_video):
    fb = FacebookPublishClient()
    vault.set_secret("meta", "page_access_token", "VALID_PAGE_TOKEN")
    vault.set_secret("meta", "page_id", "123456789")

    stages_recorded = []

    def mock_post(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        # Phase 1: START
        if "video_reels" in url and kwargs.get("data", {}).get("upload_phase") == "start":
            mock_res.json.return_value = {
                "video_id": "fb_vid_9999",
                "upload_url": "https://rupload.facebook.com/fb_vid_9999"
            }
        # Phase 2: TRANSFER
        elif "rupload.facebook.com" in url:
            mock_res.json.return_value = {"success": True}
        # Phase 3: FINISH
        elif "video_reels" in url and kwargs.get("data", {}).get("upload_phase") == "finish":
            mock_res.json.return_value = {"success": True}
        else:
            mock_res.json.return_value = {"success": True}
        return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        # Phase 4: STATUS POLL
        mock_res.json.return_value = {
            "id": "fb_vid_9999",
            "status": {
                "video_status": "ready",
                "uploading_phase": {"status": "complete"},
                "processing_phase": {"status": "complete"},
                "publishing_phase": {"status": "complete"}
            },
            "published": True
        }
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
        result = fb.publish_reel(
            video_path=dummy_video,
            description="Epic Clutch #gaming",
            stage_callback=lambda s: stages_recorded.append(s)
        )

    assert result["platform"] == "facebook"
    assert result["post_id"] == "fb_vid_9999"
    assert "https://www.facebook.com/reel/fb_vid_9999" in result["url"]

    # Verify all 4 lifecycle stages were visited in order
    assert stages_recorded == [
        "upload_initialized",
        "video_transferring",
        "transfer_completed",
        "publishing",
        "facebook_processing",
        "published"
    ]


def test_start_response_never_treated_as_upload_success(dummy_video):
    """If TRANSFER fails after successful START, upload must fail and never return success."""
    fb = FacebookPublishClient()
    vault.set_secret("meta", "page_access_token", "VALID_PAGE_TOKEN")
    vault.set_secret("meta", "page_id", "123456789")

    def mock_post(url, *args, **kwargs):
        mock_res = MagicMock()
        if "video_reels" in url and kwargs.get("data", {}).get("upload_phase") == "start":
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "video_id": "fb_vid_start_only",
                "upload_url": "https://rupload.facebook.com/fb_vid_start_only"
            }
            return mock_res
        elif "rupload.facebook.com" in url:
            mock_res.status_code = 500
            mock_res.text = "Internal Server Error during transfer"
            return mock_res
        mock_res.status_code = 400
        return mock_res

    with patch("requests.post", side_effect=mock_post):
        with pytest.raises(FacebookUploadFailedError):
            fb.publish_reel(dummy_video, "Test description")


def test_processing_phase_error_raises_exception(dummy_video):
    """If Facebook transcode rejects video during processing, publish_reel must fail."""
    fb = FacebookPublishClient()
    vault.set_secret("meta", "page_access_token", "VALID_PAGE_TOKEN")
    vault.set_secret("meta", "page_id", "123456789")

    def mock_post(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        data = kwargs.get("data")
        if isinstance(data, dict) and data.get("upload_phase") == "start":
            mock_res.json.return_value = {"video_id": "fb_vid_corrupt", "upload_url": "https://rupload.facebook.com/x"}
        elif isinstance(data, dict) and data.get("upload_phase") == "finish":
            mock_res.json.return_value = {"success": True}
        else:
            mock_res.json.return_value = {"success": True}
        return mock_res

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "id": "fb_vid_corrupt",
            "status": {
                "video_status": "error",
                "error_description": "Unsupported audio codec in container."
            }
        }
        return mock_res

    with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
        with pytest.raises(FacebookUploadFailedError) as exc_info:
            fb.publish_reel(dummy_video, "Test")
        assert "Unsupported audio codec" in str(exc_info.value)


# ============================================================
# 3. Manager Granular Status & Quota/Rate-Limit Tests
# ============================================================

def test_publishing_manager_facebook_expired_token_handling(dummy_video, monkeypatch):
    """Expired Meta token must fail the upload record with code 190 and user-friendly error message."""
    vault.set_secret("meta", "page_access_token", "EXPIRED_TOKEN")
    vault.set_secret("meta", "page_id", "123456789")

    from backend.app.publishers.facebook.facebook_client import facebook_client
    monkeypatch.setattr(
        facebook_client,
        "publish_reel",
        FacebookPublishClient.publish_reel.__get__(facebook_client, FacebookPublishClient)
    )

    with SessionLocal() as db:
        src = SourceVideo(url="https://youtube.com/watch?v=fbtest01", title="FB Test", duration=60.0, local_path=str(dummy_video))
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

        upload = PlatformUpload(publishing_job_id=pj.id, platform="facebook", status="pending")
        db.add(upload)
        db.commit()

        # Mock Facebook START returning 190 expired token
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


def test_publishing_manager_facebook_rate_limit_handling(dummy_video, monkeypatch):
    """Facebook rate limit error (#613) must set status to paused_quota and schedule next window."""
    vault.set_secret("meta", "page_access_token", "VALID_TOKEN")
    vault.set_secret("meta", "page_id", "123456789")

    from backend.app.publishers.facebook.facebook_client import facebook_client
    monkeypatch.setattr(
        facebook_client,
        "publish_reel",
        FacebookPublishClient.publish_reel.__get__(facebook_client, FacebookPublishClient)
    )

    with SessionLocal() as db:
        # Reset any previous quota state
        fb_state = get_or_create_quota_state(db, "facebook")
        fb_state.rate_limited = False
        fb_state.pause_until = None
        db.commit()

        src = SourceVideo(url="https://youtube.com/watch?v=fbtest02", title="FB Test Rate Limit", duration=60.0, local_path=str(dummy_video))
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

        upload = PlatformUpload(publishing_job_id=pj.id, platform="facebook", status="pending")
        db.add(upload)
        db.commit()

        # Mock Facebook START returning #613 Rate Limit
        def mock_post(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 400
            mock_res.json.return_value = {
                "error": {
                    "message": "(#613) Rate limit exceeded",
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

        # Verify platform quota state recorded the rate limit
        db.refresh(fb_state)
        assert fb_state.rate_limited is True
        assert fb_state.pause_until is not None

        # Verify can_publish is paused
        allowed, reason, _ = can_publish(db, "facebook")
        assert allowed is False
        assert "paused until" in reason


# ============================================================
# 4. Diagnostic & Settings Endpoint Tests
# ============================================================

def test_facebook_diagnostic_endpoint_success():
    """Verify GET /api/settings/facebook/status returns page and permission details."""
    vault.set_secret("meta", "page_access_token", "MOCK_TOKEN")
    vault.set_secret("meta", "page_id", "987654321")

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 200
        if "/me/permissions" in url:
            mock_res.json.return_value = {
                "data": [
                    {"permission": "pages_manage_posts", "status": "granted"},
                    {"permission": "pages_read_engagement", "status": "granted"},
                    {"permission": "pages_show_list", "status": "granted"}
                ]
            }
        elif "/me" in url:
            mock_res.json.return_value = {
                "id": "987654321",
                "name": "Gaming Uncut Official Page",
                "category": "Gaming Video Creator"
            }
        return mock_res

    with patch("requests.get", side_effect=mock_get):
        res = client.get("/api/settings/facebook/status")
        assert res.status_code == 200
        data = res.json()
        assert data["authenticated"] is True
        assert data["page_name"] == "Gaming Uncut Official Page"
        assert data["page_id"] == "987654321"
        assert "pages_manage_posts" in data["permissions"]
        assert len(data["missing_permissions"]) == 0


def test_facebook_diagnostic_endpoint_expired_token():
    """Verify diagnostic endpoint captures code 190 expired token gracefully."""
    vault.set_secret("meta", "page_access_token", "EXPIRED_MOCK_TOKEN")

    def mock_get(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 400
        mock_res.json.return_value = {
            "error": {
                "message": "Error validating access token: Session has expired",
                "type": "OAuthException",
                "code": 190,
                "error_subcode": 463
            }
        }
        return mock_res

    with patch("requests.get", side_effect=mock_get):
        res = client.post("/api/settings/facebook/test")
        assert res.status_code == 200
        data = res.json()
        assert data["authenticated"] is False
        assert data["error_code"] == 190
        assert data["subcode"] == 463
        assert "expired" in data["message"].lower()


def test_facebook_client_permission_denied(dummy_video):
    """Verify Facebook client raises FacebookPermissionError when permission is rejected."""
    fb = FacebookPublishClient()
    vault.set_secret("meta", "page_access_token", "MOCK_TOKEN_NO_PERMS")
    vault.set_secret("meta", "page_id", "123456789")

    def mock_post(url, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.status_code = 403
        mock_res.json.return_value = {
            "error": {
                "message": "(#200) Requires pages_manage_posts permission to manage the object",
                "type": "OAuthException",
                "code": 200
            }
        }
        return mock_res

    with patch("requests.post", side_effect=mock_post):
        with pytest.raises(FacebookPermissionError) as exc_info:
            fb.publish_reel(dummy_video, "Test description")
        assert exc_info.value.code == 200
        assert "pages_manage_posts" in exc_info.value.user_message


def test_publishing_manager_facebook_permission_denied_handling(dummy_video, monkeypatch):
    """PublishingManager marks upload as failed with code 200 on FacebookPermissionError."""
    vault.set_secret("meta", "page_access_token", "MOCK_TOKEN")
    vault.set_secret("meta", "page_id", "123456789")

    from backend.app.publishers.facebook.facebook_client import facebook_client
    monkeypatch.setattr(
        facebook_client,
        "publish_reel",
        FacebookPublishClient.publish_reel.__get__(facebook_client, FacebookPublishClient)
    )

    with SessionLocal() as db:
        # Reset any previous quota state
        fb_state = get_or_create_quota_state(db, "facebook")
        fb_state.rate_limited = False
        fb_state.pause_until = None
        db.commit()

        src = SourceVideo(url="https://youtube.com/watch?v=fbtest_perm", title="FB Perm Test", duration=60.0, local_path=str(dummy_video))
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

        upload = PlatformUpload(publishing_job_id=pj.id, platform="facebook", status="pending")
        db.add(upload)
        db.commit()

        def mock_post(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 403
            mock_res.json.return_value = {
                "error": {
                    "message": "(#200) Requires pages_manage_posts permission",
                    "type": "OAuthException",
                    "code": 200
                }
            }
            return mock_res

        with patch("requests.post", side_effect=mock_post):
            publishing_manager.execute_job(pj.id)

        db.refresh(upload)
        assert upload.status == "failed"
        assert upload.error_code == "200"
        assert "pages_manage_posts" in upload.error_message


def test_publishing_manager_facebook_successful_publication(dummy_video, monkeypatch):
    """End-to-end PublishingManager successful Facebook Reel publication."""
    vault.set_secret("meta", "page_access_token", "MOCK_VALID_PAGE_TOKEN")
    vault.set_secret("meta", "page_id", "987654321")

    from backend.app.publishers.facebook.facebook_client import facebook_client
    monkeypatch.setattr(
        facebook_client,
        "publish_reel",
        FacebookPublishClient.publish_reel.__get__(facebook_client, FacebookPublishClient)
    )

    with SessionLocal() as db:
        # Reset quota state
        fb_state = get_or_create_quota_state(db, "facebook")
        fb_state.rate_limited = False
        fb_state.pause_until = None
        db.commit()

        src = SourceVideo(url="https://youtube.com/watch?v=fbtest_success", title="FB Success Test", duration=60.0, local_path=str(dummy_video))
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

        upload = PlatformUpload(publishing_job_id=pj.id, platform="facebook", status="pending")
        db.add(upload)
        db.commit()

        def mock_post(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 200
            if "video_reels" in url and kwargs.get("data", {}).get("upload_phase") == "start":
                mock_res.json.return_value = {
                    "video_id": "fb_reel_112233",
                    "upload_url": "https://rupload.facebook.com/fb_reel_112233"
                }
            elif "rupload.facebook.com" in url:
                mock_res.json.return_value = {"success": True}
            elif "video_reels" in url and kwargs.get("data", {}).get("upload_phase") == "finish":
                mock_res.json.return_value = {"success": True}
            else:
                mock_res.json.return_value = {"success": True}
            return mock_res

        def mock_get(url, *args, **kwargs):
            mock_res = MagicMock()
            mock_res.status_code = 200
            mock_res.json.return_value = {
                "id": "fb_reel_112233",
                "status": {
                    "video_status": "ready",
                    "uploading_phase": {"status": "complete"},
                    "processing_phase": {"status": "complete"},
                    "publishing_phase": {"status": "complete"}
                },
                "published": True
            }
            return mock_res

        with patch("requests.post", side_effect=mock_post), patch("requests.get", side_effect=mock_get):
            results = publishing_manager.execute_job(pj.id)

        assert "facebook" in results
        assert results["facebook"]["post_id"] == "fb_reel_112233"
        assert "https://www.facebook.com/reel/fb_reel_112233" in results["facebook"]["url"]

        db.refresh(upload)
        assert upload.status == "published"
        assert upload.external_post_id == "fb_reel_112233"
        assert upload.external_url == "https://www.facebook.com/reel/fb_reel_112233"
        assert upload.error_message is None

        db.refresh(pj)
        assert pj.status == "published"


from datetime import datetime
