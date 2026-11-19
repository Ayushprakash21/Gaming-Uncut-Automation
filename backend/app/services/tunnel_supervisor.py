"""
Cloudflare Tunnel Supervisor for Gaming Uncut Automation.
Manages persistent, supervised connection between local NVENC processing node (FastAPI :8765)
and Cloudflare Pages edge deployment (gaming-uncut.pages.dev).

Features:
1. Supervised process management with automatic restart.
2. Supports named Cloudflare Tunnel ('gaming-uncut-node') when custom domain zone is present.
3. Automatically establishes high-performance tunnel to local port and synchronizes Cloudflare Pages BACKEND_URL.
4. Generates and manages shared INTERNAL_API_SECRET for secure API proxy communication.
5. Emits health telemetry and persistent status to data/tunnel_status.json.
"""

import os
import re
import sys
import time
import json
import logging
import secrets
import threading
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

import requests
import tomllib

from backend.app.core.config import settings
from backend.app.security.vault import vault

logger = logging.getLogger("gaming_uncut.tunnel_supervisor")

STATUS_FILE = settings.DATA_DIR / "tunnel_status.json"
NAMED_TUNNEL_NAME = "gaming-uncut-node"
NAMED_TUNNEL_ID = "6acf38fe-7443-4798-9ec7-b67878b35da8"
CLOUDFLARE_ACCOUNT_ID = "57ecb7a13c0e7f67e63dc5564ad2dfe6"
PAGES_PROJECT_NAME = "gaming-uncut"
WRANGLER_CONFIG_PATH = Path(os.environ.get("APPDATA", "")) / "xdg.config" / ".wrangler" / "config" / "gaming-uncut.toml"


class CloudflareTunnelSupervisor:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._running = False
        self._current_url: Optional[str] = None
        self._tunnel_mode = "managed"
        self._supervisor_thread: Optional[threading.Thread] = None

    def get_api_token(self) -> Optional[str]:
        """Reads Cloudflare OAuth or API token from local Wrangler config."""
        if WRANGLER_CONFIG_PATH.exists():
            try:
                with open(WRANGLER_CONFIG_PATH, "rb") as f:
                    cfg = tomllib.load(f)
                    return cfg.get("oauth_token")
            except Exception as e:
                logger.warning(f"Could not load Wrangler config: {e}")
        return os.environ.get("CLOUDFLARE_API_TOKEN")

    def get_or_create_internal_secret(self) -> str:
        """Retrieves or creates a secure 32-byte secret for backend API authentication."""
        existing = vault.get_secret("system", "internal_api_secret")
        if existing:
            return existing
        new_secret = secrets.token_hex(32)
        vault.set_secret("system", "internal_api_secret", new_secret)
        logger.info("Generated new cryptographically secure INTERNAL_API_SECRET in vault.")
        return new_secret

    def update_pages_backend_url(self, backend_url: str) -> bool:
        """Updates Cloudflare Pages project environment variables with the live backend URL."""
        token = self.get_api_token()
        if not token:
            logger.warning("No Cloudflare token available to update Pages environment variables.")
            return False

        secret = self.get_or_create_internal_secret()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/pages/projects/{PAGES_PROJECT_NAME}"
        payload = {
            "deployment_configs": {
                "production": {
                    "env_vars": {
                        "BACKEND_URL": {"type": "plain_text", "value": backend_url},
                        "INTERNAL_API_SECRET": {"type": "secret_text", "value": secret}
                    }
                }
            }
        }
        try:
            resp = requests.patch(url, headers=headers, json=payload, timeout=10.0)
            if resp.status_code == 200:
                logger.info(f"Successfully synchronized Cloudflare Pages BACKEND_URL to {backend_url}")
                return True
            else:
                logger.warning(f"Failed to update Pages env vars: {resp.status_code} - {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"Error updating Cloudflare Pages env vars: {e}")
            return False

    def write_status(self, is_alive: bool, url: Optional[str] = None, mode: str = "managed", details: str = ""):
        """Writes current tunnel status to disk for dashboard diagnostics."""
        data = {
            "is_alive": is_alive,
            "url": url,
            "tunnel_id": NAMED_TUNNEL_ID,
            "tunnel_name": NAMED_TUNNEL_NAME,
            "mode": mode,
            "backend_target": f"http://127.0.0.1:{settings.PORT}",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S IST"),
            "details": details
        }
        try:
            STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def start_supervised(self):
        """Starts the supervised tunnel worker in a background thread."""
        with self._lock:
            if self._running:
                return
            self._running = True

        def _worker():
            logger.info("Cloudflare Tunnel Supervisor started.")
            custom_domain = os.environ.get("CLOUDFLARE_CUSTOM_DOMAIN")

            while self._running:
                try:
                    if custom_domain:
                        token = "eyJhIjoiNTdlY2I3YTEzYzBlN2Y2N2U2M2RjNTU2NGFkMmRmZTYiLCJ0IjoiNmFjZjM4ZmUtNzQ0My00Nzk4LTllYzctYjY3ODc4YjM1ZGE4IiwicyI6IjYrVFoxMDM4Z2FLZW5BclZkd2RtalBvcnc1UUxzZmVPeDZFV3RsVEJydHM9In0="
                        cmd = ["cloudflared", "tunnel", "run", "--token", token]
                        self._current_url = f"https://{custom_domain}"
                        self._tunnel_mode = "named"
                        mode = "named"
                    else:
                        cmd = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{settings.PORT}"]
                        self._tunnel_mode = "auto_managed"
                        mode = "auto_managed"

                    logger.info(f"Launching cloudflared ({mode})...")
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    url_synced = False
                    start_time = time.time()

                    while self._running and self._process.poll() is None:
                        line = self._process.stdout.readline() if self._process.stdout else ""
                        if line:
                            # Look for assigned tunnel URL
                            if not url_synced and not custom_domain:
                                match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                                if match:
                                    self._current_url = match.group(0)
                                    logger.info(f"Detected Cloudflare Tunnel URL: {self._current_url}")
                                    self.write_status(True, self._current_url, mode=mode, details="Tunnel connected")
                                    self.update_pages_backend_url(self._current_url)
                                    url_synced = True

                        time.sleep(0.1)

                except Exception as e:
                    logger.error(f"Tunnel supervisor exception: {e}")
                    self.write_status(False, None, details=str(e))

                if self._running:
                    logger.warning("Tunnel process exited. Restarting in 5 seconds...")
                    time.sleep(5)

            self.write_status(False, None, details="Supervisor stopped cleanly.")

        self._supervisor_thread = threading.Thread(target=_worker, daemon=True, name="cloudflare-tunnel-supervisor")
        self._supervisor_thread.start()

    def stop(self):
        """Stops the supervised tunnel cleanly."""
        with self._lock:
            self._running = False
            if self._process:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=3)
                except Exception:
                    pass
                self._process = None
        self.write_status(False, None, details="Stopped")
        logger.info("Cloudflare Tunnel Supervisor stopped.")

    def get_status(self) -> Dict[str, Any]:
        """Returns the live status of the tunnel."""
        if STATUS_FILE.exists():
            try:
                return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "is_alive": self._running and self._process is not None and self._process.poll() is None,
            "url": self._current_url,
            "tunnel_id": NAMED_TUNNEL_ID,
            "tunnel_name": NAMED_TUNNEL_NAME,
            "backend_target": f"http://127.0.0.1:{settings.PORT}"
        }


tunnel_supervisor = CloudflareTunnelSupervisor()
