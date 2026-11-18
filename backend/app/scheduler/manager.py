"""
Persistent Scheduler and Crash Recovery Engine for Gaming Uncut Automation.
Uses APScheduler with SQLite persistence to survive application restarts.
Monitors daily quota consumption and staggers publishing across peak windows.
"""

import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from backend.app.core.config import settings
from backend.app.database.session import SessionLocal, engine
from backend.app.models.schema import PublishingJob, ProcessingJob, SystemLog
from backend.app.publishers.manager import publishing_manager

logger = logging.getLogger("gaming_uncut.scheduler")

def run_process_due_publishing_jobs():
    """Scans for approved publishing jobs where scheduled_time <= NOW(). Top-level function for clean APScheduler serialization."""
    now = datetime.utcnow()
    with SessionLocal() as db:
        due_jobs = db.query(PublishingJob).filter(
            PublishingJob.status.in_(["pending", "scheduled_next_window"]),
            PublishingJob.approval_status.in_(["approved", "auto_approved"]),
            PublishingJob.scheduled_time <= now
        ).order_by(PublishingJob.scheduled_time.asc()).limit(3).all()

        job_ids = [j.id for j in due_jobs]

    for j_id in job_ids:
        try:
            logger.info(f"Executing scheduled publication for job #{j_id}...")
            publishing_manager.execute_job(j_id)
        except Exception as e:
            logger.error(f"Scheduled publish error on job #{j_id}: {e}")

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
            interrupted_pub = db.query(PublishingJob).filter(
                PublishingJob.status == "publishing"
            ).all()
            for pjob in interrupted_pub:
                logger.warning(f"Recovering interrupted publishing job #{pjob.id}. Resetting to pending.")
                pjob.status = "pending"

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
