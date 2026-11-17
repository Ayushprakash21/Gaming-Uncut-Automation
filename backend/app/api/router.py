"""
FastAPI Master Router for Gaming Uncut Automation.
Exposes endpoints for URL Ingestion, Processing Jobs, Clip Review,
Publishing Queue, Platform Connections, Analytics, and Logs.
"""

from pathlib import Path
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Form, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.core.config import settings
from backend.app.database.session import get_db
from backend.app.models.schema import (
    SourceVideo, ProcessingJob, GeneratedClip, ClipMetadata,
    PublishingJob, PlatformUpload, PublishingAnalytics, SystemLog, CredentialsMetadata
)
from backend.app.services.pipeline_service import pipeline_service
from backend.app.publishers.manager import publishing_manager
from backend.app.security.vault import vault

router = APIRouter(prefix="/api")

# Schemas
class CreateReelRequest(BaseModel):
    url: str
    template: str = "blurred_bg"  # blurred_bg, dynamic_gameplay, split_layout
    publish_mode: str = "auto"    # auto, approval
    platforms: List[str] = ["youtube", "instagram", "facebook"]
    notes: Optional[str] = None

class ConnectionUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    meta_page_token: Optional[str] = None
    meta_ig_id: Optional[str] = None

# 1. Health & System Info
@router.get("/health")
def get_health():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "brand": settings.BRAND_NAME,
        "tagline": settings.TAGLINE,
        "version": settings.VERSION,
        "hardware": {
            "gpu": "NVIDIA GeForce GTX 1650",
            "nvenc_enabled": settings.USE_NVENC,
            "target_resolution": f"{settings.TARGET_WIDTH}x{settings.TARGET_HEIGHT}"
        }
    }

# 2. Overview Dashboard Stats
@router.get("/overview")
def get_overview(db: Session = Depends(get_db)):
    total_processed = db.query(SourceVideo).count()
    total_clips = db.query(GeneratedClip).count()
    
    published_count = db.query(PlatformUpload).filter_by(status="published").count()
    failed_count = db.query(PlatformUpload).filter_by(status="failed").count()
    pending_queue = db.query(PublishingJob).filter_by(status="pending").count()
    
    # Today's uploads
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_published = db.query(PlatformUpload).filter(
        PlatformUpload.status == "published",
        PlatformUpload.uploaded_at >= today_start
    ).count()

    # Credentials status
    yt_ready = vault.get_json("google", "oauth_tokens") is not None
    meta_ready = vault.get_secret("meta", "page_access_token") is not None
    ig_ready = vault.get_secret("meta", "instagram_account_id") is not None
    gemini_ready = vault.get_gemini_api_key() is not None

    return {
        "kpis": {
            "total_videos_processed": total_processed,
            "total_clips_generated": total_clips,
            "published_count": published_count,
            "failed_count": failed_count,
            "pending_queue": pending_queue,
            "today_published": today_published,
            "daily_target": settings.DAILY_TARGET_CLIPS,
            "target_progress_pct": min(100, round((today_published / settings.DAILY_TARGET_CLIPS) * 100))
        },
        "platforms": {
            "youtube": {"connected": yt_ready, "quota_used_today": today_published * 1600},
            "instagram": {"connected": meta_ready and ig_ready},
            "facebook": {"connected": meta_ready},
            "gemini": {"connected": gemini_ready}
        }
    }

# 3. Create Reel (URL or Path)
@router.post("/reels/create")
def create_reel(req: CreateReelRequest, background_tasks: BackgroundTasks):
    """
    Primary workflow trigger: User pastes gaming video URL and initiates autonomous production.
    """
    background_tasks.add_task(
        pipeline_service.execute_from_url_or_path,
        url_or_path=req.url,
        template=req.template,
        publish_mode=req.publish_mode,
        platforms=req.platforms
    )
    return {
        "status": "processing_queued",
        "message": "Gaming video accepted. Ingestion, AI highlight detection, and 60s vertical rendering started in background.",
        "url": req.url,
        "template": req.template
    }

# 4. Processing Queue
@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(ProcessingJob).order_by(ProcessingJob.id.desc()).limit(50).all()
    results = []
    for j in jobs:
        results.append({
            "id": j.id,
            "source_title": j.source_video.title if j.source_video else "Unknown",
            "source_url": j.source_video.url if j.source_video else "",
            "status": j.status,
            "progress_pct": j.progress_pct,
            "current_step": j.current_step,
            "error_message": j.error_message,
            "started_at": j.started_at,
            "completed_at": j.completed_at
        })
    return results

# 5. Content Library & Clips
@router.get("/clips")
def list_clips(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(GeneratedClip).order_by(GeneratedClip.id.desc())
    clips = query.limit(50).all()
    out = []
    for c in clips:
        meta = c.metadata_rel
        out.append({
            "id": c.id,
            "video_url": f"/media/output/{Path(c.output_path).name}",
            "thumbnail_url": f"/media/thumbnails/{Path(c.thumbnail_path).name}" if c.thumbnail_path else None,
            "duration_sec": c.duration_sec,
            "layout_template": c.layout_template,
            "quality_score": c.quality_score,
            "validation_status": c.validation_status,
            "title": meta.title if meta else "Gaming Moment",
            "topic": meta.topic if meta else "Gaming",
            "description": meta.description if meta else "",
            "hashtags": meta.hashtags if meta else "",
            "created_at": c.created_at
        })
    return out

# 6. Clip Approval Controls
@router.post("/clips/{clip_id}/approve")
def approve_clip(clip_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    pub_job = db.query(PublishingJob).filter_by(clip_id=clip_id).first()
    if not pub_job:
        raise HTTPException(status_code=404, detail="Publishing job for clip not found.")
    pub_job.approval_status = "approved"
    db.commit()
    background_tasks.add_task(publishing_manager.execute_job, pub_job.id)
    return {"status": "approved", "message": f"Clip #{clip_id} approved and dispatched."}

@router.post("/clips/{clip_id}/reject")
def reject_clip(clip_id: int, db: Session = Depends(get_db)):
    pub_job = db.query(PublishingJob).filter_by(clip_id=clip_id).first()
    if pub_job:
        pub_job.approval_status = "rejected"
        pub_job.status = "cancelled"
        db.commit()
    return {"status": "rejected", "message": f"Clip #{clip_id} rejected."}

# 7. Publishing Queue
@router.get("/publishing/queue")
def list_publishing_queue(db: Session = Depends(get_db)):
    uploads = db.query(PlatformUpload).order_by(PlatformUpload.id.desc()).limit(60).all()
    results = []
    for u in uploads:
        job = u.publishing_job
        clip = job.clip if job else None
        meta = clip.metadata_rel if clip else None
        results.append({
            "id": u.id,
            "job_id": u.publishing_job_id,
            "platform": u.platform,
            "status": u.status,
            "external_url": u.external_url,
            "error_message": u.error_message,
            "attempts": u.attempts,
            "title": meta.title if meta else "Clip",
            "scheduled_time": job.scheduled_time if job else None,
            "uploaded_at": u.uploaded_at
        })
    return results

@router.post("/publishing/dispatch/{job_id}")
def force_dispatch(job_id: int, background_tasks: BackgroundTasks):
    background_tasks.add_task(publishing_manager.execute_job, job_id)
    return {"status": "started", "message": f"Dispatch initiated for job #{job_id}"}

# 8. Platform Connections & Settings
@router.get("/settings/connections")
def get_connections():
    return {
        "youtube": {
            "client_configured": vault.get_json("google", "client_config") is not None,
            "authenticated": vault.get_json("google", "oauth_tokens") is not None,
            "project_id": vault.get_secret("google", "project_id") or "gaming-uncut"
        },
        "meta": {
            "app_configured": vault.get_secret("meta", "app_id") is not None,
            "token_configured": vault.get_secret("meta", "page_access_token") is not None,
            "instagram_account_id": vault.get_secret("meta", "instagram_account_id") or ""
        },
        "gemini": {
            "api_key_configured": vault.get_gemini_api_key() is not None
        }
    }

@router.post("/settings/connections")
def update_connections(req: ConnectionUpdateRequest):
    if req.gemini_api_key:
        vault.set_gemini_api_key(req.gemini_api_key)
    if req.meta_app_id and req.meta_page_token:
        vault.import_meta_credentials(
            req.meta_app_id,
            req.meta_app_secret or "",
            req.meta_page_token,
            req.meta_ig_id or ""
        )
    return {"status": "success", "message": "Credentials securely stored in Vault."}

# 9. Audit Logs
@router.get("/logs")
def get_system_logs(limit: int = 50, db: Session = Depends(get_db)):
    logs = db.query(SystemLog).order_by(SystemLog.id.desc()).limit(limit).all()
    return [{
        "id": l.id,
        "module": l.module,
        "level": l.level,
        "message": l.message,
        "created_at": l.created_at
    } for l in logs]
