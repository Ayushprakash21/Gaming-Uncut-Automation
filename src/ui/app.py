"""
FastAPI Local Desktop Server & API for Gaming Uncut Automation.
Serves the control dashboard, handles file uploads, approvals, vault management,
and real-time publishing status.
"""

import os
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.core.config import settings
from src.core.database import get_connection, log_event, init_db
from src.core.security import vault
from src.ingestion.watcher import register_footage, IngestionWatcher
from src.ai.pipeline import ClipPipeline
from src.queue.dispatcher import QueueDispatcher

app = FastAPI(title="Gaming Uncut Automation Hub", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount renders and assets for video playback in the browser
app.mount("/renders", StaticFiles(directory=str(settings.RENDERS_DIR)), name="renders")
app.mount("/data", StaticFiles(directory=str(settings.DATA_DIR)), name="data")

pipeline = ClipPipeline()
dispatcher = QueueDispatcher()
watcher = IngestionWatcher()

@app.on_event("startup")
def startup_event():
    init_db()
    try:
        watcher.start()
    except Exception as e:
        print(f"Watcher start error: {e}")

@app.on_event("shutdown")
def shutdown_event():
    try:
        watcher.stop()
    except Exception:
        pass

# ==========================================
# DASHBOARD API ENDPOINTS
# ==========================================

@app.get("/api/status")
def get_status():
    """Returns dashboard KPIs, queue statistics, and platform connection states."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Today's published count
        today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
        cursor.execute("SELECT COUNT(*) as count FROM publishing_queue WHERE status = 'published' AND published_at >= ?", (today_start,))
        published_today = cursor.fetchone()["count"]

        # Queued
        cursor.execute("SELECT COUNT(*) as count FROM publishing_queue WHERE status = 'queued'")
        queued_count = cursor.fetchone()["count"]

        # Pending Approval
        cursor.execute("SELECT COUNT(*) as count FROM rendered_clips WHERE approval_status = 'pending'")
        pending_approvals = cursor.fetchone()["count"]

        # Total Raw Footage
        cursor.execute("SELECT COUNT(*) as count FROM raw_footage")
        raw_footage_count = cursor.fetchone()["count"]

    # Check OAuth / Vault state
    google_configured = vault.get_json_secret("google_client_config") is not None
    google_tokens = vault.get_json_secret("google_oauth_tokens") is not None
    meta_configured = vault.get_secret("meta_page_access_token") is not None
    ig_configured = vault.get_secret("meta_instagram_account_id") is not None

    return {
        "kpis": {
            "published_today": published_today,
            "daily_target": settings.DAILY_TARGET_CLIPS,
            "queued": queued_count,
            "pending_approvals": pending_approvals,
            "raw_footage_count": raw_footage_count,
            "workflow_mode": settings.WORKFLOW_MODE
        },
        "integrations": {
            "youtube": {
                "client_configured": google_configured,
                "authenticated": google_tokens
            },
            "instagram": {
                "configured": meta_configured and ig_configured
            },
            "facebook": {
                "configured": meta_configured
            }
        }
    }

@app.get("/api/clips")
def list_clips(status: Optional[str] = None):
    """Lists rendered 1080x1920 vertical clips."""
    with get_connection() as conn:
        cursor = conn.cursor()
        query = """
            SELECT c.*, m.score, m.start_sec, m.end_sec, f.filename as source_file
            FROM rendered_clips c
            JOIN detected_moments m ON c.moment_id = m.id
            JOIN raw_footage f ON m.footage_id = f.id
        """
        params = []
        if status:
            query += " WHERE c.approval_status = ?"
            params.append(status)
        query += " ORDER BY c.id DESC LIMIT 50"
        
        cursor.execute(query, params)
        rows = [dict(r) for r in cursor.fetchall()]

    for r in rows:
        # Generate relative web URLs for video and thumbnail
        if r.get("filepath"):
            r["video_url"] = f"/renders/{Path(r['filepath']).name}"
        if r.get("thumbnail_path"):
            r["thumbnail_url"] = f"/renders/{Path(r['thumbnail_path']).name}"

    return rows

@app.post("/api/clips/{clip_id}/approve")
def approve_clip(clip_id: int):
    """Approves a clip for publication."""
    with get_connection() as conn:
        conn.execute("UPDATE rendered_clips SET approval_status = 'approved' WHERE id = ?", (clip_id,))
        conn.commit()
    log_event("ui", "INFO", f"Clip #{clip_id} approved by user.")
    return {"status": "success", "message": f"Clip #{clip_id} approved"}

@app.post("/api/clips/{clip_id}/reject")
def reject_clip(clip_id: int):
    """Rejects a clip and cancels queued posts."""
    with get_connection() as conn:
        conn.execute("UPDATE rendered_clips SET approval_status = 'rejected' WHERE id = ?", (clip_id,))
        conn.execute("UPDATE publishing_queue SET status = 'cancelled' WHERE clip_id = ?", (clip_id,))
        conn.commit()
    log_event("ui", "INFO", f"Clip #{clip_id} rejected by user.")
    return {"status": "success", "message": f"Clip #{clip_id} rejected"}

@app.get("/api/queue")
def list_queue():
    """Lists publishing queue items with retry status and errors."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT q.*, c.title, c.filepath, c.approval_status
            FROM publishing_queue q
            JOIN rendered_clips c ON q.clip_id = c.id
            ORDER BY q.scheduled_time ASC, q.id DESC
            LIMIT 50
        """)
        return [dict(r) for r in cursor.fetchall()]

@app.post("/api/queue/dispatch")
def trigger_dispatch(background_tasks: BackgroundTasks):
    """Triggers immediate queue check & publishing dispatch."""
    background_tasks.add_task(dispatcher.process_pending_queue)
    return {"status": "started", "message": "Queue processor triggered"}

@app.post("/api/footage/upload")
async def upload_footage(file: UploadFile = File(...), background_tasks: BackgroundTasks = None):
    """Uploads gaming footage manually via the dashboard."""
    target_path = settings.WATCH_FOLDER / file.filename
    with open(target_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    footage_id = register_footage(target_path)
    if background_tasks:
        background_tasks.add_task(pipeline.process_raw_footage, footage_id)

    return {"status": "uploaded", "footage_id": footage_id, "filename": file.filename}

@app.post("/api/footage/{footage_id}/process")
def process_footage(footage_id: int, background_tasks: BackgroundTasks):
    """Triggers AI highlight analysis and clip rendering."""
    background_tasks.add_task(pipeline.process_raw_footage, footage_id)
    return {"status": "processing_started", "footage_id": footage_id}

@app.get("/api/logs")
def get_logs(limit: int = 50):
    """Returns recent audit events and error traces."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM system_logs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in cursor.fetchall()]

@app.post("/api/settings/import-google-secrets")
def import_google_secrets(path: str = Form(...)):
    """Imports Google OAuth desktop client_secrets.json securely into the vault."""
    p = Path(path.strip('\"\' '))
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"File not found at: {p}")
    try:
        res = vault.import_google_desktop_client(p)
        log_event("security", "INFO", "Imported Google Desktop Client secrets into vault.")
        return res
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/settings/save-meta-credentials")
def save_meta_credentials(
    app_id: str = Form(...),
    app_secret: str = Form(...),
    page_token: str = Form(...),
    ig_account_id: str = Form("")
):
    """Securely writes Meta Developer App credentials into the vault."""
    try:
        vault.import_meta_credentials(app_id, app_secret, page_token, ig_account_id)
        log_event("security", "INFO", "Saved Meta API credentials in vault.")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/settings/toggle-mode")
def toggle_mode(mode: str = Form(...)):
    """Switches between 'manual' (approval required) and 'auto' (fully autonomous)."""
    if mode not in ("manual", "auto"):
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'manual' or 'auto'.")
    settings.WORKFLOW_MODE = mode
    log_event("settings", "INFO", f"Switched workflow mode to: {mode}")
    return {"status": "success", "mode": settings.WORKFLOW_MODE}

# ==========================================
# DASHBOARD UI
# ==========================================

@app.get("/", response_class=HTMLResponse)
def index_page():
    """Renders the local control dashboard."""
    html_file = settings.BASE_DIR / "src" / "ui" / "index.html"
    if html_file.exists():
        return html_file.read_text(encoding="utf-8")
    return "<h1>Gaming Uncut Automation Dashboard</h1>"
