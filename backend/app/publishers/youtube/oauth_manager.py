"""
Gaming Uncut Automation — Dedicated YouTube Desktop OAuth 2.0 Session Manager.
Brand: Gaming Uncut | Tagline: NO FILTER. JUST GAMEPLAY.

Handles Google Desktop OAuth 2.0 lifecycle:
1. Ephemeral localhost loopback server on 127.0.0.1 / localhost.
2. Generates authorization URL with CSRF state protection.
3. Attempts default Windows browser launch with fallback to explicit frontend links.
4. Handles Google callback:
   - CSRF state verification
   - User denial / cancellation handling (e.g. ?error=access_denied)
   - Code exchange for access and refresh tokens
   - Branded in-browser feedback (success and cancellation HTML)
   - Asset filtering (e.g. /favicon.ico)
5. Immediate YouTube Data API channels.list verification.
6. Encrypted Windows Vault credential persistence.
7. Safe timeout, cancellation, and socket cleanup (prevents orphan ports).
"""

import sys
import os
import json
import time
import socket
import logging
import threading
import webbrowser
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlparse, parse_qs
from http.server import HTTPServer, BaseHTTPRequestHandler

from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

from backend.app.security.vault import vault

logger = logging.getLogger("gaming_uncut.youtube_oauth")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly"
]

SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Gaming Uncut — Authorization Successful</title>
  <style>
    body {
      margin: 0;
      padding: 60px 20px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background-color: #0b0f19;
      color: #f8fafc;
      text-align: center;
    }
    .card {
      max-width: 520px;
      margin: 0 auto;
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 16px;
      padding: 40px 30px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }
    .badge {
      display: inline-block;
      padding: 6px 14px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      background: rgba(168, 85, 247, 0.15);
      color: #c084fc;
      border: 1px solid rgba(168, 85, 247, 0.3);
      border-radius: 9999px;
      margin-bottom: 20px;
    }
    h1 {
      font-size: 22px;
      margin: 0 0 8px 0;
      color: #ffffff;
      font-weight: 800;
    }
    .tagline {
      font-size: 12px;
      color: #94a3b8;
      letter-spacing: 0.08em;
      margin-bottom: 24px;
    }
    .success-box {
      background: rgba(34, 197, 94, 0.1);
      border: 1px solid rgba(34, 197, 94, 0.3);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 24px;
    }
    .success-text {
      color: #4ade80;
      font-size: 15px;
      font-weight: 600;
      margin: 0;
    }
    p {
      color: #94a3b8;
      font-size: 13px;
      line-height: 1.6;
      margin: 0 0 20px 0;
    }
    .close-hint {
      color: #64748b;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">Gaming Uncut Automation</div>
    <h1>Authorization Granted</h1>
    <div class="tagline">NO FILTER. JUST GAMEPLAY.</div>
    <div class="success-box">
      <p class="success-text">&#10003; Google consent received successfully!</p>
    </div>
    <p>Gaming Uncut is completing YouTube channel verification in the dashboard.</p>
    <div class="close-hint">You may safely close this browser window and return to the application.</div>
  </div>
</body>
</html>
"""

CANCELLED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Gaming Uncut — Authorization Cancelled</title>
  <style>
    body {
      margin: 0;
      padding: 60px 20px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background-color: #0b0f19;
      color: #f8fafc;
      text-align: center;
    }
    .card {
      max-width: 520px;
      margin: 0 auto;
      background: #111827;
      border: 1px solid #1f2937;
      border-radius: 16px;
      padding: 40px 30px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }
    .badge {
      display: inline-block;
      padding: 6px 14px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      background: rgba(244, 63, 94, 0.15);
      color: #fb7185;
      border: 1px solid rgba(244, 63, 94, 0.3);
      border-radius: 9999px;
      margin-bottom: 20px;
    }
    h1 {
      font-size: 22px;
      margin: 0 0 8px 0;
      color: #ffffff;
      font-weight: 800;
    }
    .cancel-box {
      background: rgba(244, 63, 94, 0.1);
      border: 1px solid rgba(244, 63, 94, 0.3);
      border-radius: 12px;
      padding: 16px;
      margin: 20px 0;
    }
    .cancel-text {
      color: #f87171;
      font-size: 14px;
      font-weight: 600;
      margin: 0;
    }
    p {
      color: #94a3b8;
      font-size: 13px;
      line-height: 1.6;
      margin: 0 0 20px 0;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">Google Authorization</div>
    <h1>Authorization Cancelled</h1>
    <div class="cancel-box">
      <p class="cancel-text">Google consent was cancelled or access was denied.</p>
    </div>
    <p>No permissions were granted. You can return to Gaming Uncut Automation and try again.</p>
  </div>
</body>
</html>
"""


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """
    HTTP Request Handler for Google Desktop OAuth 2.0 loopback redirect.
    Suppresses request logging to protect credentials and tokens.
    """

    def log_message(self, format, *args):
        # Explicitly suppress access logs to never write codes or tokens to stdout/stderr
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 1. Ignore favicon requests gracefully
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # 2. Match loopback path
        if path not in ("/", "/oauth2callback"):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")
            return

        session_mgr = getattr(self.server, "session_manager", None)
        if not session_mgr:
            self.send_response(500)
            self.end_headers()
            return

        expected_state = session_mgr.current_state

        req_state = query.get("state", [None])[0]
        req_error = query.get("error", [None])[0]
        req_code = query.get("code", [None])[0]

        # 3. Check for User Denial / Error
        if req_error:
            logger.warning(f"Google OAuth consent cancelled or denied: {req_error}")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(CANCELLED_HTML.encode("utf-8"))
            session_mgr.record_cancelled(f"Authorization cancelled by user ({req_error}).")
            self._schedule_shutdown()
            return

        # 4. Validate CSRF state
        if not req_state or req_state != expected_state:
            logger.error("OAuth state mismatch! Potential CSRF attack detected.")
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"400 Bad Request: State mismatch (CSRF protection failed).")
            session_mgr.record_failed("CSRF State mismatch: Authorization request could not be validated.")
            self._schedule_shutdown()
            return

        # 5. Check authorization code
        if not req_code:
            logger.error("No authorization code provided in Google OAuth callback.")
            self.send_response(400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"400 Bad Request: No authorization code received.")
            session_mgr.record_failed("No authorization code was returned by Google.")
            self._schedule_shutdown()
            return

        # 6. Valid authorization code received — respond with branded success page
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(SUCCESS_HTML.encode("utf-8"))

        # Transition session to verifying_channel and exchange code asynchronously
        session_mgr.process_authorization_code(req_code)
        self._schedule_shutdown()

    def _schedule_shutdown(self):
        """Schedules server shutdown in a separate thread to prevent deadlocks."""
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class YouTubeOAuthManager:
    """
    Singleton Manager for YouTube Desktop OAuth 2.0 flow.
    Provides non-blocking startup, live status polling, and verification.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[threading.Thread] = None
        self._flow: Optional[InstalledAppFlow] = None
        self._current_state: Optional[str] = None
        
        # Session state dictionary
        self._session: Dict[str, Any] = {
            "status": "idle",  # idle | opening | waiting_for_consent | verifying_channel | connected | failed | cancelled | timed_out
            "auth_url": None,
            "browser_opened": False,
            "expires_at": 0,
            "error": None,
            "channel": None,
            "started_at": 0
        }

    @property
    def current_state(self) -> Optional[str]:
        return self._current_state

    def start_authorization(self, open_browser: bool = True, timeout_seconds: int = 180) -> Dict[str, Any]:
        """
        Starts the desktop OAuth authorization flow:
        - Shuts down any previous session cleanly.
        - Binds an ephemeral port on 127.0.0.1.
        - Configures InstalledAppFlow with vault client configuration.
        - Generates authorization URL with CSRF state protection.
        - Spawns background loopback server thread.
        - Attempts opening the browser and returns immediately with auth_url.
        """
        with self._lock:
            # 1. Clean up any existing session
            self._stop_server_locked()

            # 2. Check client configuration in vault
            raw_config = vault.get_json("google", "client_config")
            if not raw_config:
                raise ValueError(
                    "Google Client configuration not found in Vault. "
                    "Please import your Desktop OAuth client_secrets.json first."
                )

            # Ensure complete Google Desktop client_config schema with standard OAuth endpoints
            import copy
            client_config = copy.deepcopy(raw_config)
            cfg_type = "installed" if "installed" in client_config else "web" if "web" in client_config else None
            if not cfg_type:
                raise ValueError("Google Client secrets must contain an 'installed' or 'web' configuration block.")

            cfg_block = client_config[cfg_type]
            if not cfg_block.get("auth_uri"):
                cfg_block["auth_uri"] = "https://accounts.google.com/o/oauth2/auth"
            if not cfg_block.get("token_uri"):
                cfg_block["token_uri"] = "https://oauth2.googleapis.com/token"
            if not cfg_block.get("redirect_uris"):
                cfg_block["redirect_uris"] = ["http://localhost"]

            # 3. Bind ephemeral localhost server
            try:
                server = HTTPServer(("127.0.0.1", 0), OAuthCallbackHandler)
            except Exception as e:
                logger.error(f"Failed to bind local loopback server: {e}")
                raise RuntimeError(f"Could not bind local loopback server for OAuth: {e}")

            port = server.server_address[1]
            server.session_manager = self
            self._server = server

            # 4. Initialize Google OAuth flow
            try:
                self._flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
                self._flow.redirect_uri = f"http://localhost:{port}/"
                auth_url, state = self._flow.authorization_url(
                    prompt="consent",
                    access_type="offline"
                )
            except Exception as e:
                server.server_close()
                self._server = None
                logger.error(f"Failed to generate Google authorization URL: {e}")
                raise RuntimeError(f"Failed to initialize Google OAuth flow: {e}")

            self._current_state = state
            now = time.time()
            expires_at = now + timeout_seconds

            # 5. Spawn background server thread
            self._server_thread = threading.Thread(
                target=self._run_server,
                args=(server, expires_at),
                daemon=True,
                name="youtube-oauth-callback-server"
            )
            self._server_thread.start()

            # 6. Attempt browser launch
            browser_launched = False
            if open_browser:
                try:
                    browser_launched = webbrowser.open(auth_url, new=1, autoraise=True)
                    logger.info(f"Opened default browser for Google OAuth: {browser_launched}")
                except Exception as e:
                    logger.warning(f"Could not automatically launch browser: {e}")
                    browser_launched = False

            self._session = {
                "status": "waiting_for_consent",
                "auth_url": auth_url,
                "browser_opened": browser_launched,
                "expires_at": expires_at,
                "started_at": now,
                "error": None,
                "channel": None
            }

            logger.info(f"YouTube OAuth session started on port {port}. Waiting for user consent...")
            return {
                "success": True,
                "status": "waiting_for_consent",
                "auth_url": auth_url,
                "browser_opened": browser_launched,
                "expires_in": timeout_seconds,
                "port": port,
                "message": "Waiting for Google authorization consent in browser."
            }

    def _run_server(self, server: HTTPServer, expires_at: float):
        """Runs the loopback HTTP server until request served, shutdown called, or timeout reached."""
        while time.time() < expires_at:
            if not self._server or self._server != server:
                break
            server.handle_request()
            # If session transitioned past waiting_for_consent, break loop
            with self._lock:
                if self._session["status"] not in ("waiting_for_consent", "opening"):
                    break

        # Check for timeout if still waiting
        with self._lock:
            if self._session["status"] == "waiting_for_consent" and time.time() >= expires_at:
                logger.warning("YouTube OAuth session timed out after waiting for user consent.")
                self._session["status"] = "timed_out"
                self._session["error"] = "Authorization timed out. Click Connect to try again."

        try:
            server.server_close()
        except Exception:
            pass

    def process_authorization_code(self, code: str):
        """Exchanges the authorization code for tokens and verifies channel identity."""
        def _worker():
            try:
                with self._lock:
                    self._session["status"] = "verifying_channel"

                logger.info("Exchanging Google authorization code for access and refresh tokens...")
                self._flow.fetch_token(code=code)
                creds = self._flow.credentials

                if not creds:
                    raise ValueError("No credentials returned from Google token endpoint.")

                # Persist credentials into secure vault
                token_dict = json.loads(creds.to_json())
                vault.save_json("google", "oauth_tokens", token_dict)
                logger.info("Google OAuth tokens successfully encrypted and stored in vault.")

                # Verify channel identity using YouTube Data API
                from backend.app.publishers.youtube.youtube_client import youtube_client
                channel_info = youtube_client.verify_channel(creds)

                with self._lock:
                    self._session["status"] = "connected"
                    self._session["channel"] = channel_info
                    self._session["error"] = None

                logger.info(f"YouTube authorization completed for channel '{channel_info['title']}' ({channel_info['channel_id']}).")

            except Exception as e:
                logger.error(f"YouTube token exchange or verification failed: {e}")
                with self._lock:
                    self._session["status"] = "failed"
                    self._session["error"] = str(e)

        threading.Thread(target=_worker, daemon=True, name="youtube-token-exchange").start()

    def record_cancelled(self, reason: str):
        with self._lock:
            self._session["status"] = "cancelled"
            self._session["error"] = reason

    def record_failed(self, error: str):
        with self._lock:
            self._session["status"] = "failed"
            self._session["error"] = error

    def cancel_authorization(self) -> Dict[str, Any]:
        """Cancels any active authorization session and frees the socket."""
        with self._lock:
            self._stop_server_locked()
            self._session = {
                "status": "idle",
                "auth_url": None,
                "browser_opened": False,
                "expires_at": 0,
                "error": None,
                "channel": None,
                "started_at": 0
            }
            logger.info("YouTube OAuth authorization session cancelled.")
            return {
                "success": True,
                "status": "idle",
                "message": "YouTube authorization cancelled."
            }

    def _stop_server_locked(self):
        if self._server:
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        self._current_state = None
        self._flow = None

    def get_status(self) -> Dict[str, Any]:
        """
        Returns the current live status of the authorization flow:
        - idle | waiting_for_consent | verifying_channel | connected | failed | cancelled | timed_out
        - includes auth_url if waiting for consent
        - includes verified channel information if connected
        """
        with self._lock:
            status = self._session["status"]
            now = time.time()

            # Check timeout
            if status == "waiting_for_consent" and now >= self._session["expires_at"]:
                self._session["status"] = "timed_out"
                self._session["error"] = "Authorization timed out. Click Connect to try again."
                status = "timed_out"

            # If idle, check if already verified in vault
            if status == "idle":
                channel = vault.get_json("google", "verified_channel")
                has_tokens = bool(vault.get_json("google", "oauth_tokens"))
                return {
                    "status": "idle",
                    "authenticated": bool(has_tokens and channel),
                    "channel": channel,
                    "client_configured": bool(vault.get_json("google", "client_config"))
                }

            remaining = max(0, int(self._session["expires_at"] - now)) if self._session["expires_at"] else 0

            return {
                "status": status,
                "auth_url": self._session["auth_url"],
                "browser_opened": self._session["browser_opened"],
                "expires_in": remaining,
                "error": self._session["error"],
                "channel": self._session["channel"]
            }


oauth_manager = YouTubeOAuthManager()
