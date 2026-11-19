"""
Regression Test Suite for:
GAMING UNCUT — FIX FALSE INSTAGRAM PUBLISHED STATUS

Covers all 10 verification targets:
1. Successful media_publish plus successful GET produces PUBLISHED_VERIFIED.
2. Successful media_publish plus failed GET never produces PUBLISHED_VERIFIED.
3. Failed verification never fabricates a permalink (no https://www.instagram.com/reel/{media_id}).
4. Media ID is preserved after verification failure.
5. Reconciliation does not call media_publish again on existing media ID.
6. Correct account ownership is checked (owner ID matching connected account).
7. Missing permalink remains unverified (PUBLISHED_UNVERIFIED).
8. A verified permalink is stored and displayed.
9. Existing published records are not duplicated.
10. Dashboard status matches persisted publishing state.
"""

import pytest
import json
import re
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    GeneratedClip, ClipMetadata, PublishingJob, PlatformUpload,
    PublishingIdempotencyRecord, SourceVideo, ProcessingJob
)
from backend.app.publishers.idempotency import (
    idempotency_manager, PublishingState, build_idempotency_key
)
from backend.app.publishers.instagram.instagram_client import (
    instagram_client, InstagramVerificationError, GRAPH_URL
)
from backend.app.publishers.instagram.reconciler import instagram_reconciler

client = TestClient(app)

TEST_IG_ACCOUNT_ID = "17841448213228145"
TEST_VALID_SHORTCODE = "Df5Spx2B5UP"
TEST_VALID_PERMALINK = f"https://www.instagram.com/reel/{TEST_VALID_SHORTCODE}/"
TEST_VALID_MEDIA_ID = "18123773044822375"
TEST_CONTAINER_ID = "18058887653607108"


@pytest.fixture
def test_setup(tmp_path):
    """Sets up database fixtures for Instagram verification tests."""
    with SessionLocal() as db:
        clip_file = tmp_path / "test_ig_video.mp4"
        clip_file.write_bytes(b"dummy_test_bytes_for_ig")

        src = SourceVideo(title="Test GTA 6 Drift", local_path=str(clip_file), duration=60.0)
        db.add(src)
        db.flush()

        pj = ProcessingJob(source_video_id=src.id, status="completed", progress_pct=100)
        db.add(pj)
        db.flush()

        clip = GeneratedClip(job_id=pj.id, output_path=str(clip_file), duration_sec=30.0)
        db.add(clip)
        db.flush()

        meta = ClipMetadata(clip_id=clip.id, title="Test GTA 6 Drift", instagram_caption="Cruising in Vice City #Shorts")
        db.add(meta)
        db.flush()

        job = PublishingJob(clip_id=clip.id, scheduled_time=datetime.utcnow(), status="pending", approval_status="approved")
        db.add(job)
        db.flush()

        upload = PlatformUpload(
            publishing_job_id=job.id,
            platform="instagram",
            account_id=TEST_IG_ACCOUNT_ID,
            status="upload_initialized",
            idempotency_key=build_idempotency_key(clip.id, "instagram", TEST_IG_ACCOUNT_ID),
            external_post_id=TEST_CONTAINER_ID
        )
        db.add(upload)
        db.commit()

        yield {
            "clip_id": clip.id,
            "job_id": job.id,
            "upload_id": upload.id,
            "video_path": str(clip_file)
        }

        # Cleanup fixture records from DB
        db.query(PlatformUpload).filter_by(id=upload.id).delete()
        db.query(PublishingJob).filter_by(id=job.id).delete()
        db.query(ClipMetadata).filter_by(clip_id=clip.id).delete()
        db.query(GeneratedClip).filter_by(id=clip.id).delete()
        db.query(ProcessingJob).filter_by(id=pj.id).delete()
        db.query(SourceVideo).filter_by(id=src.id).delete()
        db.commit()


def test_successful_publish_and_get_produces_published_verified():
    """1. Successful media_publish plus successful GET produces PUBLISHED_VERIFIED with canonical permalink."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": TEST_VALID_MEDIA_ID,
        "media_type": "VIDEO",
        "permalink": TEST_VALID_PERMALINK,
        "shortcode": TEST_VALID_SHORTCODE,
        "timestamp": "2026-11-19T01:00:00+0000",
        "owner": {"id": TEST_IG_ACCOUNT_ID}
    }

    with patch("backend.app.publishers.instagram.instagram_client.meta_transport.get", return_value=mock_resp):
        res = instagram_client.verify_media_published(
            media_id=TEST_VALID_MEDIA_ID,
            token="test_token",
            expected_ig_id=TEST_IG_ACCOUNT_ID
        )
        assert res["is_verified"] is True
        assert res["media_id"] == TEST_VALID_MEDIA_ID
        assert res["permalink"] == TEST_VALID_PERMALINK
        assert res["shortcode"] == TEST_VALID_SHORTCODE


def test_failed_get_never_produces_published_verified():
    """2. Successful media_publish plus failed GET never produces PUBLISHED_VERIFIED."""
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "error": {
            "message": "(#100) Tried accessing nonexisting field (media_type)",
            "code": 100,
            "type": "OAuthException"
        }
    }

    with patch("backend.app.publishers.instagram.instagram_client.meta_transport.get", return_value=mock_resp):
        with pytest.raises(InstagramVerificationError) as exc_info:
            instagram_client.verify_media_published(
                media_id=TEST_CONTAINER_ID,
                token="test_token",
                expected_ig_id=TEST_IG_ACCOUNT_ID,
                max_retries=1
            )
        assert exc_info.value.media_id == TEST_CONTAINER_ID


def test_failed_verification_never_fabricates_permalink():
    """3. Failed verification never fabricates a permalink like https://www.instagram.com/reel/{media_id}."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"error": {"message": "Server error", "code": 1}}

    with patch("backend.app.publishers.instagram.instagram_client.meta_transport.get", return_value=mock_resp):
        with pytest.raises(InstagramVerificationError) as exc_info:
            instagram_client.verify_media_published(
                media_id="18999999999999999",
                token="test_token",
                max_retries=1
            )
        # Ensure exception was raised instead of returning a fake URL
        assert "18999999999999999" in str(exc_info.value)


def test_publish_reel_preserves_media_id_when_verification_unconfirmed(test_setup, monkeypatch):
    """4. In publish_reel(), media ID is preserved and status is published_unverified when verification fails."""
    from backend.app.publishers.instagram.instagram_client import InstagramPublishClient
    # Restore genuine publish_reel method overriding global test autouse mock
    monkeypatch.setattr(instagram_client, "publish_reel", InstagramPublishClient.publish_reel.__get__(instagram_client, InstagramPublishClient))

    with patch.object(instagram_client, "_get_auth", return_value={"token": "t", "ig_id": TEST_IG_ACCOUNT_ID}), \
         patch.object(instagram_client, "optimize_video_for_reels", return_value=(test_setup["video_path"], False)), \
         patch.object(instagram_client, "create_media_container", return_value={"container_id": TEST_CONTAINER_ID, "upload_url": "https://rupload.test"}), \
         patch.object(instagram_client, "transfer_binary", return_value=None), \
         patch.object(instagram_client, "poll_container_status", return_value={"status_code": "FINISHED"}), \
         patch.object(instagram_client, "publish_container", return_value=TEST_VALID_MEDIA_ID), \
         patch.object(instagram_client, "verify_media_published", side_effect=InstagramVerificationError("Verification timeout", media_id=TEST_VALID_MEDIA_ID)):

        res = instagram_client.publish_reel(
            video_path=test_setup["video_path"],
            caption="Test Caption"
        )

        assert res["status"] == "published_unverified"
        assert res["post_id"] == TEST_VALID_MEDIA_ID
        assert res["url"] is None
        assert res["is_verified"] is False


def test_reconciliation_does_not_call_media_publish_again(test_setup):
    """5. Reconciliation of unverified media ID uses safe GET queries and never calls media_publish again."""
    with SessionLocal() as db:
        upload = db.query(PlatformUpload).filter(PlatformUpload.id == test_setup["upload_id"]).first()
        upload.status = "published_unverified"
        upload.external_post_id = TEST_VALID_MEDIA_ID
        upload.external_url = None
        db.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": TEST_VALID_MEDIA_ID,
        "media_type": "VIDEO",
        "permalink": TEST_VALID_PERMALINK,
        "shortcode": TEST_VALID_SHORTCODE,
        "timestamp": "2026-11-19T01:00:00+0000",
        "owner": {"id": TEST_IG_ACCOUNT_ID}
    }

    with patch("backend.app.publishers.instagram.reconciler.meta_token_manager.get_instagram_credentials", return_value={"token": "t", "instagram_account_id": TEST_IG_ACCOUNT_ID}), \
         patch("backend.app.publishers.instagram.instagram_client.meta_transport.get", return_value=mock_resp), \
         patch.object(instagram_client, "publish_container") as mock_publish:

        with SessionLocal() as db:
            results = instagram_reconciler.reconcile_pending_containers(db)

        # publish_container must NEVER be called
        mock_publish.assert_not_called()

        with SessionLocal() as db:
            u_after = db.query(PlatformUpload).filter(PlatformUpload.id == test_setup["upload_id"]).first()
            assert u_after.status == "published"
            assert u_after.external_url == TEST_VALID_PERMALINK
            assert u_after.external_post_id == TEST_VALID_MEDIA_ID


def test_correct_account_ownership_is_enforced():
    """6. Verifies that media owned by a different account is rejected."""
    wrong_account_id = "99999999999999999"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": TEST_VALID_MEDIA_ID,
        "media_type": "VIDEO",
        "permalink": TEST_VALID_PERMALINK,
        "shortcode": TEST_VALID_SHORTCODE,
        "owner": {"id": wrong_account_id}
    }

    with patch("backend.app.publishers.instagram.instagram_client.meta_transport.get", return_value=mock_resp):
        with pytest.raises(InstagramVerificationError) as exc_info:
            instagram_client.verify_media_published(
                media_id=TEST_VALID_MEDIA_ID,
                token="test_token",
                expected_ig_id=TEST_IG_ACCOUNT_ID
            )
        assert "belongs to account" in str(exc_info.value)


def test_missing_permalink_remains_unverified():
    """7. If media object has no permalink and no shortcode, verification fails and does not fabricate."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "id": TEST_VALID_MEDIA_ID,
        "media_type": "VIDEO",
        "permalink": None,
        "shortcode": None,
        "owner": {"id": TEST_IG_ACCOUNT_ID}
    }

    with patch("backend.app.publishers.instagram.instagram_client.meta_transport.get", return_value=mock_resp):
        with pytest.raises(InstagramVerificationError) as exc_info:
            instagram_client.verify_media_published(
                media_id=TEST_VALID_MEDIA_ID,
                token="test_token",
                expected_ig_id=TEST_IG_ACCOUNT_ID,
                max_retries=1
            )
        assert exc_info.value.is_retryable is True


def test_verified_permalink_is_stored_and_displayed(test_setup):
    """8. A verified permalink is stored in database and surfaced in the publishing queue API."""
    with SessionLocal() as db:
        upload = db.query(PlatformUpload).filter(PlatformUpload.id == test_setup["upload_id"]).first()
        upload.status = "published"
        upload.external_post_id = TEST_VALID_MEDIA_ID
        upload.external_url = TEST_VALID_PERMALINK
        db.commit()

    resp = client.get("/api/publishing/queue")
    assert resp.status_code == 200
    items = resp.json()
    ig_items = [it for it in items if it["id"] == test_setup["upload_id"]]
    assert len(ig_items) == 1
    assert ig_items[0]["external_url"] == TEST_VALID_PERMALINK
    assert ig_items[0]["status"] == "published"


def test_existing_published_records_are_not_duplicated(test_setup):
    """9. Idempotency manager prevents creating duplicate publishing jobs or containers for published clips."""
    with SessionLocal() as db:
        upload = db.query(PlatformUpload).filter(PlatformUpload.id == test_setup["upload_id"]).first()
        upload.status = "published"
        upload.external_post_id = TEST_VALID_MEDIA_ID
        upload.external_url = TEST_VALID_PERMALINK
        rec = PublishingIdempotencyRecord(
            idempotency_key=upload.idempotency_key,
            clip_id=test_setup["clip_id"],
            platform="instagram",
            account_id=TEST_IG_ACCOUNT_ID,
            destination="reels",
            publishing_job_id=test_setup["job_id"],
            platform_upload_id=upload.id,
            status=PublishingState.PUBLISHED_VERIFIED,
            external_post_id=TEST_VALID_MEDIA_ID,
            external_url=TEST_VALID_PERMALINK
        )
        db.add(rec)
        db.commit()

    # Re-queueing without allow_republish must be rejected
    resp = client.post(f"/api/clips/{test_setup['clip_id']}/queue", json={"platform": "instagram", "allow_republish": False})
    assert resp.status_code == 409


def test_dashboard_status_matches_persisted_publishing_state(test_setup):
    """10. Dashboard API correctly distinguishes published_unverified, unknown, and published."""
    with SessionLocal() as db:
        upload = db.query(PlatformUpload).filter(PlatformUpload.id == test_setup["upload_id"]).first()
        upload.status = "published_unverified"
        upload.external_url = None
        upload.error_code = "VERIFICATION_PENDING"
        db.commit()

    resp = client.get("/api/publishing/queue")
    assert resp.status_code == 200
    items = resp.json()
    item = next(it for it in items if it["id"] == test_setup["upload_id"])
    assert item["status"] == "published_unverified"
    assert item["external_url"] is None
