"""
Pytest Isolation and Sandbox Configuration for Gaming Uncut Automation.
Guarantees:
1. Isolated Test Database: Tests run against a dedicated temporary test database (gaming_uncut_test.db).
   Production database (gaming_uncut_v2.db) is 100% untouched.
2. Isolated In-Memory Test Vault: Windows Credential Manager and production .vault/ are protected.
   Keyring calls in tests are intercepted by an in-memory dictionary.
3. Safe Publisher Mocking: External API uploads (YouTube, Instagram, Facebook) are mocked
   to prevent accidental publication during test execution.
"""

import os
import sys
import shutil
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

# CRITICAL TEST ISOLATION DIRECTIVE:
# Point DATABASE_URL and VAULT_DIR to isolated test paths BEFORE any backend module is imported.
TEST_DB_FILE = ROOT_DIR / "data" / "gaming_uncut_test.db"
TEST_VAULT_DIR = ROOT_DIR / "data" / "temporary" / "test_vault"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_FILE}"
os.environ["VAULT_DIR"] = str(TEST_VAULT_DIR)

# Intercept Windows Keyring BEFORE any module imports keyring
import keyring
_TEST_KEYRING_STORE = {}

def mock_set_password(service_name, key, value):
    _TEST_KEYRING_STORE[f"{service_name}:{key}"] = str(value)
    return True

def mock_get_password(service_name, key):
    return _TEST_KEYRING_STORE.get(f"{service_name}:{key}")

def mock_delete_password(service_name, key):
    _TEST_KEYRING_STORE.pop(f"{service_name}:{key}", None)
    return True

keyring.set_password = mock_set_password
keyring.get_password = mock_get_password
keyring.delete_password = mock_delete_password

import pytest
from backend.app.core.config import settings
from backend.app.database.session import Base, engine, SessionLocal, get_db
from backend.app.security.vault import vault
from backend.app.main import app

@pytest.fixture(scope="session", autouse=True)
def setup_isolated_test_environment():
    """Prepares isolated database and isolated vault for the test session."""
    TEST_VAULT_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize all database tables on isolated test database
    Base.metadata.create_all(bind=engine)

    # Seed isolated test vault with valid mock Google and Meta configurations for test suites
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
    vault.set_secret("google", "project_id", "test-suite-project")
    vault.set_secret("meta", "page_access_token", "test-suite-meta-token")
    vault.set_secret("meta", "page_id", "test-suite-page-id")
    vault.set_secret("meta", "instagram_account_id", "test-suite-ig-account")

    yield

    # Teardown: Dispose engine and clean up test files
    engine.dispose()
    for p in [TEST_DB_FILE, Path(str(TEST_DB_FILE) + "-wal"), Path(str(TEST_DB_FILE) + "-shm")]:
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass
    shutil.rmtree(TEST_VAULT_DIR, ignore_errors=True)

@pytest.fixture(autouse=True)
def mock_external_publishers(monkeypatch):
    """Prevents live network upload attempts during tests."""
    from backend.app.publishers.youtube.youtube_client import youtube_client
    from backend.app.publishers.instagram.instagram_client import instagram_client
    from backend.app.publishers.facebook.facebook_client import facebook_client

    def mock_yt_upload(*args, **kwargs):
        video_path = kwargs.get("video_path") or (args[0] if args else None)
        if video_path:
            from backend.app.video.validator import validate_shorts_profile
            from backend.app.publishers.youtube.youtube_client import YouTubeShortsValidationError
            is_valid, reason, _ = validate_shorts_profile(Path(video_path))
            if not is_valid:
                raise YouTubeShortsValidationError(f"Cannot upload to YouTube Shorts: {reason}")

        return {
            "status": "success",
            "post_id": "test_yt_post_123",
            "url": "https://youtube.com/shorts/test_yt_post_123",
            "platform": "youtube"
        }

    def mock_ig_upload(*args, **kwargs):
        return {
            "status": "success",
            "post_id": "test_ig_post_123",
            "url": "https://instagram.com/reel/test_ig_post_123",
            "platform": "instagram"
        }

    def mock_fb_upload(*args, **kwargs):
        return {
            "status": "success",
            "post_id": "test_fb_post_123",
            "url": "https://facebook.com/reel/test_fb_post_123",
            "platform": "facebook"
        }

    monkeypatch.setattr(youtube_client, "upload_short", mock_yt_upload)
    monkeypatch.setattr(instagram_client, "publish_reel", mock_ig_upload)
    monkeypatch.setattr(facebook_client, "publish_reel", mock_fb_upload)
