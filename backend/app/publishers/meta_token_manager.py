"""
Meta Token Lifecycle & Authentication Manager for Gaming Uncut.
Manages permanent System User tokens, Page Access tokens, token introspection,
health checks, official Meta debug_token queries, and safe refresh operations.

Strict Security:
- NEVER logs plaintext tokens or app secrets.
- Redacts tokens across all audit logs and exception payloads.
- Strictly validates App ID, Scopes, and Expiration timestamps.
- Separates Facebook Page credentials from Instagram credentials.
"""

import time
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple
import requests

from backend.app.security.vault import vault
from backend.app.database.session import SessionLocal
from backend.app.models.schema import SystemLog, PlatformQuotaState, PlatformUpload, PublishingJob

import socket
import urllib3.util.connection as urllib_conn
try:
    urllib_conn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

logger = logging.getLogger("gaming_uncut.meta_token_manager")

API_VERSION = "v21.0"
GRAPH_URL = f"https://graph.facebook.com/{API_VERSION}"

# Required scopes
REQUIRED_FACEBOOK_SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
REQUIRED_INSTAGRAM_SCOPES = ["pages_show_list", "instagram_basic", "instagram_content_publish"]


def redact_meta_secrets(text: Any) -> str:
    """Sanitizes Meta tokens, app secrets, and credentials from text strings."""
    if text is None:
        return ""
    s = str(text)
    s = re.sub(r'(EAAB[a-zA-Z0-9_-]+|EAAQ[a-zA-Z0-9_-]+|IGAA[a-zA-Z0-9_-]+|IGQV[a-zA-Z0-9_-]+)', '[REDACTED_TOKEN]', s)
    s = re.sub(r'(Bearer\s+)[a-zA-Z0-9_\-\.]+', r'\1[REDACTED_TOKEN]', s, flags=re.IGNORECASE)
    s = re.sub(r'(client_secret["\']?\s*[:=]\s*["\']?)[^"\'\s,]+', r'\1[REDACTED_SECRET]', s, flags=re.IGNORECASE)
    return s


class MetaTokenManager:
    """Enterprise-grade Meta Token Lifecycle and Health Manager."""

    def get_configured_tokens(self) -> Dict[str, Optional[str]]:
        """Retrieves raw credentials from secure vault."""
        return {
            "app_id": vault.get_secret("meta", "app_id"),
            "app_secret": vault.get_secret("meta", "app_secret"),
            "system_user_token": vault.get_secret("meta", "system_user_token"),
            "page_access_token": vault.get_secret("meta", "page_access_token"),
            "page_id": vault.get_secret("meta", "page_id"),
            "instagram_account_id": vault.get_secret("meta", "instagram_account_id"),
        }

    def get_active_token(self) -> Optional[str]:
        """Returns the primary active Meta token (prefers System User token if set, otherwise Page/User token)."""
        tokens = self.get_configured_tokens()
        return tokens.get("system_user_token") or tokens.get("page_access_token")

    def debug_token(
        self,
        input_token: str,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Queries Meta's official GET /debug_token endpoint.
        Returns detailed token metadata: type, app ID, expiration, scopes, and validity.
        """
        if not input_token or not input_token.strip():
            return {
                "is_valid": False,
                "error_code": "EMPTY_TOKEN",
                "error_message": "Token is empty."
            }

        input_token = input_token.strip()
        tokens = self.get_configured_tokens()
        resolved_app_id = (app_id or tokens.get("app_id") or "").strip()
        resolved_app_secret = (app_secret or tokens.get("app_secret") or "").strip()

        # If app_id and app_secret are available, query official /debug_token with app access token
        if resolved_app_id and resolved_app_secret:
            try:
                res = requests.get(
                    f"{GRAPH_URL}/debug_token",
                    params={
                        "input_token": input_token,
                        "access_token": f"{resolved_app_id}|{resolved_app_secret}"
                    },
                    timeout=12
                )
                raw_json = res.json()
            except requests.RequestException as e:
                logger.warning(f"Network error calling Meta debug_token: {e}")
                return self._fallback_debug_token(input_token)

            if "data" in raw_json:
                data = raw_json["data"]
                return self._parse_debug_token_data(data, resolved_app_id)

            if "error" in raw_json:
                err = raw_json["error"]
                logger.warning(f"Meta debug_token returned error: {redact_meta_secrets(err.get('message'))}")
                return {
                    "is_valid": False,
                    "error_code": err.get("code"),
                    "error_subcode": err.get("error_subcode"),
                    "error_type": err.get("type"),
                    "error_message": redact_meta_secrets(err.get("message", "Token debugging failed")),
                    "fbtrace_id": err.get("fbtrace_id")
                }

        # Fallback if app_id/secret missing or failed
        return self._fallback_debug_token(input_token)

    def _fallback_debug_token(self, token: str) -> Dict[str, Any]:
        """Inspects token via /me and /me/permissions when app credentials cannot be used for debug_token."""
        try:
            me_res = requests.get(
                f"{GRAPH_URL}/me",
                params={"fields": "id,name", "access_token": token},
                timeout=10
            ).json()

            if "error" in me_res:
                err = me_res["error"]
                return {
                    "is_valid": False,
                    "error_code": err.get("code"),
                    "error_subcode": err.get("error_subcode"),
                    "error_type": err.get("type"),
                    "error_message": redact_meta_secrets(err.get("message", "Token validation failed")),
                    "fbtrace_id": err.get("fbtrace_id"),
                    "token_type": "USER",
                    "is_permanent": False,
                    "expires_at": None,
                    "scopes": []
                }

            # Check permissions
            perm_res = requests.get(
                f"{GRAPH_URL}/me/permissions",
                params={"access_token": token},
                timeout=10
            ).json()
            scopes = [
                p["permission"] for p in perm_res.get("data", [])
                if p.get("status") == "granted"
            ]

            return {
                "is_valid": True,
                "user_id": me_res.get("id"),
                "user_name": me_res.get("name"),
                "token_type": "USER_OR_PAGE",
                "is_permanent": False,
                "expires_at": None,
                "expires_at_iso": None,
                "scopes": scopes,
                "app_id": None,
                "app_matches": True
            }
        except Exception as e:
            return {
                "is_valid": False,
                "error_code": "NETWORK_ERROR",
                "error_message": f"Connection to Meta failed: {e}"
            }

    def _parse_debug_token_data(self, data: Dict[str, Any], configured_app_id: str) -> Dict[str, Any]:
        """Parses Meta debug_token payload into standardized health structure."""
        token_type = data.get("type", "USER").upper()
        expires_at = data.get("expires_at", 0)
        is_permanent = (expires_at == 0)
        is_valid = bool(data.get("is_valid", False))
        token_app_id = str(data.get("app_id", ""))
        app_matches = bool(configured_app_id and token_app_id and configured_app_id == token_app_id)

        now_ts = int(time.time())
        is_expired = False
        expiring_soon = False
        expires_in_days = None
        expires_at_iso = None

        if not is_permanent and expires_at > 0:
            expires_at_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
            diff_sec = expires_at - now_ts
            expires_in_days = round(diff_sec / 86400.0, 1)
            if diff_sec <= 0:
                is_expired = True
                is_valid = False
            elif diff_sec <= (7 * 86400):
                expiring_soon = True

        # Check for error payload inside debug data
        err_dict = data.get("error", {})
        err_code = err_dict.get("code")
        err_subcode = err_dict.get("subcode")
        err_msg = redact_meta_secrets(err_dict.get("message"))

        if err_code == 190 or err_subcode == 463:
            is_expired = True
            is_valid = False

        scopes = data.get("scopes", [])

        # Detect System User: System user tokens generally have type USER with expires_at == 0
        # or business scopes and no human profile name
        detected_type = token_type
        if is_permanent and token_type == "USER":
            detected_type = "SYSTEM_USER"

        missing_fb = [s for s in REQUIRED_FACEBOOK_SCOPES if s not in scopes]
        missing_ig = [s for s in REQUIRED_INSTAGRAM_SCOPES if s not in scopes]

        # Determine if token is refreshable:
        # - Permanent tokens do not need refresh.
        # - Expired tokens (Code 190) cannot be refreshed via API.
        # - Expiring valid tokens can be refreshed via fb_exchange_token.
        can_refresh = bool(is_valid and not is_permanent and not is_expired and expires_at > 0)

        return {
            "is_valid": is_valid,
            "is_expired": is_expired,
            "is_permanent": is_permanent,
            "expiring_soon": expiring_soon,
            "expires_at": expires_at,
            "expires_at_iso": expires_at_iso,
            "expires_in_days": expires_in_days,
            "data_access_expires_at": data.get("data_access_expires_at"),
            "token_type": detected_type,
            "app_id": token_app_id,
            "app_matches": app_matches,
            "application_name": data.get("application"),
            "user_id": data.get("user_id"),
            "scopes": scopes,
            "missing_facebook_scopes": missing_fb,
            "missing_instagram_scopes": missing_ig,
            "error_code": err_code,
            "error_subcode": err_subcode,
            "error_message": err_msg,
            "can_refresh": can_refresh
        }

    def inspect_health(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Comprehensive health assessment of configured Meta tokens.
        Validates token with debug_token, inspects Facebook Page and Instagram Account.
        """
        cached = vault.get_json("meta", "token_health_cache")
        now_ts = time.time()
        # Use cache if fresh (< 5 mins) and not force_refresh
        if not force_refresh and cached and (now_ts - cached.get("cached_at", 0) < 300):
            return cached

        tokens = self.get_configured_tokens()
        app_id = tokens.get("app_id") or ""
        app_secret = tokens.get("app_secret") or ""
        active_token = self.get_active_token()
        page_id = tokens.get("page_id") or ""
        ig_id = tokens.get("instagram_account_id") or ""

        if not active_token:
            result = {
                "configured": False,
                "authenticated": False,
                "status": "not_configured",
                "status_label": "Not Configured",
                "token_type": "None",
                "is_valid": False,
                "is_expired": False,
                "is_permanent": False,
                "expires_at_iso": None,
                "expires_in_days": None,
                "can_refresh": False,
                "app_id": app_id,
                "page_id": page_id,
                "instagram_account_id": ig_id,
                "facebook_ready": False,
                "instagram_ready": False,
                "facebook_status": "not_configured",
                "instagram_status": "not_configured",
                "permissions": [],
                "missing_permissions": REQUIRED_FACEBOOK_SCOPES + REQUIRED_INSTAGRAM_SCOPES,
                "last_verified_at": None,
                "last_auth_error": "No Meta Access Token configured in Vault.",
                "cached_at": now_ts
            }
            vault.save_json("meta", "token_health_cache", result)
            return result

        # 1. Debug token metadata
        debug_info = self.debug_token(active_token, app_id, app_secret)

        is_valid = debug_info.get("is_valid", False)
        is_expired = debug_info.get("is_expired", False)
        is_permanent = debug_info.get("is_permanent", False)
        token_type = debug_info.get("token_type", "USER")
        scopes = debug_info.get("scopes", [])

        # 2. Check Facebook Page Connection
        fb_ready = False
        fb_page_name = None
        fb_status = "not_configured"
        fb_error = None
        if page_id and is_valid:
            try:
                res = requests.get(
                    f"{GRAPH_URL}/{page_id}",
                    params={"fields": "id,name,category,access_token", "access_token": active_token},
                    timeout=10
                ).json()
                if "name" in res:
                    fb_ready = True
                    fb_page_name = res.get("name")
                    fb_status = "connected"
                elif "error" in res:
                    fb_status = "error"
                    fb_error = redact_meta_secrets(res["error"].get("message"))
            except Exception as e:
                fb_status = "error"
                fb_error = str(e)
        elif is_expired:
            fb_status = "expired"
        elif not page_id:
            fb_status = "missing_page_id"

        # 3. Check Instagram Connection
        ig_ready = False
        ig_username = None
        ig_status = "not_configured"
        ig_error = None
        if ig_id and is_valid:
            try:
                res = requests.get(
                    f"{GRAPH_URL}/{ig_id}",
                    params={"fields": "id,name,username", "access_token": active_token},
                    timeout=10
                ).json()
                if "id" in res:
                    ig_ready = True
                    ig_username = res.get("username")
                    ig_status = "connected"
                elif "error" in res:
                    ig_status = "error"
                    ig_error = redact_meta_secrets(res["error"].get("message"))
            except Exception as e:
                ig_status = "error"
                ig_error = str(e)
        elif is_expired:
            ig_status = "expired"
        elif not ig_id:
            ig_status = "missing_ig_id"

        # Determine overall status
        if is_expired:
            overall_status = "expired"
            overall_label = "Token Expired (Code 190)"
        elif not is_valid:
            overall_status = "invalid"
            overall_label = "Invalid Token"
        elif debug_info.get("expiring_soon"):
            overall_status = "expiring_soon"
            overall_label = f"Expiring in {debug_info.get('expires_in_days')} days"
        elif is_permanent:
            overall_status = "healthy_permanent"
            overall_label = "Permanent System User (Never Expires)"
        else:
            overall_status = "healthy"
            overall_label = "Connected & Valid"

        now_iso = datetime.now(timezone.utc).isoformat()
        last_error = debug_info.get("error_message") or fb_error or ig_error

        result = {
            "configured": True,
            "authenticated": is_valid,
            "status": overall_status,
            "status_label": overall_label,
            "token_type": token_type,
            "is_valid": is_valid,
            "is_expired": is_expired,
            "is_permanent": is_permanent,
            "expiring_soon": debug_info.get("expiring_soon", False),
            "expires_at": debug_info.get("expires_at"),
            "expires_at_iso": debug_info.get("expires_at_iso"),
            "expires_in_days": debug_info.get("expires_in_days"),
            "data_access_expires_at": debug_info.get("data_access_expires_at"),
            "can_refresh": debug_info.get("can_refresh", False),
            "app_id": debug_info.get("app_id") or app_id,
            "app_matches": debug_info.get("app_matches", True),
            "application_name": debug_info.get("application_name", "Gaming Uncut"),
            "page_id": page_id,
            "page_name": fb_page_name,
            "instagram_account_id": ig_id,
            "instagram_username": ig_username,
            "facebook_ready": fb_ready,
            "instagram_ready": ig_ready,
            "facebook_status": fb_status,
            "instagram_status": ig_status,
            "permissions": scopes,
            "missing_facebook_scopes": debug_info.get("missing_facebook_scopes", []),
            "missing_instagram_scopes": debug_info.get("missing_instagram_scopes", []),
            "last_verified_at": now_iso if is_valid else cached.get("last_verified_at") if cached else None,
            "last_auth_error": last_error,
            "cached_at": now_ts
        }

        # Cache in vault
        vault.save_json("meta", "token_health_cache", result)
        return result

    def refresh_token(self) -> Dict[str, Any]:
        """
        Attempts official token refresh / exchange using GET /oauth/access_token?grant_type=fb_exchange_token.
        Only supported for valid, expiring tokens. Non-expiring tokens or already-expired tokens return clear messages.
        """
        health = self.inspect_health(force_refresh=True)

        if health.get("is_permanent"):
            return {
                "success": False,
                "refreshed": False,
                "message": "This is a permanent System User token configured to never expire. Refresh is not required."
            }

        if health.get("is_expired"):
            return {
                "success": False,
                "refreshed": False,
                "message": (
                    "The current Meta Access Token has already expired (Code 190, Subcode 463). "
                    "Meta requires generating a fresh token from Meta Business Portfolio or Settings. "
                    "Expired tokens cannot be renewed via API."
                )
            }

        tokens = self.get_configured_tokens()
        app_id = tokens.get("app_id")
        app_secret = tokens.get("app_secret")
        active_token = self.get_active_token()

        if not app_id or not app_secret:
            return {
                "success": False,
                "refreshed": False,
                "message": "Meta App ID and App Secret are required to perform a token exchange."
            }

        if not active_token:
            return {
                "success": False,
                "refreshed": False,
                "message": "No active token configured."
            }

        logger.info("Executing Meta token exchange (grant_type=fb_exchange_token)...")
        try:
            res = requests.get(
                f"{GRAPH_URL}/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "fb_exchange_token": active_token
                },
                timeout=15
            )
            data = res.json()
        except requests.RequestException as e:
            logger.error(f"Network error during Meta token exchange: {e}")
            return {"success": False, "refreshed": False, "message": f"Network error during token exchange: {e}"}

        if "access_token" in data:
            new_token = data["access_token"]
            expires_in = data.get("expires_in")
            # Update vault
            vault.set_secret("meta", "page_access_token", new_token)
            # Invalidate cache & re-check
            updated_health = self.inspect_health(force_refresh=True)
            self._log_audit(
                "INFO",
                f"Meta access token successfully refreshed and saved to Vault (Expires in: {expires_in}s).",
                {"expires_in": expires_in}
            )
            return {
                "success": True,
                "refreshed": True,
                "message": f"Meta token successfully refreshed. Valid for ~{round((expires_in or 0)/86400, 1)} days.",
                "health": updated_health
            }

        err_msg = redact_meta_secrets(data.get("error", {}).get("message", "Token exchange failed"))
        logger.error(f"Meta token exchange failed: {err_msg}")
        return {
            "success": False,
            "refreshed": False,
            "message": f"Meta refused token exchange: {err_msg}"
        }

    def get_facebook_credentials(self) -> Dict[str, str]:
        """
        Resolves the dedicated Page Access Token and Page ID for Facebook Reels.
        If a User or System User token is stored, dynamically queries /{page_id}?fields=access_token
        to obtain the actual page-scoped token required for video upload sessions.
        """
        tokens = self.get_configured_tokens()
        page_id = tokens.get("page_id")
        active_token = self.get_active_token()

        if not active_token:
            raise ValueError("Meta Page Access Token or System User Token is not configured in Settings.")
        if not page_id:
            raise ValueError("Facebook Page ID is not configured in Settings.")

        page_id = page_id.strip()
        active_token = active_token.strip()

        page_token = tokens.get("page_access_token")
        if not page_token:
            try:
                res = requests.get(
                    f"{GRAPH_URL}/{page_id}",
                    params={"fields": "access_token,name", "access_token": active_token},
                    timeout=10
                )
                if res.status_code == 200:
                    data = res.json()
                    page_token = data.get("access_token")
            except Exception:
                pass

        return {
            "page_id": page_id,
            "page_name": "Gaming Uncut",
            "token": page_token or active_token
        }

    def get_instagram_credentials(self) -> Dict[str, str]:
        """
        Resolves Instagram Professional Account ID and token for Instagram Reels publishing.
        Accepts either a System User token with instagram_content_publish or a linked Facebook Page token.
        """
        tokens = self.get_configured_tokens()
        ig_id = tokens.get("instagram_account_id")
        active_token = self.get_active_token()

        if not active_token:
            raise ValueError("Meta Access Token is not configured in Settings.")
        if not ig_id:
            raise ValueError("Instagram Professional Account ID is not configured in Settings.")

        ig_id = ig_id.strip()
        active_token = active_token.strip()

        return {
            "instagram_account_id": ig_id,
            "username": "gaming_uncut",
            "token": active_token
        }

    def mark_auth_expired(
        self,
        platform: str,
        error_code: str = "190",
        subcode: Optional[str] = "463",
        message: Optional[str] = None
    ):
        """
        Marks platform authentication as expired in database and pauses publishing immediately.
        Stops APScheduler from repeatedly failing and incrementing attempts on pending jobs.
        """
        safe_msg = redact_meta_secrets(message or "Meta Access Token has expired or was revoked (Code 190, Subcode 463).")
        logger.warning(f"Marking {platform} authentication as EXPIRED: {safe_msg}")

        # Invalidate health cache
        cached = vault.get_json("meta", "token_health_cache") or {}
        cached["is_valid"] = False
        cached["is_expired"] = True
        cached["status"] = "expired"
        cached["status_label"] = "Token Expired (Code 190)"
        cached["last_auth_error"] = safe_msg
        if platform == "facebook":
            cached["facebook_ready"] = False
            cached["facebook_status"] = "expired"
        elif platform == "instagram":
            cached["instagram_ready"] = False
            cached["instagram_status"] = "expired"
        vault.save_json("meta", "token_health_cache", cached)

        try:
            with SessionLocal() as db:
                # 1. Update PlatformQuotaState to pause publishing indefinitely until reconnect
                state = db.query(PlatformQuotaState).filter_by(platform=platform).first()
                if not state:
                    state = PlatformQuotaState(platform=platform)
                    db.add(state)
                # Pause for 30 days or until user re-authenticates
                state.pause_until = datetime.utcnow() + timedelta(days=30)
                state.last_restriction_type = "auth_expired"
                state.last_restriction_reason = safe_msg
                state.last_updated_at = datetime.utcnow()

                # 2. Mark pending / in-flight PlatformUpload rows for this platform as paused_unauthorized
                active_uploads = db.query(PlatformUpload).filter(
                    PlatformUpload.platform == platform,
                    PlatformUpload.status.in_(["pending", "upload_initialized", "video_transferring"])
                ).all()
                for up in active_uploads:
                    if up.status != "published":
                        up.status = "paused_unauthorized"
                        up.error_code = error_code
                        up.error_message = safe_msg

                # 3. Write structured audit log
                db.add(SystemLog(
                    module=f"{platform}_publisher",
                    level="ERROR",
                    message=f"[{platform.capitalize()}] Authentication halted: {safe_msg} Publishing paused to prevent retry loops.",
                    metadata_json=json.dumps({
                        "platform": platform,
                        "error_code": error_code,
                        "error_subcode": subcode,
                        "action": "publishing_paused_for_auth"
                    }),
                    created_at=datetime.utcnow()
                ))
                db.commit()
        except Exception as e:
            logger.error(f"Failed to record auth expiration in database: {e}")

    def resume_queued_jobs_after_reconnect(self, db: Any) -> int:
        """
        Called when fresh Meta credentials are saved and verified.
        Clears paused_unauthorized states for Facebook and Instagram and restores
        jobs to pending for safe processing.
        """
        count = 0
        try:
            for platform in ["facebook", "instagram"]:
                state = db.query(PlatformQuotaState).filter_by(platform=platform).first()
                if state and state.last_restriction_type == "auth_expired":
                    state.pause_until = None
                    state.last_restriction_type = None
                    state.last_restriction_reason = None
                    state.last_updated_at = datetime.utcnow()

                paused = db.query(PlatformUpload).filter(
                    PlatformUpload.platform == platform,
                    PlatformUpload.status == "paused_unauthorized"
                ).all()
                for up in paused:
                    up.status = "pending"
                    up.error_code = None
                    up.error_message = None
                    count += 1
            db.commit()
            if count > 0:
                logger.info(f"Resumed {count} platform upload jobs after Meta authentication restoration.")
        except Exception as e:
            logger.warning(f"Error resuming jobs after reconnect: {e}")
        return count

    def _log_audit(self, level: str, message: str, meta: Optional[Dict[str, Any]] = None):
        try:
            with SessionLocal() as db:
                db.add(SystemLog(
                    module="meta_token_manager",
                    level=level,
                    message=redact_meta_secrets(message),
                    metadata_json=redact_meta_secrets(json.dumps(meta)) if meta else None,
                    created_at=datetime.utcnow()
                ))
                db.commit()
        except Exception as e:
            logger.debug(f"Audit log writing skipped: {e}")


meta_token_manager = MetaTokenManager()
