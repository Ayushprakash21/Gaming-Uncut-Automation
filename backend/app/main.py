"""
Gaming Uncut Automation: Main FastAPI Server Application.
Initializes Database, Scheduler, CORS, Static Media Mounts, and REST API.
"""

from pathlib import Path
import socket
from fastapi import FastAPI, Response, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse

from backend.app.core.config import settings
from backend.app.database.session import engine, Base, ensure_schema_compatibility
from backend.app.api.router import router as api_router
from backend.app.scheduler.manager import task_scheduler
from backend.app.services.pipeline_service import pipeline_service

# Create tables and guarantee column compatibility
Base.metadata.create_all(bind=engine)
ensure_schema_compatibility(engine)

app = FastAPI(
    title=settings.APP_NAME,
    description="NO FILTER. JUST GAMEPLAY. Autonomous AI Gaming Video Production & Multi-Platform Dispatch.",
    version=settings.VERSION
)

# CORS Policy: Restrict to local frontend origins
allowed_origins = [
    "http://localhost:8765",
    "http://127.0.0.1:8765",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
if settings.ENABLE_LAN_ACCESS or settings.HOST == "0.0.0.0":
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        allowed_origins.extend([
            f"http://{local_ip}:{settings.PORT}",
            f"http://{local_ip}:5173"
        ])
    except Exception:
        pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins if not (settings.ENABLE_LAN_ACCESS or settings.HOST == "0.0.0.0") else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Media Mounts for local video playback
app.mount("/media/output", StaticFiles(directory=str(settings.OUTPUT_DIR)), name="output_media")
app.mount("/media/thumbnails", StaticFiles(directory=str(settings.THUMBNAILS_DIR)), name="thumbnails_media")
app.mount("/media/input", StaticFiles(directory=str(settings.INPUT_DIR)), name="input_media")

# Include master API router
app.include_router(api_router)

# Mount standalone UI directly
dashboard_html = settings.ROOT_DIR / "src" / "ui" / "index.html"
if not dashboard_html.exists():
    dashboard_html = settings.BACKEND_DIR / "app" / "ui" / "index.html"

@app.get("/ui", response_class=HTMLResponse)
@app.get("/standalone", response_class=HTMLResponse)
def serve_standalone_dashboard(response: Response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    if dashboard_html.exists():
        return dashboard_html.read_text(encoding="utf-8")
    return "<h1>Standalone UI template not found</h1>"

# Mount frontend if built
frontend_dist = settings.ROOT_DIR / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="frontend_assets")

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"], response_class=HTMLResponse)
    def serve_frontend_spa(full_path: str, request: Request, response: Response):
        # Prevent API or media routes from falling through to the SPA catch-all
        if full_path.startswith("api/") or full_path == "api" or full_path.startswith("media/"):
            raise HTTPException(status_code=404, detail=f"API route not found: /{full_path}")

        if request.method != "GET":
            raise HTTPException(status_code=405, detail=f"Method {request.method} not allowed for SPA route: /{full_path}")

        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        index_file = frontend_dist / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8")
        return "<h1>Frontend build not found</h1>"
else:
    # Fallback to local dashboard template
    dashboard_html = settings.BACKEND_DIR / "app" / "ui" / "index.html"
    if not dashboard_html.exists():
        dashboard_html = settings.ROOT_DIR / "src" / "ui" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def index_root(response: Response):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        if dashboard_html.exists():
            return dashboard_html.read_text(encoding="utf-8")
        return f"<h1>{settings.APP_NAME} Backend Running</h1>"

@app.on_event("startup")
def on_startup():
    task_scheduler.start()
    try:
        pipeline_service.detect_and_recover_stalled_jobs()
    except Exception as e:
        import logging
        logging.getLogger("gaming_uncut").warning(f"Startup stalled job recovery notice: {e}")

@app.on_event("shutdown")
def on_shutdown():
    task_scheduler.shutdown()
