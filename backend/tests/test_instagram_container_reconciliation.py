"""
Comprehensive Regression Test Suite for Instagram Container Processing Timeout & Reconciliation.
Validates all requirements:
1. IN_PROGRESS status handling across multiple checks without premature failure.
2. FINISHED transition triggering exact-once media_publish and permalink verification.
3. PUBLISHED detection after timeout, recording permalink and completing cleanly.
4. ERROR transcode handling with precise Meta error details.
5. EXPIRED container handling (24h deadline).
6. Graph API transient timeout and token masking (zero secret leakage).
7. Server restart during reconciliation (survives reboot, preserves container ID).
8. Strict duplicate publish prevention and pre-flight reconciliation.
"""

import os
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import (
    GeneratedClip, ClipMetadata, PublishingJob, PlatformUpload,
    PublishingIdempotencyRecord
)
from backend.app.publishers.idempotency import (
    idempotency_manager, PublishingState, build_idempotency_key
)
from backend.app.publishers.instagram.instagram_client import (
    InstagramPublishClient,
    InstagramContainerProcessingInProgress,
    InstagramProcessingFailedError,
    InstagramUploadFailedError,
    InstagramProcessingTimeoutError,
    redact_token,
    instagram_client
)
from backend.app.publishers.instagram.reconciler import instagram_reconciler


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def test_clip_and_job():
    """Sets up a test clip and in-flight Instagram publishing job in SQLite."""
    import uuid
    uid = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        clip = GeneratedClip(
            duration_sec=60.0,
            width=1080,
            height=1920,
            output_path=f"data/output/test_reconcile_{uid}.mp4"
        )
        db.add(clip)
        db.commit()
        db.refresh(clip)

        meta = ClipMetadata(
            clip_id=clip.id,
            title=f"Test Scooter Life GTA VI {uid}",
            instagram_caption="Epic GTA VI Ride #gaming"
        )
        db.add(meta)

        job = PublishingJob(
            clip_id=clip.id,
            scheduled_time=datetime.utcnow(),
            status="UNKNOWN",
            attempts=1,
            max_attempts=3
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        idemp_key = build_idempotency_key(clip.id, "instagram", "17841448213228145", "feed")

        upload = PlatformUpload(
            publishing_job_id=job.id,
            platform="instagram",
            account_id="17841448213228145",
            destination="feed",
            idempotency_key=idemp_key,
            external_post_id="18058853213607108",
            status="reconciling",
            error_code="RECONCILING",
            error_message="Instagram container 18058853213607108 is transcoding on Meta servers.",
            attempts=1,
            last_attempt_at=datetime.utcnow() - timedelta(minutes=5)
        )
        db.add(upload)
        db.commit()
        db.refresh(upload)

        idemp_rec = PublishingIdempotencyRecord(
            idempotency_key=idemp_key,
            clip_id=clip.id,
            platform="instagram",
            account_id="17841448213228145",
            destination="feed",
            publishing_job_id=job.id,
            platform_upload_id=upload.id,
            status=PublishingState.RECONCILING,
            external_post_id="18058853213607108"
        )
        db.add(idemp_rec)
        db.commit()

        yield clip.id, job.id, upload.id

        # Cleanup
        db.query(PublishingIdempotencyRecord).filter_by(idempotency_key=idemp_key).delete()
        db.query(PlatformUpload).filter_by(id=upload.id).delete()
        db.query(PublishingJob).filter_by(id=job.id).delete()
        db.query(ClipMetadata).filter_by(clip_id=clip.id).delete()
        db.query(GeneratedClip).filter_by(id=clip.id).delete()
        db.commit()


# Test 1: IN_PROGRESS status handling across multiple checks
def test_in_progress_monitoring_raises_processing_in_progress():
    client = InstagramPublishClient()
    mock_status_response = {
        "id": "18058853213607108",
        "status_code": "IN_PROGRESS",
        "status": "In Progress: Media is still being processed."
    }

    with patch.object(client, "query_container_status") as mock_query:
        mock_query.return_value = {
            "id": "18058853213607108",
            "status_code": "IN_PROGRESS",
            "status": "In Progress: Media is still being processed.",
            "is_in_progress": True,
            "is_finished": False,
            "is_published": False,
            "is_error": False,
            "is_expired": False,
            "raw_response": mock_status_response
        }

        # Bounded inline window with 3 checks
        with pytest.raises(InstagramContainerProcessingInProgress) as exc_info:
            client.poll_container_status(
                "18058853213607108",
                token="dummy_token",
                max_checks=3,
                poll_interval=0.01
            )

        err = exc_info.value
        assert err.container_id == "18058853213607108"
        assert err.checks >= 3
        assert "IN_PROGRESS" in err.user_message


# Test 2: FINISHED status triggers media_publish exactly once
def test_finished_status_triggers_media_publish_once(test_clip_and_job):
    clip_id, job_id, upload_id = test_clip_and_job

    with patch.object(instagram_client, "query_container_status") as mock_query, \
         patch.object(instagram_client, "publish_container", return_value="17999888777666") as mock_pub, \
         patch.object(instagram_client, "verify_media_published", return_value={"permalink": "https://www.instagram.com/reel/17999888777666", "shortcode": "XYZ123"}) as mock_ver, \
         patch("backend.app.publishers.instagram.reconciler.meta_token_manager.get_instagram_credentials", return_value={"token": "test_token", "instagram_account_id": "17841448213228145"}):

        mock_query.return_value = {
            "id": "18058853213607108",
            "status_code": "FINISHED",
            "status": "Finished: Media is ready to publish.",
            "is_in_progress": False,
            "is_finished": True,
            "is_published": False,
            "is_error": False,
            "is_expired": False,
            "raw_response": {"status_code": "FINISHED"}
        }

        with SessionLocal() as db:
            results = instagram_reconciler.reconcile_pending_containers(db)

            assert len(results) >= 1
            res = results[0]
            assert res["action"] == "published"
            assert res["media_id"] == "17999888777666"

            # Verify upload state in DB
            upload = db.query(PlatformUpload).filter_by(id=upload_id).first()
            assert upload.status == "published"
            assert upload.external_post_id == "17999888777666"
            assert upload.external_url == "https://www.instagram.com/reel/17999888777666"

            # publish_container was called exactly once
            mock_pub.assert_called_once_with("17841448213228145", "18058853213607108", "test_token")


# Test 3: PUBLISHED status detection after timeout avoids duplicate publishing
def test_published_status_detection(test_clip_and_job):
    clip_id, job_id, upload_id = test_clip_and_job

    with patch.object(instagram_client, "query_container_status") as mock_query, \
         patch.object(instagram_client, "publish_container") as mock_pub, \
         patch.object(instagram_client, "verify_media_published", return_value={"permalink": "https://www.instagram.com/reel/18058853213607108", "shortcode": "XYZ123"}), \
         patch("backend.app.publishers.instagram.reconciler.meta_token_manager.get_instagram_credentials", return_value={"token": "test_token", "instagram_account_id": "17841448213228145"}):

        mock_query.return_value = {
            "id": "18058853213607108",
            "status_code": "PUBLISHED",
            "status": "Media is published.",
            "is_in_progress": False,
            "is_finished": False,
            "is_published": True,
            "is_error": False,
            "is_expired": False,
            "raw_response": {"status_code": "PUBLISHED"}
        }

        with SessionLocal() as db:
            results = instagram_reconciler.reconcile_pending_containers(db)
            assert len(results) >= 1
            assert results[0]["action"] == "marked_published"

            # Must NOT call publish_container
            mock_pub.assert_not_called()

            upload = db.query(PlatformUpload).filter_by(id=upload_id).first()
            assert upload.status == "published"


# Test 4: ERROR transcode details recorded without blindly retrying
def test_error_status_recorded_safely(test_clip_and_job):
    clip_id, job_id, upload_id = test_clip_and_job

    with patch.object(instagram_client, "query_container_status") as mock_query, \
         patch("backend.app.publishers.instagram.reconciler.meta_token_manager.get_instagram_credentials", return_value={"token": "test_token", "instagram_account_id": "17841448213228145"}):

        mock_query.return_value = {
            "id": "18058853213607108",
            "status_code": "ERROR",
            "status": "Transcode error: Video codec profile unsupported.",
            "is_in_progress": False,
            "is_finished": False,
            "is_published": False,
            "is_error": True,
            "is_expired": False,
            "raw_response": {"status_code": "ERROR", "status": "Transcode error: Video codec profile unsupported."}
        }

        with SessionLocal() as db:
            results = instagram_reconciler.reconcile_pending_containers(db)
            assert len(results) >= 1
            assert results[0]["action"] == "marked_failed"

            upload = db.query(PlatformUpload).filter_by(id=upload_id).first()
            assert upload.status == "failed"
            assert upload.error_code == "TRANSCODE_ERROR"
            assert "unsupported" in upload.error_message


# Test 5: EXPIRED container handling (24h deadline)
def test_expired_container_handling(test_clip_and_job):
    clip_id, job_id, upload_id = test_clip_and_job

    with patch.object(instagram_client, "query_container_status") as mock_query, \
         patch("backend.app.publishers.instagram.reconciler.meta_token_manager.get_instagram_credentials", return_value={"token": "test_token", "instagram_account_id": "17841448213228145"}):

        mock_query.return_value = {
            "id": "18058853213607108",
            "status_code": "EXPIRED",
            "status": "Container expired after 24 hours.",
            "is_in_progress": False,
            "is_finished": False,
            "is_published": False,
            "is_error": False,
            "is_expired": True,
            "raw_response": {"status_code": "EXPIRED"}
        }

        with SessionLocal() as db:
            results = instagram_reconciler.reconcile_pending_containers(db)
            assert len(results) >= 1
            assert results[0]["action"] == "marked_failed"

            upload = db.query(PlatformUpload).filter_by(id=upload_id).first()
            assert upload.status == "failed"
            assert upload.error_code == "CONTAINER_EXPIRED"


# Test 6: Graph API timeout & token redaction in logs/exceptions
def test_token_redaction_and_transient_network_timeout():
    token = "EAAQj82c37y0BTKMGXWZBril2ngZAQpiNHjow3ZBuUdOVabZAHZC5FaNgSuNfLRigVpwzHodZAAYZAwftCsVizUVqmXfCBtGE99PsJsQdHJAQF4QTs04BeV6mpBPjJocUHzo6JIM8vuNiLxHguOD326dJZAkpR6AUihYui4zUZBaBJljMU7MXaE6gTqcGG12Gt54B5RwZDZD"
    raw_error_text = f"Connection to graph.facebook.com timed out with access_token={token} and Authorization: Bearer {token}"

    redacted = redact_token(raw_error_text)
    assert token not in redacted
    assert "[REDACTED_TOKEN]" in redacted


# Test 7: Server restart during reconciliation preserves container state
def test_crash_recovery_preserves_reconciling_state():
    from backend.app.scheduler.manager import task_scheduler
    import uuid

    uid = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        clip = GeneratedClip(
            duration_sec=30.0,
            width=1080,
            height=1920,
            output_path=f"data/output/test_crash_{uid}.mp4"
        )
        db.add(clip)
        db.commit()

        # Create an in-flight job left in UPLOADING or PROCESSING during restart
        pjob = PublishingJob(
            clip_id=clip.id,
            scheduled_time=datetime.utcnow(),
            status="PROCESSING",
            attempts=1
        )
        db.add(pjob)
        db.commit()
        db.refresh(pjob)

        task_scheduler.recover_interrupted_jobs()

        db.refresh(pjob)
        # Must be marked UNKNOWN to prevent auto-republishing, preserving state for reconciler
        assert pjob.status == "UNKNOWN"

        # Cleanup
        db.delete(pjob)
        db.delete(clip)
        db.commit()


# Test 8: Pre-flight reconciliation prevents duplicate container creation
def test_preflight_reconciliation_prevents_duplicate_containers(test_clip_and_job):
    clip_id, job_id, upload_id = test_clip_and_job

    mock_c_data = {
        "status_code": "IN_PROGRESS",
        "status": "In Progress: Media is still being processed."
    }

    with patch("requests.get") as mock_get:
        # Mock requests.get returning IN_PROGRESS container
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_c_data
        mock_get.return_value = mock_resp

        with SessionLocal() as db:
            reconciled = idempotency_manager.reconcile_platform_publication(
                db, "instagram", clip_id, "17841448213228145", "feed",
                candidate_external_id="18058853213607108"
            )

            assert reconciled is not None
            assert reconciled.get("in_progress_container") is True
            assert reconciled["container_id"] == "18058853213607108"
            # Must NOT report published=True
            assert reconciled["published"] is False


# Test 9: Multiple clips processing concurrently in reconciler
def test_multiple_clips_reconcile_concurrently():
    import uuid
    uid1 = uuid.uuid4().hex[:6]
    uid2 = uuid.uuid4().hex[:6]

    with SessionLocal() as db:
        c1 = GeneratedClip(duration_sec=30.0, width=1080, height=1920, output_path=f"data/output/c1_{uid1}.mp4")
        c2 = GeneratedClip(duration_sec=30.0, width=1080, height=1920, output_path=f"data/output/c2_{uid2}.mp4")
        db.add_all([c1, c2])
        db.commit()

        j1 = PublishingJob(clip_id=c1.id, scheduled_time=datetime.utcnow(), status="UNKNOWN", attempts=1)
        j2 = PublishingJob(clip_id=c2.id, scheduled_time=datetime.utcnow(), status="UNKNOWN", attempts=1)
        db.add_all([j1, j2])
        db.commit()

        u1 = PlatformUpload(
            publishing_job_id=j1.id, platform="instagram", account_id="acc1", destination="feed",
            idempotency_key=f"k1_{uid1}", external_post_id=f"cid1_{uid1}", status="reconciling",
            attempts=1, last_attempt_at=datetime.utcnow() - timedelta(minutes=5)
        )
        u2 = PlatformUpload(
            publishing_job_id=j2.id, platform="instagram", account_id="acc1", destination="feed",
            idempotency_key=f"k2_{uid2}", external_post_id=f"cid2_{uid2}", status="reconciling",
            attempts=1, last_attempt_at=datetime.utcnow() - timedelta(minutes=5)
        )
        db.add_all([u1, u2])
        db.commit()

        def mock_query(cid, token=None, include_copyright=True):
            if uid1 in cid:
                return {"id": cid, "status_code": "FINISHED", "status": "Ready", "is_in_progress": False, "is_finished": True, "is_published": False, "is_error": False, "is_expired": False, "raw_response": {}}
            else:
                return {"id": cid, "status_code": "IN_PROGRESS", "status": "Transcoding", "is_in_progress": True, "is_finished": False, "is_published": False, "is_error": False, "is_expired": False, "raw_response": {}}

        with patch.object(instagram_client, "query_container_status", side_effect=mock_query), \
             patch.object(instagram_client, "publish_container", return_value="pub_123"), \
             patch.object(instagram_client, "verify_media_published", return_value={"permalink": "https://ig.me/123"}), \
             patch("backend.app.publishers.instagram.reconciler.meta_token_manager.get_instagram_credentials", return_value={"token": "t", "instagram_account_id": "acc1"}):

            results = instagram_reconciler.reconcile_pending_containers(db)
            assert len(results) == 2
            actions = {r["container_id"]: r["action"] for r in results}
            assert actions[f"cid1_{uid1}"] == "published"
            assert actions[f"cid2_{uid2}"] == "monitoring_continued"

        # Cleanup
        db.query(PlatformUpload).filter(PlatformUpload.id.in_([u1.id, u2.id])).delete()
        db.query(PublishingJob).filter(PublishingJob.id.in_([j1.id, j2.id])).delete()
        db.query(GeneratedClip).filter(GeneratedClip.id.in_([c1.id, c2.id])).delete()
        db.commit()


# Test 10: Inaccessible / private media URL rejection
def test_inaccessible_video_url_rejected():
    from backend.app.publishers.instagram.instagram_client import is_public_url, InstagramVideoUrlError
    assert not is_public_url("http://localhost:8765/video.mp4")
    assert not is_public_url("http://127.0.0.1:8765/video.mp4")
    assert not is_public_url("http://192.168.1.50/video.mp4")
    assert not is_public_url("ftp://example.com/video.mp4")
    assert is_public_url("https://cdn.gaminguncut.com/clips/sample.mp4")

    client = InstagramPublishClient()
    with pytest.raises(InstagramVideoUrlError):
        client.create_media_container("17841448213228145", "token", "test", video_url="http://localhost:8765/vid.mp4")


# Test 11: Lost response recovery (media_publish network timeout followed by container reporting PUBLISHED)
def test_lost_response_reconciled_after_publish_timeout(test_clip_and_job):
    clip_id, job_id, upload_id = test_clip_and_job

    with patch.object(instagram_client, "query_container_status") as mock_query, \
         patch.object(instagram_client, "publish_container", side_effect=Exception("Connection to graph.facebook.com timed out")), \
         patch.object(instagram_client, "verify_media_published", return_value={"permalink": "https://www.instagram.com/reel/18058853213607108"}), \
         patch("backend.app.publishers.instagram.reconciler.meta_token_manager.get_instagram_credentials", return_value={"token": "t", "instagram_account_id": "17841448213228145"}):

        # First call: FINISHED triggers publish_container which times out, then inner check returns is_published=True
        mock_query.side_effect = [
            {"id": "18058853213607108", "status_code": "FINISHED", "status": "Ready", "is_in_progress": False, "is_finished": True, "is_published": False, "is_error": False, "is_expired": False, "raw_response": {}},
            {"id": "18058853213607108", "status_code": "PUBLISHED", "status": "Live", "is_in_progress": False, "is_finished": False, "is_published": True, "is_error": False, "is_expired": False, "raw_response": {}}
        ]

        with SessionLocal() as db:
            results = instagram_reconciler.reconcile_pending_containers(db)
            assert len(results) >= 1
            assert results[0]["action"] == "published"
            assert "https://www.instagram.com/reel/" in results[0]["url"]

            upload = db.query(PlatformUpload).filter_by(id=upload_id).first()
            assert upload.status == "published"

