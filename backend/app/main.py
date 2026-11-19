"""
Gaming Uncut Automation: Main FastAPI Server Application.
Initializes Database, Scheduler, CORS, Static Media Mounts, and REST API.
"""

from pathlib import Path
import socket
import logging
from fastapi import FastAPI, Response, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.app.core.config import settings
from backend.app.database.session import engine, Base, ensure_schema_compatibility
from backend.app.api.router import router as api_router
from backend.app.scheduler.manager import task_scheduler
from backend.app.services.pipeline_service import pipeline_service
from backend.app.video.worker import video_worker
from backend.app.video.ffmpeg_executor import cleanup_orphaned_ffmpeg_processes

logger = logging.getLogger("gaming_uncut.api")

# Create tables and guarantee column compatibility
Base.metadata.create_all(bind=engine)
ensure_schema_compatibility(engine)

app = FastAPI(
    title=settings.APP_NAME,
    description="NO FILTER. JUST GAMEPLAY. Autonomous AI Gaming Video Production & Multi-Platform Dispatch.",
    version=settings.VERSION
)

# Standardized Error Code Mapping
HTTP_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    408: "REQUEST_TIMEOUT",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_SERVER_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}

@app.exception_handler(StarletteHTTPException)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code_str = HTTP_STATUS_CODES.get(exc.status_code, f"HTTP_{exc.status_code}")
    msg = str(exc.detail) if exc.detail else "An error occurred."
    details = None
    if isinstance(exc.detail, dict):
        msg = exc.detail.get("message") or exc.detail.get("msg") or str(exc.detail)
        details = exc.detail.get("details")

    # If it's a non-API route with GET on missing page, let SPA serve index.html if available
    if not request.url.path.startswith("/api") and exc.status_code == 404 and request.method == "GET":
        f_dist = settings.ROOT_DIR / "frontend" / "dist"
        if f_dist.exists() and (f_dist / "index.html").exists():
            return HTMLResponse((f_dist / "index.html").read_text(encoding="utf-8"))

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": code_str,
                "message": msg,
                "details": details
            },
            "detail": msg
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    err_msgs = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        m = err.get("msg", "Invalid value")
        err_msgs.append(f"{loc}: {m}" if loc else m)

    clean_msg = "Validation failed: " + "; ".join(err_msgs) if err_msgs else "Invalid request data."
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": clean_msg,
                "details": err_msgs
            },
            "detail": clean_msg
        }
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server exception processing %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An internal server error occurred.",
                "details": None
            },
            "detail": "An internal server error occurred."
        }
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

# API Security Middleware: Protect administrative & publishing actions from unauthorized remote access
@app.middleware("http")
async def verify_internal_secret_middleware(request: Request, call_next):
    # Allow preflight OPTIONS
    if request.method == "OPTIONS":
        return await call_next(request)

    # Allow public endpoints (health check, diagnostics, media serving, and UI)
    path = request.url.path
    if (
        path in ("/api/health", "/api/system/health", "/api/diagnostics/system", "/api/system/diagnostics")
        or path.startswith("/media/")
        or path in ("/", "/ui", "/standalone")
    ):
        return await call_next(request)

    # Check if request originates from localhost loopback directly (not via remote tunnel)
    is_tunnel = bool(request.headers.get("cf-ray") or request.headers.get("cf-connecting-ip"))
    client_host = request.client.host if request.client else ""
    is_loopback = (client_host in ("127.0.0.1", "::1", "localhost", "testclient")) and not is_tunnel

    # If from remote tunnel/internet, check internal secret for mutating administrative endpoints
    from backend.app.security.vault import vault
    import os
    internal_secret = vault.get_secret("system", "internal_api_secret") or os.getenv("INTERNAL_API_SECRET")

    if internal_secret and not is_loopback:
        # Require authentication for state-changing or publishing actions
        if request.method in ("POST", "PUT", "DELETE", "PATCH") or "/publishing/" in path or "/settings/" in path:
            provided = request.headers.get("X-Internal-Secret") or request.headers.get("x-internal-secret")
            if not provided or provided != internal_secret:
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "error": {
                            "code": "UNAUTHORIZED",
                            "message": "Missing or invalid internal API authentication secret."
                        },
                        "detail": "Unauthorized: remote administrative access requires authentication."
                    }
                )

    return await call_next(request)

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
    video_worker.start()
    task_scheduler.start()
    try:
        pipeline_service.detect_and_recover_stalled_jobs()
    except Exception as e:
        logger.warning(f"Startup stalled job recovery notice: {e}")
    try:
        cleanup_orphaned_ffmpeg_processes()
    except Exception as e:
        logger.debug(f"Startup orphan cleanup notice: {e}")

@app.on_event("shutdown")
def on_shutdown():
    video_worker.stop()
    task_scheduler.shutdown()
