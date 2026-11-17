"""
Unified Multi-Platform Publishing Manager for Gaming Uncut Automation.
Coordinates YouTube Shorts, Instagram Reels, and Facebook Reels.
Manages retry policies, duplicate prevention, and status verification.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from backend.app.database.session import SessionLocal
from backend.app.models.schema import PublishingJob, PlatformUpload, GeneratedClip, ClipMetadata, SystemLog
from backend.app.publishers.youtube.youtube_client import youtube_client
from backend.app.publishers.instagram.instagram_client import instagram_client
from backend.app.publishers.facebook.facebook_client import facebook_client
from backend.app.publishers.quota_tracker import (
    can_publish, record_upload_success, record_platform_restriction,
    YouTubeQuotaExceededError, YouTubeChannelUploadLimitError,
    InstagramRateLimitError, FacebookRateLimitError
)

logger = logging.getLogger("gaming_uncut.publish_manager")

class PublishingManager:
    def execute_job(self, publishing_job_id: int) -> Dict[str, Any]:
        with SessionLocal() as db:
            job = db.query(PublishingJob).filter_by(id=publishing_job_id).first()
            if not job:
                raise ValueError(f"Publishing job #{publishing_job_id} not found.")

            clip = db.query(GeneratedClip).filter_by(id=job.clip_id).first()
            if not clip:
                raise ValueError(f"Clip for job #{publishing_job_id} not found.")

            meta = db.query(ClipMetadata).filter_by(clip_id=clip.id).first()
            video_path = Path(clip.output_path)
            
            # Check file presence
            if not video_path.exists():
                job.status = "failed"
                db.commit()
                raise FileNotFoundError(f"Clip video file missing: {video_path}")

            job.status = "publishing"
            job.attempts += 1
            db.commit()

            title = meta.title if meta else "Epic Gaming Moment | Gaming Uncut"
            desc = meta.description if meta else "Watch this high intensity moment from Gaming Uncut."
            hashtags = [h.strip() for h in (meta.hashtags if meta else "#GamingUncut #Shorts").split() if h.startswith("#")]
            caption = f"{title}\n\n{desc}\n\n{' '.join(hashtags)}"

            results = {}
            rescheduled_windows = []

            # 1. YouTube Shorts
            yt_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="youtube").first()
            if yt_upload and yt_upload.status != "published":
                allowed, pause_reason, next_win = can_publish(db, "youtube")
                if not allowed:
                    if next_win:
                        yt_upload.status = "paused_quota"
                        rescheduled_windows.append(next_win)
                    else:
                        yt_upload.status = "paused_unauthorized"
                    yt_upload.error_message = pause_reason
                    logger.info(f"Safely skipping YouTube for Job #{job.id}: {pause_reason}")
                else:
                    try:
                        yt_upload.status = "uploading"
                        yt_upload.attempts += 1
                        db.commit()
                        res = youtube_client.upload_short(video_path, meta.youtube_title or title, meta.youtube_desc or desc, hashtags)
                        yt_upload.status = "published"
                        yt_upload.external_post_id = res["post_id"]
                        yt_upload.external_url = res["url"]
                        yt_upload.uploaded_at = datetime.utcnow()
                        record_upload_success(db, "youtube", quota_units=1600)
                        results["youtube"] = res
                    except YouTubeQuotaExceededError as e:
                        next_win = record_platform_restriction(db, "youtube", "api_quota_exceeded", str(e))
                        yt_upload.status = "paused_quota"
                        yt_upload.error_message = f"Paused: YouTube Project API Quota Exceeded (10,000 unit bucket). Rescheduled for next window."
                        rescheduled_windows.append(next_win)
                    except YouTubeChannelUploadLimitError as e:
                        next_win = record_platform_restriction(db, "youtube", "channel_limit_reached", str(e))
                        yt_upload.status = "paused_quota"
                        yt_upload.error_message = f"Paused: YouTube Channel Daily Upload Limit reached. Rescheduled for next window."
                        rescheduled_windows.append(next_win)
                    except Exception as e:
                        yt_upload.status = "failed"
                        yt_upload.error_message = str(e)
                        logger.error(f"YouTube upload failed: {e}")
                db.commit()

            # 2. Instagram Reels
            ig_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="instagram").first()
            if ig_upload and ig_upload.status != "published":
                allowed, pause_reason, next_win = can_publish(db, "instagram")
                if not allowed:
                    if next_win:
                        ig_upload.status = "paused_quota"
                        rescheduled_windows.append(next_win)
                    else:
                        ig_upload.status = "paused_unauthorized"
                    ig_upload.error_message = pause_reason
                    logger.info(f"Safely skipping Instagram for Job #{job.id}: {pause_reason}")
                else:
                    try:
                        ig_upload.status = "uploading"
                        ig_upload.attempts += 1
                        db.commit()
                        res = instagram_client.publish_reel(video_path, meta.instagram_caption or caption)
                        ig_upload.status = "published"
                        ig_upload.external_post_id = res["post_id"]
                        ig_upload.external_url = res["url"]
                        ig_upload.uploaded_at = datetime.utcnow()
                        record_upload_success(db, "instagram", quota_units=0)
                        results["instagram"] = res
                    except InstagramRateLimitError as e:
                        next_win = record_platform_restriction(db, "instagram", "rate_limited", str(e))
                        ig_upload.status = "paused_quota"
                        ig_upload.error_message = f"Paused: Instagram publishing rate limit. Rescheduled for next window."
                        rescheduled_windows.append(next_win)
                    except Exception as e:
                        ig_upload.status = "failed"
                        ig_upload.error_message = str(e)
                        logger.error(f"Instagram upload failed: {e}")
                db.commit()

            # 3. Facebook Reels
            fb_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="facebook").first()
            if fb_upload and fb_upload.status != "published":
                allowed, pause_reason, next_win = can_publish(db, "facebook")
                if not allowed:
                    if next_win:
                        fb_upload.status = "paused_quota"
                        rescheduled_windows.append(next_win)
                    else:
                        fb_upload.status = "paused_unauthorized"
                    fb_upload.error_message = pause_reason
                    logger.info(f"Safely skipping Facebook for Job #{job.id}: {pause_reason}")
                else:
                    try:
                        fb_upload.status = "uploading"
                        fb_upload.attempts += 1
                        db.commit()
                        res = facebook_client.publish_reel(video_path, meta.facebook_caption or caption)
                        fb_upload.status = "published"
                        fb_upload.external_post_id = res["post_id"]
                        fb_upload.external_url = res["url"]
                        fb_upload.uploaded_at = datetime.utcnow()
                        record_upload_success(db, "facebook", quota_units=0)
                        results["facebook"] = res
                    except FacebookRateLimitError as e:
                        next_win = record_platform_restriction(db, "facebook", "rate_limited", str(e))
                        fb_upload.status = "paused_quota"
                        fb_upload.error_message = f"Paused: Facebook publishing rate limit. Rescheduled for next window."
                        rescheduled_windows.append(next_win)
                    except Exception as e:
                        fb_upload.status = "failed"
                        fb_upload.error_message = str(e)
                        logger.error(f"Facebook upload failed: {e}")
                db.commit()

            # Evaluate overall job status & auto-rescheduling
            all_uploads = db.query(PlatformUpload).filter_by(publishing_job_id=job.id).all()
            if all(u.status == "published" for u in all_uploads):
                job.status = "published"
            elif any(u.status == "published" for u in all_uploads):
                job.status = "partially_published"
                if any(u.status == "paused_quota" for u in all_uploads) and rescheduled_windows:
                    job.scheduled_time = min(rescheduled_windows)
            elif any(u.status == "paused_quota" for u in all_uploads) and rescheduled_windows:
                job.status = "scheduled_next_window"
                job.scheduled_time = min(rescheduled_windows)
                logger.info(f"Job #{job.id} automatically scheduled for next permitted window: {job.scheduled_time}")
            elif any(u.status == "paused_unauthorized" for u in all_uploads):
                job.status = "paused_unauthorized"
                logger.info(f"Publishing Job #{job.id} safely paused: Platform authorization required.")
            else:
                job.status = "failed"

            db.commit()
            return results

            db.commit()
            return results

publishing_manager = PublishingManager()
