"""Encrypted backup and restore for Presidium.

Provides secure backup and restore of all user data including:
    - Identity keys (encrypted with a separate backup password)
    - Contact list
    - Chat sessions and message history
    - Ratchet states
    - Settings and preferences
    - Plugin data

Security model:
    - Backup is encrypted with AES-256-GCM using a key derived from
      the user's backup password via Argon2id
    - Each backup file is self-contained (includes all data)
    - Backups are versioned and include integrity checks
    - The backup password is separate from the identity password
    - Backup files can be safely stored on untrusted media

Backup format (version 1):
    {
        "version": 1,
        "created_at": "2025-06-20T12:00:00Z",
        "presidium_version": "0.4.0",
        "salt": "<base64>",
        "nonce": "<base64>",
        "data": "<base64 AES-256-GCM ciphertext>",
        "checksum": "<SHA-256 of plaintext before encryption>"
    }
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from presidium.core.crypto import AES256GCM, CryptoError, derive_key_from_password

logger = logging.getLogger(__name__)

BACKUP_VERSION = 1


# ---------------------------------------------------------------------------
# Backup data collector
# ---------------------------------------------------------------------------

@dataclass
class BackupData:
    """Structured representation of all data to be backed up."""
    identity: dict[str, Any] = None  # type: ignore[assignment]
    contacts: list[dict[str, Any]] = None  # type: ignore[assignment]
    messages: list[dict[str, Any]] = None  # type: ignore[assignment]
    ratchet_states: list[dict[str, Any]] = None  # type: ignore[assignment]
    settings: dict[str, Any] = None  # type: ignore[assignment]
    plugin_data: dict[str, Any] = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity or {},
            "contacts": self.contacts or [],
            "messages": self.messages or [],
            "ratchet_states": self.ratchet_states or [],
            "settings": self.settings or {},
            "plugin_data": self.plugin_data or {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupData:
        return cls(
            identity=data.get("identity", {}),
            contacts=data.get("contacts", []),
            messages=data.get("messages", []),
            ratchet_states=data.get("ratchet_states", []),
            settings=data.get("settings", {}),
            plugin_data=data.get("plugin_data", {}),
        )


# ---------------------------------------------------------------------------
# Backup file format
# ---------------------------------------------------------------------------

@dataclass
class BackupFile:
    """Represents an encrypted backup file on disk."""
    version: int = BACKUP_VERSION
    created_at: str = ""
    presidium_version: str = "0.4.0"
    salt: str = ""        # Base64-encoded Argon2id salt
    nonce: str = ""       # Base64-encoded AES-GCM nonce
    data: str = ""        # Base64-encoded ciphertext
    checksum: str = ""    # SHA-256 hex of plaintext

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "created_at": self.created_at,
            "presidium_version": self.presidium_version,
            "salt": self.salt,
            "nonce": self.nonce,
            "data": self.data,
            "checksum": self.checksum,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupFile:
        return cls(
            version=data.get("version", 1),
            created_at=data.get("created_at", ""),
            presidium_version=data.get("presidium_version", ""),
            salt=data.get("salt", ""),
            nonce=data.get("nonce", ""),
            data=data.get("data", ""),
            checksum=data.get("checksum", ""),
        )


# ---------------------------------------------------------------------------
# Backup manager
# ---------------------------------------------------------------------------

class BackupManager:
    """Creates encrypted backups of all Presidium user data.

    Usage:
        backup_mgr = BackupManager(data_dir=Path("~/.presidium"))
        await backup_mgr.create_backup(
            password="backup-password",
            output_path=Path("presidium-backup.json"),
            data_providers={...},
        )
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    async def create_backup(
        self,
        password: str,
        output_path: Path,
        data_providers: dict[str, Any],
    ) -> Path:
        """Create an encrypted backup of all user data.

        Args:
            password: Backup encryption password.
            output_path: Where to save the backup file.
            data_providers: Dict with callbacks to fetch each data type.
                Expected keys: "identity", "contacts", "messages",
                "ratchet_states", "settings", "plugin_data"

        Returns:
            Path to the created backup file.

        Raises:
            BackupError: If backup creation fails.
        """
        if not password or len(password) < 8:
            raise BackupError("Backup password must be at least 8 characters.")

        try:
            # Collect data from providers
            backup_data = BackupData()
            for attr_name in ["identity", "contacts", "messages",
                              "ratchet_states", "settings", "plugin_data"]:
                provider = data_providers.get(attr_name)
                if provider and callable(provider):
                    result = provider()
                    if asyncio.iscoroutine(result):
                        result = await result
                    setattr(backup_data, attr_name, result)

            # Serialize to JSON
            plaintext = json.dumps(backup_data.to_dict(), ensure_ascii=False)
            plaintext_bytes = plaintext.encode("utf-8")

            # Compute checksum
            import hashlib
            checksum = hashlib.sha256(plaintext_bytes).hexdigest()

            # Derive encryption key from password
            salt, key = derive_key_from_password(
                password,
                salt=None,  # Generate new salt
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
            )

            # Encrypt with AES-256-GCM
            nonce, ciphertext = AES256GCM.encrypt(plaintext_bytes, key)

            # Build backup file
            import base64
            backup = BackupFile(
                created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                salt=base64.b64encode(salt).decode("ascii"),
                nonce=base64.b64encode(nonce).decode("ascii"),
                data=base64.b64encode(ciphertext).decode("ascii"),
                checksum=checksum,
            )

            # Write to disk
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(backup.to_dict(), f, indent=2)

            logger.info(
                "Backup created: %s (%d bytes, checksum: %s...)",
                output_path, len(ciphertext), checksum[:16],
            )
            return output_path

        except CryptoError as exc:
            raise BackupError(f"Encryption failed: {exc}") from exc
        except Exception as exc:
            raise BackupError(f"Backup creation failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Restore manager
# ---------------------------------------------------------------------------

class RestoreManager:
    """Restores user data from an encrypted backup file.

    Usage:
        restore_mgr = RestoreManager()
        data = await restore_mgr.restore(
            backup_path=Path("presidium-backup.json"),
            password="backup-password",
        )
        # data is a BackupData with all restored fields
    """

    async def restore(
        self,
        backup_path: Path,
        password: str,
    ) -> BackupData:
        """Restore data from an encrypted backup file.

        Args:
            backup_path: Path to the backup file.
            password: Backup encryption password.

        Returns:
            BackupData with all restored fields.

        Raises:
            BackupError: If restoration fails.
            BackupChecksumError: If the backup is corrupted.
        """
        try:
            # Read backup file
            with open(backup_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            backup = BackupFile.from_dict(raw)

            if backup.version > BACKUP_VERSION:
                raise BackupError(
                    f"Backup version {backup.version} is newer than "
                    f"supported version {BACKUP_VERSION}. Please update Presidium."
                )

            # Derive decryption key
            import base64
            salt = base64.b64decode(backup.salt)
            _, key = derive_key_from_password(
                password,
                salt=salt,
                time_cost=3,
                memory_cost=65536,
                parallelism=4,
            )

            # Decrypt
            nonce = base64.b64decode(backup.nonce)
            ciphertext = base64.b64decode(backup.data)
            plaintext_bytes = AES256GCM.decrypt(ciphertext, key, nonce)

            # Verify checksum
            import hashlib
            actual_checksum = hashlib.sha256(plaintext_bytes).hexdigest()
            if actual_checksum != backup.checksum:
                raise BackupChecksumError(
                    "Backup checksum mismatch. The backup may be corrupted "
                    "or the password is incorrect."
                )

            # Parse JSON
            plaintext = plaintext_bytes.decode("utf-8")
            data_dict = json.loads(plaintext)
            backup_data = BackupData.from_dict(data_dict)

            logger.info(
                "Backup restored from %s: %d contacts, %d messages",
                backup_path,
                len(backup_data.contacts or []),
                len(backup_data.messages or []),
            )
            return backup_data

        except CryptoError:
            raise BackupError(
                "Decryption failed. Wrong password?"
            )
        except BackupChecksumError:
            raise
        except json.JSONDecodeError as exc:
            raise BackupError(f"Invalid backup file format: {exc}") from exc
        except Exception as exc:
            raise BackupError(f"Restore failed: {exc}") from exc

    async def verify(
        self,
        backup_path: Path,
        password: str,
    ) -> dict[str, Any]:
        """Verify a backup file without fully restoring it.

        Returns:
            Dict with 'valid' (bool) and 'info' (metadata).
        """
        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            backup = BackupFile.from_dict(raw)

            # Try to derive key and decrypt just to verify
            import base64, hashlib
            salt = base64.b64decode(backup.salt)
            _, key = derive_key_from_password(
                password, salt=salt,
                time_cost=3, memory_cost=65536, parallelism=4,
            )
            nonce = base64.b64decode(backup.nonce)
            ciphertext = base64.b64decode(backup.data)
            plaintext_bytes = AES256GCM.decrypt(ciphertext, key, nonce)

            actual_checksum = hashlib.sha256(plaintext_bytes).hexdigest()
            valid = actual_checksum == backup.checksum

            return {
                "valid": valid,
                "info": {
                    "version": backup.version,
                    "created_at": backup.created_at,
                    "presidium_version": backup.presidium_version,
                    "size_bytes": len(ciphertext),
                },
            }

        except Exception as exc:
            return {"valid": False, "info": {"error": str(exc)}}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BackupError(Exception):
    """Base exception for backup operations."""


class BackupChecksumError(BackupError):
    """Backup checksum verification failed."""
