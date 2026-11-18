"""
Production-grade Credential Vault for Gaming Uncut Automation.
Uses Windows Credential Manager (keyring) with machine-bound Fernet encryption.
Updates database metadata without exposing plaintext secrets.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional, Dict
from datetime import datetime

import keyring
from cryptography.fernet import Fernet
from backend.app.core.config import settings
from backend.app.database.session import SessionLocal
from backend.app.models.schema import CredentialsMetadata

logger = logging.getLogger("gaming_uncut.vault")
VAULT_SERVICE = "GamingUncutAutomation"

class SecureVault:
    def __init__(self):
        self._vault_dir = settings.VAULT_DIR
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        self._cipher = self._init_key()

    def _init_key(self) -> Fernet:
        key_file = self._vault_dir / ".vault_key"
        if not key_file.exists():
            key = Fernet.generate_key()
            key_file.write_bytes(key)
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(str(key_file), 0x02)  # Hidden
            except Exception:
                pass
        else:
            key = key_file.read_bytes()
        return Fernet(key)

    def set_secret(self, service: str, key: str, value: str) -> bool:
        """Stores a secret securely and tracks metadata in DB."""
        full_key = f"{service}:{key}"
        success = False
        try:
            keyring.set_password(VAULT_SERVICE, full_key, value)
            success = True
        except Exception as e:
            logger.warning(f"Keyring unavailable, using encrypted vault: {e}")
            enc_file = self._vault_dir / f"{service}_{key}.enc"
            enc_file.write_bytes(self._cipher.encrypt(value.encode("utf-8")))
            success = True

        if success:
            self._update_meta(service, key, is_set=True)
        return success

    def get_secret(self, service: str, key: str) -> Optional[str]:
        """Retrieves a secret."""
        full_key = f"{service}:{key}"
        try:
            val = keyring.get_password(VAULT_SERVICE, full_key)
            if val is not None:
                return val
        except Exception:
            pass

        enc_file = self._vault_dir / f"{service}_{key}.enc"
        if enc_file.exists():
            try:
                dec = self._cipher.decrypt(enc_file.read_bytes())
                return dec.decode("utf-8")
            except Exception as e:
                logger.error(f"Decryption error for {full_key}: {e}")
        return None

    def delete_secret(self, service: str, key: str) -> bool:
        full_key = f"{service}:{key}"
        try:
            keyring.delete_password(VAULT_SERVICE, full_key)
        except Exception:
            pass
        enc_file = self._vault_dir / f"{service}_{key}.enc"
        if enc_file.exists():
            enc_file.unlink()
        self._update_meta(service, key, is_set=False)
        return True

    def save_json(self, service: str, key: str, data: Dict[str, Any]) -> bool:
        return self.set_secret(service, key, json.dumps(data))

    def get_json(self, service: str, key: str) -> Optional[Dict[str, Any]]:
        val = self.get_secret(service, key)
        if not val:
            return None
        try:
            return json.loads(val)
        except Exception as e:
            logger.error(f"JSON parse error for {service}:{key}: {e}")
            return None

    def _update_meta(self, service: str, key: str, is_set: bool):
        try:
            with SessionLocal() as db:
                meta = db.query(CredentialsMetadata).filter_by(service_name=service, key_name=key).first()
                if not meta:
                    meta = CredentialsMetadata(service_name=service, key_name=key, is_set=is_set)
                    db.add(meta)
                else:
                    meta.is_set = is_set
                    meta.last_updated_at = datetime.utcnow()
                db.commit()
        except Exception as e:
            logger.warning(f"Failed to update credentials metadata: {e}")

    # Convenience domain helpers
    def import_google_client_secrets(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        info = raw.get("installed") or raw.get("web")
        if not info:
            raise ValueError("Invalid client_secrets.json structure.")
        self.save_json("google", "client_config", raw)
        self.set_secret("google", "client_id", info.get("client_id", ""))
        self.set_secret("google", "client_secret", info.get("client_secret", ""))
        self.set_secret("google", "project_id", info.get("project_id", ""))
        return {
            "status": "success",
            "project_id": info.get("project_id", ""),
            "client_id": info.get("client_id", "")[:12] + "..."
        }

    def import_meta_credentials(self, app_id: str, app_secret: str, page_token: str, ig_id: str = "", page_id: str = "", token_type: str = "system_user"):
        self.set_secret("meta", "app_id", app_id.strip())
        self.set_secret("meta", "app_secret", app_secret.strip())
        self.set_secret("meta", "page_access_token", page_token.strip())
        if token_type == "system_user":
            self.set_secret("meta", "system_user_token", page_token.strip())
        if ig_id:
            self.set_secret("meta", "instagram_account_id", ig_id.strip())
        if page_id:
            self.set_secret("meta", "page_id", page_id.strip())
        # Clear cached token health so fresh verification occurs immediately
        self.delete_secret("meta", "token_health_cache")

    def set_gemini_api_key(self, api_key: str):
        self.set_secret("gemini", "api_key", api_key.strip())

    def get_gemini_api_key(self) -> Optional[str]:
        return self.get_secret("gemini", "api_key")

vault = SecureVault()
