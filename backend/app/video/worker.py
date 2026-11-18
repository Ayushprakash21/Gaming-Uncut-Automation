"""
Managed Video Processing Worker & Resource Management Engine.
Brand: Gaming Uncut | Tagline: NO FILTER. JUST GAMEPLAY.

Guarantees:
- Strict bounded concurrency (maximum 1 active FFmpeg render, maximum 1 active AI analysis).
- Complete isolation: FFmpeg failures or render crashes NEVER terminate the FastAPI server.
- Memory pressure protection: pauses work if system memory approaches exhaustion.
- State persistence in SQLite: all jobs track queued, processing, completed, failed, or stalled states.
- Crash diagnostics: tracks last FFmpeg error, last worker crash, and recovery metrics.
- Subprocess protection: streaming logs, timeouts, and process-tree termination.
"""

import os
import sys
import time
import shutil
import threading
import traceback
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

import psutil

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import ProcessingJob, SourceVideo, SystemLog

logger = logging.getLogger("gaming_uncut.video_worker")

# ==============================================================================
# 1. Bounded Concurrency Semaphores (Strict Concurrency Limits)
# ==============================================================================
# Maximum ONE active FFmpeg render at a time across the entire application
RENDER_SEMAPHORE = threading.Semaphore(1)

# Maximum ONE active AI video-analysis task at a time
ANALYSIS_SEMAPHORE = threading.Semaphore(1)

# Tracking locks
_CONCURRENCY_LOCK = threading.Lock()
_active_renders: int = 0
_active_analyses: int = 0

# ==============================================================================
# 2. In-Memory & Database Crash Diagnostics Registry
# ==============================================================================
_DIAGNOSTICS_LOCK = threading.Lock()
_last_ffmpeg_error: Optional[Dict[str, Any]] = None
_last_worker_crash: Optional[Dict[str, Any]] = None
_stalled_jobs_recovered_count: int = 0
_last_recovery_time: Optional[datetime] = None


def record_ffmpeg_error(
    job_id: Optional[int],
    exit_code: int,
    error_message: str,
    stderr: str = ""
):
    """Records an FFmpeg error in the crash diagnostics registry and system audit log."""
    global _last_ffmpeg_error
    now = datetime.utcnow()
    with _DIAGNOSTICS_LOCK:
        _last_ffmpeg_error = {
            "timestamp": now.isoformat(),
            "job_id": job_id,
            "exit_code": exit_code,
            "hex_code": f"0x{(exit_code & 0xFFFFFFFF):08X}" if exit_code != 0 else "0x00000000",
            "error_message": error_message,
            "stderr_snippet": (stderr[-1000:] if stderr else error_message)
        }

    try:
        with SessionLocal() as db:
            log = SystemLog(
                level="ERROR",
                module="ffmpeg_crash_protection",
                message=f"[Job #{job_id}] FFmpeg failed (code {exit_code}): {error_message[:400]}"
            )
            db.add(log)
            db.commit()
    except Exception:
        pass


def record_worker_crash(error_message: str, exc: Optional[Exception] = None):
    """Records a worker-level exception in the crash diagnostics registry."""
    global _last_worker_crash
    now = datetime.utcnow()
    tb = traceback.format_exc() if exc else ""
    with _DIAGNOSTICS_LOCK:
        _last_worker_crash = {
            "timestamp": now.isoformat(),
            "error_message": error_message,
            "traceback": tb
        }

    try:
        with SessionLocal() as db:
            log = SystemLog(
                level="CRITICAL",
                module="video_worker",
                message=f"Video worker crash event: {error_message[:400]}"
            )
            db.add(log)
            db.commit()
    except Exception:
        pass


def record_recovery_event(recovered_count: int):
    """Updates recovery metrics."""
    global _stalled_jobs_recovered_count, _last_recovery_time
    with _DIAGNOSTICS_LOCK:
        _stalled_jobs_recovered_count += recovered_count
        _last_recovery_time = datetime.utcnow()


# ==============================================================================
# 3. System Resource & Hardware Monitoring
# ==============================================================================
def get_system_metrics() -> Dict[str, Any]:
    """Retrieves real-time RAM, CPU, VRAM, and Disk space metrics."""
    # RAM
    vmem = psutil.virtual_memory()
    total_mb = round(vmem.total / (1024 * 1024), 1)
    avail_mb = round(vmem.available / (1024 * 1024), 1)
    used_pct = round(vmem.percent, 1)

    # CPU
    cpu_pct = round(psutil.cpu_percent(interval=None), 1)

    # Disk
    data_dir = settings.DATA_DIR
    disk_free_gb = 0.0
    try:
        usage = shutil.disk_usage(data_dir)
        disk_free_gb = round(usage.free / (1024 * 1024 * 1024), 2)
    except Exception:
        pass

    # GPU
    nvenc_ok = False
    gpu_name = "N/A"
    vram_total_mb = 0
    vram_used_mb = 0
    try:
        import subprocess
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0
        )
        if res.returncode == 0 and res.stdout.strip():
            parts = [p.strip() for p in res.stdout.strip().split(",")]
            if len(parts) >= 3:
                gpu_name = parts[0]
                vram_total_mb = int(parts[1])
                vram_used_mb = int(parts[2])
                nvenc_ok = True
    except Exception:
        pass

    return {
        "ram": {
            "total_mb": total_mb,
            "available_mb": avail_mb,
            "used_pct": used_pct,
            "is_critical": used_pct > 90.0 or avail_mb < 500.0
        },
        "cpu": {
            "used_pct": cpu_pct
        },
        "disk": {
            "free_gb": disk_free_gb,
            "is_low": disk_free_gb < 2.0
        },
        "gpu": {
            "name": gpu_name,
            "nvenc_enabled": nvenc_ok,
            "vram_total_mb": vram_total_mb,
            "vram_used_mb": vram_used_mb
        }
    }


def is_memory_pressure(threshold_pct: float = 90.0, min_free_mb: float = 500.0) -> bool:
    """Returns True if system is under severe memory pressure."""
    try:
        vmem = psutil.virtual_memory()
        free_mb = vmem.available / (1024 * 1024)
        return vmem.percent >= threshold_pct or free_mb <= min_free_mb
    except Exception:
        return False


# ==============================================================================
# 4. Context Managers for Strict Concurrency Throttling
# ==============================================================================
class RenderLockContext:
    """Acquires RENDER_SEMAPHORE to ensure at most 1 active FFmpeg render."""
    def __enter__(self):
        global _active_renders
        RENDER_SEMAPHORE.acquire()
        with _CONCURRENCY_LOCK:
            _active_renders += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _active_renders
        with _CONCURRENCY_LOCK:
            _active_renders = max(0, _active_renders - 1)
        RENDER_SEMAPHORE.release()


class AnalysisLockContext:
    """Acquires ANALYSIS_SEMAPHORE to ensure at most 1 active AI video analysis."""
    def __enter__(self):
        global _active_analyses
        ANALYSIS_SEMAPHORE.acquire()
        with _CONCURRENCY_LOCK:
            _active_analyses += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        global _active_analyses
        with _CONCURRENCY_LOCK:
            _active_analyses = max(0, _active_analyses - 1)
        ANALYSIS_SEMAPHORE.release()


def render_concurrency_lock():
    return RenderLockContext()


def analysis_concurrency_lock():
    return AnalysisLockContext()


# ==============================================================================
# 5. Managed Video Processing Worker
# ==============================================================================
class VideoProcessingWorker:
    """
    Dedicated worker thread that processes queued video production jobs:
    - Runs independently from the FastAPI request/response loop.
    - Polls SQLite `processing_jobs` for jobs with status 'queued'.
    - Enforces bounded concurrency (1 render, 1 analysis).
    - Checks memory pressure before picking up jobs.
    - Catches all exceptions: an FFmpeg crash NEVER kills the API server.
    - Updates heartbeats and states in SQLite.
    """

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._status: str = "idle"  # idle, processing, rendering, paused_memory, stopped
        self._current_job_id: Optional[int] = None
        self._worker_lock = threading.Lock()

    @property
    def status(self) -> str:
        return self._status

    @property
    def current_job_id(self) -> Optional[int]:
        return self._current_job_id

    def start(self):
        """Starts the managed worker thread if not already running."""
        with self._worker_lock:
            if self._thread and self._thread.is_alive():
                logger.info("VideoProcessingWorker already running.")
                return

            self._stop_event.clear()
            self._status = "idle"
            self._thread = threading.Thread(
                target=self._run_loop,
                name="ManagedVideoProcessingWorker",
                daemon=True
            )
            self._thread.start()
            logger.info("Managed VideoProcessingWorker started.")

    def stop(self, wait_timeout: float = 5.0):
        """Signals worker to stop and waits for exit."""
        self._stop_event.set()
        self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=wait_timeout)
        self._status = "stopped"
        logger.info("Managed VideoProcessingWorker stopped.")

    def notify_new_job(self):
        """Wakes up the worker immediately when a new job is queued."""
        self._wake_event.set()

    def _run_loop(self):
        """Main worker loop."""
        logger.info("Managed VideoProcessingWorker loop entered.")
        while not self._stop_event.is_set():
            try:
                # 1. Check for memory pressure
                if is_memory_pressure():
                    self._status = "paused_memory"
                    logger.warning("[VideoWorker] High memory pressure detected (>90% or <500MB free). Pausing queue processing...")
                    time.sleep(5.0)
                    continue

                # 2. Fetch next queued job
                next_job = self._fetch_next_job()
                if not next_job:
                    self._status = "idle"
                    self._current_job_id = None
                    # Wait up to 3 seconds or until a new job is posted
                    self._wake_event.wait(timeout=3.0)
                    self._wake_event.clear()
                    continue

                # 3. Process the job under full crash isolation
                self._current_job_id = next_job["job_id"]
                self._status = "processing"
                self._process_job_safely(next_job)

            except Exception as e:
                self._status = "error"
                logger.exception(f"[VideoWorker] Unhandled loop error: {e}")
                record_worker_crash(f"Worker loop exception: {e}", exc=e)
                time.sleep(2.0)

        self._status = "stopped"
        logger.info("Managed VideoProcessingWorker loop exited.")

    def _fetch_next_job(self) -> Optional[Dict[str, Any]]:
        """Queries database for the oldest 'queued' job."""
        try:
            with SessionLocal() as db:
                job = db.query(ProcessingJob).filter_by(status="queued").order_by(ProcessingJob.created_at.asc()).first()
                if not job:
                    return None

                # Transition to processing atomically
                job.status = "processing"
                job.started_at = datetime.utcnow()
                job.last_heartbeat = datetime.utcnow()
                job.worker_pid = os.getpid()
                db.commit()

                source_video = db.query(SourceVideo).filter_by(id=job.source_video_id).first()
                url_or_path = source_video.url or source_video.local_path if source_video else ""

                return {
                    "job_id": job.id,
                    "url_or_path": url_or_path,
                    "game_name": getattr(source_video, "user_game_name", None) if source_video else None,
                    "target_duration": int(getattr(source_video, "duration", 60) or 60) if source_video else 60
                }
        except Exception as e:
            logger.error(f"[VideoWorker] Error querying next job: {e}")
            return None

    def _process_job_safely(self, job_data: Dict[str, Any]):
        """Executes full pipeline for a job with total crash containment."""
        job_id = job_data["job_id"]
        url_or_path = job_data["url_or_path"]
        game_name = job_data.get("game_name")

        logger.info(f"[VideoWorker] Starting execution for Job #{job_id} ({url_or_path})...")
        try:
            from backend.app.services.pipeline_service import pipeline_service
            pipeline_service.execute_from_url_or_path(
                url_or_path=url_or_path,
                job_id=job_id,
                game_name=game_name
            )
            logger.info(f"[VideoWorker] Job #{job_id} completed successfully.")
        except Exception as e:
            # We catch ALL exceptions here so the worker thread NEVER terminates!
            logger.error(f"[VideoWorker] Job #{job_id} failed during execution: {e}", exc_info=True)
            try:
                with SessionLocal() as db:
                    db_job = db.query(ProcessingJob).filter_by(id=job_id).first()
                    if db_job and db_job.status != "cancelled":
                        db_job.status = "failed"
                        db_job.error_message = str(e)
                        db_job.completed_at = datetime.utcnow()
                        db.commit()
            except Exception:
                pass


# Global singleton worker instance
video_worker = VideoProcessingWorker()


# ==============================================================================
# 6. Unified Diagnostics Summary
# ==============================================================================
def get_diagnostics_snapshot() -> Dict[str, Any]:
    """Generates complete diagnostic payload for health and diagnostics dashboard."""
    with SessionLocal() as db:
        queued_count = db.query(ProcessingJob).filter_by(status="queued").count()
        processing_count = db.query(ProcessingJob).filter_by(status="processing").count()
        failed_count = db.query(ProcessingJob).filter_by(status="failed").count()

    with _CONCURRENCY_LOCK:
        active_renders = _active_renders
        active_analyses = _active_analyses

    with _DIAGNOSTICS_LOCK:
        last_ff_err = dict(_last_ffmpeg_error) if _last_ffmpeg_error else None
        last_w_crash = dict(_last_worker_crash) if _last_worker_crash else None
        rec_count = _stalled_jobs_recovered_count
        rec_time = _last_recovery_time.isoformat() if _last_recovery_time else None

    if not last_ff_err:
        try:
            with SessionLocal() as db:
                log_item = db.query(SystemLog).filter_by(module="ffmpeg_crash_protection").order_by(SystemLog.created_at.desc()).first()
                if log_item:
                    last_ff_err = {
                        "timestamp": log_item.created_at.isoformat() if log_item.created_at else datetime.utcnow().isoformat(),
                        "job_id": None,
                        "exit_code": -1,
                        "hex_code": "RECORDED",
                        "error_message": log_item.message,
                        "stderr_snippet": log_item.message
                    }
        except Exception:
            pass

    metrics = get_system_metrics()

    return {
        "api_server": {
            "status": "healthy",
            "uptime_seconds": time.time() - psutil.Process().create_time()
        },
        "worker": {
            "status": video_worker.status,
            "current_job_id": video_worker.current_job_id,
            "active_renders": active_renders,
            "active_analyses": active_analyses,
            "queued_jobs": queued_count,
            "processing_jobs": processing_count,
            "failed_jobs": failed_count
        },
        "system_resources": metrics,
        "last_ffmpeg_error": last_ff_err,
        "last_worker_crash": last_w_crash,
        "recovery_status": {
            "stalled_jobs_recovered": rec_count,
            "last_recovery": rec_time
        }
    }
