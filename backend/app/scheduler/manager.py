"""
Persistent Scheduler and Crash Recovery Engine for Gaming Uncut Automation.
Uses APScheduler with SQLite persistence to survive application restarts.
Monitors daily quota consumption and staggers publishing across peak windows.
"""

import logging
import threading
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal, engine
from backend.app.models.schema import PublishingJob, ProcessingJob, SystemLog
from backend.app.publishers.manager import publishing_manager

logger = logging.getLogger("gaming_uncut.scheduler")

_publishing_tick_lock = threading.Lock()

def run_process_due_publishing_jobs():
    """
    Scans for approved publishing jobs where scheduled_time <= NOW().
    Uses a non-blocking thread lock to ensure concurrent scheduler ticks never process the same queue items.
    Respects the auto-publishing safety switch.
    """
    if not _publishing_tick_lock.acquire(blocking=False):
        logger.info("[Scheduler] Previous publishing tick still running. Skipping concurrent tick.")
        return

    try:
        # Check safety switch
        auto_pub_enabled = getattr(settings, "AUTO_PUBLISHING_ENABLED", False)
        if not auto_pub_enabled:
            # Automatic scheduler publishing is paused for safety
            return

        now = datetime.utcnow()
        with SessionLocal() as db:
            due_jobs = db.query(PublishingJob).filter(
                PublishingJob.status.in_(["pending", "scheduled_next_window", "QUEUED"]),
                PublishingJob.approval_status.in_(["approved", "auto_approved"]),
                PublishingJob.scheduled_time <= now
            ).order_by(PublishingJob.scheduled_time.asc()).limit(3).all()

            job_ids = [j.id for j in due_jobs]

        for j_id in job_ids:
            try:
                logger.info(f"[Scheduler] Executing scheduled publication for job #{j_id}...")
                publishing_manager.execute_job(j_id)
            except Exception as e:
                logger.error(f"[Scheduler] Scheduled publish error on job #{j_id}: {e}")
    finally:
        _publishing_tick_lock.release()

def run_stalled_jobs_watchdog():
    """Periodically scans for stalled/zombie processing jobs whose worker heartbeat has lapsed."""
    try:
        from backend.app.services.pipeline_service import pipeline_service
        pipeline_service.detect_and_recover_stalled_jobs(stale_threshold_seconds=180)
    except Exception as e:
        logger.warning(f"Watchdog stalled job check warning: {e}")

def run_scheduled_discovery():
    """Checks if automatic YouTube gameplay discovery scan is due and executes it."""
    try:
        from backend.app.discovery.service import discovery_service
        with SessionLocal() as db:
            discovery_service.execute_scheduled_discovery(db)
    except Exception as e:
        logger.warning(f"Scheduled discovery periodic check error: {e}")

class TaskScheduler:
    def __init__(self):
        jobstores = {
            "default": SQLAlchemyJobStore(engine=engine)
        }
        self.scheduler = BackgroundScheduler(jobstores=jobstores)

    def start(self):
        self.recover_interrupted_jobs()
        # Periodic runner every 60 seconds
        if not self.scheduler.running:
            self.scheduler.add_job(
                run_process_due_publishing_jobs,
                "interval",
                seconds=60,
                id="queue_checker",
                replace_existing=True
            )
            self.scheduler.add_job(
                run_stalled_jobs_watchdog,
                "interval",
                seconds=60,
                id="stalled_watchdog",
                replace_existing=True
            )
            self.scheduler.add_job(
                run_scheduled_discovery,
                "interval",
                seconds=60,
                id="discovery_scanner",
                replace_existing=True
            )
            self.scheduler.start()
            logger.info("Persistent background scheduler started with stalled watchdog and discovery engine.")

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def recover_interrupted_jobs(self):
        """Recovers any jobs left in running or analyzing states during crash or reboot."""
        try:
            from backend.app.services.pipeline_service import pipeline_service
            pipeline_service.detect_and_recover_stalled_jobs(stale_threshold_seconds=0)
        except Exception as e:
            logger.warning(f"Crash recovery warning: {e}")

        with SessionLocal() as db:
            # Safe crash recovery: NEVER blindly reset active publishing jobs to pending,
            # which causes duplicate publications upon reboot! Mark as UNKNOWN for audit & reconciliation.
            interrupted_pub = db.query(PublishingJob).filter(
                PublishingJob.status.in_(["publishing", "CLAIMED", "UPLOADING", "PROCESSING"])
            ).all()
            for pjob in interrupted_pub:
                logger.warning(f"[Crash Recovery] Interrupted publishing job #{pjob.id} detected. Marking as UNKNOWN to prevent duplicate publishing.")
                pjob.status = "UNKNOWN"
                pjob.locked_at = None
                pjob.lock_expires_at = None
                pjob.worker_id = None
                
                # Log audit record
                log = SystemLog(
                    level="WARNING",
                    module="crash_recovery",
                    message=f"Publishing job #{pjob.id} was in-flight during crash/restart. Marked UNKNOWN for reconciliation."
                )
                db.add(log)

            try:
                from backend.app.models.schema import DiscoveryJob, DiscoverySchedule
                interrupted_disc = db.query(DiscoveryJob).filter_by(status="running").all()
                for djob in interrupted_disc:
                    logger.warning(f"Recovering interrupted discovery job #{djob.id}. Marking as interrupted.")
                    djob.status = "interrupted"
                    djob.error_message = "Interrupted by system restart."
                    djob.completed_at = datetime.utcnow()
                sched = db.query(DiscoverySchedule).first()
                if sched and sched.last_scan_status == "running":
                    sched.last_scan_status = "idle"
            except Exception as e:
                logger.warning(f"Discovery recovery warning: {e}")

            db.commit()

    def process_due_publishing_jobs(self):
        return run_process_due_publishing_jobs()

task_scheduler = TaskScheduler()
