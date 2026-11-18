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
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Form, UploadFile, File, Request, Query
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
    game_name: Optional[str] = None
    local_file_path: Optional[str] = None

class CreateReelRequest(BaseModel):
    url: str
    game_name: Optional[str] = None
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
    local_file_path: Optional[str] = None

class UpdateClipMetadataRequest(BaseModel):
    title: str
    topic: Optional[str] = None
    description: Optional[str] = None
    hashtags: Optional[str] = None
    detected_game: Optional[str] = None
    youtube_title: Optional[str] = None
    youtube_desc: Optional[str] = None
    instagram_caption: Optional[str] = None
    facebook_caption: Optional[str] = None

class AddToQueueRequest(BaseModel):
    platforms: Optional[List[str]] = ["youtube", "instagram", "facebook"]
    scheduled_time: Optional[datetime] = None
    allow_republish: Optional[bool] = False

class DispatchRequest(BaseModel):
    allow_republish: Optional[bool] = False

class ConnectionUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    meta_app_id: Optional[str] = None
    meta_app_secret: Optional[str] = None
    meta_page_token: Optional[str] = None
    meta_page_id: Optional[str] = None
    meta_ig_id: Optional[str] = None
    meta_token_type: Optional[str] = "system_user"

class BatchRegenerateMetadataRequest(BaseModel):
    clip_ids: Optional[List[int]] = None
    force_all: Optional[bool] = False

# 1. Health & System Info
@router.get("/health")
def get_health():
    from backend.app.video.worker import get_diagnostics_snapshot
    diag = get_diagnostics_snapshot()
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "brand": settings.BRAND_NAME,
        "tagline": settings.TAGLINE,
        "version": settings.VERSION,
        "hardware": {
            "gpu": diag["system_resources"]["gpu"]["name"] or "NVIDIA GeForce GTX 1650",
            "nvenc_enabled": diag["system_resources"]["gpu"]["nvenc_enabled"] if diag["system_resources"]["gpu"]["name"] != "N/A" else settings.USE_NVENC,
            "target_resolution": f"{settings.TARGET_WIDTH}x{settings.TARGET_HEIGHT}"
        },
        "worker": diag["worker"],
        "system_resources": diag["system_resources"],
        "last_ffmpeg_error": diag["last_ffmpeg_error"],
        "last_worker_crash": diag["last_worker_crash"],
        "recovery_status": diag["recovery_status"]
    }

@router.get("/diagnostics/system")
@router.get("/system/diagnostics")
def get_system_diagnostics():
    """Returns comprehensive real-time diagnostics: worker, memory, CPU, active renders, and crash telemetry."""
    from backend.app.video.worker import get_diagnostics_snapshot
    return get_diagnostics_snapshot()

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
        result = ingestion_manager.validate_source(
            req.url.strip(),
            local_override_path=req.local_file_path
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Validation error for '{req.url}': {e}")
        raise HTTPException(status_code=400, detail=str(e))

# 3b. Local / Creator Source Video Upload Endpoint
@router.post("/reels/upload-source")
async def upload_source_video(
    file: UploadFile = File(...),
    youtube_url: Optional[str] = Form(None)
):
    """
    Uploads and ingests a creator-provided or local gameplay video file.
    Saves securely into data/input, probes media attributes, and optionally
    pairs with a YouTube URL for AI clipping and metadata generation.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded.")

    ext = Path(file.filename).suffix.lower()
    if ext not in [".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts"]:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported video format: '{ext}'. Allowed: MP4, MKV, MOV, WEBM."
        )

    clean_filename = f"upload_{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    target_path = settings.INPUT_DIR / clean_filename

    try:
        with open(target_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except Exception as e:
        logger.error(f"Failed to save uploaded video: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save video: {e}")

    from backend.app.ingestion.adapters import probe_local_media
    meta = probe_local_media(target_path)
    if meta.get("duration", 0) < 3.0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Uploaded video duration is too short or file is corrupted (minimum 3s required)."
        )

    minutes = int(meta["duration"] // 60)
    seconds = int(meta["duration"] % 60)

    # If youtube_url was provided, retrieve YouTube metadata to combine
    yt_meta = {}
    if youtube_url:
        from backend.app.ingestion.youtube_source import youtube_source_service, extract_youtube_id
        vid = extract_youtube_id(youtube_url)
        if vid:
            yt_meta = youtube_source_service.get_unified_metadata(vid) or {}

    return {
        "status": "success",
        "local_path": str(target_path.resolve()),
        "filename": file.filename,
        "size_mb": round(target_path.stat().st_size / (1024 * 1024), 2),
        "duration": meta["duration"],
        "duration_formatted": f"{minutes}:{seconds:02d}",
        "resolution": f"{meta.get('width', 1920)}x{meta.get('height', 1080)}",
        "title": yt_meta.get("title") or Path(file.filename).stem,
        "author": yt_meta.get("author") or "Creator File",
        "thumbnail_url": yt_meta.get("thumbnail_url") or "",
        "message": "Local video file uploaded and verified successfully."
    }

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
    clean_game_name = req.game_name.strip() if (req.game_name and req.game_name.strip()) else None
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
            local_path=req.local_file_path or (clean_url if is_local else f"pending_{clean_url}"),
            source_type="youtube" if ("youtube.com" in clean_url or "youtu.be" in clean_url) else ("direct_url" if clean_url.startswith("http") else "local"),
            user_game_name=clean_game_name,
            status="ingesting"
        )
        db.add(source_video)
        db.commit()
        db.refresh(source_video)
    else:
        if clean_game_name:
            source_video.user_game_name = clean_game_name
        if req.local_file_path:
            source_video.local_path = req.local_file_path
        db.commit()

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
        notes=req.notes,
        game_name=clean_game_name,
        local_file_path=req.local_file_path
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
        game_name=source_video.user_game_name
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
            "detected_game": meta.detected_game if meta else "Unknown Game",
            "user_game_name": meta.user_game_name if meta else None,
            "game_mismatch_flag": bool(meta.game_mismatch_flag) if meta else False,
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
        "completed_at": job.completed_at,
        "cancelled_at": job.completed_at if job.status == "cancelled" else None
    }

# 6. Processing Queue List & Individual Job Actions
@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(ProcessingJob).filter(ProcessingJob.status != "dismissed").order_by(ProcessingJob.id.desc()).limit(50).all()
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
            "completed_at": j.completed_at,
            "cancelled_at": j.completed_at if j.status == "cancelled" else None,
            "user_game_name": j.source_video.user_game_name if j.source_video else None
        })
    return results

@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    """
    Cancels an individual video processing job.
    - Validates job exists (404).
    - If status is 'completed', returns 400.
    - If status is 'cancelled', returns 200 (idempotent).
    - Signals cancellation to the pipeline and worker process tree.
    - Persists cancelled state.
    """
    job = db.query(ProcessingJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")

    if job.status.lower() == "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job #{job_id} has already completed and cannot be cancelled."
        )

    if job.status.lower() == "cancelled":
        return {
            "status": "already_cancelled",
            "job_id": job_id,
            "message": f"Job #{job_id} is already cancelled."
        }

    # Request cancellation via pipeline service (kills active subprocesses, sets flags)
    pipeline_service.request_job_cancellation(job_id)
    
    # Reload job status from DB
    db.refresh(job)

    return {
        "status": job.status,
        "job_id": job.id,
        "message": f"Job #{job.id} cancellation requested successfully."
    }

@router.post("/jobs/{job_id}/dismiss")
def dismiss_job(job_id: int, db: Session = Depends(get_db)):
    """
    Dismisses a failed or cancelled job from the processing queue UI.
    """
    job = db.query(ProcessingJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job #{job_id} not found.")

    if job.status.lower() not in ("failed", "cancelled", "stalled"):
        raise HTTPException(
            status_code=400,
            detail=f"Only failed or cancelled jobs can be dismissed (Job #{job_id} is '{job.status}')."
        )

    job.status = "dismissed"
    db.commit()

    return {
        "status": "dismissed",
        "job_id": job.id,
        "message": f"Job #{job.id} dismissed from queue."
    }

# 5. Content Library & Clips
@router.get("/clips")
def list_clips(status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(GeneratedClip).order_by(GeneratedClip.id.desc())
    clips = query.limit(50).all()
    out = []
    for c in clips:
        meta = c.metadata_rel
        events = json.loads(meta.detected_events_json) if meta and meta.detected_events_json else []
        candidate_titles = json.loads(meta.candidate_titles_json) if meta and meta.candidate_titles_json else ([meta.title] if meta else [])
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
            "detected_game": meta.detected_game if meta else "Unknown Game",
            "game_confidence": meta.game_confidence if meta else 0.0,
            "game_franchise": meta.game_franchise if meta else "",
            "game_genre": meta.game_genre if meta else "",
            "user_game_name": meta.user_game_name if meta else None,
            "game_mismatch_flag": bool(meta.game_mismatch_flag) if meta else False,
            "gameplay_summary": meta.gameplay_summary if meta else "",
            "detected_events": events,
            "candidate_titles": candidate_titles,
            "analysis_provider": meta.analysis_provider if meta else "heuristic",
            "analysis_status": meta.analysis_status if meta else "complete",
            "created_at": c.created_at
        })
    return out

@router.get("/clips/{clip_id}")
def get_clip(clip_id: int, db: Session = Depends(get_db)):
    clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found.")
    meta = clip.metadata_rel
    events = json.loads(meta.detected_events_json) if meta and meta.detected_events_json else []
    candidate_titles = json.loads(meta.candidate_titles_json) if meta and meta.candidate_titles_json else ([meta.title] if meta else [])
    return {
        "id": clip.id,
        "video_url": f"/media/output/{Path(clip.output_path).name}",
        "thumbnail_url": f"/media/thumbnails/{Path(clip.thumbnail_path).name}" if clip.thumbnail_path else None,
        "duration_sec": clip.duration_sec,
        "layout_template": clip.layout_template,
        "quality_score": clip.quality_score,
        "validation_status": clip.validation_status,
        "title": meta.title if meta else "Gaming Moment",
        "topic": meta.topic if meta else "Gaming",
        "description": meta.description if meta else "",
        "hashtags": meta.hashtags if meta else "",
        "detected_game": meta.detected_game if meta else "Unknown Game",
        "game_confidence": meta.game_confidence if meta else 0.0,
        "game_franchise": meta.game_franchise if meta else "",
        "game_genre": meta.game_genre if meta else "",
        "user_game_name": meta.user_game_name if meta else None,
        "game_mismatch_flag": bool(meta.game_mismatch_flag) if meta else False,
        "gameplay_summary": meta.gameplay_summary if meta else "",
        "detected_events": events,
        "candidate_titles": candidate_titles,
        "analysis_provider": meta.analysis_provider if meta else "heuristic",
        "analysis_status": meta.analysis_status if meta else "complete",
        "created_at": clip.created_at
    }

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
    if req.detected_game is not None:
        meta.detected_game = req.detected_game.strip()
    if req.youtube_title is not None:
        meta.youtube_title = req.youtube_title.strip()
    if req.youtube_desc is not None:
        meta.youtube_desc = req.youtube_desc.strip()
    if req.instagram_caption is not None:
        meta.instagram_caption = req.instagram_caption.strip()
    if req.facebook_caption is not None:
        meta.facebook_caption = req.facebook_caption.strip()
    
    db.commit()
    return {
        "status": "success",
        "message": f"Metadata updated for Clip #{clip_id}",
        "metadata": {
            "title": meta.title,
            "topic": meta.topic,
            "description": meta.description,
            "hashtags": meta.hashtags,
            "detected_game": meta.detected_game,
            "youtube_title": meta.youtube_title,
            "youtube_desc": meta.youtube_desc,
            "instagram_caption": meta.instagram_caption,
            "facebook_caption": meta.facebook_caption
        }
    }

@router.post("/clips/{clip_id}/regenerate-metadata")
@router.post("/clips/{clip_id}/reanalyze")
def regenerate_clip_metadata_endpoint(clip_id: int, db: Session = Depends(get_db)):
    """
    Regenerates grounded, clip-specific metadata for a single historical clip
    using its actual rendered MP4 video file on disk. Never auto-publishes.
    """
    from backend.app.services.metadata_regenerator import regenerate_clip_metadata
    try:
        res = regenerate_clip_metadata(clip_id=clip_id, db=db)
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to regenerate metadata: {str(e)}")

@router.post("/clips/batch-regenerate-metadata")
def batch_regenerate_metadata_endpoint(req: BatchRegenerateMetadataRequest, db: Session = Depends(get_db)):
    """
    Batch regenerates metadata for specified clip IDs or all clips flagged with generic templates.
    """
    from backend.app.services.metadata_regenerator import batch_regenerate_metadata
    try:
        report = batch_regenerate_metadata(db=db, clip_ids=req.clip_ids, force_all=bool(req.force_all))
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch metadata regeneration failed: {str(e)}")

@router.post("/clips/{clip_id}/queue")
def add_clip_to_queue(clip_id: int, req: AddToQueueRequest, db: Session = Depends(get_db)):
    """Adds a generated Reel to the multi-platform publishing queue with idempotency protection."""
    clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found.")

    sched_time = req.scheduled_time or datetime.utcnow()
    platforms = req.platforms or ["youtube", "instagram", "facebook"]
    allow_republish = bool(req.allow_republish)

    # Check if publishing job already exists for this clip
    pub_job = db.query(PublishingJob).filter_by(clip_id=clip_id).first()
    if not pub_job:
        pub_job = PublishingJob(
            clip_id=clip_id,
            scheduled_time=sched_time,
            status="pending",
            approval_status="pending_review",
            allow_republish=allow_republish
        )
        db.add(pub_job)
        db.flush()
    else:
        # Protect existing job from being reset if already published or in-flight unless allow_republish is explicitly True
        is_published = pub_job.status in ["published", "PUBLISHED"]
        is_in_flight = pub_job.status in ["publishing", "CLAIMED", "UPLOADING", "PROCESSING"] and pub_job.lock_expires_at and pub_job.lock_expires_at > datetime.utcnow()
        if is_in_flight and not allow_republish:
            raise HTTPException(status_code=409, detail=f"Job #{pub_job.id} for Clip #{clip_id} is currently in-flight.")

        if not is_published or allow_republish:
            pub_job.status = "pending"
            pub_job.approval_status = "pending_review"
            pub_job.scheduled_time = sched_time
            pub_job.allow_republish = allow_republish

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
            # If already published, DO NOT reset to pending unless allow_republish is True
            if existing_upload.status in ["published", "PUBLISHED"]:
                if allow_republish:
                    existing_upload.status = "pending"
                    existing_upload.error_message = None
                    existing_upload.error_code = None
            elif existing_upload.status not in ["claiming", "uploading", "processing", "transferring_video"]:
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

        size_mb = None
        if clip and clip.output_path:
            p = Path(clip.output_path)
            if p.exists():
                size_mb = round(p.stat().st_size / (1024 * 1024), 2)

        results.append({
            "id": u.id,
            "job_id": u.publishing_job_id,
            "job_status": job.status if job else None,
            "platform": u.platform,
            "status": u.status,
            "external_url": u.external_url,
            "error_code": u.error_code,
            "error_message": u.error_message,
            "attempts": u.attempts,
            "title": meta.title if meta else "Clip",
            "scheduled_time": job.scheduled_time if job else None,
            "uploaded_at": u.uploaded_at,
            "file_size_mb": size_mb,
            "duration_sec": clip.duration_sec if clip else None,
            "resolution": f"{clip.width}x{clip.height}" if clip and clip.width else None
        })
    return results

@router.get("/publishing/diagnostics/{job_id}")
def get_publishing_diagnostics(job_id: int, db: Session = Depends(get_db)):
    pub_job = db.query(PublishingJob).filter_by(id=job_id).first()
    if not pub_job:
        raise HTTPException(status_code=404, detail=f"Publishing job #{job_id} not found.")

    clip = pub_job.clip
    meta = clip.metadata_rel if clip else None
    uploads = db.query(PlatformUpload).filter_by(publishing_job_id=job_id).all()

    from backend.app.publishers.instagram.diagnostics import inspect_video_for_instagram
    from backend.app.publishers.instagram.validator import instagram_video_validator

    orig_path = Path(clip.output_path) if clip and clip.output_path else None
    orig_diag = inspect_video_for_instagram(orig_path) if orig_path and orig_path.exists() else None

    # Check for normalized instagram file
    norm_path = settings.OUTPUT_DIR / "instagram" / f"{orig_path.stem}_instagram.mp4" if orig_path else None
    norm_diag = inspect_video_for_instagram(norm_path) if norm_path and norm_path.exists() else None

    logs = db.query(SystemLog).filter(
        SystemLog.message.like(f"%Job #{job_id}%") | SystemLog.message.like(f"%Clip #{clip.id}%")
    ).order_by(SystemLog.id.desc()).limit(20).all()

    return {
        "job_id": job_id,
        "clip_id": clip.id if clip else None,
        "title": meta.title if meta else "Clip",
        "original_specs": orig_diag,
        "normalized_specs": norm_diag,
        "uploads": [
            {
                "platform": u.platform,
                "status": u.status,
                "attempts": u.attempts,
                "error_code": u.error_code,
                "error_message": u.error_message,
                "external_url": u.external_url,
                "uploaded_at": u.uploaded_at
            }
            for u in uploads
        ],
        "audit_logs": [
            {"id": l.id, "level": l.level, "module": l.module, "message": l.message, "created_at": l.created_at}
            for l in logs
        ]
    }

@router.post("/publishing/dispatch/{job_id}")
def force_dispatch(
    job_id: int,
    background_tasks: BackgroundTasks,
    req: Optional[DispatchRequest] = None,
    db: Session = Depends(get_db)
):
    pub_job = db.query(PublishingJob).filter_by(id=job_id).first()
    if not pub_job:
        raise HTTPException(status_code=404, detail=f"Publishing job #{job_id} not found.")

    allow_rep = req.allow_republish if req else False
    now = datetime.utcnow()

    # Check if job is currently claimed/in-flight with an active lease
    if pub_job.status in ["CLAIMED", "UPLOADING", "PROCESSING", "publishing"] and pub_job.lock_expires_at and pub_job.lock_expires_at > now:
        raise HTTPException(
            status_code=409,
            detail=f"Publishing job #{job_id} is currently in-flight (status: {pub_job.status}). Concurrent dispatch prevented."
        )

    # Section 1 Safeguard: Disable Publish Now for all jobs/uploads in RECONCILING or UNKNOWN state
    reconciling_upload = db.query(PlatformUpload).filter(
        PlatformUpload.publishing_job_id == job_id,
        PlatformUpload.status.in_(["reconciling", "RECONCILING", "unknown", "UNKNOWN"])
    ).first()
    if reconciling_upload and not allow_rep:
        raise HTTPException(
            status_code=409,
            detail=f"Publishing job #{job_id} has platform upload '{reconciling_upload.platform}' in {reconciling_upload.status.upper()} state. "
                   "Publish Now is disabled to prevent duplicate container creation while reconciliation is active."
        )

    # Check if job already finished publishing
    if pub_job.status in ["published", "PUBLISHED"] and not allow_rep:
        has_pending = db.query(PlatformUpload).filter(
            PlatformUpload.publishing_job_id == job_id,
            PlatformUpload.status.notin_(["published", "PUBLISHED", "paused_quota"])
        ).first()
        if not has_pending:
            raise HTTPException(
                status_code=409,
                detail=f"Publishing job #{job_id} has already been published across all targets. Enable republish to run again."
            )

    if allow_rep:
        pub_job.allow_republish = True
        db.commit()

    background_tasks.add_task(publishing_manager.execute_job, job_id, allow_rep)
    return {"status": "started", "message": f"Dispatch initiated for job #{job_id}"}

@router.post("/publishing/reconcile-instagram")
def trigger_instagram_reconciliation(db: Session = Depends(get_db)):
    """Triggers on-demand background reconciliation of in-flight Instagram containers."""
    from backend.app.publishers.instagram.reconciler import instagram_reconciler
    results = instagram_reconciler.reconcile_pending_containers(db)
    return {
        "success": True,
        "message": f"Reconciliation executed. Processed {len(results)} container(s).",
        "results": results
    }

# 8. Platform Connections & Settings
@router.get("/settings/connections")
def get_connections():
    verified_channel = vault.get_json("google", "verified_channel")
    yt_client_cfg = vault.get_json("google", "client_config") is not None
    # YouTube is only Connected when OAuth tokens exist AND the channel is verified!
    yt_auth = bool(verified_channel and vault.get_json("google", "oauth_tokens"))
    from backend.app.publishers.meta_token_manager import meta_token_manager
    meta_health = meta_token_manager.inspect_health()
    meta_app = vault.get_secret("meta", "app_id") is not None
    meta_token = meta_health.get("is_valid", False)
    meta_page_id = vault.get_secret("meta", "page_id")
    ig_account = vault.get_secret("meta", "instagram_account_id")
    ig_configured = bool(meta_health.get("instagram_ready", False))
    fb_configured = bool(meta_health.get("facebook_ready", False))
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
            "status": meta_health.get("instagram_status", "not_configured"),
            "status_label": "Connected" if ig_configured else ("Expired" if meta_health.get("is_expired") else "Not Connected"),
            "account_id": ig_account or "",
            "username": meta_health.get("instagram_username") or ""
        },
        "facebook": {
            "connected": fb_configured,
            "status": meta_health.get("facebook_status", "not_configured"),
            "status_label": "Connected" if fb_configured else ("Expired" if meta_health.get("is_expired") else "Not Connected"),
            "page_id": meta_page_id or "",
            "page_name": meta_health.get("page_name") or ""
        },
        "meta": {
            "app_configured": meta_app,
            "token_configured": bool(vault.get_secret("meta", "system_user_token") or vault.get_secret("meta", "page_access_token")),
            "page_id": meta_page_id or "",
            "instagram_account_id": ig_account or "",
            "health": meta_health
        },
        "gemini": {
            "api_key_configured": gemini_key,
            "connected": gemini_key,
            "status_label": "Configured" if gemini_key else "Not Configured"
        }
    }

@router.get("/settings/meta/health")
def get_meta_token_health():
    """Returns official live health, expiration, type, and permissions for Meta token."""
    from backend.app.publishers.meta_token_manager import meta_token_manager
    return meta_token_manager.inspect_health(force_refresh=False)

@router.post("/settings/meta/verify")
def verify_meta_connection(db: Session = Depends(get_db)):
    """Triggers live introspection and verification of Meta credentials via official debug_token."""
    from backend.app.publishers.meta_token_manager import meta_token_manager
    health = meta_token_manager.inspect_health(force_refresh=True)
    resumed = 0
    if health.get("is_valid"):
        resumed = meta_token_manager.resume_queued_jobs_after_reconnect(db)
    return {
        "success": health.get("is_valid", False),
        "health": health,
        "resumed_jobs": resumed,
        "message": "Meta credentials verified successfully." if health.get("is_valid") else f"Verification failed: {health.get('last_auth_error') or 'Token invalid'}"
    }

@router.post("/settings/meta/refresh")
def refresh_meta_token():
    """Refreshes expiring Meta token where supported using official fb_exchange_token."""
    from backend.app.publishers.meta_token_manager import meta_token_manager
    return meta_token_manager.refresh_token()

@router.get("/settings/facebook/status")
@router.post("/settings/facebook/test")
def test_facebook_connection():
    """Live diagnostic verification of Facebook Page credentials and publishing permissions."""
    from backend.app.publishers.facebook.facebook_client import facebook_client
    return facebook_client.test_connection()

@router.get("/settings/instagram/status")
@router.post("/settings/instagram/test")
def test_instagram_connection():
    """Live diagnostic verification of Instagram Professional Account credentials and publishing permissions."""
    from backend.app.publishers.instagram.instagram_client import instagram_client
    return instagram_client.test_connection()

# Dedicated YouTube OAuth Endpoints
@router.post("/settings/youtube/connect/start")
def start_youtube_oauth(open_browser: bool = True):
    """
    Initiates Google's official Desktop OAuth 2.0 flow:
    - Binds an ephemeral port on 127.0.0.1 loopback
    - Generates authorization URL with CSRF state protection
    - Spawns background callback server
    - Launches default Windows browser
    - Returns immediately with auth_url and session metadata
    """
    from backend.app.publishers.youtube.oauth_manager import oauth_manager
    try:
        result = oauth_manager.start_authorization(open_browser=open_browser)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to start YouTube OAuth: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start OAuth flow: {e}")

@router.get("/settings/youtube/connect/status")
def get_youtube_oauth_status():
    """
    Returns live progress and state of the YouTube OAuth authorization flow:
    idle | waiting_for_consent | verifying_channel | connected | failed | cancelled | timed_out
    """
    from backend.app.publishers.youtube.oauth_manager import oauth_manager
    return oauth_manager.get_status()

@router.post("/settings/youtube/connect/cancel")
def cancel_youtube_oauth():
    """
    Cancels any active YouTube OAuth session and cleanly shuts down the loopback listener.
    """
    from backend.app.publishers.youtube.oauth_manager import oauth_manager
    return oauth_manager.cancel_authorization()

@router.post("/settings/youtube/connect")
def connect_youtube_account(open_browser: bool = True, db: Session = Depends(get_db)):
    """
    Initiates Google Desktop OAuth 2.0 InstalledAppFlow on localhost.
    Starts background callback server and returns authorization details immediately.
    """
    from backend.app.publishers.youtube.oauth_manager import oauth_manager
    try:
        result = oauth_manager.start_authorization(open_browser=open_browser)
        log_entry = SystemLog(
            module="youtube_oauth",
            level="INFO",
            message="YouTube Desktop OAuth authorization session initiated."
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
def update_connections(req: ConnectionUpdateRequest, db: Session = Depends(get_db)):
    if req.gemini_api_key:
        vault.set_gemini_api_key(req.gemini_api_key)
    if req.meta_page_token:
        app_id = (req.meta_app_id or vault.get_secret("meta", "app_id") or "").strip()
        app_secret = (req.meta_app_secret or vault.get_secret("meta", "app_secret") or "").strip()
        page_id = (req.meta_page_id or vault.get_secret("meta", "page_id") or "").strip()
        ig_id = (req.meta_ig_id or vault.get_secret("meta", "instagram_account_id") or "").strip()
        vault.import_meta_credentials(
            app_id,
            app_secret,
            req.meta_page_token.strip(),
            ig_id,
            page_id,
            token_type=req.meta_token_type or "system_user"
        )
        from backend.app.publishers.meta_token_manager import meta_token_manager
        health = meta_token_manager.inspect_health(force_refresh=True)
        if health.get("is_valid"):
            resumed = meta_token_manager.resume_queued_jobs_after_reconnect(db)
            return {
                "status": "success",
                "message": f"Meta credentials verified. Resumed {resumed} queued publishing jobs.",
                "health": health
            }
        return {
            "status": "warning" if not health.get("is_valid") else "success",
            "message": "Credentials stored. Token status: " + (health.get("last_auth_error") or "Check token scopes"),
            "health": health
        }
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
def get_system_logs(
    limit: int = 100,
    level: Optional[str] = None,
    module: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(SystemLog)
    if level and level.strip():
        query = query.filter(SystemLog.level == level.strip().upper())
    if module and module.strip():
        query = query.filter(SystemLog.module.ilike(f"%{module.strip()}%"))
    logs = query.order_by(SystemLog.id.desc()).limit(limit).all()
    return [{
        "id": l.id,
        "module": l.module,
        "level": l.level,
        "message": l.message,
        "metadata_json": l.metadata_json,
        "created_at": l.created_at.isoformat() if l.created_at else None
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


# =========================================================================
# 11. Gameplay Discovery Engine (Manual Search, Queue, Scheduler, Auth)
# =========================================================================

class ManualSearchRequest(BaseModel):
    query: Optional[str] = ""
    game_titles: Optional[List[str]] = None
    channel_id: Optional[str] = None
    published_after: Optional[str] = None
    duration_filter: Optional[str] = None
    min_views: int = 0
    page_token: Optional[str] = None
    max_results: int = 15

class ImportVideosRequest(BaseModel):
    videos: List[Dict[str, Any]]
    search_id: Optional[int] = None

class AuthorizeSourceRequest(BaseModel):
    auth_type: str = "creator_authorized"
    authorized_by: Optional[str] = None
    source_owner: Optional[str] = None
    permitted_scope: str = "shorts_clips_derivative"
    evidence_url: Optional[str] = None
    notes: Optional[str] = None
    local_file_path: Optional[str] = None
    enqueue_now: bool = True

class ProcessDiscoveredVideoRequest(BaseModel):
    local_file_path: Optional[str] = None
    auto_authorize: bool = True

class CreateClipsRequest(BaseModel):
    video_ids: Optional[List[int]] = None
    videos: Optional[List[Dict[str, Any]]] = None
    local_file_paths: Optional[Dict[str, str]] = None
    target_duration: Optional[int] = 60
    game_name: Optional[str] = None

class DiscoverySourceRequest(BaseModel):
    id: Optional[int] = None
    name: Optional[str] = None
    game_title: str
    keywords: Optional[List[str]] = None
    is_active: bool = True
    min_duration_sec: float = 60.0
    max_duration_sec: float = 1800.0
    min_views: int = 0
    max_results_per_scan: int = 10
    priority: int = 1

class DiscoveryChannelRequest(BaseModel):
    channel_id: str
    channel_title: str
    custom_url: Optional[str] = None
    is_approved: bool = True
    auto_authorize_sources: bool = False
    notes: Optional[str] = None

class DiscoveryScheduleUpdateRequest(BaseModel):
    interval_minutes: Optional[int] = None
    is_active: Optional[bool] = None
    max_discoveries_per_scan: Optional[int] = None
    max_videos_processed_per_day: Optional[int] = None
    max_clips_generated_per_day: Optional[int] = None
    ai_budget_daily: Optional[int] = None
    auto_approve_clips: Optional[bool] = None
    auto_publish: Optional[bool] = None

def _safe_isoformat(val):
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)

def _handle_manual_search(
    query: Optional[str],
    game_titles: Optional[List[str]],
    channel_id: Optional[str],
    published_after_raw: Optional[str],
    duration_filter: Optional[str],
    min_views: Optional[int],
    page_token: Optional[str],
    max_results: int,
    db: Session
):
    from backend.app.discovery.service import discovery_service
    from datetime import timedelta, timezone
    pub_after = None
    if published_after_raw:
        now = datetime.utcnow()
        p_lower = published_after_raw.lower().strip()
        if p_lower == "today":
            pub_after = now - timedelta(days=1)
        elif p_lower == "week":
            pub_after = now - timedelta(days=7)
        elif p_lower == "month":
            pub_after = now - timedelta(days=30)
        else:
            try:
                parsed = datetime.fromisoformat(published_after_raw.replace("Z", "+00:00"))
                pub_after = parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
            except Exception:
                pass

    try:
        return discovery_service.search_manual(
            db=db,
            query=query or "",
            game_titles=game_titles,
            channel_id=channel_id,
            published_after=pub_after,
            duration_filter=duration_filter,
            min_views=min_views if min_views is not None else 0,
            page_token=page_token,
            max_results=max_results
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/discovery/search")
@router.post("/discovery/search/")
def manual_youtube_search_post(req: ManualSearchRequest, db: Session = Depends(get_db)):
    """Executes a manual search via YouTube Data API v3 with quota tracking and game filtering (POST JSON)."""
    return _handle_manual_search(
        query=req.query,
        game_titles=req.game_titles,
        channel_id=req.channel_id,
        published_after_raw=req.published_after,
        duration_filter=req.duration_filter,
        min_views=req.min_views,
        page_token=req.page_token,
        max_results=req.max_results,
        db=db
    )

@router.get("/discovery/search")
@router.get("/discovery/search/")
def manual_youtube_search_get(
    query: Optional[str] = Query(None),
    game: Optional[List[str]] = Query(None),
    channel_id: Optional[str] = Query(None),
    published_after: Optional[str] = Query(None),
    duration: Optional[str] = Query(None),
    min_views: Optional[int] = Query(None),
    page_token: Optional[str] = Query(None),
    max_results: int = Query(25),
    db: Session = Depends(get_db)
):
    """Executes a manual search via YouTube Data API v3 with quota tracking and game filtering (GET Query)."""
    return _handle_manual_search(
        query=query,
        game_titles=game,
        channel_id=channel_id,
        published_after_raw=published_after,
        duration_filter=duration,
        min_views=min_views,
        page_token=page_token,
        max_results=max_results,
        db=db
    )

@router.post("/discovery/import")
def import_discovered_videos(req: ImportVideosRequest, db: Session = Depends(get_db)):
    """Imports selected discovered videos into the discovery queue."""
    from backend.app.discovery.service import discovery_service
    if not req.videos:
        raise HTTPException(status_code=400, detail="No videos provided for import.")
    try:
        imported = discovery_service.import_videos_to_queue(
            db=db,
            videos=req.videos,
            discovery_mode="manual",
            search_id=req.search_id
        )
        return {
            "success": True,
            "status": "success",
            "imported_count": len(imported),
            "items": [{
                "id": v.id,
                "youtube_video_id": v.youtube_video_id,
                "title": v.title,
                "status": v.status
            } for v in imported]
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to import discovered videos: {str(e)}")

@router.get("/discovery/queue")
def get_discovery_queue(
    status: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Retrieves paginated discovery queue items."""
    from backend.app.discovery.service import discovery_service
    try:
        items, total = discovery_service.get_queue(
            db=db, status=status, search=search, limit=limit, offset=offset
        )
        return {
            "success": True,
            "total": total,
            "items": [{
                "id": it.id,
                "youtube_video_id": it.youtube_video_id,
                "title": it.title,
                "channel_id": it.channel_id,
                "channel_title": it.channel_title,
                "published_at": _safe_isoformat(it.published_at),
                "duration_sec": it.duration_sec,
                "view_count": it.view_count,
                "thumbnail_url": it.thumbnail_url,
                "youtube_url": it.youtube_url,
                "game_title": it.game_title,
                "discovery_mode": it.discovery_mode,
                "status": it.status,
                "processing_job_id": it.processing_job_id,
                "clips_count": len(it.processing_job.clips) if (it.processing_job and it.processing_job.clips) else it.clips_count,
                "job_progress_pct": it.processing_job.progress_pct if it.processing_job else 0,
                "job_current_step": it.processing_job.current_step if it.processing_job else None,
                "job_status": it.processing_job.status if it.processing_job else None,
                "error_message": it.error_message,
                "source_alternatives": [
                    {"type": "local_file", "label": "Attach Local Video File", "description": "Select or enter an MP4/MKV file on your system."},
                    {"type": "creator_upload", "label": "Upload Creator Video", "description": "Provide a video file provided by the content creator."},
                    {"type": "authorised_integration", "label": "Connect Authorized Source", "description": "Link your authorized gameplay capture feed."},
                    {"type": "permitted_stream", "label": "Use Permitted Source Stream", "description": "Input a direct accessible video stream URL."}
                ] if it.status in ["failed", "acquisition_failed", "source_restricted", "authorization_required"] else [],
                "authorization": {
                    "auth_type": it.authorization.auth_type,
                    "authorized_by": it.authorization.authorized_by,
                    "source_owner": it.authorization.source_owner,
                    "permitted_scope": it.authorization.permitted_scope,
                    "evidence_url": it.authorization.evidence_url,
                    "confirmed_at": _safe_isoformat(it.authorization.confirmed_at)
                } if it.authorization else None,
                "created_at": _safe_isoformat(it.created_at)
            } for it in items]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load discovery queue: {str(e)}")

@router.post("/discovery/create-clips")
def create_clips_endpoint(req: CreateClipsRequest, db: Session = Depends(get_db)):
    """Streamlined bulk/single clipping workflow directly from video discovery without popups."""
    from backend.app.discovery.service import discovery_service
    try:
        queued = discovery_service.create_clips_for_videos(
            db=db,
            video_ids=req.video_ids,
            videos=req.videos,
            local_file_paths=req.local_file_paths,
            target_duration=req.target_duration or 60,
            game_name=req.game_name
        )
        return {
            "success": True,
            "queued_count": len([q for q in queued if q.get("status") == "queued"]),
            "queued_jobs": queued,
            "message": f"Successfully enqueued {len([q for q in queued if q.get('status') == 'queued'])} video(s) for AI highlight clipping."
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/discovery/queue/{video_id}/authorize")
def authorize_discovered_video(
    video_id: int,
    req: AuthorizeSourceRequest,
    db: Session = Depends(get_db)
):
    """Attaches explicit creator authorization or local file to a discovered video."""
    from backend.app.discovery.service import discovery_service
    try:
        video = discovery_service.authorize_source(
            db=db,
            discovered_video_id=video_id,
            auth_type=req.auth_type,
            authorized_by=req.authorized_by,
            source_owner=req.source_owner,
            permitted_scope=req.permitted_scope,
            evidence_url=req.evidence_url,
            notes=req.notes,
            local_file_path=req.local_file_path,
            enqueue_now=req.enqueue_now
        )
        return {
            "status": "success",
            "message": f"Video #{video_id} authorized.",
            "video_status": video.status
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/discovery/queue/{video_id}/process")
def process_discovered_video(
    video_id: int,
    req: Optional[ProcessDiscoveredVideoRequest] = None,
    db: Session = Depends(get_db)
):
    """Enqueues a discovered video into the AI clipping pipeline without mandatory popups."""
    from backend.app.discovery.service import discovery_service
    try:
        local_path = req.local_file_path if req else None
        auto_auth = req.auto_authorize if (req and req.auto_authorize is not None) else True
        job = discovery_service.enqueue_for_processing(
            db=db,
            discovered_video_id=video_id,
            local_file_path=local_path,
            auto_authorize=auto_auth
        )
        return {
            "status": "success",
            "job_id": job.id,
            "message": f"Video enqueued for AI pipeline as Job #{job.id}."
        }
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/discovery/queue/{video_id}/retry")
def retry_discovered_video(video_id: int, db: Session = Depends(get_db)):
    """Retries a failed discovered video."""
    from backend.app.discovery.service import discovery_service
    try:
        video = discovery_service.retry_video(db, video_id)
        return {"status": "success", "video_status": video.status}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/discovery/queue/{video_id}/skip")
def skip_discovered_video(video_id: int, db: Session = Depends(get_db)):
    """Skips a discovered video."""
    from backend.app.discovery.service import discovery_service
    try:
        video = discovery_service.skip_video(db, video_id)
        return {"status": "success", "video_status": video.status}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/discovery/sources")
def list_discovery_sources(db: Session = Depends(get_db)):
    """Lists configured gaming sources to monitor."""
    from backend.app.discovery.service import discovery_service
    sources = discovery_service.list_sources(db)
    return [{
        "id": s.id,
        "name": s.name,
        "game_title": s.game_title,
        "keywords": json.loads(s.keywords_json) if s.keywords_json else [],
        "is_active": s.is_active,
        "min_duration_sec": s.min_duration_sec,
        "max_duration_sec": s.max_duration_sec,
        "min_views": s.min_views,
        "max_results_per_scan": s.max_results_per_scan,
        "priority": s.priority,
        "last_scanned_at": s.last_scanned_at.isoformat() if s.last_scanned_at else None,
        "next_scan_at": s.next_scan_at.isoformat() if s.next_scan_at else None
    } for s in sources]

@router.post("/discovery/sources")
def add_or_update_discovery_source(req: DiscoverySourceRequest, db: Session = Depends(get_db)):
    """Creates or updates a monitored gaming source."""
    from backend.app.discovery.service import discovery_service
    src = discovery_service.add_or_update_source(
        db=db,
        game_title=req.game_title,
        name=req.name,
        keywords=req.keywords,
        is_active=req.is_active,
        min_duration_sec=req.min_duration_sec,
        max_duration_sec=req.max_duration_sec,
        min_views=req.min_views,
        max_results_per_scan=req.max_results_per_scan,
        priority=req.priority,
        source_id=req.id
    )
    return {"status": "success", "source_id": src.id}

@router.delete("/discovery/sources/{source_id}")
def delete_discovery_source(source_id: int, db: Session = Depends(get_db)):
    """Deletes a monitored gaming source."""
    from backend.app.discovery.service import discovery_service
    ok = discovery_service.delete_source(db, source_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Source #{source_id} not found.")
    return {"status": "success", "message": f"Source #{source_id} removed."}

@router.get("/discovery/channels")
def list_discovery_channels(db: Session = Depends(get_db)):
    """Lists allowlisted / approved YouTube channels."""
    from backend.app.discovery.service import discovery_service
    channels = discovery_service.list_channels(db)
    return [{
        "id": c.id,
        "channel_id": c.channel_id,
        "channel_title": c.channel_title,
        "custom_url": c.custom_url,
        "is_approved": c.is_approved,
        "auto_authorize_sources": c.auto_authorize_sources,
        "notes": c.notes
    } for c in channels]

@router.post("/discovery/channels")
def add_or_update_discovery_channel(req: DiscoveryChannelRequest, db: Session = Depends(get_db)):
    """Adds or updates an approved channel."""
    from backend.app.discovery.service import discovery_service
    chan = discovery_service.add_or_update_channel(
        db=db,
        channel_id=req.channel_id,
        channel_title=req.channel_title,
        custom_url=req.custom_url,
        is_approved=req.is_approved,
        auto_authorize_sources=req.auto_authorize_sources,
        notes=req.notes
    )
    return {"status": "success", "channel_id": chan.channel_id}

@router.delete("/discovery/channels/{channel_id}")
def delete_discovery_channel(channel_id: str, db: Session = Depends(get_db)):
    """Removes an approved channel."""
    from backend.app.discovery.service import discovery_service
    ok = discovery_service.delete_channel(db, channel_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Channel '{channel_id}' not found.")
    return {"status": "success", "message": f"Channel '{channel_id}' removed."}

@router.get("/discovery/schedule")
def get_discovery_schedule(db: Session = Depends(get_db)):
    """Gets current automatic discovery schedule settings and status."""
    from backend.app.discovery.service import discovery_service
    sched = discovery_service.get_or_create_schedule(db)
    return {
        "id": sched.id,
        "name": sched.name,
        "interval_minutes": sched.interval_minutes,
        "is_active": sched.is_active,
        "max_discoveries_per_scan": sched.max_discoveries_per_scan,
        "max_videos_processed_per_day": sched.max_videos_processed_per_day,
        "max_clips_generated_per_day": sched.max_clips_generated_per_day,
        "ai_budget_daily": sched.ai_budget_daily,
        "auto_approve_clips": sched.auto_approve_clips,
        "auto_publish": sched.auto_publish,
        "last_scan_at": sched.last_scan_at.isoformat() if sched.last_scan_at else None,
        "next_scan_at": sched.next_scan_at.isoformat() if sched.next_scan_at else None,
        "last_scan_status": sched.last_scan_status
    }

@router.post("/discovery/schedule")
def update_discovery_schedule(req: DiscoveryScheduleUpdateRequest, db: Session = Depends(get_db)):
    """Updates automatic discovery schedule settings."""
    from backend.app.discovery.service import discovery_service
    updates = req.dict(exclude_unset=True)
    sched = discovery_service.update_schedule_settings(db, updates)
    return {
        "status": "success",
        "message": "Discovery schedule updated.",
        "schedule": {
            "interval_minutes": sched.interval_minutes,
            "is_active": sched.is_active,
            "max_discoveries_per_scan": sched.max_discoveries_per_scan,
            "max_videos_processed_per_day": sched.max_videos_processed_per_day,
            "max_clips_generated_per_day": sched.max_clips_generated_per_day,
            "ai_budget_daily": sched.ai_budget_daily,
            "auto_approve_clips": sched.auto_approve_clips,
            "auto_publish": sched.auto_publish
        }
    }

@router.post("/discovery/schedule/scan-now")
def trigger_immediate_discovery_scan(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Triggers an immediate automatic discovery scan in the background."""
    from backend.app.discovery.service import discovery_service
    def _run_scan():
        with SessionLocal() as session:
            discovery_service.execute_scheduled_discovery(session, force=True)
    background_tasks.add_task(_run_scan)
    return {"status": "started", "message": "Immediate automatic discovery scan initiated in background."}

@router.post("/discovery/schedule/pause")
def pause_discovery_schedule(db: Session = Depends(get_db)):
    """Pauses scheduled automatic discovery."""
    from backend.app.discovery.service import discovery_service
    sched = discovery_service.pause_schedule(db)
    return {"status": "paused", "is_active": sched.is_active}

@router.post("/discovery/schedule/resume")
def resume_discovery_schedule(db: Session = Depends(get_db)):
    """Resumes scheduled automatic discovery."""
    from backend.app.discovery.service import discovery_service
    sched = discovery_service.resume_schedule(db)
    return {"status": "active", "is_active": sched.is_active, "next_scan_at": sched.next_scan_at.isoformat() if sched.next_scan_at else None}

# =========================================================================
# AI SEO & Growth Intelligence Engine Endpoints
# =========================================================================

class SeoGenerateRequest(BaseModel):
    platforms: Optional[List[str]] = None
    bypass_cache: bool = False
    custom_keywords: Optional[List[str]] = None

class SeoApproveRequest(BaseModel):
    platform: Optional[str] = "all"

class SeoBulkGenerateRequest(BaseModel):
    clip_ids: Optional[List[int]] = None
    platforms: Optional[List[str]] = None
    bypass_cache: bool = True

class SeoBulkApproveRequest(BaseModel):
    clip_ids: List[int]
    platform: Optional[str] = "all"

@router.get("/seo/dashboard")
def get_seo_dashboard(db: Session = Depends(get_db)):
    """Returns executive metrics, KPIs, platform breakdown, and growth insights."""
    from backend.app.analytics.growth_intelligence import growth_intelligence_service
    return growth_intelligence_service.get_dashboard_summary(db)

@router.get("/seo/clips/{clip_id}")
def get_clip_seo(clip_id: int, db: Session = Depends(get_db)):
    """Retrieves platform-specific SEO packages for a clip; generates if missing."""
    from backend.app.models.schema import ClipSeoMetadata, GeneratedClip
    from backend.app.metadata.seo_engine import seo_engine

    clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail=f"Clip #{clip_id} not found.")

    records = db.query(ClipSeoMetadata).filter_by(clip_id=clip_id).all()
    if not records or len(records) < 3:
        # Generate on the fly
        res = seo_engine.generate_clip_seo(clip_id=clip_id, db=db)
        records = db.query(ClipSeoMetadata).filter_by(clip_id=clip_id).all()

    platforms_payload = {}
    for r in records:
        platforms_payload[r.platform] = {
            "platform": r.platform,
            "recommended_title": r.recommended_title,
            "candidate_titles": json.loads(r.candidate_titles_json or "[]"),
            "recommended_caption": r.recommended_caption,
            "description": r.description,
            "primary_keyword": r.primary_keyword,
            "secondary_keywords": json.loads(r.secondary_keywords_json or "[]"),
            "tags": json.loads(r.tags_json or "[]"),
            "hashtags": json.loads(r.hashtags_json or "[]"),
            "search_phrases": json.loads(r.search_phrases_json or "[]"),
            "gameplay_event_keywords": json.loads(r.gameplay_event_keywords_json or "[]"),
            "thumbnail_hook_text": r.thumbnail_hook_text,
            "opening_hook": r.opening_hook,
            "search_intent": r.search_intent,
            "cta": r.cta,
            "onscreen_text": r.onscreen_text,
            "topic_suggestions": json.loads(r.topic_suggestions_json or "[]"),
            "seo_score": r.seo_score,
            "score_breakdown": json.loads(r.score_breakdown_json or "{}"),
            "warnings": json.loads(r.warnings_json or "[]"),
            "recommendations": json.loads(r.recommendations_json or "[]"),
            "approval_status": r.approval_status,
            "ai_provider": r.ai_provider,
            "ai_model": r.ai_model,
            "version": r.version,
            "updated_at": r.updated_at
        }

    meta = clip.metadata_rel
    return {
        "clip_id": clip_id,
        "video_url": f"/media/output/{Path(clip.output_path).name}" if clip.output_path else None,
        "thumbnail_url": f"/media/thumbnails/{Path(clip.thumbnail_path).name}" if clip.thumbnail_path else None,
        "game_name": meta.detected_game if meta else "Unknown Game",
        "user_game_name": getattr(meta, "user_game_name", None) if meta else None,
        "game_mismatch_flag": bool(getattr(meta, "game_mismatch_flag", False)) if meta else False,
        "platforms": platforms_payload
    }

@router.post("/seo/clips/{clip_id}/generate")
def generate_clip_seo_endpoint(clip_id: int, req: SeoGenerateRequest, db: Session = Depends(get_db)):
    """Triggers generation or regeneration of platform-specific SEO metadata for a clip."""
    from backend.app.metadata.seo_engine import seo_engine
    try:
        res = seo_engine.generate_clip_seo(
            clip_id=clip_id,
            db=db,
            target_platforms=req.platforms,
            bypass_cache=req.bypass_cache,
            custom_keywords=req.custom_keywords
        )
        return {
            "status": "success",
            "clip_id": clip_id,
            "message": "Platform-specific SEO packages generated successfully.",
            **res
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SEO generation failed: {e}")

@router.post("/seo/clips/{clip_id}/approve")
def approve_clip_seo(clip_id: int, req: SeoApproveRequest, db: Session = Depends(get_db)):
    """Approves SEO metadata and syncs directly with ClipMetadata for publishing."""
    from backend.app.models.schema import ClipSeoMetadata, GeneratedClip, ClipMetadata
    clip = db.query(GeneratedClip).filter_by(id=clip_id).first()
    if not clip:
        raise HTTPException(status_code=404, detail=f"Clip #{clip_id} not found.")

    meta = clip.metadata_rel
    target_plats = ["youtube", "instagram", "facebook"] if (req.platform == "all" or not req.platform) else [req.platform.lower()]

    approved_count = 0
    for plat in target_plats:
        seo_rec = db.query(ClipSeoMetadata).filter_by(clip_id=clip_id, platform=plat).first()
        if seo_rec:
            seo_rec.approval_status = "approved"
            approved_count += 1

            # Sync to ClipMetadata so publishing publishers use the approved package
            if meta:
                if plat == "youtube":
                    if seo_rec.recommended_title:
                        meta.youtube_title = seo_rec.recommended_title[:100]
                    if seo_rec.description:
                        meta.youtube_desc = seo_rec.description
                elif plat == "instagram":
                    if seo_rec.recommended_caption:
                        meta.instagram_caption = seo_rec.recommended_caption
                elif plat == "facebook":
                    if seo_rec.description or seo_rec.recommended_caption:
                        meta.facebook_caption = seo_rec.description or seo_rec.recommended_caption

    db.commit()
    return {
        "status": "approved",
        "clip_id": clip_id,
        "platforms_approved": target_plats,
        "count": approved_count,
        "synced_to_publishing": True,
        "message": "SEO metadata approved and synchronized with publishing engine."
    }

@router.post("/seo/bulk/generate")
def bulk_generate_seo(req: SeoBulkGenerateRequest, db: Session = Depends(get_db)):
    """Batch generates SEO metadata packages across multiple clips."""
    from backend.app.models.schema import GeneratedClip
    from backend.app.metadata.seo_engine import seo_engine

    query = db.query(GeneratedClip).order_by(GeneratedClip.id.desc())
    if req.clip_ids:
        query = query.filter(GeneratedClip.id.in_(req.clip_ids))
    else:
        query = query.limit(20)

    clips = query.all()
    results = []
    errors = []

    for c in clips:
        try:
            res = seo_engine.generate_clip_seo(
                clip_id=c.id,
                db=db,
                target_platforms=req.platforms,
                bypass_cache=req.bypass_cache
            )
            results.append({"clip_id": c.id, "status": "generated"})
        except Exception as e:
            errors.append({"clip_id": c.id, "error": str(e)})

    return {
        "status": "completed",
        "total_requested": len(req.clip_ids) if req.clip_ids else len(clips),
        "generated_count": len(results),
        "failed_count": len(errors),
        "results": results,
        "errors": errors
    }

@router.post("/seo/bulk/approve")
def bulk_approve_seo(req: SeoBulkApproveRequest, db: Session = Depends(get_db)):
    """Batch approves SEO metadata packages across multiple clips."""
    from backend.app.models.schema import ClipSeoMetadata, GeneratedClip
    if not req.clip_ids:
        raise HTTPException(status_code=400, detail="clip_ids array is required.")

    target_plats = ["youtube", "instagram", "facebook"] if (req.platform == "all" or not req.platform) else [req.platform.lower()]
    total_approved = 0

    for cid in req.clip_ids:
        clip = db.query(GeneratedClip).filter_by(id=cid).first()
        if not clip:
            continue
        meta = clip.metadata_rel

        for plat in target_plats:
            seo_rec = db.query(ClipSeoMetadata).filter_by(clip_id=cid, platform=plat).first()
            if seo_rec:
                seo_rec.approval_status = "approved"
                total_approved += 1

                if meta:
                    if plat == "youtube":
                        if seo_rec.recommended_title:
                            meta.youtube_title = seo_rec.recommended_title[:100]
                        if seo_rec.description:
                            meta.youtube_desc = seo_rec.description
                    elif plat == "instagram":
                        if seo_rec.recommended_caption:
                            meta.instagram_caption = seo_rec.recommended_caption
                    elif plat == "facebook":
                        if seo_rec.description or seo_rec.recommended_caption:
                            meta.facebook_caption = seo_rec.description or seo_rec.recommended_caption

    db.commit()
    return {
        "status": "completed",
        "approved_count": total_approved,
        "approved_records": total_approved,
        "clips_count": len(req.clip_ids),
        "message": f"Successfully approved {total_approved} SEO platform entries."
    }

@router.get("/seo/keywords/research")
def research_keywords_endpoint(game_name: str, event_type: str = "", db: Session = Depends(get_db)):
    """Performs live keyword research and returns measured vs estimated phrases."""
    from backend.app.metadata.keyword_research import keyword_research_service
    res = keyword_research_service.research_keywords(game_name=game_name, event_type=event_type, db=db)
    return res

@router.get("/seo/growth/intelligence")
def get_growth_intelligence(db: Session = Depends(get_db)):
    """Returns AI growth insights and historical performance patterns."""
    from backend.app.analytics.growth_intelligence import growth_intelligence_service
    summary = growth_intelligence_service.get_dashboard_summary(db)
    return {
        "insights": summary.get("growth_insights", []),
        "top_keywords": summary.get("top_keywords", []),
        "best_games": summary.get("best_games", [])
    }

@router.post("/seo/growth/compute-insights")
@router.post("/seo/growth/compute-insights/")
@router.get("/seo/growth/compute-insights")
@router.get("/seo/growth/compute-insights/")
def trigger_compute_insights(db: Session = Depends(get_db)):
    """Computes fresh continuous learning growth insights from historical data (supports POST and GET)."""
    from backend.app.analytics.growth_intelligence import growth_intelligence_service
    insights = growth_intelligence_service.compute_continuous_learning_insights(db)
    return {
        "status": "completed",
        "insights_generated": len(insights),
        "message": "Continuous learning feedback loop executed successfully."
    }

@router.get("/seo/export")
def export_seo_metadata(format: str = "json", db: Session = Depends(get_db)):
    """Exports all generated SEO metadata records in JSON or CSV format."""
    from backend.app.models.schema import ClipSeoMetadata, GeneratedClip, ClipMetadata
    from fastapi.responses import PlainTextResponse

    records = db.query(ClipSeoMetadata).all()
    export_items = []
    for r in records:
        clip = r.clip
        meta = clip.metadata_rel if clip else None
        export_items.append({
            "clip_id": r.clip_id,
            "platform": r.platform,
            "game_name": meta.detected_game if meta else "Unknown Game",
            "recommended_title": r.recommended_title or "",
            "title_or_caption": r.recommended_title or r.recommended_caption,
            "description": r.description,
            "primary_keyword": r.primary_keyword,
            "seo_score": r.seo_score,
            "approval_status": r.approval_status,
            "ai_provider": r.ai_provider,
            "updated_at": r.updated_at.isoformat() if r.updated_at else ""
        })

    if format.lower() == "csv":
        import io
        import csv
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["clip_id", "platform", "game_name", "recommended_title", "title_or_caption", "description", "primary_keyword", "seo_score", "approval_status", "ai_provider", "updated_at"])
        writer.writeheader()
        for item in export_items:
            writer.writerow(item)
        return PlainTextResponse(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=gaming_uncut_seo_export.csv"})

    return export_items
