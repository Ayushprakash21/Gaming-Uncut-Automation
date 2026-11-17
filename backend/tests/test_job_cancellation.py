import time
import threading
import sys
import uuid
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database.session import SessionLocal
from backend.app.models.schema import ProcessingJob, SourceVideo, GeneratedClip, PublishingJob
from backend.app.services.pipeline_service import pipeline_service, JobCancelledException

client = TestClient(app)

def create_test_job(status="processing", current_step="AI video analysis", progress_pct=30):
    unique_id = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        src = SourceVideo(
            title=f"Test Video {unique_id}",
            url=f"https://youtube.com/watch?v={unique_id}",
            local_path=f"data/inputs/test_{unique_id}.mp4",
            duration=60.0,
            status="ready"
        )
        db.add(src)
        db.flush()

        job = ProcessingJob(
            source_video_id=src.id,
            status=status,
            current_step=current_step,
            progress_pct=progress_pct,
            worker_pid=1234
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id, src.id


def test_cancel_queued_or_pending_job():
    job_id, _ = create_test_job(status="queued", current_step="Queued", progress_pct=0)
    
    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"

    with SessionLocal() as db:
        j = db.query(ProcessingJob).filter_by(id=job_id).first()
        assert j.status == "cancelled"
        assert j.completed_at is not None
        assert j.cancellation_requested is True


def test_cancel_completed_job_rejected(tmp_path):
    job_id, src_id = create_test_job(status="completed", current_step="Completed", progress_pct=100)
    
    # Add dummy generated clip
    dummy_output = tmp_path / "dummy_clip.mp4"
    dummy_output.write_bytes(b"important completed video bytes")

    with SessionLocal() as db:
        clip = GeneratedClip(
            job_id=job_id,
            output_path=str(dummy_output),
            duration_sec=60.0,
            quality_score=95.0,
            validation_status="passed"
        )
        db.add(clip)
        db.commit()

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 400
    assert "already completed and cannot be cancelled" in resp.json()["detail"]

    # Verify file was NOT deleted
    assert dummy_output.exists()
    assert dummy_output.stat().st_size > 0

    with SessionLocal() as db:
        j = db.query(ProcessingJob).filter_by(id=job_id).first()
        assert j.status == "completed"


def test_cancel_idempotent():
    job_id, _ = create_test_job(status="queued", current_step="Queued", progress_pct=0)

    resp1 = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "cancelled"

    # Second cancel call
    resp2 = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "already_cancelled"


def test_cancel_nonexistent_job():
    resp = client.post("/api/jobs/99999999/cancel")
    assert resp.status_code == 404


def test_dismiss_failed_and_cancelled_job():
    job_id_fail, _ = create_test_job(status="failed", current_step="Failed", progress_pct=30)
    job_id_active, _ = create_test_job(status="processing", current_step="Rendering", progress_pct=70)

    # Cannot dismiss an active processing job
    resp_bad = client.post(f"/api/jobs/{job_id_active}/dismiss")
    assert resp_bad.status_code == 400

    # Dismiss failed job
    resp_ok = client.post(f"/api/jobs/{job_id_fail}/dismiss")
    assert resp_ok.status_code == 200
    assert resp_ok.json()["status"] == "dismissed"

    # Verify excluded from /api/jobs
    list_resp = client.get("/api/jobs")
    job_ids = [j["id"] for j in list_resp.json()]
    assert job_id_fail not in job_ids


def test_multi_job_cancellation_isolation():
    job_a, _ = create_test_job(status="processing", current_step="Media ingestion", progress_pct=20)
    job_b, _ = create_test_job(status="processing", current_step="AI video analysis", progress_pct=30)
    job_c, _ = create_test_job(status="processing", current_step="Video rendering", progress_pct=70)

    pipeline_service.register_active_job(job_a)
    pipeline_service.register_active_job(job_b)
    pipeline_service.register_active_job(job_c)

    try:
        # Cancel Job B only
        pipeline_service.request_job_cancellation(job_b)

        assert pipeline_service.is_cancellation_requested(job_b) is True
        assert pipeline_service.is_cancellation_requested(job_a) is False
        assert pipeline_service.is_cancellation_requested(job_c) is False

        with SessionLocal() as db:
            j_a = db.query(ProcessingJob).filter_by(id=job_a).first()
            j_b = db.query(ProcessingJob).filter_by(id=job_b).first()
            j_c = db.query(ProcessingJob).filter_by(id=job_c).first()

            assert j_b.cancellation_requested is True
            assert j_a.cancellation_requested is False
            assert j_c.cancellation_requested is False
            assert j_a.status == "processing"
            assert j_c.status == "processing"

    finally:
        pipeline_service.unregister_active_job(job_a)
        pipeline_service.unregister_active_job(job_b)
        pipeline_service.unregister_active_job(job_c)


def test_run_cancellable_subprocess_kills_active_process():
    job_id, _ = create_test_job(status="processing", current_step="Video rendering", progress_pct=70)
    pipeline_service.register_active_job(job_id)

    # Spawn a sleeping python script as a cancellable subprocess
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]

    def trigger_cancel():
        time.sleep(0.5)
        pipeline_service.request_job_cancellation(job_id)

    cancel_thread = threading.Thread(target=trigger_cancel)
    cancel_thread.start()

    t0 = time.time()
    with pytest.raises(JobCancelledException):
        pipeline_service.run_cancellable_subprocess(cmd, job_id=job_id)
    duration = time.time() - t0

    cancel_thread.join()
    pipeline_service.unregister_active_job(job_id)

    # Ensure it terminated promptly (well under the 10-second sleep)
    assert duration < 5.0, f"Process did not terminate promptly, took {duration}s"


def test_pipeline_cancels_safely_and_no_publishing():
    job_id, src_id = create_test_job(status="processing", current_step="Source validation", progress_pct=10)

    # Flag cancellation immediately
    with SessionLocal() as db:
        j = db.query(ProcessingJob).filter_by(id=job_id).first()
        j.cancellation_requested = True
        db.commit()

    # Run pipeline execution on a mock url
    res = pipeline_service.execute_from_url_or_path("https://youtube.com/watch?v=cancel_mock_run", job_id=job_id)
    assert res["status"] == "cancelled"

    with SessionLocal() as db:
        final_job = db.query(ProcessingJob).filter_by(id=job_id).first()
        assert final_job.status == "cancelled"
        assert final_job.completed_at is not None

        # Verify NO publishing job was created
        pub_item = db.query(PublishingJob).filter_by(clip_id=job_id).first()
        assert pub_item is None
