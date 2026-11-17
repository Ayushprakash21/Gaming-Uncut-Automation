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
            self.scheduler.start()
            logger.info("Persistent background scheduler started.")

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def recover_interrupted_jobs(self):
        """Recovers any jobs left in running or analyzing states during crash or reboot."""
        with SessionLocal() as db:
            interrupted = db.query(ProcessingJob).filter(
                ProcessingJob.status.in_(["analyzing", "rendering"])
            ).all()
            for job in interrupted:
                logger.warning(f"Recovering interrupted processing job #{job.id}. Resetting to queued.")
                job.status = "queued"
                job.current_step = "Recovered after application restart"
            
            interrupted_pub = db.query(PublishingJob).filter(
                PublishingJob.status == "publishing"
            ).all()
            for pjob in interrupted_pub:
                logger.warning(f"Recovering interrupted publishing job #{pjob.id}. Resetting to pending.")
                pjob.status = "pending"

            db.commit()

    def process_due_publishing_jobs(self):
        return run_process_due_publishing_jobs()

task_scheduler = TaskScheduler()
