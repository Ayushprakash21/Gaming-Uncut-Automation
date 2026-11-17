import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.app.security.vault import vault

def test_vault_operations():
    # 1. Basic secret
    vault.set_secret("test_service", "api_token", "secret_token_12345")
    retrieved = vault.get_secret("test_service", "api_token")
    assert retrieved == "secret_token_12345", f"Expected secret_token_12345, got {retrieved}"

    # 2. JSON secret
    sample_oauth = {"access_token": "abc", "refresh_token": "xyz", "expires_in": 3600}
    vault.save_json("test_oauth", "tokens", sample_oauth)
    tokens = vault.get_json("test_oauth", "tokens")
    assert tokens["refresh_token"] == "xyz"

    # 3. Cleanup
    vault.delete_secret("test_service", "api_token")
    vault.delete_secret("test_oauth", "tokens")
    assert vault.get_secret("test_service", "api_token") is None

    print("TEST PASSED: Secure Credential Vault operations verified.")

if __name__ == "__main__":
    test_vault_operations()
