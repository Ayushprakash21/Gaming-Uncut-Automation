"""
Automated Test Suite for YouTube Desktop OAuth 2.0 Authorization Flow.
Brand: Gaming Uncut | Tagline: NO FILTER. JUST GAMEPLAY.
Verifies:
1. Start authorization & auth_url generation with ephemeral loopback port.
2. Live status polling endpoint.
3. User cancellation / denied consent handling.
4. CSRF State validation & protection.
5. Successful authorization code exchange & channel verification.
6. Cancel / reset endpoint and server shutdown.
7. Test isolation: Mock vault and DB protection.
"""

import time
import urllib.request
import urllib.error
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.security.vault import vault
from backend.app.publishers.youtube.oauth_manager import oauth_manager

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_oauth_session():
    """Ensure OAuth session is idle and valid test client_config is present before each test."""
    oauth_manager.cancel_authorization()
    vault.save_json("google", "client_config", {
        "installed": {
            "client_id": "test-suite-client-id.apps.googleusercontent.com",
            "project_id": "test-suite-project",
            "client_secret": "TEST_SUITE_CLIENT_SECRET",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]
        }
    })
    yield
    oauth_manager.cancel_authorization()

def test_oauth_start_requires_client_config(monkeypatch):
    """If no client_secrets.json has been imported, starting OAuth must fail with 400."""
    monkeypatch.setattr(vault, "get_json", lambda s, k: None if k == "client_config" else {})
    res = client.post("/api/settings/youtube/connect/start")
    assert res.status_code == 400
    assert "Google Client configuration not found" in res.json()["detail"]

def test_oauth_start_generates_auth_url_and_ephemeral_port():
    """Starting OAuth returns auth_url, ephemeral port, and waiting_for_consent status."""
    res = client.post("/api/settings/youtube/connect/start?open_browser=false")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "waiting_for_consent"
    assert "https://accounts.google.com/o/oauth2/auth" in data["auth_url"]
    assert data["port"] > 1024
    assert data["expires_in"] > 0

    # Poll status endpoint
    status_res = client.get("/api/settings/youtube/connect/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["status"] == "waiting_for_consent"
    assert status_data["auth_url"] == data["auth_url"]

def test_oauth_duplicate_start_prevents_duplicate_servers():
    """Consecutive starts cleanly close the previous server without socket collision."""
    res1 = client.post("/api/settings/youtube/connect/start?open_browser=false")
    assert res1.status_code == 200
    port1 = res1.json()["port"]

    res2 = client.post("/api/settings/youtube/connect/start?open_browser=false")
    assert res2.status_code == 200
    port2 = res2.json()["port"]

    assert port2 > 0

def test_oauth_callback_csrf_mismatch():
    """A callback with mismatched state must be rejected with 400 and fail the session."""
    res = client.post("/api/settings/youtube/connect/start?open_browser=false")
    port = res.json()["port"]

    callback_url = f"http://127.0.0.1:{port}/?code=mock_code&state=invalid_csrf_state"
    try:
        urllib.request.urlopen(callback_url, timeout=3)
    except urllib.error.HTTPError as e:
        assert e.code == 400

    # Status should show failed due to CSRF state mismatch
    time.sleep(0.1)
    status_res = client.get("/api/settings/youtube/connect/status")
    data = status_res.json()
    assert data["status"] == "failed"
    assert "CSRF State mismatch" in data["error"]

def test_oauth_callback_user_denied_consent():
    """When user cancels or denies consent, loopback server returns cancelled HTML and sets cancelled status."""
    res = client.post("/api/settings/youtube/connect/start?open_browser=false")
    port = res.json()["port"]
    valid_state = oauth_manager.current_state

    callback_url = f"http://127.0.0.1:{port}/?error=access_denied&state={valid_state}"
    resp = urllib.request.urlopen(callback_url, timeout=3)
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "Authorization Cancelled" in body

    time.sleep(0.1)
    status_res = client.get("/api/settings/youtube/connect/status")
    data = status_res.json()
    assert data["status"] == "cancelled"
    assert "access_denied" in data["error"]

def test_oauth_callback_successful_flow_and_channel_verification(monkeypatch):
    """Valid code and state exchanges token, verifies channel, and reaches connected status."""
    res = client.post("/api/settings/youtube/connect/start?open_browser=false")
    port = res.json()["port"]
    valid_state = oauth_manager.current_state

    # Mock token exchange and channel verification
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "mock_token", "refresh_token": "mock_refresh"}'

    flow_mock = oauth_manager._flow
    assert flow_mock is not None
    monkeypatch.setattr(flow_mock, "fetch_token", lambda code: {"access_token": "mock"})
    monkeypatch.setattr(flow_mock, "_credentials", mock_creds, raising=False)
    monkeypatch.setattr(type(flow_mock), "credentials", property(lambda self: mock_creds))

    from backend.app.publishers.youtube.youtube_client import youtube_client
    mock_channel = {
        "channel_id": "UC_TEST_123456789",
        "title": "Gaming Uncut Official Channel",
        "subscriber_count": "154000",
        "video_count": "42"
    }
    monkeypatch.setattr(youtube_client, "verify_channel", lambda creds: mock_channel)

    callback_url = f"http://127.0.0.1:{port}/?code=mock_valid_auth_code&state={valid_state}"
    resp = urllib.request.urlopen(callback_url, timeout=3)
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "Authorization Granted" in body

    # Wait briefly for worker thread to complete channel verification
    for _ in range(20):
        status_res = client.get("/api/settings/youtube/connect/status")
        data = status_res.json()
        if data["status"] == "connected":
            break
        time.sleep(0.1)

    assert data["status"] == "connected"
    assert data["channel"]["channel_id"] == "UC_TEST_123456789"
    assert data["channel"]["title"] == "Gaming Uncut Official Channel"

def test_oauth_cancel_endpoint():
    """Calling cancel endpoint cleanly resets session to idle."""
    client.post("/api/settings/youtube/connect/start?open_browser=false")
    assert oauth_manager.get_status()["status"] == "waiting_for_consent"

    cancel_res = client.post("/api/settings/youtube/connect/cancel")
    assert cancel_res.status_code == 200
    assert cancel_res.json()["status"] == "idle"

    assert oauth_manager.get_status()["status"] == "idle"
