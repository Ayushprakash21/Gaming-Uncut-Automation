"""
Test Suite for Meta Token Lifecycle Management in Gaming Uncut Automation.
Tests:
1. Valid System User token (permanent, expires_at=0).
2. Expiring System User token (future timestamp, warning flags).
3. Expired token (Code 190, Subcode 463).
4. Revoked token.
5. Wrong App ID mismatch detection.
6. Missing permissions detection (Facebook & Instagram scopes).
7. Refresh success (fb_exchange_token).
8. Refresh failure / unsupported (permanent or expired token).
9. Facebook publishing authentication credential resolution.
10. Instagram publishing authentication credential resolution.
11. Pausing publishing on Code 190 to prevent infinite retry loops.
"""

import pytest
import time
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, timezone

from backend.app.publishers.meta_token_manager import (
    MetaTokenManager, REQUIRED_FACEBOOK_SCOPES, REQUIRED_INSTAGRAM_SCOPES, redact_meta_secrets
)
from backend.app.publishers.quota_tracker import is_platform_authorized, can_publish
from backend.app.security.vault import vault
from backend.app.models.schema import PlatformQuotaState, PlatformUpload, PublishingJob


@pytest.fixture
def token_mgr():
    return MetaTokenManager()


def test_redact_meta_secrets():
    """Confirms Meta tokens and secrets are never printed in plaintext."""
    token = "EAAB1234567890abcdef"
    text = f"Failed with token {token} and secret client_secret='xyz123'"
    sanitized = redact_meta_secrets(text)
    assert token not in sanitized
    assert "[REDACTED_TOKEN]" in sanitized
    assert "xyz123" not in sanitized


def test_valid_permanent_system_user_token(token_mgr):
    """Scenario 1: Valid System User Token configured with Expiration: Never (expires_at=0)."""
    mock_debug_payload = {
        "data": {
            "app_id": "1165428222586669",
            "type": "USER",
            "application": "Gaming Uncut",
            "expires_at": 0,
            "is_valid": True,
            "scopes": [
                "pages_show_list", "pages_read_engagement", "pages_manage_posts",
                "instagram_basic", "instagram_content_publish", "business_management"
            ],
            "user_id": "28899252343079117"
        }
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debug_payload
        mock_get.return_value.status_code = 200

        res = token_mgr.debug_token("MOCK_SYSTEM_USER_TOKEN", app_id="1165428222586669", app_secret="secret")

        assert res["is_valid"] is True
        assert res["is_permanent"] is True
        assert res["is_expired"] is False
        assert res["token_type"] == "SYSTEM_USER"
        assert res["expires_at"] == 0
        assert res["can_refresh"] is False  # Never expires, so refresh not needed
        assert res["missing_facebook_scopes"] == []
        assert res["missing_instagram_scopes"] == []
        assert res["app_matches"] is True


def test_expiring_system_user_token(token_mgr):
    """Scenario 2: Expiring System User or 60-day User Token with future expiration date."""
    future_ts = int(time.time()) + (3 * 86400)  # Expires in 3 days
    mock_debug_payload = {
        "data": {
            "app_id": "1165428222586669",
            "type": "USER",
            "application": "Gaming Uncut",
            "expires_at": future_ts,
            "is_valid": True,
            "scopes": [
                "pages_show_list", "pages_read_engagement", "pages_manage_posts",
                "instagram_basic", "instagram_content_publish"
            ],
            "user_id": "28899252343079117"
        }
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debug_payload
        mock_get.return_value.status_code = 200

        res = token_mgr.debug_token("MOCK_EXPIRING_TOKEN", app_id="1165428222586669", app_secret="secret")

        assert res["is_valid"] is True
        assert res["is_permanent"] is False
        assert res["is_expired"] is False
        assert res["expiring_soon"] is True
        assert res["expires_in_days"] == pytest.approx(3.0, abs=0.2)
        assert res["can_refresh"] is True  # Supported before expiry


def test_expired_token_code_190(token_mgr):
    """Scenario 3: Expired Token (Code 190, Subcode 463)."""
    past_ts = int(time.time()) - 3600
    mock_debug_payload = {
        "data": {
            "app_id": "1165428222586669",
            "type": "USER",
            "application": "Gaming Uncut",
            "expires_at": past_ts,
            "is_valid": False,
            "error": {
                "code": 190,
                "subcode": 463,
                "message": "Error validating access token: Session has expired on Tuesday."
            },
            "scopes": ["pages_show_list"],
            "user_id": "28899252343079117"
        }
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debug_payload
        mock_get.return_value.status_code = 200

        res = token_mgr.debug_token("EXPIRED_TOKEN", app_id="1165428222586669", app_secret="secret")

        assert res["is_valid"] is False
        assert res["is_expired"] is True
        assert res["error_code"] == 190
        assert res["error_subcode"] == 463
        assert res["can_refresh"] is False  # Cannot refresh an already expired token via API


def test_revoked_token(token_mgr):
    """Scenario 4: Revoked Token (User changed password or deauthorized app)."""
    mock_debug_payload = {
        "data": {
            "app_id": "1165428222586669",
            "type": "USER",
            "is_valid": False,
            "error": {
                "code": 190,
                "subcode": 458,
                "message": "The user has de-authorized the application."
            }
        }
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debug_payload
        mock_get.return_value.status_code = 200

        res = token_mgr.debug_token("REVOKED_TOKEN", app_id="1165428222586669", app_secret="secret")

        assert res["is_valid"] is False
        assert res["error_code"] == 190
        assert res["error_subcode"] == 458


def test_wrong_app_id_detection(token_mgr):
    """Scenario 5: Token generated for another App ID."""
    mock_debug_payload = {
        "data": {
            "app_id": "9999999999999999",  # Different App ID
            "type": "USER",
            "application": "Some Other App",
            "expires_at": 0,
            "is_valid": True,
            "scopes": ["pages_show_list"]
        }
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debug_payload
        mock_get.return_value.status_code = 200

        res = token_mgr.debug_token("TOKEN_FOR_DIFFERENT_APP", app_id="1165428222586669", app_secret="secret")

        assert res["app_matches"] is False
        assert res["app_id"] == "9999999999999999"


def test_missing_permissions_detection(token_mgr):
    """Scenario 6: Token missing required publishing permissions."""
    mock_debug_payload = {
        "data": {
            "app_id": "1165428222586669",
            "type": "USER",
            "application": "Gaming Uncut",
            "expires_at": 0,
            "is_valid": True,
            "scopes": ["pages_show_list"],  # Missing manage_posts, instagram_content_publish
            "user_id": "28899252343079117"
        }
    }

    with patch("requests.get") as mock_get:
        mock_get.return_value.json.return_value = mock_debug_payload
        mock_get.return_value.status_code = 200

        res = token_mgr.debug_token("PARTIAL_SCOPES_TOKEN", app_id="1165428222586669", app_secret="secret")

        assert "pages_manage_posts" in res["missing_facebook_scopes"]
        assert "instagram_content_publish" in res["missing_instagram_scopes"]


def test_refresh_token_success(token_mgr):
    """Scenario 7: Token refresh succeeds via fb_exchange_token."""
    with patch.object(token_mgr, "inspect_health") as mock_health, \
         patch.object(token_mgr, "get_configured_tokens") as mock_tokens, \
         patch.object(token_mgr, "get_active_token") as mock_active, \
         patch("requests.get") as mock_get:

        mock_health.return_value = {
            "is_valid": True,
            "is_permanent": False,
            "is_expired": False
        }
        mock_tokens.return_value = {"app_id": "1165428222586669", "app_secret": "mock_secret"}
        mock_active.return_value = "VALID_SHORT_LIVED_TOKEN"

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "access_token": "NEW_REFRESHED_LONG_LIVED_TOKEN",
            "token_type": "bearer",
            "expires_in": 5184000  # 60 days
        }

        res = token_mgr.refresh_token()
        assert res["success"] is True
        assert res["refreshed"] is True
        assert "5184000" not in res["message"] or "60" in res["message"]


def test_refresh_token_failure_on_expired(token_mgr):
    """Scenario 8: Token refresh blocked on already expired token."""
    with patch.object(token_mgr, "inspect_health") as mock_health:
        mock_health.return_value = {
            "is_valid": False,
            "is_permanent": False,
            "is_expired": True
        }

        res = token_mgr.refresh_token()
        assert res["success"] is False
        assert "expired" in res["message"].lower()


def test_facebook_publishing_credentials_resolution(token_mgr):
    """Scenario 9: Facebook publishing resolves Page Access Token from Page ID."""
    with patch.object(token_mgr, "get_configured_tokens") as mock_tokens, \
         patch.object(token_mgr, "get_active_token") as mock_active, \
         patch("requests.get") as mock_get:

        mock_tokens.return_value = {"page_id": "1507158329141344"}
        mock_active.return_value = "SYSTEM_USER_TOKEN"

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "id": "1507158329141344",
            "name": "Gaming Uncut",
            "access_token": "RESOLVED_PAGE_ACCESS_TOKEN"
        }

        creds = token_mgr.get_facebook_credentials()
        assert creds["page_id"] == "1507158329141344"
        assert creds["token"] == "RESOLVED_PAGE_ACCESS_TOKEN"
        assert creds["page_name"] == "Gaming Uncut"


def test_instagram_publishing_credentials_resolution(token_mgr):
    """Scenario 10: Instagram publishing resolves Instagram Professional Account ID."""
    with patch.object(token_mgr, "get_configured_tokens") as mock_tokens, \
         patch.object(token_mgr, "get_active_token") as mock_active, \
         patch("requests.get") as mock_get:

        mock_tokens.return_value = {"instagram_account_id": "17841448213228145"}
        mock_active.return_value = "SYSTEM_USER_TOKEN"

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "id": "17841448213228145",
            "username": "gaming_uncut"
        }

        creds = token_mgr.get_instagram_credentials()
        assert creds["instagram_account_id"] == "17841448213228145"
        assert creds["token"] == "SYSTEM_USER_TOKEN"
        assert creds["username"] == "gaming_uncut"


def test_quota_tracker_pauses_publishing_when_meta_token_expired():
    """Scenario 11: Quota tracker strictly halts publishing and does not retry on expired token."""
    from backend.app.database.session import SessionLocal

    with patch("backend.app.publishers.meta_token_manager.meta_token_manager.inspect_health") as mock_health:
        mock_health.return_value = {
            "configured": True,
            "is_valid": False,
            "is_expired": True,
            "status": "expired",
            "last_auth_error": "Meta Access Token has expired (Code 190, Subcode 463)."
        }

        is_auth, reason = is_platform_authorized("instagram")
        assert is_auth is False
        assert "Code 190" in reason

        is_auth_fb, reason_fb = is_platform_authorized("facebook")
        assert is_auth_fb is False
        assert "Code 190" in reason_fb

        # can_publish should refuse permission
        with SessionLocal() as db:
            allowed, pause_reason, win = can_publish(db, "instagram")
            assert allowed is False
            assert "Code 190" in pause_reason
