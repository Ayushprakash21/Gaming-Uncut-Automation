import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.security.vault import vault

client = TestClient(app)

def test_youtube_channel_unauthorized_initially():
    vault.delete_secret("google", "verified_channel")
    res = client.get("/api/settings/youtube/channel")
    assert res.status_code == 200
    data = res.json()
    assert data["authenticated"] is False
    assert data["channel"] is None

def test_youtube_test_fails_without_credentials():
    vault.delete_secret("google", "oauth_tokens")
    res = client.post("/api/settings/youtube/test")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is False
    assert data["connected"] is False
    assert "No YouTube authorization tokens" in data["error"]

def test_youtube_disconnect_clears_state():
    vault.save_json("google", "oauth_tokens", {"access_token": "mock"})
    vault.save_json("google", "verified_channel", {"id": "mock_id", "title": "Mock Channel"})
    
    res = client.post("/api/settings/youtube/disconnect")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["status"] == "disconnected"

    # Vault check
    assert vault.get_json("google", "oauth_tokens") is None
    assert vault.get_json("google", "verified_channel") is None
