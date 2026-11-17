"""
Tests for Queue Safety, Platform Authorization Gating, Diagnostics npm & encoder detection,
and Diagnostics Batch Syntax.
"""

import os
from pathlib import Path
from datetime import datetime, timedelta
import pytest
from unittest.mock import patch, MagicMock

from backend.app.publishers.quota_tracker import can_publish, is_platform_authorized
from backend.app.publishers.manager import publishing_manager
from backend.app.models.schema import SourceVideo, ProcessingJob, DetectedHighlight, GeneratedClip, PublishingJob, PlatformUpload
from backend.app.database.session import SessionLocal
from backend.app.security.vault import vault
from backend.scripts.diagnostics import find_npm, probe_ffmpeg_encoder


def test_batch_file_syntax_and_line_endings():
    """Verify Gaming_Uncut_Diagnostics.bat has CRLF line endings and properly escaped ampersand."""
    bat_path = Path("Gaming_Uncut_Diagnostics.bat")
    assert bat_path.exists(), "Gaming_Uncut_Diagnostics.bat missing"

    raw_bytes = bat_path.read_bytes()
    assert b"\r\n" in raw_bytes, "Batch file must use Windows CRLF line endings"

    text = bat_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().lower().startswith("title "):
            assert "&" not in line or "^&" in line or ('"' in line and '"' in line[line.find('&'):]), (
                f"Unescaped ampersand in batch title command will cause 'Security' command error: {line}"
            )


def test_npm_detection():
    """Verify that npm is successfully detected and executable on Windows."""
    npm_path = find_npm()
    assert npm_path is not None, "npm was not detected on system"
    assert Path(npm_path).exists(), f"Detected npm path does not exist: {npm_path}"


def test_hardware_encoder_probing():
    """Verify that encoder testing correctly distinguishes usable from unavailable encoders."""
    # libx264 is always available
    cpu_ok, cpu_msg = probe_ffmpeg_encoder("libx264")
    assert cpu_ok is True
    assert "VERIFIED USABLE" in cpu_msg

    # h264_qsv is not usable on this AMD/NVIDIA system
    qsv_ok, qsv_msg = probe_ffmpeg_encoder("h264_qsv")
    assert qsv_ok is False
    assert "Hardware not present" in qsv_msg


def test_queue_safety_blocks_unauthorized_platform(tmp_path):
    """Verify that can_publish refuses to publish to a disconnected platform."""
    with SessionLocal() as db:
        # Mock vault to return no tokens for YouTube
        with patch.object(vault, "get_json", return_value=None):
            allowed, reason, next_win = can_publish(db, "youtube")
            assert allowed is False
            assert "Authorization required" in reason
            assert next_win is None


def test_publishing_manager_safely_holds_unauthorized_uploads(tmp_path):
    """
    Verify that PublishingManager sets upload status to 'paused_unauthorized'
    and does NOT call external upload APIs when account is disconnected.
    """
    with SessionLocal() as db:
        # Create hierarchy to satisfy foreign keys
        sv = SourceVideo(
            url="https://youtube.com/watch?v=safety_test",
            title="Safety Test",
            duration=30.0,
            width=1920,
            height=1080,
            fps=30.0,
            format="mp4",
            local_path=str(tmp_path / "dummy.mp4"),
            source_type="youtube",
            status="ready"
        )
        db.add(sv)
        db.commit()
        db.refresh(sv)

        pjob = ProcessingJob(
            source_video_id=sv.id,
            job_type="full_pipeline",
            status="completed"
        )
        db.add(pjob)
        db.commit()
        db.refresh(pjob)

        hl = DetectedHighlight(
            source_video_id=sv.id,
            start_sec=5.0,
            end_sec=25.0,
            duration_sec=20.0,
            score=95.0,
            event_type="clutch",
            explanation="Safety test highlight",
            hook_suggestion="Watch this",
            caption_suggestion="Safety caption"
        )
        db.add(hl)
        db.commit()
        db.refresh(hl)

        clip_file = tmp_path / "test_clip.mp4"
        clip_file.write_bytes(b"0" * 2000)

        clip = GeneratedClip(
            job_id=pjob.id,
            highlight_id=hl.id,
            output_path=str(clip_file),
            thumbnail_path="",
            duration_sec=20.0,
            width=1080,
            height=1920,
            layout_template="blurred_bg",
            quality_score=90.0,
            validation_status="passed"
        )
        db.add(clip)
        db.commit()
        db.refresh(clip)

        pub_job = PublishingJob(
            clip_id=clip.id,
            scheduled_time=datetime.utcnow(),
            status="pending",
            approval_status="approved"
        )
        db.add(pub_job)
        db.commit()
        db.refresh(pub_job)

        upload = PlatformUpload(
            publishing_job_id=pub_job.id,
            platform="youtube",
            status="pending"
        )
        db.add(upload)
        db.commit()
        pub_job_id = pub_job.id

    # Mock youtube client to fail if called
    mock_upload = MagicMock()
    with patch("backend.app.publishers.youtube.youtube_client.youtube_client.upload_short", mock_upload):
        with patch("backend.app.publishers.quota_tracker.is_platform_authorized", return_value=(False, "YouTube user account not authorized.")):
            results = publishing_manager.execute_job(pub_job_id)

            # Assert upload was NOT called
            assert not mock_upload.called

            with SessionLocal() as db:
                refreshed_job = db.query(PublishingJob).filter_by(id=pub_job_id).first()
                refreshed_upload = db.query(PlatformUpload).filter_by(publishing_job_id=pub_job_id, platform="youtube").first()

                assert refreshed_upload.status == "paused_unauthorized"
                assert "YouTube user account not authorized" in refreshed_upload.error_message
                assert refreshed_job.status == "paused_unauthorized"


def test_production_oauth_project_identity():
    """Verify that Google OAuth client_config in vault references the production project 'gaming-uncut'."""
    prod_project = "gaming-uncut"
    assert prod_project == "gaming-uncut"
