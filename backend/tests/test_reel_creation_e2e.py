"""
Automated Test Suite for Gaming Uncut URL Ingestion & AI Reel Creation Workflow.
Tests:
1. Source validation (YouTube, Direct URL, Local file).
2. Authorization confirmation enforcement.
3. 10-stage processing job creation and lifecycle.
4. Reel metadata editing.
5. Publishing queue dispatch with Manual Review default.
6. YouTube connection status reporting rule (Not Connected unless tokens exist).
"""

import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import SourceVideo, ProcessingJob, GeneratedClip, ClipMetadata, PublishingJob, PlatformUpload
from backend.app.security.vault import vault

client = TestClient(app)

@pytest.fixture(scope="module")
def sample_video():
    """Generates a small test mp4 video for testing local ingestion."""
    p = settings.INPUT_DIR / "sample_gameplay_test.mp4"
    if not p.exists():
        import subprocess
        # Generate 6-second synthetic test video with color & tone
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=6:size=1280x720:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-c:a", "aac",
            str(p)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    yield p

def test_source_validation_local(sample_video):
    """Test POST /api/reels/validate-source with a valid local video file."""
    res = client.post("/api/reels/validate-source", json={"url": str(sample_video)})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["valid"] is True
    assert data["source_type"] == "local"
    assert data["accessible"] is True
    assert "1280x720" in data["resolution"]
    assert data["duration"] >= 5.0

def test_source_validation_youtube():
    """Test POST /api/reels/validate-source with YouTube URL."""
    yt_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    res = client.post("/api/reels/validate-source", json={"url": yt_url})
    # Since network may vary in tests, if accessible, it should parse format correctly
    if res.status_code == 200:
        data = res.json()
        assert data["source_type"] == "youtube"
        assert data["valid"] is True
    else:
        # If offline or blocked by yt-dlp rate limit, it should return 400 with message
        assert res.status_code in (400, 404)

def test_source_validation_empty_or_invalid():
    """Test POST /api/reels/validate-source with empty or invalid path."""
    res = client.post("/api/reels/validate-source", json={"url": ""})
    assert res.status_code == 400

    res2 = client.post("/api/reels/validate-source", json={"url": "C:\\non_existent_video_path.mp4"})
    assert res2.status_code in (400, 404)

def test_reel_generation_requires_authorization():
    """Test POST /api/reels/generate rejects when source_authorized is False."""
    payload = {
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "source_authorized": False,
        "template": "blurred_bg"
    }
    res = client.post("/api/reels/generate", json=payload)
    assert res.status_code == 400
    assert "authorization confirmation is required" in res.json()["detail"].lower()

def test_reel_generation_and_stage_tracking(sample_video):
    """Test POST /api/reels/generate creates job and exposes 10 stages."""
    payload = {
        "url": str(sample_video),
        "source_authorized": True,
        "template": "blurred_bg",
        "target_duration": 60,
        "resolution": "1080x1920",
        "aspect_ratio": "9:16",
        "ai_highlight_detection": True,
        "smart_cropping": True,
        "animated_captions": True,
        "watermark": True,
        "ai_title_generation": True,
        "ai_topic_classification": True,
        "ai_description_generation": True,
        "ai_hashtag_generation": True,
        "publish_mode": "approval",
        "platforms": ["youtube", "instagram", "facebook"],
        "notes": "E2E Unit Test Reel"
    }
    res = client.post("/api/reels/generate", json=payload)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "processing_queued"
    assert "job_id" in data
    job_id = data["job_id"]

    # Check 10-stage tracking
    job_res = client.get(f"/api/jobs/{job_id}")
    assert job_res.status_code == 200
    job_data = job_res.json()
    assert "stages" in job_data
    assert len(job_data["stages"]) == 10
    stage_names = [s["name"] for s in job_data["stages"]]
    assert "Source validation" in stage_names
    assert "Media ingestion" in stage_names
    assert "AI video analysis" in stage_names
    assert "Highlight detection" in stage_names
    assert "Clip composition" in stage_names
    assert "Caption generation" in stage_names
    assert "Video rendering" in stage_names
    assert "Quality validation" in stage_names
    assert "Metadata generation" in stage_names
    assert "Reel ready" in stage_names

def test_metadata_editing_and_queue_dispatch():
    """Test POST /api/clips/{id}/metadata and POST /api/clips/{id}/queue."""
    import time
    # Seed a clip in db
    with SessionLocal() as db:
        unique_id = int(time.time() * 1000)
        sv = SourceVideo(url=f"test_url_{unique_id}", title="Test Video", local_path=f"test_path_{unique_id}")
        db.add(sv)
        db.commit()
        db.refresh(sv)

        job = ProcessingJob(source_video_id=sv.id, status="completed", progress_pct=100)
        db.add(job)
        db.commit()
        db.refresh(job)

        dummy_path = settings.OUTPUT_DIR / f"test_clip_{job.id}_{unique_id}.mp4"
        dummy_path.write_bytes(b"dummy")

        clip = GeneratedClip(
            job_id=job.id,
            output_path=str(dummy_path),
            duration_sec=60.0,
            width=1080,
            height=1920,
            layout_template="blurred_bg",
            quality_score=92.5,
            validation_status="passed"
        )
        db.add(clip)
        db.commit()
        db.refresh(clip)

        meta = ClipMetadata(
            clip_id=clip.id,
            title="Initial Title",
            topic="FPS",
            description="Initial Description",
            hashtags="#gaming"
        )
        db.add(meta)
        db.commit()
        clip_id = clip.id

    # 1. Update metadata
    meta_payload = {
        "title": "UPDATED CLUTCH HIGHLIGHT 1080x1920",
        "topic": "Battle Royale",
        "description": "Insane 1v4 squad wipe in the final circle! #GamingUncut",
        "hashtags": "#gaming #clutch #gaminguncut #shorts"
    }
    meta_res = client.post(f"/api/clips/{clip_id}/metadata", json=meta_payload)
    assert meta_res.status_code == 200, meta_res.text
    res_data = meta_res.json()
    assert res_data["metadata"]["title"] == "UPDATED CLUTCH HIGHLIGHT 1080x1920"
    assert res_data["metadata"]["topic"] == "Battle Royale"

    # 2. Add to queue
    queue_payload = {
        "platforms": ["youtube", "instagram", "facebook"]
    }
    queue_res = client.post(f"/api/clips/{clip_id}/queue", json=queue_payload)
    assert queue_res.status_code == 200, queue_res.text
    q_data = queue_res.json()
    assert q_data["status"] == "success"

    with SessionLocal() as db:
        pj = db.query(PublishingJob).filter_by(clip_id=clip_id).first()
        assert pj is not None
        assert pj.approval_status == "pending_review"  # Manual review default
        uploads = db.query(PlatformUpload).filter_by(publishing_job_id=pj.id).all()
        assert len(uploads) == 3

def test_youtube_connection_status_rule():
    """
    Test YouTube connection status:
    Must report Not Connected if oauth_tokens is not in Vault,
    even if client_config (client_secrets.json) is present.
    """
    res = client.get("/api/settings/connections")
    assert res.status_code == 200
    data = res.json()
    yt = data["youtube"]

    # Check rule
    if not yt["authenticated"]:
        assert yt["connected"] is False
        assert "Not Connected" in yt["status_label"]
