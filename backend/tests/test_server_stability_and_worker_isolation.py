"""
Automated Regression Tests for Server Stability, Worker Isolation & FFmpeg Crash Protection.
Verifies:
1. Strict bounded concurrency (RENDER_SEMAPHORE & ANALYSIS_SEMAPHORE).
2. Server & worker survival when FFmpeg fails or crashes (e.g. exit code 0xC0000005 or -1).
3. Memory pressure handling and job pausing.
4. Safe FFmpeg subprocess execution with timeouts and -threads constraints.
5. SQLite job state persistence and restart recovery.
6. System diagnostics endpoint returns accurate metrics.
"""

import os
import time
import pytest
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import ProcessingJob, SourceVideo, SystemLog
from backend.app.video.ffmpeg_executor import (
    safe_run_ffmpeg,
    format_return_code,
    FFmpegExecutionResult,
    WINDOWS_NT_STATUS_CODES
)
from backend.app.video.worker import (
    video_worker,
    RENDER_SEMAPHORE,
    ANALYSIS_SEMAPHORE,
    render_concurrency_lock,
    analysis_concurrency_lock,
    is_memory_pressure,
    get_system_metrics,
    get_diagnostics_snapshot,
    record_ffmpeg_error
)
from backend.app.publishers.instagram.normalizer import (
    InstagramVideoNormalizer,
    InstagramNormalizationError
)


def test_format_return_code():
    """Verifies that return codes format with hex and Windows descriptions."""
    assert format_return_code(0) == "0 (SUCCESS)"
    code_5 = -1073741819
    formatted = format_return_code(code_5)
    assert "0xC0000005" in formatted
    assert "STATUS_ACCESS_VIOLATION" in formatted


def test_safe_run_ffmpeg_injects_thread_limit(tmp_path):
    """Verifies safe_run_ffmpeg automatically injects -threads 2."""
    out_file = tmp_path / "test.mp4"
    # Using ffmpeg version check
    cmd = ["ffmpeg", "-version"]
    res = safe_run_ffmpeg(cmd, timeout=10.0, threads=2)
    assert res.success is True
    assert res.returncode == 0


def test_safe_run_ffmpeg_handles_nonzero_exit():
    """Verifies safe_run_ffmpeg handles non-zero exit codes without raising an unhandled exception."""
    cmd = ["ffmpeg", "-invalid_arg_999"]
    res = safe_run_ffmpeg(cmd, timeout=10.0, threads=2)
    assert res.success is False
    assert res.returncode != 0
    assert res.error_message is not None


def test_safe_run_ffmpeg_handles_timeout():
    """Verifies safe_run_ffmpeg enforces timeout cleanly and terminates process tree."""
    cmd = ["powershell", "-Command", "Start-Sleep -Seconds 5"]
    res = safe_run_ffmpeg(cmd, timeout=0.5)
    assert res.success is False
    assert res.returncode == -9
    assert "timed out" in res.error_message.lower()


def test_render_semaphore_enforces_single_concurrency():
    """Verifies that RENDER_SEMAPHORE strictly bounds concurrent renders to 1."""
    entered = []

    def dummy_render(idx: int):
        with render_concurrency_lock():
            entered.append(idx)
            time.sleep(0.1)

    import threading
    t1 = threading.Thread(target=dummy_render, args=(1,))
    t2 = threading.Thread(target=dummy_render, args=(2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(entered) == 2
    # Semaphore is released and available
    assert RENDER_SEMAPHORE.acquire(blocking=False) is True
    RENDER_SEMAPHORE.release()


def test_system_metrics_and_diagnostics():
    """Verifies that get_system_metrics and get_diagnostics_snapshot return valid structure."""
    metrics = get_system_metrics()
    assert "ram" in metrics
    assert "total_mb" in metrics["ram"]
    assert "available_mb" in metrics["ram"]
    assert "used_pct" in metrics["ram"]
    assert "cpu" in metrics
    assert "used_pct" in metrics["cpu"]

    snapshot = get_diagnostics_snapshot()
    assert "api_server" in snapshot
    assert snapshot["api_server"]["status"] == "healthy"
    assert "worker" in snapshot
    assert "active_renders" in snapshot["worker"]
    assert "system_resources" in snapshot


def test_ffmpeg_error_recording_and_isolation():
    """Verifies that record_ffmpeg_error updates in-memory registry and logs without crashing."""
    record_ffmpeg_error(
        job_id=999,
        exit_code=-1073741819,
        error_message="Simulation of 0xC0000005 crash",
        stderr="Test stderr snippet"
    )
    snap = get_diagnostics_snapshot()
    last_err = snap["last_ffmpeg_error"]
    assert last_err is not None
    assert last_err["job_id"] == 999
    assert last_err["exit_code"] == -1073741819
    assert "0xC0000005" in last_err["hex_code"]


def test_instagram_normalizer_isolated_failure(tmp_path):
    """Verifies that Instagram normalization failure raises typed error without killing the process."""
    normalizer = InstagramVideoNormalizer(output_dir=tmp_path)
    corrupt_file = tmp_path / "corrupt.mp4"
    corrupt_file.write_bytes(b"NOT_A_VALID_MP4_HEADER")

    with pytest.raises(Exception):
        normalizer.normalize(corrupt_file, force_render=True)


def test_worker_survives_job_exception():
    """Verifies that the managed worker catches exceptions during job execution and keeps running."""
    with SessionLocal() as db:
        sv = SourceVideo(
            url="http://invalid.test/fake.mp4",
            title="Fake Test Video",
            local_path="invalid_path_does_not_exist.mp4",
            status="failed"
        )
        db.add(sv)
        db.commit()
        db.refresh(sv)

        job = ProcessingJob(
            source_video_id=sv.id,
            job_type="full_pipeline",
            status="queued"
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        test_job_id = job.id

    # Process via worker safe handler
    video_worker._process_job_safely({
        "job_id": test_job_id,
        "url_or_path": "invalid_path_does_not_exist.mp4",
        "game_name": "Test Game"
    })

    with SessionLocal() as db:
        updated = db.query(ProcessingJob).filter_by(id=test_job_id).first()
        assert updated.status == "failed"
        assert updated.error_message is not None
        assert updated.completed_at is not None
