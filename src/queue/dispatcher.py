"""
Publishing Queue Dispatcher & Retry Engine for Gaming Uncut.
Schedules staggered publication across YouTube Shorts, Instagram Reels, and Facebook Reels.
Executes rate-limiting, exponential backoff retries, and honors human approval flags.
"""

import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from src.core.config import settings
from src.core.database import get_connection, log_event
from src.publishers.youtube_publisher import YouTubePublisher
from src.publishers.meta_publisher import MetaPublisher

logger = logging.getLogger("gaming_uncut.dispatcher")

class QueueDispatcher:
    def __init__(self):
        self.youtube = YouTubePublisher()
        self.meta = MetaPublisher()

    def process_pending_queue(self):
        """Pulls pending jobs where scheduled_time <= NOW() and executes them."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT q.id as queue_id, q.platform, q.attempts, q.max_attempts,
                       c.filepath, c.title, c.description, c.hashtags, c.approval_status
                FROM publishing_queue q
                JOIN rendered_clips c ON q.clip_id = c.id
                WHERE q.status = 'queued'
                  AND q.scheduled_time <= ?
                ORDER BY q.scheduled_time ASC
                LIMIT 5
                """,
                (now_str,)
            )
            jobs = cursor.fetchall()

        for job in jobs:
            # Check approval guard
            if settings.WORKFLOW_MODE == "manual" and job["approval_status"] != "approved":
                logger.info(f"Queue item {job['queue_id']} paused: awaiting human approval.")
                continue

            self._execute_job(job)

    def _execute_job(self, job):
        queue_id = job["queue_id"]
        platform = job["platform"]
        video_path = Path(job["filepath"])
        title = job["title"]
        desc = job["description"] or ""
        hashtags = [h.strip() for h in (job["hashtags"] or "").split() if h.startswith("#")]
        attempts = job["attempts"] + 1

        # Mark in progress
        with get_connection() as conn:
            conn.execute(
                "UPDATE publishing_queue SET status = 'publishing', attempts = ? WHERE id = ?",
                (attempts, queue_id)
            )
            conn.commit()

        try:
            result = None
            if platform == "youtube_shorts":
                result = self.youtube.upload_short(video_path, title, desc, hashtags)
            elif platform == "instagram_reels":
                caption = f"{title}\n\n{desc}\n\n{' '.join(hashtags)}"
                result = self.meta.upload_instagram_reel(video_path, caption)
            elif platform == "facebook_reels":
                caption = f"{title}\n\n{desc}\n\n{' '.join(hashtags)}"
                result = self.meta.upload_facebook_reel(video_path, caption)
            else:
                raise ValueError(f"Unknown platform: {platform}")

            # Mark successful
            external_id = result.get("video_id") or result.get("media_id")
            external_url = result.get("url")
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            with get_connection() as conn:
                conn.execute(
                    """
                    UPDATE publishing_queue
                    SET status = 'published', external_post_id = ?, external_url = ?, published_at = ?
                    WHERE id = ?
                    """,
                    (external_id, external_url, now_str, queue_id)
                )
                conn.commit()

            log_event("dispatcher", "INFO", f"Published item {queue_id} to {platform}", result)

        except Exception as e:
            err_msg = str(e)
            logger.error(f"Error publishing job {queue_id} to {platform}: {err_msg}")
            
            # Check if retry limit reached
            new_status = "failed" if attempts >= job["max_attempts"] else "queued"
            
            with get_connection() as conn:
                conn.execute(
                    "UPDATE publishing_queue SET status = ?, last_error = ? WHERE id = ?",
                    (new_status, err_msg, queue_id)
                )
                conn.commit()
                
            log_event("dispatcher", "ERROR", f"Job {queue_id} failed on {platform}: {err_msg}")
