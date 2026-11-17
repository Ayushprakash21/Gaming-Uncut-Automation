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

            # 1. YouTube Shorts
            yt_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="youtube").first()
            if yt_upload and yt_upload.status != "published":
                try:
                    yt_upload.status = "uploading"
                    yt_upload.attempts += 1
                    db.commit()
                    res = youtube_client.upload_short(video_path, meta.youtube_title or title, meta.youtube_desc or desc, hashtags)
                    yt_upload.status = "published"
                    yt_upload.external_post_id = res["post_id"]
                    yt_upload.external_url = res["url"]
                    yt_upload.uploaded_at = datetime.utcnow()
                    results["youtube"] = res
                except Exception as e:
                    yt_upload.status = "failed"
                    yt_upload.error_message = str(e)
                    logger.error(f"YouTube upload failed: {e}")
                db.commit()

            # 2. Instagram Reels
            ig_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="instagram").first()
            if ig_upload and ig_upload.status != "published":
                try:
                    ig_upload.status = "uploading"
                    ig_upload.attempts += 1
                    db.commit()
                    res = instagram_client.publish_reel(video_path, meta.instagram_caption or caption)
                    ig_upload.status = "published"
                    ig_upload.external_post_id = res["post_id"]
                    ig_upload.external_url = res["url"]
                    ig_upload.uploaded_at = datetime.utcnow()
                    results["instagram"] = res
                except Exception as e:
                    ig_upload.status = "failed"
                    ig_upload.error_message = str(e)
                    logger.error(f"Instagram upload failed: {e}")
                db.commit()

            # 3. Facebook Reels
            fb_upload = db.query(PlatformUpload).filter_by(publishing_job_id=job.id, platform="facebook").first()
            if fb_upload and fb_upload.status != "published":
                try:
                    fb_upload.status = "uploading"
                    fb_upload.attempts += 1
                    db.commit()
                    res = facebook_client.publish_reel(video_path, meta.facebook_caption or caption)
                    fb_upload.status = "published"
                    fb_upload.external_post_id = res["post_id"]
                    fb_upload.external_url = res["url"]
                    fb_upload.uploaded_at = datetime.utcnow()
                    results["facebook"] = res
                except Exception as e:
                    fb_upload.status = "failed"
                    fb_upload.error_message = str(e)
                    logger.error(f"Facebook upload failed: {e}")
                db.commit()

            # Evaluate overall job status
            all_uploads = db.query(PlatformUpload).filter_by(publishing_job_id=job.id).all()
            if all(u.status == "published" for u in all_uploads):
                job.status = "published"
            elif any(u.status == "published" for u in all_uploads):
                job.status = "partially_published"
            else:
                job.status = "failed"

            db.commit()
            return results

publishing_manager = PublishingManager()
