"""
Cloudflare Tunnel Supervisor for Gaming Uncut Automation.
Manages persistent, supervised connection between local NVENC processing node (FastAPI :8765)
and Cloudflare Pages edge deployment (gaming-uncut.pages.dev) via named tunnel and Workers VPC.

Features:
1. Supervised process management with automatic restart and zombie process cleanup.
2. Uses dedicated named Cloudflare Tunnel ('gaming-uncut-node', ID: 6acf38fe-7443-4798-9ec7-b67878b35da8).
3. Connects securely to Cloudflare Edge using credentials token stored in encrypted Windows vault.
4. Synchronizes live backend URL (https://gaming-uncut-backend.gaminguncut1.workers.dev) to Cloudflare Pages & Workers KV.
5. Strict validation rejects all ephemeral trycloudflare.com URLs.
6. Manages shared INTERNAL_API_SECRET for secure API proxy communication.
7. Emits health telemetry and persistent status to data/tunnel_status.json.
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
KV_NAMESPACE_ID = "cd43c79a79d749f98a9f456997b565b9"
WRANGLER_CONFIG_PATH = Path(os.environ.get("APPDATA", "")) / "xdg.config" / ".wrangler" / "config" / "gaming-uncut.toml"
PERMANENT_BACKEND_URL = "https://gaming-uncut-backend.gaminguncut1.workers.dev"


class CloudflareTunnelSupervisor:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._running = False
        self._current_url: Optional[str] = PERMANENT_BACKEND_URL
        self._tunnel_mode = "named"
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

    def get_named_tunnel_token(self) -> Optional[str]:
        """Retrieves tunnel token from vault or fetches from Cloudflare API if absent."""
        token = vault.get_secret("cloudflare", "named_tunnel_token")
        if token:
            return token

        api_token = self.get_api_token()
        if not api_token:
            logger.warning("No Cloudflare API token to fetch named tunnel token.")
            return None

        url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/{NAMED_TUNNEL_ID}/token"
        headers = {"Authorization": f"Bearer {api_token}"}
        try:
            resp = requests.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                fetched_token = data.get("result")
                if fetched_token:
                    vault.set_secret("cloudflare", "named_tunnel_token", fetched_token)
                    logger.info("Successfully fetched named tunnel token and persisted to secure vault.")
                    return fetched_token
        except Exception as e:
            logger.error(f"Error fetching named tunnel token from Cloudflare API: {e}")

        return None

    def get_or_create_internal_secret(self) -> str:
        """Retrieves or creates a secure 32-byte secret for backend API authentication."""
        existing = vault.get_secret("system", "internal_api_secret")
        if existing:
            return existing
        new_secret = secrets.token_hex(32)
        vault.set_secret("system", "internal_api_secret", new_secret)
        logger.info("Generated new cryptographically secure INTERNAL_API_SECRET in vault.")
        return new_secret

    def sync_to_kv(self, backend_url: str, secret: Optional[str] = None) -> bool:
        """Synchronizes live backend URL and internal API secret to Cloudflare Workers KV for real-time edge routing."""
        token = self.get_api_token()
        if not token:
            logger.warning("No Cloudflare token available to sync with KV.")
            return False

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain"
        }
        base_url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values"

        success = True
        try:
            r1 = requests.put(f"{base_url}/backend_url", headers=headers, data=backend_url, timeout=5.0)
            if r1.status_code != 200:
                logger.warning(f"Failed to update KV backend_url: {r1.status_code} {r1.text[:100]}")
                success = False
            else:
                logger.info(f"Synchronized KV backend_url -> {backend_url}")

            if secret:
                r2 = requests.put(f"{base_url}/internal_api_secret", headers=headers, data=secret, timeout=5.0)
                if r2.status_code != 200:
                    logger.warning(f"Failed to update KV internal_api_secret: {r2.status_code}")
                    success = False
                else:
                    logger.info("Synchronized KV internal_api_secret.")
        except Exception as e:
            logger.error(f"Error syncing to Cloudflare KV: {e}")
            success = False

        return success

    def update_pages_backend_url(self, backend_url: str) -> bool:
        """
        Updates Cloudflare Pages project environment variables and KV with the backend URL.
        Enforces strict production validation: rejects trycloudflare.com and non-HTTPS URLs.
        """
        # Strict URL validation
        if not backend_url or "trycloudflare.com" in backend_url:
            logger.error(f"Rejected invalid/ephemeral backend URL: {backend_url}")
            return False

        if not backend_url.startswith("https://"):
            logger.error(f"Rejected non-HTTPS backend URL: {backend_url}")
            return False

        secret = self.get_or_create_internal_secret()
        # 1. Update real-time KV namespace first
        self.sync_to_kv(backend_url, secret)

        token = self.get_api_token()
        if not token:
            logger.warning("No Cloudflare token available to update Pages environment variables.")
            return False

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

    def write_status(self, is_alive: bool, url: Optional[str] = None, mode: str = "named", details: str = ""):
        """Writes current tunnel status to disk for dashboard diagnostics without leaking credentials."""
        data = {
            "is_alive": is_alive,
            "url": url or PERMANENT_BACKEND_URL,
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
        """Starts the supervised named tunnel worker in a background thread."""
        with self._lock:
            if self._running:
                return
            self._running = True

        def _worker():
            logger.info("Cloudflare Tunnel Supervisor started.")

            # Terminate any lingering cloudflared processes on Windows
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            token = self.get_named_tunnel_token()
            if not token:
                logger.error("Failed to retrieve named tunnel token. Tunnel cannot start.")
                self.write_status(False, None, mode="named", details="Missing named tunnel token")
                return

            cmd = ["cloudflared", "tunnel", "run", "--token", token]
            self._current_url = PERMANENT_BACKEND_URL
            self._tunnel_mode = "named"

            while self._running:
                try:
                    logger.info(f"Launching cloudflared (named tunnel: {NAMED_TUNNEL_NAME})...")
                    self._process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    # Update status and sync configuration
                    self.write_status(True, self._current_url, mode="named", details="Tunnel process running")
                    self.update_pages_backend_url(self._current_url)

                    while self._running and self._process.poll() is None:
                        line = self._process.stdout.readline() if self._process.stdout else ""
                        if line:
                            clean_line = line.strip()
                            if "Registered tunnel connection" in clean_line or "Initial protocol quic" in clean_line:
                                logger.info(f"Tunnel edge connection active: {clean_line}")
                                self.write_status(True, self._current_url, mode="named", details="Connected to Cloudflare Edge")

                        time.sleep(0.1)

                except Exception as e:
                    logger.error(f"Tunnel supervisor exception: {e}")
                    self.write_status(False, None, details=str(e))

                if self._running:
                    logger.warning("Tunnel process exited. Restarting in 5 seconds...")
                    self.write_status(False, None, mode="named", details="Tunnel disconnected, reconnecting...")
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

            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/IM", "cloudflared.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        self.sync_to_kv("offline")
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
            "mode": self._tunnel_mode,
            "backend_target": f"http://127.0.0.1:{settings.PORT}"
        }


tunnel_supervisor = CloudflareTunnelSupervisor()
