"""
Gaming Uncut Automation — Master Application Launcher.
Brand: Gaming Uncut | Tagline: NO FILTER. JUST GAMEPLAY.
Launches the complete unified platform:
- Persistent SQLite database & SQLAlchemy models
- APScheduler persistent background queue
- Encrypted Windows Credential Manager vault (auto-detects Google OAuth client)
- NVENC hardware-accelerated video rendering engine
- Built React + TypeScript + Tailwind SPA Dashboard
"""

import sys
import os
import webbrowser
import logging
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

import uvicorn
from backend.app.core.config import settings
from backend.app.database.session import engine, Base
from backend.app.security.vault import vault

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(settings.LOGS_DIR / "app.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("gaming_uncut.main")

def auto_detect_google_credentials():
    """Checks if client_secrets.json is in user's Downloads and imports into vault if missing."""
    if not vault.get_json("google", "client_config"):
        downloads_path = Path.home() / "Downloads" / "client_secrets.json"
        if downloads_path.exists():
            try:
                res = vault.import_google_client_secrets(downloads_path)
                logger.info(f"Automatically imported Google Desktop OAuth client ({res['project_id']}) into secure vault.")
            except Exception as e:
                logger.warning(f"Could not auto-import Google secrets: {e}")

def get_local_ip() -> str:
    """Detects the primary outbound IPv4 address of this machine."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def main():
    logger.info("Initializing Gaming Uncut Automation...")
    logger.info("Tagline: NO FILTER. JUST GAMEPLAY.")
    
    # 1. Initialize DB tables
    Base.metadata.create_all(bind=engine)
    
    # 2. Ingest credentials if available
    auto_detect_google_credentials()
    
    local_ip = get_local_ip()
    local_url = f"http://localhost:{settings.PORT}"
    network_url = f"http://{local_ip}:{settings.PORT}"

    logger.info("==================================================================")
    logger.info("   GAMING UNCUT AUTOMATION — NO FILTER. JUST GAMEPLAY.")
    logger.info("   Server is LIVE and accessible on your local network:")
    logger.info(f"   -> Local Machine:   {local_url}")
    logger.info(f"   -> Network IP URL:  {network_url}")
    logger.info(f"   -> Standalone UI:   {network_url}/ui")
    logger.info(f"   -> API Health:      {network_url}/api/health")
    logger.info("==================================================================")
    
    # 3. Auto open browser to dashboard
    try:
        webbrowser.open(local_url)
    except Exception:
        pass

    # 4. Run FastAPI app binding to all interfaces (0.0.0.0)
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=settings.PORT, reload=False, log_level="info")

if __name__ == "__main__":
    main()
