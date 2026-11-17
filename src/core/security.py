"""
Secure Credential Vault for Gaming Uncut Automation.
Uses Windows Credential Manager via keyring with encrypted fallback.
Never exposes API tokens, client secrets, or OAuth refresh tokens in plaintext.
"""

import os
import json
import base64
import logging
from pathlib import Path
from typing import Any, Optional
import keyring
from cryptography.fernet import Fernet
from src.core.config import settings

logger = logging.getLogger("gaming_uncut.security")

VAULT_SERVICE_NAME = "GamingUncutAutomation"

class CredentialVault:
    def __init__(self):
        self._vault_dir = settings.BASE_DIR / ".vault"
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        self._cipher = self._init_cipher()

    def _init_cipher(self) -> Fernet:
        """Initializes machine-local key for fallback encrypted storage."""
        key_file = self._vault_dir / ".vault_key"
        if not key_file.exists():
            key = Fernet.generate_key()
            key_file.write_bytes(key)
            try:
                # Set hidden and system attributes on Windows
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(key_file), 0x02) # FILE_ATTRIBUTE_HIDDEN
            except Exception:
                pass
        else:
            key = key_file.read_bytes()
        return Fernet(key)

    def set_secret(self, key: str, value: str) -> bool:
        """Store a sensitive secret securely."""
        try:
            keyring.set_password(VAULT_SERVICE_NAME, key, value)
            return True
        except Exception as e:
            logger.warning(f"Keyring failed, falling back to encrypted file vault: {e}")
            encrypted = self._cipher.encrypt(value.encode("utf-8"))
            (self._vault_dir / f"{key}.enc").write_bytes(encrypted)
            return True

    def get_secret(self, key: str) -> Optional[str]:
        """Retrieve a stored secret."""
        try:
            val = keyring.get_password(VAULT_SERVICE_NAME, key)
            if val is not None:
                return val
        except Exception:
            pass

        # Fallback
        enc_file = self._vault_dir / f"{key}.enc"
        if enc_file.exists():
            try:
                decrypted = self._cipher.decrypt(enc_file.read_bytes())
                return decrypted.decode("utf-8")
            except Exception as e:
                logger.error(f"Failed to decrypt secret for {key}: {e}")
        return None

    def delete_secret(self, key: str) -> bool:
        """Deletes a stored secret."""
        success = False
        try:
            keyring.delete_password(VAULT_SERVICE_NAME, key)
            success = True
        except Exception:
            pass
        enc_file = self._vault_dir / f"{key}.enc"
        if enc_file.exists():
            try:
                enc_file.unlink()
                success = True
            except Exception:
                pass
        return success

    def save_json_secret(self, key: str, data: dict[str, Any]) -> bool:
        """Serializes and securely stores a JSON object (e.g. OAuth tokens)."""
        return self.set_secret(key, json.dumps(data))

    def get_json_secret(self, key: str) -> Optional[dict[str, Any]]:
        """Retrieves and deserializes a stored JSON secret."""
        val = self.get_secret(key)
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception as e:
            logger.error(f"Failed to parse JSON secret {key}: {e}")
            return None

    def import_google_desktop_client(self, client_secrets_path: Path) -> dict[str, Any]:
        """
        Securely imports Google OAuth 2.0 Desktop client credentials
        directly into the vault and verifies client validity.
        """
        if not client_secrets_path.exists():
            raise FileNotFoundError(f"Google client secrets not found at: {client_secrets_path}")

        raw = json.loads(client_secrets_path.read_text(encoding="utf-8"))
        info = raw.get("installed") or raw.get("web")
        if not info:
            raise ValueError("Invalid client_secrets.json: missing 'installed' or 'web' application definition.")

        self.save_json_secret("google_client_config", raw)
        self.set_secret("google_client_id", info.get("client_id", ""))
        self.set_secret("google_client_secret", info.get("client_secret", ""))
        self.set_secret("google_project_id", info.get("project_id", ""))

        return {
            "status": "success",
            "client_id": info.get("client_id")[:12] + "..." if info.get("client_id") else "",
            "project_id": info.get("project_id", "")
        }

    def import_meta_credentials(self, app_id: str, app_secret: str, page_access_token: str, instagram_account_id: str = "") -> dict[str, Any]:
        """Securely imports Meta Developer App credentials and Page tokens."""
        self.set_secret("meta_app_id", app_id.strip())
        self.set_secret("meta_app_secret", app_secret.strip())
        self.set_secret("meta_page_access_token", page_access_token.strip())
        if instagram_account_id:
            self.set_secret("meta_instagram_account_id", instagram_account_id.strip())
        return {"status": "success", "app_id": app_id}

vault = CredentialVault()
