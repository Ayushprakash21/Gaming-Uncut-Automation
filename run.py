"""
Gaming Uncut Automation: Main Application Launcher.
Initializes secure storage, database, file watchers, and runs the local dashboard.
"""

import sys
import os
import webbrowser
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from src.core.config import settings
from src.core.database import init_db, log_event
from src.core.security import vault

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
    if not vault.get_json_secret("google_client_config"):
        downloads_path = Path.home() / "Downloads" / "client_secrets.json"
        if downloads_path.exists():
            try:
                res = vault.import_google_desktop_client(downloads_path)
                logger.info(f"Automatically imported Google Desktop OAuth client ({res['project_id']}) into secure vault.")
            except Exception as e:
                logger.warning(f"Could not auto-import Google secrets: {e}")

def main():
    logger.info("Starting Gaming Uncut Automation...")
    init_db()
    auto_detect_google_credentials()
    
    dashboard_url = f"http://{settings.HOST}:{settings.PORT}"
    logger.info(f"Dashboard available at: {dashboard_url}")
    
    # Auto open browser
    try:
        webbrowser.open(dashboard_url)
    except Exception:
        pass

    uvicorn.run("src.ui.app:app", host=settings.HOST, port=settings.PORT, reload=False, log_level="info")

if __name__ == "__main__":
    main()
