"""
Tests for YouTube URL normalization, Windows file lock handling,
FFmpeg guard validation, and job retry functionality.
"""

import os
import time
import threading
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.ingestion.adapters import (
    extract_youtube_id,
    normalize_youtube_url,
    safe_windows_replace,
    get_source_lock,
    YouTubeAdapter,
    IngestionManager
)
from backend.app.ai.audio_analyzer import extract_pcm_wav
from backend.app.models.schema import SourceVideo, ProcessingJob


def test_youtube_url_normalization_variants():
    """Verify that all YouTube URL variations (including Windows backslashes) normalize correctly."""
    target_id = "nDZAO-zQRas"
    expected = f"https://www.youtube.com/watch?v={target_id}"

    variants = [
        f"https://www.youtube.com/watch?v={target_id}",
        f"https://www.youtube.com/watch?v={target_id}&t=120s",
        f"https://youtu.be/{target_id}",
        f"https://youtu.be/{target_id}?si=abcdef123456",
        f"https://m.youtube.com/watch?v={target_id}",
        f"https://www.youtube.com/shorts/{target_id}",
        f"https://www.youtube.com/live/{target_id}",
        f"https://www.youtube.com/embed/{target_id}",
        f"https:\\\\www.youtube.com\\\\watch?v={target_id}",
        f"https:\\/\\/www.youtube.com\\/watch?v={target_id}",
        f"  https://www.youtube.com/watch?v={target_id}  ",
    ]

    for variant in variants:
        extracted = extract_youtube_id(variant)
        assert extracted == target_id, f"Failed extracting ID from {variant}, got {extracted}"
        normalized = normalize_youtube_url(variant)
        assert normalized == expected, f"Failed normalizing {variant}, got {normalized}"


def test_audio_extractor_rejects_remote_urls(tmp_path):
    """FFmpeg audio extraction must refuse remote URLs with backslashes or http/https immediately."""
    output_wav = tmp_path / "out.wav"
    bad_urls = [
        "https://www.youtube.com/watch?v=nDZAO-zQRas",
        "https:\\\\www.youtube.com\\\\watch?v=nDZAO-zQRas",
        "http://example.com/video.mp4",
        "https://example.com/stream.m3u8"
    ]

    for bad in bad_urls:
        with pytest.raises(ValueError, match="Cannot extract audio from a remote URL"):
            extract_pcm_wav(Path(bad), output_wav)


def test_audio_extractor_rejects_missing_file(tmp_path):
    """FFmpeg audio extraction must refuse non-existent local files with FileNotFoundError."""
    missing_file = tmp_path / "does_not_exist.mp4"
    output_wav = tmp_path / "out.wav"
    with pytest.raises(FileNotFoundError, match="Input video file not found"):
        extract_pcm_wav(missing_file, output_wav)


def test_per_source_lock_concurrency():
    """Verify that get_source_lock returns the same lock for the same video ID and enforces mutual exclusion."""
    vid = "lock_test_123"
    lock1 = get_source_lock(vid)
    lock2 = get_source_lock(vid)
    assert lock1 is lock2

    history = []

    def worker(worker_id: int):
        with get_source_lock(vid):
            history.append(f"{worker_id}_start")
            time.sleep(0.05)
            history.append(f"{worker_id}_end")

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Mutual exclusion ensures start and end pairs don't interleave
    assert history in (
        ["1_start", "1_end", "2_start", "2_end"],
        ["2_start", "2_end", "1_start", "1_end"]
    )


def test_safe_windows_replace_retries_transient_error(tmp_path):
    """Verify safe_windows_replace recovers from transient file locks with backoff."""
    src = tmp_path / "temp.mp4"
    dst = tmp_path / "final.mp4"
    src.write_text("test video content")

    calls = 0

    def mock_replace(s, d):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError("[WinError 32] The process cannot access the file")
        # On 3rd attempt, succeed
        dst.write_text(src.read_text())
        src.unlink()

    with patch("os.replace", side_effect=mock_replace):
        res = safe_windows_replace(src, dst, max_retries=5, initial_delay=0.01)
        assert res == dst
        assert calls == 3
        assert dst.exists()
        assert dst.read_text() == "test video content"


def test_ingestion_manager_updates_existing_source_local_path(tmp_path):
    """Verify that IngestionManager updates existing SourceVideo records with the downloaded local_path."""
    test_file = tmp_path / "sample.mp4"
    test_file.write_bytes(b"0" * 2000)

    url = "https://www.youtube.com/watch?v=test_update12"

    with SessionLocal() as db:
        # Seed an existing SourceVideo where local_path was incorrectly set to the URL
        sv = SourceVideo(
            url=url,
            title="Un-ingested Video",
            duration=60.0,
            width=1920,
            height=1080,
            fps=30.0,
            format="mp4",
            local_path=url,  # The bug: local_path holds the URL
            source_type="youtube",
            status="ingesting"
        )
        db.add(sv)
        db.commit()
        db.refresh(sv)
        sv_id = sv.id

    mock_ingest_data = {
        "source_type": "youtube",
        "url": url,
        "title": "Downloaded Title",
        "duration": 58.5,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "format": "mp4",
        "local_path": str(test_file.resolve()),
        "author": "Creator",
        "license_info": "Authorized",
        "auth_type": "creator_authorized"
    }

    mgr = IngestionManager()
    with patch.object(YouTubeAdapter, "validate_and_ingest", return_value=mock_ingest_data):
        updated_sv = mgr.process_url_or_path(url)
        assert updated_sv.id == sv_id
        assert updated_sv.local_path == str(test_file.resolve())
        assert updated_sv.local_path != url
        assert updated_sv.status == "ready"


def test_job_retry_endpoint(tmp_path):
    """Verify POST /api/jobs/{id}/retry cleanly resets failed job state and re-enqueues."""
    test_file = tmp_path / "source.mp4"
    test_file.write_bytes(b"0" * 2000)

    with SessionLocal() as db:
        sv = SourceVideo(
            url="https://www.youtube.com/watch?v=retry_test123",
            title="Retry Source",
            duration=45.0,
            width=1920,
            height=1080,
            fps=30.0,
            format="mp4",
            local_path=str(test_file.resolve()),
            source_type="youtube",
            status="ready"
        )
        db.add(sv)
        db.commit()
        db.refresh(sv)

        job = ProcessingJob(
            source_video_id=sv.id,
            job_type="full_pipeline",
            status="failed",
            progress_pct=20,
            current_step="Media ingestion",
            error_message="Simulated WinError 32 file lock"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    client = TestClient(app)
    with patch("backend.app.services.pipeline_service.pipeline_service.execute_from_url_or_path"):
        res = client.post(f"/api/jobs/{job_id}/retry")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "retrying"
        assert data["job_id"] == job_id

        # Verify job was reset in DB
        with SessionLocal() as db:
            refreshed_job = db.query(ProcessingJob).filter_by(id=job_id).first()
            assert refreshed_job.status == "processing"
            assert refreshed_job.error_message is None
            assert refreshed_job.progress_pct == 5
            assert refreshed_job.current_step == "Source validation"
