"""
Automated Test Suite for YouTube OAuth Credential Import & Validation.
Tests:
- Valid client_secrets.json import & parsing
- Windows path resolution with surrounding quotes and whitespace
- Missing file error handling
- Malformed JSON and missing OAuth client keys
- Vault storage and write verification
- Confirmation that secrets are never leaked in responses
"""

import sys
import json
from pathlib import Path

# Add project root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.security.vault import vault

client = TestClient(app)

def test_oauth_import_missing_file():
    # 1. Non-existent file
    res = client.post(
        "/api/settings/import-google-secrets",
        json={"path": "C:\\non_existent_folder_xyz\\client_secrets.json"}
    )
    assert res.status_code == 400
    assert "File not found" in res.json()["detail"]
    print("PASS: Missing file correctly rejected with 400.")

def test_oauth_import_missing_path_param():
    # 2. Empty path
    res = client.post("/api/settings/import-google-secrets", json={"path": "   "})
    assert res.status_code == 400
    assert "Missing 'path' parameter" in res.json()["detail"]
    print("PASS: Empty path correctly rejected.")

def test_oauth_import_invalid_json():
    # 3. Invalid JSON file
    tmp_file = ROOT_DIR / "data" / "temporary" / "invalid.json"
    tmp_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_file.write_text("NOT_JSON_DATA!!!", encoding="utf-8")

    res = client.post("/api/settings/import-google-secrets", json={"path": str(tmp_file)})
    assert res.status_code == 400
    assert "Invalid JSON" in res.json()["detail"]
    tmp_file.unlink()
    print("PASS: Non-JSON file correctly rejected.")

def test_oauth_import_missing_keys():
    # 4. JSON without 'installed' or 'web'
    tmp_file = ROOT_DIR / "data" / "temporary" / "bad_structure.json"
    tmp_file.write_text(json.dumps({"some_key": "some_value"}), encoding="utf-8")

    res = client.post("/api/settings/import-google-secrets", json={"path": str(tmp_file)})
    assert res.status_code == 400
    assert "Missing 'installed' or 'web'" in res.json()["detail"]
    tmp_file.unlink()
    print("PASS: Missing installed/web keys correctly rejected.")

def test_oauth_import_valid_file_and_quoted_path():
    # 5. Valid file with Windows quotes
    tmp_file = ROOT_DIR / "data" / "temporary" / "test_client_secrets.json"
    sample_data = {
        "installed": {
            "client_id": "test-client-id-12345.apps.googleusercontent.com",
            "project_id": "test-gaming-project",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_secret": "TOP_SECRET_STRING_DO_NOT_LEAK",
            "redirect_uris": ["http://localhost"]
        }
    }
    tmp_file.write_text(json.dumps(sample_data), encoding="utf-8")

    # Pass with surrounding double quotes (simulating Windows 'Copy as Path')
    quoted_path = f'"{tmp_file.resolve()}"'
    res = client.post("/api/settings/import-google-secrets", json={"path": quoted_path})
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert data["project_id"] == "test-gaming-project"
    # Ensure secret is NOT in response
    assert "TOP_SECRET_STRING_DO_NOT_LEAK" not in json.dumps(data)
    # Check client ID preview exists
    assert "client_id_preview" in data

    # 6. Verify vault storage directly
    stored_id = vault.get_secret("google", "client_id")
    stored_secret = vault.get_secret("google", "client_secret")
    stored_project = vault.get_secret("google", "project_id")

    assert stored_id == "test-client-id-12345.apps.googleusercontent.com"
    assert stored_secret == "TOP_SECRET_STRING_DO_NOT_LEAK"
    assert stored_project == "test-gaming-project"

    tmp_file.unlink()
    print("PASS: Valid OAuth JSON imported, Windows quoted path parsed, secret verified in Vault and excluded from response.")

def test_oauth_import_form_data_support():
    # 7. Form-encoded payload support
    tmp_file = ROOT_DIR / "data" / "temporary" / "test_form_secrets.json"
    sample_data = {
        "installed": {
            "client_id": "form-client-id-999.apps.googleusercontent.com",
            "project_id": "form-project",
            "client_secret": "FORM_SECRET_STRING"
        }
    }
    tmp_file.write_text(json.dumps(sample_data), encoding="utf-8")

    res = client.post(
        "/api/settings/import-google-secrets",
        data={"path": str(tmp_file.resolve())}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "success"
    assert res.json()["project_id"] == "form-project"

    tmp_file.unlink()
    print("PASS: Form data encoding supported and verified.")

if __name__ == "__main__":
    test_oauth_import_missing_file()
    test_oauth_import_missing_path_param()
    test_oauth_import_invalid_json()
    test_oauth_import_missing_keys()
    test_oauth_import_valid_file_and_quoted_path()
    test_oauth_import_form_data_support()
    print("\nALL OAUTH IMPORT TESTS PASSED SUCCESSFULLY!")
