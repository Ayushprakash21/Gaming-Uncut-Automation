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
    PublishingJob, PlatformUpload, PublishingAnalytics, SystemLog, CredentialsMetadata
)
from backend.app.ingestion.adapters import ingestion_manager
from backend.app.services.pipeline_service import pipeline_service
from backend.app.publishers.manager import publishing_manager
from backend.app.security.vault import vault

logger = logging.getLogger("gaming_uncut.api")
router = APIRouter(prefix="/api")

# Schemas
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
            "published_today": today_published,
            "failed_count": failed_count,
            "pending_queue": pending_queue,
            "pending_approvals": pending_queue,
            "queued": pending_queue,
            "today_published": today_published,
            "daily_target": settings.DAILY_TARGET_CLIPS,
            "target_progress_pct": min(100, round((today_published / settings.DAILY_TARGET_CLIPS) * 100)),
            "workflow_mode": "manual"
        },
        "platforms": {
            "youtube": {
                "connected": yt_ready,
                "status": "Connected" if yt_ready else ("Not Connected (Channel Auth Required)" if vault.get_json("google", "client_config") else "Not Connected"),
                "quota_used_today": today_published * 1600
            },
            "instagram": {"connected": meta_ready and ig_ready},
            "facebook": {"connected": meta_ready},
            "gemini": {"connected": gemini_ready}
        },
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
    clean_url = req.url.strip()
    source_video = db.query(SourceVideo).filter(
        (SourceVideo.url == clean_url) | (SourceVideo.local_path == clean_url)
    ).first()
    if not source_video:
        source_video = SourceVideo(
            url=clean_url,
            title=Path(clean_url).stem if not clean_url.startswith("http") else f"Source {abs(hash(clean_url)) % 100000}",
            duration=float(req.target_duration),
            width=1920,
            height=1080,
            fps=30.0,
            format="mp4",
            local_path=clean_url,
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
        url_or_path=req.url.strip(),
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
        "url": req.url,
        "template": req.template
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
    yt_client_cfg = vault.get_json("google", "client_config") is not None
    yt_auth = vault.get_json("google", "oauth_tokens") is not None
    meta_app = vault.get_secret("meta", "app_id") is not None
    meta_token = vault.get_secret("meta", "page_access_token") is not None
    ig_account = vault.get_secret("meta", "instagram_account_id")
    ig_configured = bool(meta_token and ig_account)
    gemini_key = vault.get_gemini_api_key() is not None

    return {
        "youtube": {
            "client_configured": yt_client_cfg,
            "authenticated": yt_auth,
            "connected": yt_auth,  # Explicitly false until OAuth tokens/channel verified
            "status": "connected" if yt_auth else ("client_ready_auth_needed" if yt_client_cfg else "not_configured"),
            "status_label": "Connected" if yt_auth else ("Not Connected (Channel Auth Required)" if yt_client_cfg else "Not Connected"),
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
