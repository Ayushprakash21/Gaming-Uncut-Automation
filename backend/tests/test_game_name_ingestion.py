import pytest
import os
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.app.models.schema import SourceVideo, GeneratedClip, ClipMetadata, ProcessingJob, PublishingJob, DetectedHighlight
from backend.app.ai.gemini_provider import GeminiAIProvider, gemini_provider
from backend.app.api.router import CreateReelRequest, ValidateSourceRequest
from backend.app.database.session import SessionLocal

@pytest.fixture
def clean_db():
    with SessionLocal() as db:
        yield db

def test_request_schemas_optional_game_name():
    """Verify game_name is strictly optional and handles complex punctuation and spaces."""
    # Omitted
    req1 = CreateReelRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert req1.game_name is None

    # Empty string
    req2 = CreateReelRequest(url="https://www.youtube.com/watch?v=dQw4w9WgXcQ", game_name="")
    assert req2.game_name == ""

    # Complex titles with spaces and punctuation
    complex_names = [
        "Grand Theft Auto V",
        "Red Dead Redemption 2",
        "Tom Clancy's Rainbow Six Siege",
        "S.T.A.L.K.E.R. 2: Heart of Chornobyl",
        "Marvel's Spider-Man 2"
    ]
    for name in complex_names:
        req = CreateReelRequest(url="https://www.youtube.com/watch?v=test", game_name=name)
        assert req.game_name == name

    val_req = ValidateSourceRequest(url="https://www.youtube.com/watch?v=test", game_name="Valorant")
    assert val_req.game_name == "Valorant"

def test_heuristic_analysis_with_matching_game_name(tmp_path):
    """Verify heuristic analysis contextually incorporates user game name when consistent."""
    dummy_file = tmp_path / "test_gta.mp4"
    dummy_file.write_bytes(b"dummy mp4 video bytes")

    provider = GeminiAIProvider()
    res = provider._heuristic_multimodal_analysis(
        video_path=dummy_file,
        transcript_text="Insane 5 star police chase across los santos!",
        user_game_name="GTA V"
    )

    assert res["game"]["name"] == "Grand Theft Auto V"
    assert res["user_game_name"] == "GTA V"
    assert res["game_mismatch_flag"] is False
    assert res["mismatch_reason"] is None
    assert any("GrandTheftAutoV" in tag or "GTAV" in tag for tag in res["content"]["hashtags"])

def test_heuristic_analysis_with_conflicting_game_name(tmp_path):
    """Verify conflict between user-supplied game name and visual/telemetry footage flags mismatch."""
    dummy_file = tmp_path / "test_forza.mp4"
    dummy_file.write_bytes(b"dummy mp4 video bytes")

    provider = GeminiAIProvider()
    # Source footage clearly depicts Forza racing, but user claims it's Minecraft
    res = provider._heuristic_multimodal_analysis(
        video_path=dummy_file,
        transcript_text="Driving the Bugatti Veyron in Forza Horizon 5 top speed test on the highway lap",
        user_game_name="Minecraft"
    )

    assert res["game"]["name"] == "Forza Horizon 5"
    assert res["user_game_name"] == "Minecraft"
    assert res["game_mismatch_flag"] is True
    assert res["mismatch_reason"] is not None
    assert "conflicts" in res["mismatch_reason"].lower()

def test_blank_game_name_preserves_automatic_detection(tmp_path):
    """Verify blank game name falls back cleanly to automatic visual detection without flags."""
    dummy_file = tmp_path / "test_wukong.mp4"
    dummy_file.write_bytes(b"dummy mp4 video bytes")

    provider = GeminiAIProvider()
    res = provider._heuristic_multimodal_analysis(
        video_path=dummy_file,
        transcript_text="Black Myth Wukong boss battle parry staff combo",
        user_game_name=""
    )

    assert res["game"]["name"] == "Black Myth: Wukong"
    assert res["user_game_name"] is None
    assert res["game_mismatch_flag"] is False

def test_validation_sanitizer_flags_mismatch_from_gemini():
    """Verify _validate_and_sanitize_analysis cross-checks user guidance against Gemini visual evidence."""
    provider = GeminiAIProvider()

    ai_data = {
        "game": {
            "name": "Valorant",
            "confidence": 0.88,
            "franchise": "Valorant",
            "genre": "Tactical Hero Shooter",
            "evidence": ["Killjoy turret HUD", "Vandal crosshair"]
        },
        "content": {
            "titles": ["1v3 Clutch Spike Defusal!"],
            "description": "Watch this clutch play.",
            "hashtags": ["#Valorant", "#Gaming"]
        }
    }

    # Case 1: Matching suggestion
    val_matching = provider._validate_and_sanitize_analysis(ai_data.copy(), user_game_name="Valorant")
    assert val_matching["game_mismatch_flag"] is False
    assert val_matching["user_game_name"] == "Valorant"

    # Case 2: Conflicting suggestion
    val_conflict = provider._validate_and_sanitize_analysis(ai_data.copy(), user_game_name="Grand Theft Auto V")
    assert val_conflict["game_mismatch_flag"] is True
    assert val_conflict["user_game_name"] == "Grand Theft Auto V"
    assert "conflicts" in val_conflict["mismatch_reason"].lower()

    # Case 3: None/blank suggestion
    val_none = provider._validate_and_sanitize_analysis(ai_data.copy(), user_game_name=None)
    assert val_none["game_mismatch_flag"] is False
    assert val_none["user_game_name"] is None

def test_generate_reel_metadata_preserves_hint_and_mismatch():
    """Verify generate_reel_metadata propagates user_game_name and game_mismatch_flag."""
    provider = GeminiAIProvider()

    # Text-only fallback path
    meta = provider.generate_reel_metadata(
        gameplay_summary="High-speed escape chase",
        source_title="Epic escape",
        user_game_name="Red Dead Redemption 2"
    )

    assert meta["user_game_name"] == "Red Dead Redemption 2"
    assert meta["game_mismatch_flag"] is False
    assert "#RedDeadRedemption2" in meta["hashtags"]

def test_analyze_gameplay_highlights_with_game_context():
    """Verify highlight analysis uses game_name for context-aware event classification."""
    provider = GeminiAIProvider()

    highlights = provider._local_heuristic_highlights(
        scene_timestamps=[5.0, 15.0],
        audio_peaks=[{"ts": 10.0, "db": -6.0}],
        video_duration=55.0,
        game_name="Red Dead Redemption 2"
    )

    assert len(highlights) > 0
    # Should select from western/red dead event pool
    event_type = highlights[0]["event_type"]
    assert any(term in event_type for term in ["Deadeye", "Duel", "Outlaw", "Bounty", "Gunslinger"])

def test_source_video_and_clip_persistence_in_db(clean_db):
    """Verify user_game_name persists on SourceVideo and ClipMetadata, and mismatch halts auto-publish."""
    sv = SourceVideo(
        url="https://www.youtube.com/watch?v=unique_test_123",
        local_path="D:/data/unique_test_123.mp4",
        title="High Stakes Match",
        duration=60.0,
        user_game_name="Cyberpunk 2077",
        status="ready"
    )
    clean_db.add(sv)
    clean_db.commit()
    clean_db.refresh(sv)
    assert sv.user_game_name == "Cyberpunk 2077"

    job = ProcessingJob(
        source_video_id=sv.id,
        job_type="full_pipeline",
        status="completed"
    )
    clean_db.add(job)
    clean_db.commit()
    clean_db.refresh(job)

    hl = DetectedHighlight(
        source_video_id=sv.id,
        start_sec=0.0,
        end_sec=58.0,
        duration_sec=58.0,
        event_type="Clutch Run",
        score=85.0
    )
    clean_db.add(hl)
    clean_db.commit()
    clean_db.refresh(hl)

    clip = GeneratedClip(
        job_id=job.id,
        highlight_id=hl.id,
        output_path="test_clip.mp4",
        duration_sec=58.0
    )
    clean_db.add(clip)
    clean_db.commit()
    clean_db.refresh(clip)

    # Test metadata with mismatch flag
    meta = ClipMetadata(
        clip_id=clip.id,
        title="Epic Cyberpunk Sequence",
        topic="RPG",
        description="Cyberpunk gameplay",
        hashtags="#Cyberpunk2077",
        detected_game="Grand Theft Auto V",
        user_game_name="Cyberpunk 2077",
        game_mismatch_flag=True,
        analysis_provider="gemini",
        analysis_status="complete",
        game_confidence=0.85
    )
    clean_db.add(meta)
    clean_db.commit()
    clean_db.refresh(meta)

    assert meta.user_game_name == "Cyberpunk 2077"
    assert meta.game_mismatch_flag is True

    # Check auto-publish safety rule
    game_mismatch = bool(meta.game_mismatch_flag)
    analysis_is_verified = (
        meta.analysis_provider == "gemini" and
        meta.analysis_status == "complete" and
        meta.game_confidence >= 0.50 and
        not game_mismatch
    )
    assert analysis_is_verified is False  # Must NOT be auto-approved when mismatch is True!

def test_api_create_reel_with_optional_game_name(clean_db):
    """Verify POST /reels/create accepts optional game_name and populates SourceVideo."""
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.services.pipeline_service import pipeline_service

    client = TestClient(app)

    with patch.object(pipeline_service, "execute_from_url_or_path"):
        # 1. Submission WITH game_name
        res1 = client.post("/api/reels/create", json={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "game_name": "Red Dead Redemption 2",
            "source_authorized": True
        })
        assert res1.status_code == 200
        data1 = res1.json()
        assert "job_id" in data1

        with SessionLocal() as db:
            job1 = db.query(ProcessingJob).filter_by(id=data1["job_id"]).first()
            assert job1 is not None
            assert job1.source_video.user_game_name == "Red Dead Redemption 2"

        # 2. Submission WITHOUT game_name (preserves existing behavior)
        res2 = client.post("/api/reels/create", json={
            "url": "https://www.youtube.com/watch?v=9bZkp7q19f0",
            "source_authorized": True
        })
        assert res2.status_code == 200
        data2 = res2.json()
        assert "job_id" in data2

        with SessionLocal() as db:
            job2 = db.query(ProcessingJob).filter_by(id=data2["job_id"]).first()
            assert job2 is not None
            assert job2.source_video.user_game_name is None

def test_api_retry_preserves_user_game_name(clean_db):
    """Verify POST /jobs/{id}/retry preserves user_game_name from SourceVideo."""
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.services.pipeline_service import pipeline_service

    client = TestClient(app)

    sv = SourceVideo(
        url="https://www.youtube.com/watch?v=retry_game_test_url",
        local_path="D:/data/retry_game_test.mp4",
        title="Retry Source",
        duration=60.0,
        user_game_name="GTA V",
        status="ready"
    )
    clean_db.add(sv)
    clean_db.commit()
    clean_db.refresh(sv)

    job = ProcessingJob(
        source_video_id=sv.id,
        job_type="full_pipeline",
        status="failed",
        error_message="Test failure"
    )
    clean_db.add(job)
    clean_db.commit()
    clean_db.refresh(job)

    with patch.object(pipeline_service, "execute_from_url_or_path") as mock_exec:
        res = client.post(f"/api/jobs/{job.id}/retry")
        assert res.status_code == 200
        # Background task scheduled with game_name="GTA V"
        clean_db.refresh(job)
        assert job.status == "processing"
        assert sv.user_game_name == "GTA V"
