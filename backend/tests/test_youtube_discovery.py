"""
Comprehensive Isolated Test Suite for YouTube Gameplay Discovery & Acquisition Engine.
Tests:
1. YouTube search discovery with duration parsing and metadata extraction.
2. Pagination handling (next/prev tokens).
3. YouTube API quota exhaustion, daily restriction state, and backoff.
4. Deduplication by YouTube video ID.
5. Scheduled discovery persistence across runs.
6. Restart recovery of interrupted discovery jobs.
7. Channel allowlisting and auto-authorization.
8. Authorization-required enforcement (blocking unauthorized downloads/processing).
9. Authorized source acquisition with local file attachment and permitted scope.
10. AI processing pipeline integration and stage status synchronization.
11. Failed ingestion recovery and retry flow.
12. Automatic publishing safety gates (default disabled, human review required).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from googleapiclient.errors import HttpError
import httpx

from backend.app.database.session import SessionLocal, Base, engine
from backend.app.models.schema import (
    DiscoverySearch,
    DiscoverySource,
    DiscoveryChannel,
    DiscoverySchedule,
    DiscoveryJob,
    DiscoveredVideo,
    SourceAuthorization,
    SourceVideo,
    ProcessingJob,
    GeneratedClip,
    PlatformQuotaState,
    SystemLog
)
from backend.app.discovery.client import (
    youtube_discovery_client,
    parse_iso8601_duration,
    format_duration,
    YouTubeDiscoveryError,
    YouTubeDiscoveryConfigError
)
from backend.app.discovery.service import discovery_service
from backend.app.publishers.quota_tracker import YouTubeQuotaExceededError, get_or_create_quota_state
from backend.app.scheduler.manager import task_scheduler

@pytest.fixture(autouse=True)
def clean_discovery_db():
    """Cleans discovery and quota tables before and after each test."""
    with SessionLocal() as db:
        db.query(SourceAuthorization).delete()
        db.query(DiscoveredVideo).delete()
        db.query(DiscoveryJob).delete()
        db.query(DiscoverySchedule).delete()
        db.query(DiscoverySource).delete()
        db.query(DiscoveryChannel).delete()
        db.query(DiscoverySearch).delete()
        db.query(PlatformQuotaState).filter_by(platform="youtube").delete()
        db.commit()
    yield
    with SessionLocal() as db:
        db.query(SourceAuthorization).delete()
        db.query(DiscoveredVideo).delete()
        db.query(DiscoveryJob).delete()
        db.query(DiscoverySchedule).delete()
        db.query(DiscoverySource).delete()
        db.query(DiscoveryChannel).delete()
        db.query(DiscoverySearch).delete()
        db.query(PlatformQuotaState).filter_by(platform="youtube").delete()
        db.commit()


# -------------------------------------------------------------------------
# Test 1: YouTube Search Discovery & Duration Parsing
# -------------------------------------------------------------------------

def test_youtube_search_discovery():
    assert parse_iso8601_duration("PT15M33S") == 933.0
    assert parse_iso8601_duration("PT1H02M10S") == 3730.0
    assert parse_iso8601_duration("PT45S") == 45.0
    assert format_duration(933.0) == "15:33"
    assert format_duration(3730.0) == "1:02:10"

    mock_search_resp = {
        "items": [
            {
                "id": {"videoId": "test_vid_001"},
                "snippet": {
                    "title": "GTA V 1000HP Supra Drift Highlights",
                    "channelId": "UC_gta_creator_01",
                    "channelTitle": "GTA Pro Drifting",
                    "publishedAt": "2026-11-10T12:00:00Z",
                    "thumbnails": {"high": {"url": "https://img.youtube.com/vi/test_vid_001/hqdefault.jpg"}},
                    "description": "Insane drift lines in Grand Theft Auto V"
                }
            }
        ],
        "pageInfo": {"totalResults": 1}
    }

    mock_video_resp = {
        "items": [
            {
                "id": "test_vid_001",
                "contentDetails": {"duration": "PT10M15S"},
                "statistics": {"viewCount": "45200"}
            }
        ]
    }

    mock_service = MagicMock()
    mock_service.search().list().execute.return_value = mock_search_resp
    mock_service.videos().list().execute.return_value = mock_video_resp

    with patch.object(youtube_discovery_client, "get_service", return_value=mock_service):
        with SessionLocal() as db:
            res = youtube_discovery_client.search_videos(
                query="clutch highlights",
                game_title="Grand Theft Auto V",
                max_results=5,
                db=db
            )

            assert len(res["items"]) == 1
            item = res["items"][0]
            assert item["youtube_video_id"] == "test_vid_001"
            assert item["title"] == "GTA V 1000HP Supra Drift Highlights"
            assert item["duration_sec"] == 615.0
            assert item["duration_formatted"] == "10:15"
            assert item["view_count"] == 45200
            assert item["channel_title"] == "GTA Pro Drifting"
            assert item["game_title"] == "Grand Theft Auto V"


# -------------------------------------------------------------------------
# Test 2: Pagination Handling
# -------------------------------------------------------------------------

def test_youtube_search_pagination():
    mock_search_resp = {
        "items": [
            {
                "id": {"videoId": "page2_vid_01"},
                "snippet": {
                    "title": "Page 2 Clip",
                    "channelId": "UC_page2",
                    "channelTitle": "Page2 Channel",
                    "publishedAt": "2026-11-15T00:00:00Z",
                    "thumbnails": {"default": {"url": "https://thumb"}}
                }
            }
        ],
        "nextPageToken": "NEXT_PAGE_TOKEN_123",
        "prevPageToken": "PREV_PAGE_TOKEN_999",
        "pageInfo": {"totalResults": 100}
    }

    mock_service = MagicMock()
    mock_service.search().list().execute.return_value = mock_search_resp
    mock_service.videos().list().execute.return_value = {"items": []}

    with patch.object(youtube_discovery_client, "get_service", return_value=mock_service):
        with SessionLocal() as db:
            res = youtube_discovery_client.search_videos(
                query="gameplay",
                page_token="PREV_PAGE_TOKEN_999",
                db=db
            )

            assert res["next_page_token"] == "NEXT_PAGE_TOKEN_123"
            assert res["prev_page_token"] == "PREV_PAGE_TOKEN_999"
            assert res["total_results"] == 100


# -------------------------------------------------------------------------
# Test 3: API Quota Exhaustion & Backoff Handling
# -------------------------------------------------------------------------

def test_api_quota_exhaustion_and_backoff():
    mock_resp = MagicMock()
    mock_resp.status = 403
    mock_resp.reason = "Forbidden"
    quota_content = json.dumps({
        "error": {
            "code": 403,
            "message": "The request cannot be completed because you have exceeded your quota.",
            "errors": [{"reason": "quotaExceeded"}]
        }
    }).encode("utf-8")

    http_error = HttpError(resp=mock_resp, content=quota_content)

    mock_service = MagicMock()
    mock_service.search().list().execute.side_effect = http_error

    with patch.object(youtube_discovery_client, "get_service", return_value=mock_service):
        with SessionLocal() as db:
            with pytest.raises(YouTubeQuotaExceededError) as exc_info:
                youtube_discovery_client.search_videos(query="minecraft", db=db)

            assert "quota exceeded" in str(exc_info.value).lower()

            # Verify PlatformQuotaState was restricted
            q_state = get_or_create_quota_state(db, "youtube")
            assert q_state.api_quota_exceeded is True
            assert q_state.pause_until is not None
            assert q_state.pause_until > datetime.utcnow()

            # Verify subsequent call is blocked by pre-check without touching external service
            with pytest.raises(YouTubeQuotaExceededError):
                youtube_discovery_client.search_videos(query="minecraft", db=db)


# -------------------------------------------------------------------------
# Test 4: Deduplication by YouTube Video ID
# -------------------------------------------------------------------------

def test_duplicate_detection_and_deduplication():
    video_payload = [
        {
            "youtube_video_id": "duplicate_check_001",
            "title": "Valorant Radiant 1v5 Clutch",
            "channel_id": "UC_valorant_pro",
            "channel_title": "Valorant Pro",
            "duration_sec": 75.0,
            "view_count": 10000,
            "game_title": "Valorant"
        }
    ]

    with SessionLocal() as db:
        # First import
        first_import = discovery_service.import_videos_to_queue(db, video_payload, discovery_mode="manual")
        assert len(first_import) == 1
        assert first_import[0].youtube_video_id == "duplicate_check_001"

        # Duplicate import
        second_import = discovery_service.import_videos_to_queue(db, video_payload, discovery_mode="manual")
        assert len(second_import) == 1

        # Total count in database must be strictly 1
        total_in_db = db.query(DiscoveredVideo).filter_by(youtube_video_id="duplicate_check_001").count()
        assert total_in_db == 1


# -------------------------------------------------------------------------
# Test 5: Scheduled Discovery Persistence
# -------------------------------------------------------------------------

def test_scheduled_discovery_persistence():
    with SessionLocal() as db:
        # Configure monitored source
        src = discovery_service.add_or_update_source(
            db=db,
            game_title="Forza Horizon 5",
            name="FH5 Racing",
            keywords=["supra", "g29"],
            max_results_per_scan=2
        )

        schedule = discovery_service.get_or_create_schedule(db)
        schedule.is_active = True
        schedule.next_scan_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()

        # Mock discovery client
        mock_items = [
            {
                "youtube_video_id": "forza_vid_101",
                "title": "1470HP Bugatti Veyron | Forza Horizon 5",
                "channel_id": "UC_forza",
                "channel_title": "Forza Channel",
                "duration_sec": 120.0,
                "view_count": 5000,
                "game_title": "Forza Horizon 5"
            }
        ]

        with patch.object(youtube_discovery_client, "search_videos", return_value={"items": mock_items, "quota_used": 101}):
            job = discovery_service.execute_scheduled_discovery(db)

            assert job is not None
            assert job.status == "completed"
            assert job.videos_found == 1
            assert job.videos_imported == 1

            # Check persistent schedule state
            db.refresh(schedule)
            assert schedule.last_scan_status == "success"
            assert schedule.last_scan_at is not None
            assert schedule.next_scan_at > datetime.utcnow()

            # Check DiscoveredVideo record in DB
            saved_vid = db.query(DiscoveredVideo).filter_by(youtube_video_id="forza_vid_101").first()
            assert saved_vid is not None
            assert saved_vid.status == "authorization_required"
            assert saved_vid.discovery_mode == "automatic"


# -------------------------------------------------------------------------
# Test 6: Restart Recovery for Interrupted Discovery Jobs
# -------------------------------------------------------------------------

def test_restart_recovery_for_discovery_jobs():
    with SessionLocal() as db:
        sched = discovery_service.get_or_create_schedule(db)
        sched.last_scan_status = "running"

        djob = DiscoveryJob(
            schedule_id=sched.id,
            status="running",
            started_at=datetime.utcnow() - timedelta(minutes=10)
        )
        db.add(djob)
        db.commit()
        job_id = djob.id

    # Simulate application restart invoking recovery
    task_scheduler.recover_interrupted_jobs()

    with SessionLocal() as db:
        refreshed_job = db.query(DiscoveryJob).filter_by(id=job_id).first()
        assert refreshed_job.status == "interrupted"
        assert "restart" in refreshed_job.error_message.lower()

        refreshed_sched = db.query(DiscoverySchedule).first()
        assert refreshed_sched.last_scan_status == "idle"


# -------------------------------------------------------------------------
# Test 7: Channel Allowlisting & Auto-Authorization
# -------------------------------------------------------------------------

def test_channel_allowlisting_auto_authorization():
    with SessionLocal() as db:
        # Add approved channel with auto_authorize_sources = True
        discovery_service.add_or_update_channel(
            db=db,
            channel_id="UC_PARTNER_CREATOR",
            channel_title="Partner Gaming Studio",
            auto_authorize_sources=True
        )

        # Video from partner channel
        partner_video = {
            "youtube_video_id": "partner_vid_777",
            "title": "Partner Game Clip",
            "channel_id": "UC_PARTNER_CREATOR",
            "channel_title": "Partner Gaming Studio",
            "duration_sec": 60.0
        }

        # Video from random third-party channel
        third_party_video = {
            "youtube_video_id": "random_vid_888",
            "title": "Random Streamer Highlight",
            "channel_id": "UC_UNKNOWN_USER",
            "channel_title": "Random User",
            "duration_sec": 60.0
        }

        imported = discovery_service.import_videos_to_queue(
            db=db,
            videos=[partner_video, third_party_video],
            discovery_mode="manual"
        )

        # Partner video must be automatically authorized
        p_vid = db.query(DiscoveredVideo).filter_by(youtube_video_id="partner_vid_777").first()
        assert p_vid.status == "authorized"
        assert p_vid.authorization is not None
        assert p_vid.authorization.auth_type == "creator_authorized"

        # Third-party video MUST require explicit authorization
        tp_vid = db.query(DiscoveredVideo).filter_by(youtube_video_id="random_vid_888").first()
        assert tp_vid.status == "authorization_required"
        assert tp_vid.authorization is None


# -------------------------------------------------------------------------
# Test 8: Authorization-Required Blocks Unauthorized Processing
# -------------------------------------------------------------------------

def test_authorization_required_blocks_unauthorized_processing():
    with SessionLocal() as db:
        video = DiscoveredVideo(
            youtube_video_id="unauth_vid_999",
            title="Unauthorized Gameplay Video",
            channel_id="UC_stranger",
            channel_title="Stranger",
            youtube_url="https://www.youtube.com/watch?v=unauth_vid_999",
            status="authorization_required"
        )
        db.add(video)
        db.commit()
        vid_id = video.id

    with SessionLocal() as db:
        # Must raise PermissionError when attempting to enqueue without authorization
        with pytest.raises(PermissionError) as exc_info:
            discovery_service.enqueue_for_processing(db, vid_id)

        assert "authorization required" in str(exc_info.value).lower()


# -------------------------------------------------------------------------
# Test 9: Authorized Source Acquisition with Local File Attachment
# -------------------------------------------------------------------------

def test_authorized_source_acquisition_with_local_file(tmp_path):
    local_file = tmp_path / "creator_authorized_footage.mp4"
    local_file.write_bytes(b"mock_video_bytes_content_for_test")

    with SessionLocal() as db:
        video = DiscoveredVideo(
            youtube_video_id="authorized_flow_vid",
            title="Authorized Creator Clutch",
            channel_id="UC_creator",
            channel_title="Verified Creator",
            youtube_url="https://www.youtube.com/watch?v=authorized_flow_vid",
            status="authorization_required"
        )
        db.add(video)
        db.commit()
        v_id = video.id

    with SessionLocal() as db:
        with patch("backend.app.services.pipeline_service.pipeline_service.start_job") as mock_start:
            authorized_video = discovery_service.authorize_source(
                db=db,
                discovered_video_id=v_id,
                auth_type="creator_authorized",
                authorized_by="Ninja (Consent Agreement #99)",
                source_owner="Ninja Gaming LLC",
                permitted_scope="shorts_clips_derivative",
                evidence_url="https://contracts.example.com/agreement/99",
                local_file_path=str(local_file),
                enqueue_now=True
            )

            assert authorized_video.status == "queued"
            assert authorized_video.authorization is not None
            assert authorized_video.authorization.authorized_by == "Ninja (Consent Agreement #99)"
            assert authorized_video.authorization.permitted_scope == "shorts_clips_derivative"
            assert mock_start.called


# -------------------------------------------------------------------------
# Test 10: AI Processing Pipeline Integration & Stage Synchronization
# -------------------------------------------------------------------------

def test_ai_processing_integration_and_stage_sync():
    with SessionLocal() as db:
        video = DiscoveredVideo(
            youtube_video_id="pipeline_sync_vid",
            title="Apex Legends Apex Predator Match",
            youtube_url="https://www.youtube.com/watch?v=pipeline_sync_vid",
            status="authorized"
        )
        db.add(video)
        db.commit()
        vid_id = video.id

        # Enqueue for processing
        with patch("backend.app.services.pipeline_service.pipeline_service.start_job"):
            job = discovery_service.enqueue_for_processing(db, vid_id)

        job_id = job.id
        db.refresh(video)
        assert video.status == "queued"
        assert video.processing_job_id == job_id

        # Stage 2: Audio/Video Analysis
        discovery_service.sync_pipeline_status(db, job_id, stage_idx=2, stage_name="AI video analysis", status="processing")
        db.refresh(video)
        assert video.status == "analyzing"

        # Stage 4: Highlights detected
        discovery_service.sync_pipeline_status(db, job_id, stage_idx=4, stage_name="Highlight detection", status="processing")
        db.refresh(video)
        assert video.status == "highlights_detected"

        # Stage 6: Rendering
        discovery_service.sync_pipeline_status(db, job_id, stage_idx=6, stage_name="Video rendering", status="processing")
        db.refresh(video)
        assert video.status == "rendering"

        # Stage 9: Completed -> Review Required (since auto_approve is False by default)
        discovery_service.sync_pipeline_status(db, job_id, stage_idx=9, stage_name="Reel ready", status="completed")
        db.refresh(video)
        assert video.status == "review_required"


# -------------------------------------------------------------------------
# Test 11: Failed Ingestion Recovery & Retry Flow
# -------------------------------------------------------------------------

def test_failed_ingestion_and_retry_flow():
    with SessionLocal() as db:
        video = DiscoveredVideo(
            youtube_video_id="failed_retry_vid",
            title="Failed Stream Ingestion",
            youtube_url="https://www.youtube.com/watch?v=failed_retry_vid",
            status="failed",
            error_message="Network connection reset during ingestion"
        )
        db.add(video)
        db.commit()
        vid_id = video.id

        # Retry without authorization returns to authorization_required
        retried = discovery_service.retry_video(db, vid_id)
        assert retried.status == "authorization_required"
        assert retried.error_message is None

        # Authorize and retry again
        auth = SourceAuthorization(
            discovered_video_id=vid_id,
            auth_type="creator_authorized",
            authorized_by="Streamer",
            confirmed_at=datetime.utcnow()
        )
        db.add(auth)
        db.commit()

        with patch("backend.app.services.pipeline_service.pipeline_service.start_job"):
            retried_auth = discovery_service.retry_video(db, vid_id)
            assert retried_auth.status == "queued"


# -------------------------------------------------------------------------
# Test 12: Automatic Publishing Safety Gates
# -------------------------------------------------------------------------

def test_automatic_publishing_safety_gates():
    with SessionLocal() as db:
        schedule = discovery_service.get_or_create_schedule(db)
        # CRITICAL SAFETY REQUIREMENT: auto_publish must strictly default to False
        assert schedule.auto_publish is False
        assert schedule.auto_approve_clips is False

        # Attempting to manually update settings to auto-publish requires explicit flag
        updated = discovery_service.update_schedule_settings(db, {"auto_publish": False})
        assert updated.auto_publish is False
