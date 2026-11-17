"""
FastAPI Master Router for Gaming Uncut Automation.
Exposes endpoints for URL Ingestion, Processing Jobs, Clip Review,
Publishing Queue, Platform Connections, Analytics, and Logs.
"""

from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any
import os
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Form, UploadFile, File, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.core.config import settings
from backend.app.database.session import get_db
from backend.app.models.schema import (
    SourceVideo, ProcessingJob, GeneratedClip, ClipMetadata,
    PublishingJob, PlatformUpload, PublishingAnalytics, SystemLog, CredentialsMetadata,
    SystemSettings, PlatformQuotaState
)
from backend.app.ingestion.adapters import ingestion_manager, normalize_youtube_url
from backend.app.services.pipeline_service import pipeline_service
from backend.app.publishers.manager import publishing_manager
from backend.app.publishers.youtube.youtube_client import youtube_client, YouTubeAuthError
from backend.app.publishers.quota_tracker import get_or_create_settings, get_or_create_quota_state, get_today_bucket
from backend.app.security.vault import vault

logger = logging.getLogger("gaming_uncut.api")
router = APIRouter(prefix="/api")

# Schemas
class SystemLimitsUpdateRequest(BaseModel):
    daily_generation_target: Optional[int] = 15
    daily_publishing_target: Optional[int] = 15
    youtube_daily_limit: Optional[int] = 15
    instagram_daily_limit: Optional[int] = 25
    facebook_daily_limit: Optional[int] = 25

class ValidateSourceRequest(BaseModel):
    url: str

class CreateReelRequest(BaseModel):
    url: str
    source_authorized: bool = True
    target_duration: int = 60
    resolution: str = "1080x1920"
    aspect_ratio: str = "9:16"
    template: str = "blurred_bg"  # blurred_bg, dynamic_gameplay, split_layout
    ai_highlight_detection: bool = True
    smart_cropping: bool = True
    animated_captions: bool = True
    watermark: bool = True
    ai_title_generation: bool = True
    ai_topic_classification: bool = True
    ai_description_generation: bool = True
    ai_hashtag_generation: bool = True
    publish_mode: str = "approval"  # default to manual review until verified
    platforms: List[str] = ["youtube", "instagram", "facebook"]
    notes: Optional[str] = None

class UpdateClipMetadataRequest(BaseModel):
    title: str
    topic: Optional[str] = None
    description: Optional[str] = None
    hashtags: Optional[str] = None

class AddToQueueRequest(BaseModel):
    platforms: Optional[List[str]] = ["youtube", "instagram", "facebook"]
    scheduled_time: Optional[datetime] = None

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
@router.get("/status")
@router.get("/overview")
def get_overview(db: Session = Depends(get_db)):
    # Settings & Quotas
    sys_settings = get_or_create_settings(db)
    yt_quota = get_or_create_quota_state(db, "youtube")
    ig_quota = get_or_create_quota_state(db, "instagram")
    fb_quota = get_or_create_quota_state(db, "facebook")

    total_processed = db.query(SourceVideo).count()
    total_clips = db.query(GeneratedClip).count()
    
    published_count = db.query(PlatformUpload).filter_by(status="published").count()
    failed_count = db.query(PlatformUpload).filter_by(status="failed").count()
    pending_queue = db.query(PublishingJob).filter(
        PublishingJob.status.in_(["pending", "scheduled_next_window"])
    ).count()
    
    # Today's uploads & generation
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_generated = db.query(GeneratedClip).filter(
        GeneratedClip.created_at >= today_start
    ).count()
    today_published = db.query(PlatformUpload).filter(
        PlatformUpload.status == "published",
        PlatformUpload.uploaded_at >= today_start
    ).count()
    today_failed = db.query(PlatformUpload).filter(
        PlatformUpload.status == "failed",
        PlatformUpload.uploaded_at >= today_start
    ).count()

    gen_target = sys_settings.daily_generation_target or 15
    pub_target = sys_settings.daily_publishing_target or 15
    gen_pct = min(100, round((today_generated / gen_target) * 100)) if gen_target > 0 else 0
    pub_pct = min(100, round((today_published / pub_target) * 100)) if pub_target > 0 else 0

    # Credentials & Verified Status
    verified_channel = vault.get_json("google", "verified_channel")
    yt_client_cfg = vault.get_json("google", "client_config") is not None
    yt_ready = bool(verified_channel and vault.get_json("google", "oauth_tokens"))
    meta_ready = vault.get_secret("meta", "page_access_token") is not None
    ig_ready = vault.get_secret("meta", "instagram_account_id") is not None
    gemini_ready = vault.get_gemini_api_key() is not None

    quotas = {
        "youtube": {
            "published_today": yt_quota.published_today,
            "user_daily_limit": sys_settings.youtube_daily_limit,
            "quota_units_used_today": yt_quota.quota_units_used,
            "quota_units_bucket_limit": 10000,
            "api_quota_exceeded": yt_quota.api_quota_exceeded,
            "channel_limit_reached": yt_quota.channel_limit_reached,
            "pause_until": yt_quota.pause_until.isoformat() if yt_quota.pause_until else None,
            "status": "api_quota_paused" if yt_quota.api_quota_exceeded else (
                "channel_limit_paused" if yt_quota.channel_limit_reached else (
                    "user_limit_reached" if yt_quota.published_today >= sys_settings.youtube_daily_limit else "normal"
                )
            ),
            "last_restriction_reason": yt_quota.last_restriction_reason
        },
        "instagram": {
            "published_today": ig_quota.published_today,
            "user_daily_limit": sys_settings.instagram_daily_limit,
            "rate_limited": ig_quota.rate_limited,
            "pause_until": ig_quota.pause_until.isoformat() if ig_quota.pause_until else None,
            "status": "rate_limited" if ig_quota.rate_limited else (
                "user_limit_reached" if ig_quota.published_today >= sys_settings.instagram_daily_limit else "normal"
            ),
            "last_restriction_reason": ig_quota.last_restriction_reason
        },
        "facebook": {
            "published_today": fb_quota.published_today,
            "user_daily_limit": sys_settings.facebook_daily_limit,
            "rate_limited": fb_quota.rate_limited,
            "pause_until": fb_quota.pause_until.isoformat() if fb_quota.pause_until else None,
            "status": "rate_limited" if fb_quota.rate_limited else (
                "user_limit_reached" if fb_quota.published_today >= sys_settings.facebook_daily_limit else "normal"
            ),
            "last_restriction_reason": fb_quota.last_restriction_reason
        }
    }

    return {
        "kpis": {
            "total_videos_processed": total_processed,
            "total_clips_generated": total_clips,
            "today_generated": today_generated,
            "today_queued": pending_queue,
            "today_published": today_published,
            "today_failed": today_failed,
            "published_count": published_count,
            "published_today": today_published,
            "failed_count": failed_count,
            "pending_queue": pending_queue,
            "pending_approvals": pending_queue,
            "queued": pending_queue,
            "daily_target": pub_target,
            "daily_generation_target": gen_target,
            "daily_publishing_target": pub_target,
            "generation_progress_pct": gen_pct,
            "publishing_progress_pct": pub_pct,
            "target_progress_pct": pub_pct,
            "workflow_mode": "manual"
        },
        "platforms": {
            "youtube": {
                "connected": yt_ready,
                "status": f"Connected ({verified_channel.get('title')})" if (yt_ready and verified_channel) else ("Not Connected (Channel Auth Required)" if yt_client_cfg else "Not Connected"),
                "channel": verified_channel if yt_ready else None,
                "quota_used_today": yt_quota.quota_units_used,
                "quota_bucket_limit": 10000,
                "published_today": yt_quota.published_today,
                "limit": sys_settings.youtube_daily_limit,
                "paused": yt_quota.api_quota_exceeded or yt_quota.channel_limit_reached,
                "pause_until": yt_quota.pause_until.isoformat() if yt_quota.pause_until else None
            },
            "instagram": {
                "connected": meta_ready and ig_ready,
                "published_today": ig_quota.published_today,
                "limit": sys_settings.instagram_daily_limit,
                "paused": ig_quota.rate_limited,
                "pause_until": ig_quota.pause_until.isoformat() if ig_quota.pause_until else None
            },
            "facebook": {
                "connected": meta_ready,
                "published_today": fb_quota.published_today,
                "limit": sys_settings.facebook_daily_limit,
                "paused": fb_quota.rate_limited,
                "pause_until": fb_quota.pause_until.isoformat() if fb_quota.pause_until else None
            },
            "gemini": {"connected": gemini_ready}
        },
        "quotas": quotas,
        "integrations": {
            "youtube": {"authenticated": yt_ready, "configured": vault.get_json("google", "client_config") is not None},
            "instagram": {"configured": meta_ready and ig_ready},
            "facebook": {"configured": meta_ready}
        }
    }

# 3. Source Validation Endpoint
@router.post("/reels/validate-source")
async def validate_source(req: ValidateSourceRequest):
    """
    Validates gaming video source URL or local path without downloading full payload.
    Checks platform detection, accessibility, duration, resolution, and usage rights requirements.
    """
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="Video URL or path is required.")
    
    try:
        result = ingestion_manager.validate_source(req.url.strip())
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Validation error for '{req.url}': {e}")
        raise HTTPException(status_code=400, detail=str(e))

# 4. Create / Generate Reel Workflow
@router.post("/reels/generate")
@router.post("/reels/create")
def generate_reel(req: CreateReelRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Primary workflow trigger: User validates source, confirms authorization, and initiates
    10-stage autonomous AI Reel production. Returns job_id for real-time progress polling.
    """
    if not req.url or not req.url.strip():
        raise HTTPException(status_code=400, detail="Video URL or path is required.")

    if not req.source_authorized:
        raise HTTPException(
            status_code=400,
            detail="Source authorization confirmation is required. Please confirm you have usage rights or creator permission."
        )

    # Ensure or create SourceVideo record for foreign key consistency
    raw_url = req.url.strip()
    clean_url = normalize_youtube_url(raw_url) if ("youtube.com" in raw_url or "youtu.be" in raw_url) else raw_url
    source_video = db.query(SourceVideo).filter(
        (SourceVideo.url == clean_url) | (SourceVideo.local_path == clean_url)
    ).first()
    if not source_video:
        is_local = Path(clean_url).exists() and Path(clean_url).is_file()
        source_video = SourceVideo(
            url=clean_url,
            title=Path(clean_url).stem if is_local else f"Source {abs(hash(clean_url)) % 100000}",
            duration=float(req.target_duration),
            width=1920,
            height=1080,
            fps=30.0,
            format="mp4",
            local_path=clean_url if is_local else "",
            source_type="youtube" if ("youtube.com" in clean_url or "youtu.be" in clean_url) else ("direct_url" if clean_url.startswith("http") else "local"),
            status="ingesting"
        )
        db.add(source_video)
        db.commit()
        db.refresh(source_video)

    job = ProcessingJob(
        source_video_id=source_video.id,
        job_type="full_pipeline",
        status="processing",
        progress_pct=5,
        current_step="Source validation",
        started_at=datetime.utcnow()
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(
        pipeline_service.execute_from_url_or_path,
        url_or_path=clean_url,
        template=req.template,
        publish_mode=req.publish_mode,
        platforms=req.platforms,
        job_id=job.id,
        target_duration=req.target_duration,
        resolution=req.resolution,
        aspect_ratio=req.aspect_ratio,
        enable_ai_highlights=req.ai_highlight_detection,
        enable_smart_cropping=req.smart_cropping,
        enable_captions=req.animated_captions,
        enable_watermark=req.watermark,
        enable_ai_metadata=(req.ai_title_generation or req.ai_description_generation),
        notes=req.notes
    )

    return {
        "status": "processing_queued",
        "job_id": job.id,
        "message": "Reel production initiated. Real-time stages tracking active.",
        "url": clean_url,
        "template": req.template
    }

@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Retries a failed processing job cleanly without creating duplicate job IDs.
    Resets status, error_message, progress, and re-dispatches the pipeline.
    """
    job = db.query(ProcessingJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Processing job #{job_id} not found.")

    if job.status == "processing":
        raise HTTPException(status_code=400, detail=f"Job #{job_id} is already currently processing.")

    source_video = db.query(SourceVideo).filter_by(id=job.source_video_id).first()
    if not source_video:
        raise HTTPException(status_code=404, detail="Associated source video not found for this job.")

    url_or_path = source_video.url or source_video.local_path
    if not url_or_path:
        raise HTTPException(status_code=400, detail="No source URL or local path found for this job.")

    job.status = "processing"
    job.error_message = None
    job.progress_pct = 5
    job.current_step = "Source validation"
    job.started_at = datetime.utcnow()
    job.completed_at = None
    db.commit()

    background_tasks.add_task(
        pipeline_service.execute_from_url_or_path,
        url_or_path=url_or_path,
        job_id=job.id,
        target_duration=int(source_video.duration or 60),
    )

    return {
        "status": "retrying",
        "job_id": job.id,
        "message": f"Job #{job.id} re-enqueued for autonomous processing."
    }

# 5. Job Progress & 10-Stage Tracking
@router.get("/jobs/{job_id}")
def get_job_progress(job_id: int, db: Session = Depends(get_db)):
    job = db.query(ProcessingJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")

    stages_master = [
        "Source validation",
        "Media ingestion",
        "AI video analysis",
        "Highlight detection",
        "Clip composition",
        "Caption generation",
        "Video rendering",
        "Quality validation",
        "Metadata generation",
        "Reel ready"
    ]

    current_idx = 0
    for idx, s in enumerate(stages_master):
        if s.lower() in (job.current_step or "").lower():
            current_idx = idx
            break

    stages_list = []
    for idx, s in enumerate(stages_master):
        if job.status == "completed":
            st_status = "completed"
        elif job.status == "failed" and idx == current_idx:
            st_status = "failed"
        elif idx < current_idx:
            st_status = "completed"
        elif idx == current_idx:
            st_status = "in_progress" if job.status != "failed" else "failed"
        else:
            st_status = "pending"
        stages_list.append({"index": idx + 1, "name": s, "status": st_status})

    # If job has a finished clip, include full preview payload
    clip_data = None
    clip = db.query(GeneratedClip).filter_by(job_id=job.id).first()
    if clip:
        meta = clip.metadata_rel
        clip_data = {
            "id": clip.id,
            "video_url": f"/media/output/{Path(clip.output_path).name}",
            "thumbnail_url": f"/media/thumbnails/{Path(clip.thumbnail_path).name}" if clip.thumbnail_path else None,
            "duration": round(clip.duration_sec, 1),
            "resolution": f"{clip.width}x{clip.height}",
            "aspect_ratio": "9:16",
            "layout_template": clip.layout_template,
            "quality_score": round(clip.quality_score, 1),
            "ai_highlight_score": round(clip.quality_score, 1),
            "validation_status": clip.validation_status,
            "title": meta.title if meta else "Gaming Highlight",
            "topic": meta.topic if meta else "Gaming",
            "description": meta.description if meta else "",
            "hashtags": meta.hashtags if meta else "",
            "created_at": clip.created_at
        }

    return {
        "id": job.id,
        "status": job.status,
        "progress_pct": job.progress_pct,
        "current_step": job.current_step,
        "error_message": job.error_message,
        "stages": stages_list,
        "clip": clip_data,
        "started_at": job.started_at,
        "completed_at": job.completed_at
    }

# 6. Processing Queue List
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

# 6. Clip Controls & Metadata Updates
@router.post("/clips/{clip_id}/metadata")
def update_clip_metadata(clip_id: int, req: UpdateClipMetadataRequest, db: Session = Depends(get_db)):
    """Allows user to edit and save metadata (title, topic, description, hashtags) for a generated Reel."""
    clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found.")
    
    meta = clip.metadata_rel
    if not meta:
        meta = ClipMetadata(clip_id=clip_id, title=req.title)
        db.add(meta)
    
    meta.title = req.title.strip()
    if req.topic is not None:
        meta.topic = req.topic.strip()
    if req.description is not None:
        meta.description = req.description.strip()
    if req.hashtags is not None:
        meta.hashtags = req.hashtags.strip()
    
    db.commit()
    return {
        "status": "success",
        "message": f"Metadata updated for Clip #{clip_id}",
        "metadata": {
            "title": meta.title,
            "topic": meta.topic,
            "description": meta.description,
            "hashtags": meta.hashtags
        }
    }

@router.post("/clips/{clip_id}/queue")
def add_clip_to_queue(clip_id: int, req: AddToQueueRequest, db: Session = Depends(get_db)):
    """Adds a generated Reel to the multi-platform publishing queue with Manual Review default."""
    clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found.")

    sched_time = req.scheduled_time or datetime.utcnow()
    platforms = req.platforms or ["youtube", "instagram", "facebook"]

    # Check if publishing job already exists for this clip
    pub_job = db.query(PublishingJob).filter_by(clip_id=clip_id).first()
    if not pub_job:
        pub_job = PublishingJob(
            clip_id=clip_id,
            scheduled_time=sched_time,
            status="pending",
            approval_status="pending_review"
        )
        db.add(pub_job)
        db.flush()
    else:
        pub_job.status = "pending"
        pub_job.approval_status = "pending_review"
        pub_job.scheduled_time = sched_time

    # Add or update platform upload targets
    for plat in platforms:
        existing_upload = db.query(PlatformUpload).filter_by(
            publishing_job_id=pub_job.id, platform=plat
        ).first()
        if not existing_upload:
            upload = PlatformUpload(
                publishing_job_id=pub_job.id,
                platform=plat,
                status="pending"
            )
            db.add(upload)
        else:
            existing_upload.status = "pending"

    db.commit()
    return {
        "status": "success",
        "message": f"Clip #{clip_id} successfully added to Publishing Queue (Manual Review mode).",
        "publishing_job_id": pub_job.id,
        "platforms": platforms
    }

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
    verified_channel = vault.get_json("google", "verified_channel")
    yt_client_cfg = vault.get_json("google", "client_config") is not None
    # YouTube is only Connected when OAuth tokens exist AND the channel is verified!
    yt_auth = bool(verified_channel and vault.get_json("google", "oauth_tokens"))
    meta_app = vault.get_secret("meta", "app_id") is not None
    meta_token = vault.get_secret("meta", "page_access_token") is not None
    ig_account = vault.get_secret("meta", "instagram_account_id")
    ig_configured = bool(meta_token and ig_account)
    gemini_key = vault.get_gemini_api_key() is not None

    return {
        "youtube": {
            "client_configured": yt_client_cfg,
            "authenticated": yt_auth,
            "connected": yt_auth,  # Strictly false until Google OAuth and channel verification complete
            "status": "connected" if yt_auth else ("client_ready_auth_needed" if yt_client_cfg else "not_configured"),
            "status_label": f"Connected ({verified_channel.get('title')})" if (yt_auth and verified_channel) else ("Not Connected (Channel Auth Required)" if yt_client_cfg else "Not Connected"),
            "channel": verified_channel if yt_auth else None,
            "project_id": vault.get_secret("google", "project_id") or "gaming-uncut"
        },
        "instagram": {
            "connected": ig_configured,
            "status": "connected" if ig_configured else "not_configured",
            "status_label": "Connected" if ig_configured else "Not Connected",
            "account_id": ig_account or ""
        },
        "facebook": {
            "connected": meta_token,
            "status": "connected" if meta_token else "not_configured",
            "status_label": "Connected" if meta_token else "Not Connected"
        },
        "meta": {
            "app_configured": meta_app,
            "token_configured": meta_token,
            "instagram_account_id": ig_account or ""
        },
        "gemini": {
            "api_key_configured": gemini_key,
            "connected": gemini_key,
            "status_label": "Configured" if gemini_key else "Not Configured"
        }
    }

# Dedicated YouTube OAuth Endpoints
@router.post("/settings/youtube/connect")
def connect_youtube_account(open_browser: bool = True, db: Session = Depends(get_db)):
    """
    Initiates Google's official Desktop OAuth 2.0 InstalledAppFlow on localhost.
    Opens the default browser for Google user consent, stores credentials securely,
    and calls channels.list to verify channel identity.
    """
    try:
        result = youtube_client.connect_account(open_browser=open_browser)
        channel = result.get("channel", {})
        log_entry = SystemLog(
            module="youtube_oauth",
            level="INFO",
            message=f"YouTube channel successfully authorized & verified: '{channel.get('title')}' (ID: {channel.get('channel_id')})."
        )
        db.add(log_entry)
        db.commit()
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except YouTubeAuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"YouTube OAuth connection error: {e}")
        raise HTTPException(status_code=500, detail=f"OAuth connection failed: {e}")

@router.post("/settings/youtube/test")
def test_youtube_connection():
    """
    Tests existing YouTube connection by making an authentic API call to channels.list.
    """
    result = youtube_client.test_connection()
    return result

@router.post("/settings/youtube/disconnect")
def disconnect_youtube_account(db: Session = Depends(get_db)):
    """
    Disconnects YouTube: wipes OAuth tokens and verified channel information from vault.
    """
    result = youtube_client.disconnect_account()
    log_entry = SystemLog(
        module="youtube_oauth",
        level="INFO",
        message="YouTube account disconnected and tokens removed from vault."
    )
    db.add(log_entry)
    db.commit()
    return result

@router.get("/settings/youtube/channel")
def get_youtube_channel_info():
    """
    Retrieves verified YouTube channel information from vault.
    """
    channel = vault.get_json("google", "verified_channel")
    if not channel:
        return {"authenticated": False, "channel": None}
    return {"authenticated": True, "channel": channel}

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

@router.post("/settings/import-google-secrets")
async def import_google_secrets(request: Request, db: Session = Depends(get_db)):
    """
    Imports and validates Google OAuth 2.0 Desktop Client JSON secrets file into secure vault.
    Supports JSON body {"path": "..."}, Form data (path=...), or direct payload.
    Ensures safe Windows path resolution, format verification, and vault write confirmation.
    Never exposes or logs secret tokens.
    """
    target_path = None
    content_type = request.headers.get("content-type", "")

    # Extract path from JSON or Form
    if "application/json" in content_type:
        try:
            body = await request.json()
            target_path = body.get("path") or body.get("file_path")
        except Exception:
            pass
    elif "form" in content_type or "multipart" in content_type:
        try:
            form = await request.form()
            target_path = form.get("path") or form.get("file_path")
        except Exception:
            pass

    if not target_path:
        # Fallback inspection
        try:
            body = await request.json()
            target_path = body.get("path")
        except Exception:
            pass

    if not target_path or not str(target_path).strip():
        raise HTTPException(
            status_code=400,
            detail="Missing 'path' parameter. Please specify the path to your downloaded client_secrets.json file."
        )

    # 1. Clean & resolve Windows path
    raw_str = str(target_path).strip().strip('\"\'')
    expanded_str = os.path.expandvars(os.path.expanduser(raw_str))
    p = Path(expanded_str).resolve()

    # 2. Check existence & file type
    if not p.exists():
        raise HTTPException(
            status_code=400,
            detail=f"File not found at: '{p}'. Please verify that the file exists and the path is correct."
        )

    if not p.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"The specified path is a directory, not a file: '{p}'."
        )

    # 3. Read & validate JSON structure
    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Failed to read file: The file is not valid UTF-8 text."
        )
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid JSON format in file: {e}"
        )

    info = data.get("installed") or data.get("web")
    if not info or not isinstance(info, dict):
        raise HTTPException(
            status_code=400,
            detail="Invalid Google OAuth file: Missing 'installed' or 'web' root configuration. Ensure this is a Desktop OAuth client credentials file from Google Cloud Console."
        )

    client_id = info.get("client_id")
    client_secret = info.get("client_secret")
    project_id = info.get("project_id", "gaming-uncut")

    if not client_id or not isinstance(client_id, str) or not client_id.strip():
        raise HTTPException(status_code=400, detail="Invalid Google OAuth file: 'client_id' is missing or empty.")

    if not client_secret or not isinstance(client_secret, str) or not client_secret.strip():
        raise HTTPException(status_code=400, detail="Invalid Google OAuth file: 'client_secret' is missing or empty.")

    # 4. Secure storage in Vault
    try:
        # Backend vault
        vault.save_json("google", "client_config", data)
        vault.set_secret("google", "client_id", client_id.strip())
        vault.set_secret("google", "client_secret", client_secret.strip())
        vault.set_secret("google", "project_id", project_id.strip())

        # Also sync to root legacy vault for backwards compatibility
        try:
            from src.core.security import vault as legacy_vault
            legacy_vault.save_json_secret("google_client_config", data)
            legacy_vault.set_secret("google_client_id", client_id.strip())
            legacy_vault.set_secret("google_client_secret", client_secret.strip())
            legacy_vault.set_secret("google_project_id", project_id.strip())
        except Exception:
            pass

        # 5. Confirm successful storage
        stored_id = vault.get_secret("google", "client_id")
        stored_secret = vault.get_secret("google", "client_secret")
        if not stored_id or not stored_secret or stored_id != client_id.strip():
            raise RuntimeError("Vault write confirmation failed.")

    except Exception as e:
        logger.error(f"Failed to securely store Google credentials: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Secure storage error while saving to vault: {e}"
        )

    # 6. Audit logging (Never log secret values)
    log_entry = SystemLog(
        module="security",
        level="INFO",
        message=f"Google OAuth Desktop Client successfully imported and verified for project: '{project_id}'."
    )
    db.add(log_entry)
    db.commit()

    client_id_preview = f"{client_id[:12]}...{client_id[-16:]}" if len(client_id) > 28 else f"{client_id[:8]}..."
    return {
        "status": "success",
        "message": "Google Desktop OAuth credentials verified and securely stored in Vault.",
        "project_id": project_id,
        "client_id_preview": client_id_preview
    }

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

# 10. Daily Limits & System Settings
@router.get("/settings/limits")
def get_system_limits(db: Session = Depends(get_db)):
    """
    Returns current daily generation and publishing target configuration,
    and individual platform upload limits.
    """
    settings_obj = get_or_create_settings(db)
    return {
        "daily_generation_target": settings_obj.daily_generation_target,
        "daily_publishing_target": settings_obj.daily_publishing_target,
        "youtube_daily_limit": settings_obj.youtube_daily_limit,
        "instagram_daily_limit": settings_obj.instagram_daily_limit,
        "facebook_daily_limit": settings_obj.facebook_daily_limit,
        "auto_reschedule_on_quota": settings_obj.auto_reschedule_on_quota,
        "updated_at": settings_obj.updated_at.isoformat() if settings_obj.updated_at else None
    }

@router.post("/settings/limits")
def update_system_limits(req: SystemLimitsUpdateRequest, db: Session = Depends(get_db)):
    """
    Updates configurable targets and limits:
    - Daily generation target (clips created per day, default 15)
    - Daily publishing target (clips published across platforms per day, default 15)
    - YouTube daily limit (upload attempts per day, default 15)
    - Instagram daily limit (reels per day, default 25)
    - Facebook daily limit (reels per day, default 25)
    """
    settings_obj = get_or_create_settings(db)
    changed = False

    if req.daily_generation_target is not None:
        val = max(1, req.daily_generation_target)
        if settings_obj.daily_generation_target != val:
            settings_obj.daily_generation_target = val
            changed = True
    if req.daily_publishing_target is not None:
        val = max(1, req.daily_publishing_target)
        if settings_obj.daily_publishing_target != val:
            settings_obj.daily_publishing_target = val
            changed = True
    if req.youtube_daily_limit is not None:
        val = max(1, req.youtube_daily_limit)
        if settings_obj.youtube_daily_limit != val:
            settings_obj.youtube_daily_limit = val
            changed = True
    if req.instagram_daily_limit is not None:
        val = max(1, req.instagram_daily_limit)
        if settings_obj.instagram_daily_limit != val:
            settings_obj.instagram_daily_limit = val
            changed = True
    if req.facebook_daily_limit is not None:
        val = max(1, req.facebook_daily_limit)
        if settings_obj.facebook_daily_limit != val:
            settings_obj.facebook_daily_limit = val
            changed = True

    if changed:
        settings_obj.updated_at = datetime.utcnow()
        log_entry = SystemLog(
            module="settings",
            level="INFO",
            message=f"System targets & limits updated: Gen Target={settings_obj.daily_generation_target}, Pub Target={settings_obj.daily_publishing_target}, YT Limit={settings_obj.youtube_daily_limit}, IG Limit={settings_obj.instagram_daily_limit}, FB Limit={settings_obj.facebook_daily_limit}"
        )
        db.add(log_entry)
        db.commit()
        db.refresh(settings_obj)

    return {
        "status": "success",
        "message": "System limits and daily targets updated successfully.",
        "settings": {
            "daily_generation_target": settings_obj.daily_generation_target,
            "daily_publishing_target": settings_obj.daily_publishing_target,
            "youtube_daily_limit": settings_obj.youtube_daily_limit,
            "instagram_daily_limit": settings_obj.instagram_daily_limit,
            "facebook_daily_limit": settings_obj.facebook_daily_limit,
            "auto_reschedule_on_quota": settings_obj.auto_reschedule_on_quota
        }
    }
